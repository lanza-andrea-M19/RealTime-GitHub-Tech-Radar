import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
import httpx
from rich.console import Console

from src.common.config import settings
from src.streaming.models import IncomingGitHubEvent
from src.streaming.processor import EventStreamProcessor
from src.services.radar_core import DEFAULT_MONITORED_REPOSITORIES

console = Console()
logger = logging.getLogger(__name__)


class StreamingPollerDaemon:
    """Continuously polls target GitHub repositories for new events and pushes to the stream processor."""

    def __init__(
        self,
        processor: EventStreamProcessor,
        repos: list[str] | None = None,
        poll_interval: int | None = None,
    ):
        self.processor = processor
        self.repos = repos or DEFAULT_MONITORED_REPOSITORIES
        self.poll_interval = poll_interval or settings.poll_interval_seconds
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "TechRadar-Streaming-Poller/2.0",
        }
        if settings.github_token:
            self.headers["Authorization"] = f"Bearer {settings.github_token}"

        # repo -> ISO 8601 high-watermark timestamp
        self._watermarks: dict[str, str] = {}
        # repo -> etag string
        self._etags: dict[str, str] = {}
        self._running = False

    async def fetch_updates_for_repo(self, client: httpx.AsyncClient, repo: str) -> list[dict[str, Any]]:
        """Polls for issues created or updated since the last watermark."""
        url = f"https://api.github.com/repos/{repo}/issues"
        params: dict[str, Any] = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": 20,
        }
        if repo in self._watermarks:
            params["since"] = self._watermarks[repo]

        headers = dict(self.headers)
        if repo in self._etags:
            headers["If-None-Match"] = self._etags[repo]

        try:
            resp = await client.get(url, params=params, headers=headers, timeout=20.0)
            if resp.status_code == 304:
                # Not Modified - no new events
                return []

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                logger.warning(f"GitHub rate limit hit while polling {repo}.")
                return []

            resp.raise_for_status()

            if "ETag" in resp.headers:
                self._etags[repo] = resp.headers["ETag"]

            items = resp.json()
            if items:
                # Update watermark with the most recent updated_at
                latest_updated = items[0].get("updated_at") or items[0].get("created_at")
                if latest_updated:
                    self._watermarks[repo] = latest_updated

            return items
        except Exception as e:
            logger.error(f"Error polling {repo}: {e}")
            return []

    async def run(self) -> None:
        """Continuous async polling loop."""
        self._running = True
        logger.info(f"StreamingPollerDaemon active for {self.repos} (interval: {self.poll_interval}s).")

        async with httpx.AsyncClient() as client:
            while self._running:
                for repo in self.repos:
                    if not self._running:
                        break
                    try:
                        raw_items = await self.fetch_updates_for_repo(client, repo)
                        for raw in raw_items:
                            labels = [lbl.get("name", "") for lbl in raw.get("labels", []) if isinstance(lbl, dict)]
                            author = raw.get("user", {}).get("login") if raw.get("user") else None
                            event = IncomingGitHubEvent(
                                repo=repo,
                                event_type="pull_request" if "pull_request" in raw else "issue",
                                action="opened" if raw.get("state") == "open" else "closed",
                                issue_number=raw.get("number", 0),
                                title=raw.get("title", ""),
                                author=author,
                                state=raw.get("state", "open"),
                                labels=labels,
                                body=raw.get("body") or "",
                                comments_count=raw.get("comments", 0),
                                upstream_created_at=raw.get("created_at") or datetime.now(timezone.utc).isoformat(),
                                upstream_updated_at=raw.get("updated_at"),
                                raw_payload=raw,
                            )
                            # Dispatch to processor
                            await self.processor.process_single_event(event)
                    except Exception as e:
                        logger.error(f"Polling loop exception on {repo}: {e}")

                # Sleep before next round
                try:
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    break

    def stop(self) -> None:
        """Stops the daemon."""
        self._running = False
