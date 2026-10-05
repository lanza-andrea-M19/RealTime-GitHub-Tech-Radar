import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from src.common.db import get_db, verify_connection, ensure_schema
from src.common.config import settings
from src.streaming.models import IncomingGitHubEvent, RealTimeAlert
from src.streaming.broker import StreamBroker, get_stream_broker
from src.streaming.spike_detector import SlidingWindowSpikeDetector
from src.streaming.triage import RealTimeTriager
from src.services.radar_core import save_realtime_alert

logger = logging.getLogger(__name__)


class EventStreamProcessor:
    """Asynchronous real-time stream processor for GitHub telemetry."""

    def __init__(
        self,
        broker: StreamBroker | None = None,
        spike_detector: SlidingWindowSpikeDetector | None = None,
        triager: RealTimeTriager | None = None,
    ):
        self.broker = broker
        self.spike_detector = spike_detector or SlidingWindowSpikeDetector()
        self.triager = triager or RealTimeTriager()
        self._running = False
        self._listeners: list[asyncio.Queue[RealTimeAlert]] = []
        self._processed_count = 0
        self._alerts_count = 0

    def subscribe_alerts(self) -> asyncio.Queue[RealTimeAlert]:
        """Subscribes an SSE or websocket listener queue for live alerts."""
        q: asyncio.Queue[RealTimeAlert] = asyncio.Queue(maxsize=100)
        self._listeners.append(q)
        return q

    def unsubscribe_alerts(self, q: asyncio.Queue[RealTimeAlert]) -> None:
        """Removes an alert listener queue."""
        if q in self._listeners:
            self._listeners.remove(q)

    async def broadcast_alert(self, alert: RealTimeAlert) -> None:
        """Dispatches an alert to all registered real-time listeners and database."""
        self._alerts_count += 1
        # 1. Save to DB
        new_id = save_realtime_alert(alert.model_dump())
        if new_id:
            alert.id = new_id

        # 2. Broadcast to memory queues (SSE/Webhooks/CLI)
        for q in list(self._listeners):
            try:
                q.put_nowait(alert)
            except asyncio.QueueFull:
                logger.warning("Listener queue full, dropping alert.")

    def save_event_to_postgres(self, event: IncomingGitHubEvent) -> int | None:
        """Persists the incoming event into PostgreSQL raw_github_events and returns event ID."""
        if not verify_connection():
            logger.warning("Database offline, event not persisted to PostgreSQL.")
            return None

        upsert_query = """
        INSERT INTO raw_github_events (
            repo, event_type, issue_number, title, author, state, labels,
            body, comments_count, upstream_created_at, upstream_updated_at,
            payload, ingested_at
        ) VALUES (
            %(repo)s, %(event_type)s, %(issue_number)s, %(title)s, %(author)s,
            %(state)s, %(labels)s::jsonb, %(body)s, %(comments_count)s,
            %(upstream_created_at)s, %(upstream_updated_at)s,
            %(payload)s::jsonb, %(ingested_at)s
        )
        ON CONFLICT (repo, issue_number, event_type) DO UPDATE SET
            title = EXCLUDED.title,
            author = EXCLUDED.author,
            state = EXCLUDED.state,
            labels = EXCLUDED.labels,
            body = EXCLUDED.body,
            comments_count = EXCLUDED.comments_count,
            upstream_updated_at = EXCLUDED.upstream_updated_at,
            payload = EXCLUDED.payload,
            ingested_at = EXCLUDED.ingested_at
        RETURNING id;
        """
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        upsert_query,
                        {
                            "repo": event.repo,
                            "event_type": event.event_type,
                            "issue_number": event.issue_number,
                            "title": event.title,
                            "author": event.author,
                            "state": event.state,
                            "labels": json.dumps(event.labels),
                            "body": event.body,
                            "comments_count": event.comments_count,
                            "upstream_created_at": event.upstream_created_at,
                            "upstream_updated_at": event.upstream_updated_at,
                            "payload": json.dumps(event.raw_payload or event.model_dump()),
                            "ingested_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return row["id"] if row else None
        except Exception as e:
            logger.error(f"Error persisting raw event: {e}")
            return None

    async def index_vector_embedding(self, event_id: int, event: IncomingGitHubEvent) -> bool:
        """Immediately generates and saves vector embedding for real-time semantic retrieval."""
        if not settings.gemini_api_key or not verify_connection():
            return False

        try:
            from src.embeddings.indexer import get_embedding_model, build_chunk_text
            embedder = get_embedding_model()
            chunk_text = build_chunk_text(
                repo=event.repo,
                issue_number=event.issue_number,
                title=event.title,
                body=event.body,
                labels_raw=event.labels,
            )
            # Embed asynchronously using run_in_executor to avoid blocking event loop
            loop = asyncio.get_running_loop()
            vectors = await loop.run_in_executor(None, embedder.embed_documents, [chunk_text])

            if not vectors:
                return False

            insert_query = """
            INSERT INTO issue_embeddings (
                event_id, repo, issue_number, title, chunk_text, embedding
            ) VALUES (
                %(event_id)s, %(repo)s, %(issue_number)s, %(title)s, %(chunk_text)s, %(embedding)s::vector
            )
            ON CONFLICT (event_id) DO UPDATE SET
                title = EXCLUDED.title,
                chunk_text = EXCLUDED.chunk_text,
                embedding = EXCLUDED.embedding;
            """
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        insert_query,
                        {
                            "event_id": event_id,
                            "repo": event.repo,
                            "issue_number": event.issue_number,
                            "title": event.title,
                            "chunk_text": chunk_text,
                            "embedding": vectors[0],
                        },
                    )
                conn.commit()
            logger.debug(f"Generated real-time embedding for event {event_id} ({event.repo}#{event.issue_number})")
            return True
        except Exception as e:
            logger.warning(f"Real-time embedding generation skipped or failed: {e}")
            return False

    async def process_single_event(self, event: IncomingGitHubEvent) -> list[RealTimeAlert]:
        """Processes one event: persists it, computes embeddings, detects spikes, and triages."""
        self._processed_count += 1
        alerts: list[RealTimeAlert] = []

        # 1. Save to DB
        event_id = self.save_event_to_postgres(event)

        # 2. Real-time embedding indexing
        if event_id:
            await self.index_vector_embedding(event_id, event)

        # 3. Sliding Window Spike Detection
        spike_alert = self.spike_detector.check_event(event)
        if spike_alert:
            alerts.append(spike_alert)
            await self.broadcast_alert(spike_alert)

        # 4. Real-time Triage Classification
        triage_res = await self.triager.triage_event(event)
        if triage_res.is_alert_worthy:
            alert_type = (
                "security_cve" if triage_res.category == "Security/CVE"
                else "breaking_change" if triage_res.category == "Breaking Change"
                else "critical_bug"
            )
            triage_alert = RealTimeAlert(
                repo=event.repo,
                issue_number=event.issue_number,
                title=f"[{triage_res.category}] {event.title}",
                alert_type=alert_type,
                severity=triage_res.severity,
                summary=triage_res.summary,
                affected_versions=triage_res.affected_versions,
                workaround=triage_res.workaround,
                payload={
                    "category": triage_res.category,
                    "author": event.author,
                    "upstream_created_at": event.upstream_created_at,
                },
            )
            alerts.append(triage_alert)
            await self.broadcast_alert(triage_alert)

        return alerts

    async def run_consumer_loop(self) -> None:
        """Main processing loop consuming from the stream broker."""
        if not self.broker:
            self.broker = await get_stream_broker()

        await self.broker.start()
        ensure_schema()
        self._running = True
        logger.info("Real-time EventStreamProcessor consumer loop started.")

        try:
            async for event in self.broker.consume_events():
                if not self._running:
                    break
                try:
                    await self.process_single_event(event)
                except Exception as e:
                    logger.error(f"Error processing event {event.repo}#{event.issue_number}: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stops the processor and cleanly terminates broker connections."""
        self._running = False
        if self.broker:
            await self.broker.stop()
        logger.info("EventStreamProcessor stopped.")

    def get_telemetry(self) -> dict[str, Any]:
        """Returns processing metrics and sliding window state."""
        return {
            "processed_events": self._processed_count,
            "total_alerts_emitted": self._alerts_count,
            "active_sse_listeners": len(self._listeners),
            "sliding_window_stats": self.spike_detector.get_stats(),
        }
