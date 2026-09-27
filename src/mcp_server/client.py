"""Lightweight MCP CLI client for Tech Radar testing and discovery."""

import argparse
import asyncio
import json
import sys
from typing import Any
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


async def inspect_server(server_command: str, server_args: list[str]) -> None:
    """Connects to the Tech Radar MCP server via stdio and displays all capabilities."""
    server_params = StdioServerParameters(command=server_command, args=server_args)

    console.print(f"[bold cyan]Connecting to MCP server via stdio: {server_command} {' '.join(server_args)}[/bold cyan]")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_res = await session.initialize()
            console.print(f"[bold green]Connected! Protocol version: {init_res.protocol_version}[/bold green]\n")

            # Tools
            tools = await session.list_tools()
            table_tools = Table(title="Available MCP Tools")
            table_tools.add_column("Tool Name", style="cyan", no_wrap=True)
            table_tools.add_column("Description", style="white")

            for t in tools.tools:
                table_tools.add_row(t.name, t.description or "No description")
            console.print(table_tools)
            console.print()

            # Resources
            resources = await session.list_resources()
            table_res = Table(title="Available MCP Resources")
            table_res.add_column("URI", style="green", no_wrap=True)
            table_res.add_column("Description", style="white")

            for r in resources.resources:
                table_res.add_row(str(r.uri), r.description or "No description")
            console.print(table_res)
            console.print()

            # Resource Templates
            templates = await session.list_resource_templates()
            if templates.resource_templates:
                table_tpl = Table(title="Available MCP Resource Templates")
                table_tpl.add_column("URI Template", style="yellow", no_wrap=True)
                table_tpl.add_column("Description", style="white")
                for tpl in templates.resource_templates:
                    table_tpl.add_row(str(tpl.uri_template), tpl.description or "")
                console.print(table_tpl)
                console.print()

            # Prompts
            prompts = await session.list_prompts()
            table_prompts = Table(title="Available MCP Prompts")
            table_prompts.add_column("Prompt Name", style="magenta", no_wrap=True)
            table_prompts.add_column("Description", style="white")

            for p in prompts.prompts:
                table_prompts.add_row(p.name, p.description or "")
            console.print(table_prompts)
            console.print()


async def execute_tool_call(
    server_command: str,
    server_args: list[str],
    tool_name: str,
    tool_args: dict[str, Any],
) -> None:
    """Calls an MCP tool and displays the formatted response."""
    server_params = StdioServerParameters(command=server_command, args=server_args)

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            console.print(f"[cyan]Calling tool [bold]{tool_name}[/bold] with args: {tool_args}...[/cyan]")
            result = await session.call_tool(tool_name, tool_args)

            for item in result.content:
                text_content = getattr(item, "text", str(item))
                console.print(Panel(text_content, title=f"Result: {tool_name}", border_style="green"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Test and interact with Tech Radar MCP Server.")
    parser.add_argument(
        "--command",
        default=sys.executable,
        help="Command to run MCP server (default: current python executable).",
    )
    parser.add_argument(
        "--args",
        nargs="*",
        default=["-m", "src.mcp_server"],
        help="Arguments to pass to server (default: -m src.mcp_server).",
    )
    parser.add_argument(
        "--call",
        help="Name of the tool to call.",
    )
    parser.add_argument(
        "--call-args",
        default="{}",
        help="JSON string of arguments for the tool call (e.g. '{\"table_name\": \"raw_github_events\"}').",
    )

    parsed = parser.parse_args()

    if parsed.call:
        args_dict: dict[str, Any] = {}
        if parsed.call_args:
            try:
                args_dict = json.loads(parsed.call_args)
            except Exception:
                # Fallback to key=val pairs
                for part in parsed.call_args.split(","):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        args_dict[k.strip()] = v.strip()
        asyncio.run(execute_tool_call(parsed.command, parsed.args, parsed.call, args_dict))
    else:
        asyncio.run(inspect_server(parsed.command, parsed.args))


if __name__ == "__main__":
    main()
