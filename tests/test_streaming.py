import asyncio
import hashlib
import hmac
import json
import pytest
from starlette.testclient import TestClient

from src.streaming.models import IncomingGitHubEvent, RealTimeAlert, TriageResult
from src.streaming.spike_detector import SlidingWindowSpikeDetector
from src.streaming.triage import RealTimeTriager
from src.streaming.broker import InMemoryBroker
from src.streaming.processor import EventStreamProcessor
from src.streaming.webhook_server import app, verify_github_signature
from src.mcp_server.server import app as mcp_app


def test_models_instantiation():
    """Verify that streaming data models instantiate and validate as expected."""
    event = IncomingGitHubEvent(
        repo="duckdb/duckdb",
        issue_number=1234,
        title="Segmentation fault when running multi-threaded parquet scan",
        labels=["bug", "crash"],
        body="Reproduction: SELECT * FROM read_parquet('test.parquet'); crashes with SIGSEGV in v1.0.0",
    )
    assert event.repo == "duckdb/duckdb"
    assert event.issue_number == 1234
    assert "crash" in event.labels

    alert = RealTimeAlert(
        repo="duckdb/duckdb",
        issue_number=1234,
        title="Crash Alert",
        alert_type="critical_bug",
        severity="critical",
        summary="Segmentation fault in parquet scan",
    )
    assert alert.severity == "critical"
    assert alert.alert_type == "critical_bug"


def test_sliding_window_spike_detector():
    """Verify that sliding window triggers alerts when threshold is met."""
    detector = SlidingWindowSpikeDetector(window_seconds=60, threshold=3, cooldown_seconds=10)

    event1 = IncomingGitHubEvent(repo="pydantic/pydantic", issue_number=1, title="Issue 1")
    event2 = IncomingGitHubEvent(repo="pydantic/pydantic", issue_number=2, title="Issue 2")
    event3 = IncomingGitHubEvent(repo="pydantic/pydantic", issue_number=3, title="Issue 3")

    alert1 = detector.check_event(event1)
    assert alert1 is None  # 1 event < 3

    alert2 = detector.check_event(event2)
    assert alert2 is None  # 2 events < 3

    alert3 = detector.check_event(event3)
    assert alert3 is not None  # 3 events == 3 -> Spike Alert!
    assert alert3.alert_type == "spike_detected"
    assert "pydantic/pydantic" in alert3.repo

    # Immediate 4th event should be suppressed by cooldown
    event4 = IncomingGitHubEvent(repo="pydantic/pydantic", issue_number=4, title="Issue 4")
    alert4 = detector.check_event(event4)
    assert alert4 is None


def test_realtime_triager_heuristics():
    """Verify deterministic triage classifications for security, crashes, and breaking changes."""
    triager = RealTimeTriager()

    # 1. Security CVE
    sec_event = IncomingGitHubEvent(
        repo="fastapi/fastapi",
        issue_number=999,
        title="CVE-2024-1234 Remote code execution vulnerability in header parsing",
        body="Discovered in v0.110.0. Workaround: sanitize headers with custom middleware.",
    )
    res_sec = triager.triage_heuristic(sec_event)
    assert res_sec.category == "Security/CVE"
    assert res_sec.severity == "critical"
    assert res_sec.is_alert_worthy is True
    assert res_sec.affected_versions == "v0.110.0"
    assert res_sec.workaround is not None

    # 2. Crash / Segfault
    crash_event = IncomingGitHubEvent(
        repo="pola-rs/polars",
        issue_number=1001,
        title="Memory leak and crash during group_by on large DataFrame",
        body="Causes fatal OOM panic in 0.20.1",
    )
    res_crash = triager.triage_heuristic(crash_event)
    assert res_crash.category == "Critical Bug/Crash"
    assert res_crash.severity == "critical"
    assert res_crash.is_alert_worthy is True
    assert res_crash.affected_versions == "0.20.1"

    # 3. Breaking change
    break_event = IncomingGitHubEvent(
        repo="pydantic/pydantic",
        issue_number=1002,
        title="Breaking change: validator syntax deprecated and removed in v2.0",
        body="Throws incompatible error after upgrade",
    )
    res_break = triager.triage_heuristic(break_event)
    assert res_break.category == "Breaking Change"
    assert res_break.severity == "high"
    assert res_break.is_alert_worthy is True

    # 4. Noise / Question
    question_event = IncomingGitHubEvent(
        repo="duckdb/duckdb",
        issue_number=1003,
        title="How do I configure persistent storage in Python?",
        body="Just looking for usage examples in the docs.",
    )
    res_q = triager.triage_heuristic(question_event)
    assert res_q.category == "Noise/Question"
    assert res_q.severity == "low"
    assert res_q.is_alert_worthy is False


