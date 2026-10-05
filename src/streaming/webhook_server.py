import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from src.common.config import settings
from src.streaming.models import IncomingGitHubEvent, RealTimeAlert
from src.streaming.processor import EventStreamProcessor
from src.services.radar_core import fetch_active_alerts, get_database_stats

logger = logging.getLogger(__name__)

# Global shared processor instance
processor = EventStreamProcessor()


def verify_github_signature(payload_body: bytes, secret: str, header_signature: str | None) -> bool:
    """Verifies HMAC SHA-256 signature from GitHub webhook headers."""
    if not secret:
        return True
    if not header_signature:
        return False

    prefix = "sha256="
    if not header_signature.startswith(prefix):
        return False

    expected_signature = header_signature[len(prefix):]
    mac = hmac.new(secret.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    computed_signature = mac.hexdigest()
    return hmac.compare_digest(computed_signature, expected_signature)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages background consumer loop during app lifespan."""
    consumer_task = asyncio.create_task(processor.run_consumer_loop())
    logger.info("Webhook server initialized and consumer loop launched.")
    try:
        yield
    finally:
        await processor.stop()
        consumer_task.cancel()


app = FastAPI(
    title="Tech Radar Real-Time Telemetry & Webhook Gateway",
    description="Real-time GitHub Webhook ingestion, SSE alert streaming, and sliding-window early warnings.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, Any]:
    """Health status and telemetry."""
    return {
        "status": "healthy",
        "service": "tech-radar-realtime",
        "telemetry": processor.get_telemetry(),
    }


@app.get("/api/v1/stats")
def get_stats() -> dict[str, Any]:
    """Retrieves real-time processing statistics and database metrics."""
    db_stats = {}
    try:
        db_stats = json.loads(get_database_stats())
    except Exception:
        db_stats = {"database": "offline"}

    return {
        "processor_telemetry": processor.get_telemetry(),
        "database_metrics": db_stats,
    }


@app.get("/api/v1/alerts")
def list_alerts(repo: str | None = None, severity: str | None = None, limit: int = 20) -> Any:
    """Returns stored real-time alerts."""
    raw = fetch_active_alerts(repo=repo, severity=severity, limit=limit)
    try:
        return json.loads(raw)
    except Exception:
        return {"message": raw}


@app.post("/api/v1/github/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook_endpoint(
    request: Request,
    x_github_event: str = Header(default="issues", alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """Receives and processes live GitHub Webhook events in real time."""
    body_bytes = await request.body()

    if settings.webhook_secret:
        if not verify_github_signature(body_bytes, settings.webhook_secret, x_hub_signature_256):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Hub-Signature-256 header.",
            )

    # Ping event response
    if x_github_event == "ping":
        return {"status": "ok", "message": "Pong! Webhook configured successfully."}

    # Only process issues and pull requests
    if x_github_event not in ("issues", "pull_request"):
        return {"status": "ignored", "reason": f"Event '{x_github_event}' is not monitored."}

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    action = payload.get("action", "unknown")
    item = payload.get("issue") or payload.get("pull_request")
    if not item:
        return {"status": "ignored", "reason": "No issue or pull_request object in payload."}

    repo_data = payload.get("repository", {})
    repo_name = repo_data.get("full_name") or f"{repo_data.get('owner', {}).get('login')}/{repo_data.get('name')}"

    labels = [lbl.get("name", "") for lbl in item.get("labels", []) if isinstance(lbl, dict)]
    author = item.get("user", {}).get("login") if item.get("user") else None

    event = IncomingGitHubEvent(
        repo=repo_name,
        event_type="pull_request" if x_github_event == "pull_request" else "issue",
        action=action,
        issue_number=item.get("number", 0),
        title=item.get("title", ""),
        author=author,
        state=item.get("state", "open"),
        labels=labels,
        body=item.get("body") or "",
        comments_count=item.get("comments", 0),
        upstream_created_at=item.get("created_at") or datetime.now(timezone.utc).isoformat(),
        upstream_updated_at=item.get("updated_at"),
        raw_payload=payload,
    )

    # Publish to stream broker if available, or process immediately
    if processor.broker:
        await processor.broker.publish_event(event)
    else:
        # Direct async processing
        asyncio.create_task(processor.process_single_event(event))

    return {
        "status": "accepted",
        "repo": repo_name,
        "issue_number": event.issue_number,
        "event_type": event.event_type,
        "action": action,
    }


@app.get("/api/v1/stream/alerts")
async def stream_alerts() -> StreamingResponse:
    """Streams real-time alerts live to connected clients via Server-Sent Events (SSE)."""
    async def event_generator():
        listener_q = processor.subscribe_alerts()
        try:
            # Welcome handshake event
            yield f"event: connected\ndata: {json.dumps({'message': 'Connected to Tech Radar SSE alert stream', 'timestamp': processor.get_telemetry()})}\n\n"
            while True:
                try:
                    alert: RealTimeAlert = await asyncio.wait_for(listener_q.get(), timeout=15.0)
                    yield f"event: alert\ndata: {alert.model_dump_json()}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat ping
                    yield ": ping\n\n"
        finally:
            processor.unsubscribe_alerts(listener_q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
