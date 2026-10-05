import time
import logging
from collections import defaultdict, deque
from typing import NamedTuple
from src.common.config import settings
from src.streaming.models import IncomingGitHubEvent, RealTimeAlert

logger = logging.getLogger(__name__)

CRITICAL_KEYWORDS = {
    "crash", "segfault", "segmentation fault", "memory leak", "oom",
    "panic", "fatal", "regression", "breaking change", "data corruption"
}


class WindowEntry(NamedTuple):
    timestamp: float
    issue_number: int
    title: str
    is_critical_keyword: bool


class SlidingWindowSpikeDetector:
    """Detects sudden spikes or bursts of issues in a repository using sliding time windows."""

    def __init__(
        self,
        window_seconds: int | None = None,
        threshold: int | None = None,
        cooldown_seconds: int = 180,
    ):
        self.window_seconds = window_seconds or settings.spike_window_seconds
        self.threshold = threshold or settings.spike_threshold
        self.cooldown_seconds = cooldown_seconds

        # repo -> deque of WindowEntry
        self._windows: dict[str, deque[WindowEntry]] = defaultdict(deque)
        # repo -> last_alert_time
        self._last_alert: dict[str, float] = {}

    def _purge_old(self, repo: str, now: float) -> None:
        """Removes entries older than the window horizon."""
        window = self._windows[repo]
        cutoff = now - self.window_seconds
        while window and window[0].timestamp < cutoff:
            window.popleft()

    def check_event(self, event: IncomingGitHubEvent) -> RealTimeAlert | None:
        """Records an incoming event and evaluates whether a spike condition has been met."""
        now = time.time()
        repo = event.repo
        self._purge_old(repo, now)

        text_to_check = f"{event.title} {event.body} {' '.join(event.labels)}".lower()
        has_critical_keyword = any(kw in text_to_check for kw in CRITICAL_KEYWORDS)

        entry = WindowEntry(
            timestamp=now,
            issue_number=event.issue_number,
            title=event.title,
            is_critical_keyword=has_critical_keyword,
        )
        self._windows[repo].append(entry)

        current_count = len(self._windows[repo])
        critical_count = sum(1 for e in self._windows[repo] if e.is_critical_keyword)

        # Spike condition: general threshold exceeded OR lower threshold with critical keywords
        is_spike = current_count >= self.threshold or (critical_count >= 3 and current_count >= 3)

        if not is_spike:
            return None

        # Check cooldown to prevent duplicate alerts
        last_alert = self._last_alert.get(repo, 0.0)
        if (now - last_alert) < self.cooldown_seconds:
            logger.debug(f"Spike detected on {repo} but suppressed by cooldown ({int(now - last_alert)}s < {self.cooldown_seconds}s).")
            return None

        self._last_alert[repo] = now
        severity = "critical" if critical_count >= 2 else "high"
        summary = (
            f"Anomalous spike detected on {repo}: {current_count} events recorded within "
            f"{int(self.window_seconds / 60)} minutes ({critical_count} critical/crash mentions)."
        )

        logger.warning(f"🚨 SPIKE ALERT: {summary}")

        return RealTimeAlert(
            repo=repo,
            issue_number=event.issue_number,
            title=f"Spike Alert: {current_count} issues in {int(self.window_seconds / 60)}m on {repo}",
            alert_type="spike_detected",
            severity=severity,
            summary=summary,
            payload={
                "events_in_window": current_count,
                "critical_keyword_count": critical_count,
                "window_seconds": self.window_seconds,
                "recent_issues": [e.issue_number for e in self._windows[repo]],
            },
        )

    def get_stats(self) -> dict[str, dict[str, int]]:
        """Returns current counts per repository in the active sliding window."""
        now = time.time()
        stats = {}
        for repo in list(self._windows.keys()):
            self._purge_old(repo, now)
            stats[repo] = {
                "active_window_events": len(self._windows[repo]),
                "critical_mentions": sum(1 for e in self._windows[repo] if e.is_critical_keyword),
            }
        return stats
