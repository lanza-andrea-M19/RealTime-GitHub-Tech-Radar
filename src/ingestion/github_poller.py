import json
import logging
from datetime import datetime, timezone
from typing import Any
import httpx
from rich.console import Console
from rich.table import Table

from src.common.config import settings
from src.common.db import get_db, verify_connection

console = Console()
logger = logging.getLogger(__name__)

DEFAULT_REPOSITORIES = [
    "duckdb/duckdb",
    "pola-rs/polars",
    "pydantic/pydantic",
]


class GitHubIssuePoller:
    """Fetches issues from GitHub REST API and persists them into PostgreSQL."""

    def __init__(self, token: str | None = None):
        self.token = token or settings.github_token
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "TechRadar-Ingestion-Engine/1.0",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def fetch_repo_issues(
        self,
        repo: str,
        state: str = "all",
        per_page: int = 50,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Downloads issues from GitHub REST API for a specific repository."""
        url = f"https://api.github.com/repos/{repo}/issues"
        params = {
            "state": state,
            "sort": "created",
            "direction": "desc",
            "per_page": per_page,
            "page": page,
        }

        console.print(f"[cyan]Fetching {per_page} issues from [bold]{repo}[/bold] (page {page})...[/cyan]")
        with httpx.Client(timeout=30.0, headers=self.headers) as client:
            response = client.get(url, params=params)

            # Check rate limiting headers
            rate_limit = response.headers.get("x-ratelimit-limit", "N/A")
            rate_remaining = response.headers.get("x-ratelimit-remaining", "N/A")
            console.print(f"  [dim]API Rate limit: {rate_remaining}/{rate_limit} remaining[/dim]")

            if response.status_code == 403 and "rate limit" in response.text.lower():
                raise RuntimeError(
                    f"GitHub API rate limit exceeded. Provide a valid GITHUB_TOKEN in .env. Details: {response.text}"
                )

            response.raise_for_status()
            items = response.json()
            console.print(f"  [green]Successfully fetched {len(items)} items from {repo}.[/green]")
            return items

    def parse_issue(self, repo: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalizes a raw GitHub issue payload into a structured event dict."""
        is_pr = "pull_request" in raw
        event_type = "pull_request" if is_pr else "issue"

        labels = [l.get("name", "") for l in raw.get("labels", []) if isinstance(l, dict)]
        author = raw.get("user", {}).get("login") if raw.get("user") else None

        created_at_str = raw.get("created_at")
        updated_at_str = raw.get("updated_at")

        return {
            "repo": repo,
            "event_type": event_type,
            "issue_number": raw.get("number"),
            "title": raw.get("title", ""),
            "author": author,
            "state": raw.get("state", "open"),
            "labels": json.dumps(labels),
            "body": raw.get("body") or "",
            "comments_count": raw.get("comments", 0),
            "upstream_created_at": created_at_str,
            "upstream_updated_at": updated_at_str,
            "payload": json.dumps(raw),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

    def save_to_postgres(self, records: list[dict[str, Any]]) -> int:
        """Stores or updates issue records in PostgreSQL raw_github_events table."""
        if not records:
            return 0

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
            ingested_at = EXCLUDED.ingested_at;
        """

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.executemany(upsert_query, records)
            conn.commit()

        return len(records)

    def run(self, repos: list[str] | None = None, limit_per_repo: int = 50) -> dict[str, int]:
        """Runs the ingestion process across target repositories."""
        target_repos = repos or DEFAULT_REPOSITORIES
        summary = {}

        db_available = verify_connection()
        if not db_available:
            console.print("[yellow]PostgreSQL is currently unreachable. Make sure Docker is running.[/yellow]")
            console.print("[yellow]To start the database: docker compose -f docker/docker-compose.yml up -d postgres[/yellow]")

        for repo in target_repos:
            try:
                raw_issues = self.fetch_repo_issues(repo, per_page=min(limit_per_repo, 100))
                parsed_records = [self.parse_issue(repo, item) for item in raw_issues]

                if db_available:
                    saved_count = self.save_to_postgres(parsed_records)
                    summary[repo] = saved_count
                    console.print(f"  [bold green]Saved {saved_count} records into PostgreSQL for {repo}.[/bold green]\n")
                else:
                    summary[repo] = len(parsed_records)
                    console.print(f"  [dim]Fetched {len(parsed_records)} records (skipped DB write because DB is offline).[/dim]\n")

            except Exception as e:
                console.print(f"  [bold red]Error ingesting {repo}: {e}[/bold red]\n")
                summary[repo] = 0

        self.print_summary(summary)
        return summary

    def print_summary(self, summary: dict[str, int]) -> None:
        """Prints a summary table of the ingestion execution."""
        table = Table(title="GitHub Ingestion Summary")
        table.add_column("Repository", style="cyan", no_wrap=True)
        table.add_column("Processed Records", justify="right", style="green")

        total = 0
        for repo, count in summary.items():
            table.add_row(repo, str(count))
            total += count

        table.add_section()
        table.add_row("Total", str(total), style="bold yellow")
        console.print(table)


if __name__ == "__main__":
    poller = GitHubIssuePoller()
    poller.run()
