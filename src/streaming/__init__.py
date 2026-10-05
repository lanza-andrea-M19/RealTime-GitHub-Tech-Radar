"""Real-time streaming and telemetry engine for Tech Radar."""

from src.streaming.models import (
    IncomingGitHubEvent,
    RealTimeAlert,
    TriageResult,
    TriageCategory,
    AlertSeverity,
)
from src.streaming.broker import StreamBroker, InMemoryBroker, RedpandaBroker, get_stream_broker
from src.streaming.spike_detector import SlidingWindowSpikeDetector
from src.streaming.triage import RealTimeTriager
from src.streaming.processor import EventStreamProcessor
from src.streaming.poller_daemon import StreamingPollerDaemon

__all__ = [
    "IncomingGitHubEvent",
    "RealTimeAlert",
    "TriageResult",
    "TriageCategory",
    "AlertSeverity",
    "StreamBroker",
    "InMemoryBroker",
    "RedpandaBroker",
    "get_stream_broker",
    "SlidingWindowSpikeDetector",
    "RealTimeTriager",
    "EventStreamProcessor",
    "StreamingPollerDaemon",
]