@pytest.mark.anyio
async def test_in_memory_broker():
    """Verify that InMemoryBroker produces and consumes events asynchronously."""
    broker = InMemoryBroker()
    await broker.start()

    event = IncomingGitHubEvent(repo="duckdb/duckdb", issue_number=42, title="Broker Test")
    await broker.publish_event(event)

    consumer = broker.consume_events()
    consumed_event = await consumer.__anext__()

    assert consumed_event.repo == "duckdb/duckdb"
    assert consumed_event.issue_number == 42
    await broker.stop()


@pytest.mark.anyio
async def test_event_stream_processor_processing():
    """Verify EventStreamProcessor processes events and emits alerts."""
    detector = SlidingWindowSpikeDetector(threshold=2, cooldown_seconds=60)
    processor = EventStreamProcessor(spike_detector=detector)

    q = processor.subscribe_alerts()

    # Event with critical crash
    event = IncomingGitHubEvent(
        repo="duckdb/duckdb",
        issue_number=777,
        title="Critical segfault panic in hash join",
        body="Reproduced in v1.0.0",
    )

    alerts = await processor.process_single_event(event)
    assert len(alerts) > 0
    assert any(a.alert_type == "critical_bug" for a in alerts)

    # Queue should receive the alert
    received_alert = await asyncio.wait_for(q.get(), timeout=2.0)
    assert received_alert.repo == "duckdb/duckdb"
    assert received_alert.issue_number == 777

    processor.unsubscribe_alerts(q)


def test_webhook_signature_verification():
    """Verify HMAC SHA-256 webhook signature validation."""
    secret = "my-test-secret"
    body = b'{"action": "opened", "issue": {"number": 1}}'

    # Compute valid signature
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    valid_header = f"sha256={mac.hexdigest()}"

    assert verify_github_signature(body, secret, valid_header) is True
    assert verify_github_signature(body, secret, "sha256=invalidhex") is False
    assert verify_github_signature(body, secret, None) is False
    # If no secret configured, should pass
    assert verify_github_signature(body, "", None) is True


def test_webhook_api_endpoints():
    """Verify FastAPI webhook endpoints with TestClient."""
    client = TestClient(app)

    # 1. Health check
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "healthy"

    # 2. Stats endpoint
    resp_stats = client.get("/api/v1/stats")
    assert resp_stats.status_code == 200
    assert "processor_telemetry" in resp_stats.json()

    # 3. Webhook ping
    resp_ping = client.post("/api/v1/github/webhook", headers={"X-GitHub-Event": "ping"}, json={"zen": "test"})
    assert resp_ping.status_code == 202
    assert resp_ping.json()["status"] == "ok"

    # 4. Webhook issue event
    issue_payload = {
        "action": "opened",
        "repository": {"full_name": "duckdb/duckdb"},
        "issue": {
            "number": 555,
            "title": "Severe memory leak in window function",
            "body": "Causes segfault in v1.1.0",
            "labels": [{"name": "bug"}],
            "state": "open",
        },
    }
    resp_issue = client.post(
        "/api/v1/github/webhook",
        headers={"X-GitHub-Event": "issues"},
        json=issue_payload,
    )
    assert resp_issue.status_code == 202
    data = resp_issue.json()
    assert data["status"] == "accepted"
    assert data["repo"] == "duckdb/duckdb"
    assert data["issue_number"] == 555


@pytest.mark.anyio
async def test_mcp_server_new_alert_capabilities():
    """Verify that real-time alert tools and resources are registered on MCP server."""
    tools = await mcp_app.list_tools()
    tool_names = {t.name for t in tools}
    assert "get_active_alerts" in tool_names
    assert "triage_incoming_issue" in tool_names

    resources = await mcp_app.list_resources()
    resource_uris = {str(r.uri) for r in resources}
    assert "techradar://alerts" in resource_uris

    # Test triage tool directly
    triage_output = await mcp_app.call_tool(
        "triage_incoming_issue",
        {
            "repo": "duckdb/duckdb",
            "issue_number": 99,
            "title": "Fatal segfault on startup",
            "body": "Crash occurs on Linux x86_64",
        },
    )
    content = triage_output.content[0].text
    parsed = json.loads(content)
    assert parsed["category"] == "Critical Bug/Crash"
    assert parsed["severity"] in ("critical", "high")
