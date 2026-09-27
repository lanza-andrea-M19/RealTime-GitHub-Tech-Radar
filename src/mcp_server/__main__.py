import argparse
import sys
from src.mcp_server.server import app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Tech Radar Model Context Protocol (MCP) server."
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport layer to use: 'stdio' (default, for local clients like Claude Desktop/Cursor) or 'sse' (HTTP Server-Sent Events).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind for SSE transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind for SSE transport (default: 8000).",
    )

    args = parser.parse_args()

    if args.transport == "stdio":
        app.run(transport="stdio")
    elif args.transport == "sse":
        app.run(transport="sse", host=args.host, port=args.port)
    else:
        sys.exit(f"Unsupported transport: {args.transport}")


if __name__ == "__main__":
    main()
