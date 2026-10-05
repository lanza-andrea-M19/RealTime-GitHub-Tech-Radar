import argparse
import asyncio
import logging
import uvicorn
from rich.console import Console
from rich.panel import Panel

from src.common.config import settings
from src.streaming.processor import EventStreamProcessor
from src.streaming.webhook_server import app, processor
from src.streaming.poller_daemon import StreamingPollerDaemon

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def run_all(host: str, port: int, poll_interval: int) -> None:
    """Runs both the Webhook Server and the Streaming Poller Daemon concurrently."""
    console.print(
        Panel(
            f"[bold green]Starting Tech Radar Real-Time Engine[/bold green]\n\n"
            f"• Webhook Gateway: [cyan]http://{host}:{port}/api/v1/github/webhook[/cyan]\n"
            f"• Real-Time SSE Feed: [cyan]http://{host}:{port}/api/v1/stream/alerts[/cyan]\n"
            f"• Telemetry Stats: [cyan]http://{host}:{port}/api/v1/stats[/cyan]\n"
            f"• Monitored Repos: [yellow]duckdb/duckdb, pola-rs/polars, pydantic/pydantic[/yellow]\n"
            f"• Continuous Poller Interval: [magenta]{poll_interval}s[/magenta]",
            title="⚡ Real-Time Engine ⚡",
            border_style="cyan",
        )
    )

    # Configure Uvicorn server
    config = uvicorn.Config(app=app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    # Configure Poller Daemon attached to the same processor instance
    poller = StreamingPollerDaemon(processor=processor, poll_interval=poll_interval)

    # Run both as concurrent tasks
    await asyncio.gather(
        server.serve(),
        poller.run(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Tech Radar Real-Time Engine.")
    parser.add_argument(
        "--mode",
        choices=["all", "webhook", "poller"],
        default="all",
        help="Components to run: 'all' (default), 'webhook', or 'poller'.",
    )
    parser.add_argument(
        "--host",
        default=settings.webhook_host,
        help=f"Host for Webhook Server (default: {settings.webhook_host}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.webhook_port,
        help=f"Port for Webhook Server (default: {settings.webhook_port}).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=settings.poll_interval_seconds,
        help=f"Interval in seconds between GitHub polls (default: {settings.poll_interval_seconds}).",
    )

    args = parser.parse_args()

    if args.mode == "webhook":
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.mode == "poller":
        proc = EventStreamProcessor()
        daemon = StreamingPollerDaemon(processor=proc, poll_interval=args.poll_interval)
        asyncio.run(daemon.run())
    else:
        asyncio.run(run_all(args.host, args.port, args.poll_interval))


if __name__ == "__main__":
    main()
