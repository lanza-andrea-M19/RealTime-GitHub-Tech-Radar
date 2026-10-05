"""Lightweight MCP CLI client and Interactive Conversational Host for Tech Radar."""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from src.common.config import settings

console = Console()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Tech Radar Assistant, an expert AI engineer connected via the Model Context Protocol (MCP) to live repository telemetry.
You have direct access to tools for:
- Querying PostgreSQL raw events with read-only SQL (execute_readonly_sql)
- Semantic similarity vector search on issue bodies and crash symptoms (semantic_vector_search)
- Checking active early-warning alerts and spike detections (get_active_alerts)
- Inspecting database schemas (get_table_schema)
- Ingesting latest issues and indexing vector embeddings
- Triaging incoming issues (triage_incoming_issue)

Guidelines:
1. Formulate clear, concise, actionable technical answers.
2. When referencing GitHub issues, always provide links in the format https://github.com/<owner>/<repo>/issues/<number>.
3. Call the appropriate tools whenever specific data or telemetry is requested.
"""


def extract_text(content: Any) -> str:
    """Extracts plain text from string or structured content list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
        return "\n".join(texts)
    return str(content)


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


async def run_interactive_mcp_chat(
    server_command: str,
    server_args: list[str],
    model_name: str = "gemini-3.5-flash-lite",
) -> None:
    """Runs a full-featured conversational chat session powered entirely by the MCP server."""
    if not settings.gemini_api_key:
        console.print(
            "[bold red]Error: GEMINI_API_KEY is not set in .env[/bold red]\n"
            "Please configure GEMINI_API_KEY to enable conversational chat with MCP tools."
        )
        return

    from langchain_google_genai import ChatGoogleGenerativeAI

    server_params = StdioServerParameters(command=server_command, args=server_args)

    console.print(f"[dim]Spawning MCP Server subprocess: {server_command} {' '.join(server_args)}...[/dim]")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_res = await session.initialize()
            mcp_tools = await session.list_tools()

            # Convert MCP tool specifications into standard OpenAI/LangChain function definitions
            tool_schemas = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": getattr(t, "input_schema", getattr(t, "inputSchema", {})),
                    },
                }
                for t in mcp_tools.tools
            ]

            llm = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=settings.gemini_api_key,
                temperature=0.2,
            )
            llm_with_tools = llm.bind_tools(tool_schemas)

            mcp_prompts = await session.list_prompts()
            prompts_map = {p.name: p for p in mcp_prompts.prompts}
            mcp_resources = await session.list_resources()
            mcp_templates = await session.list_resource_templates()

            def print_tools_table() -> None:
                table = Table(title="Currently Registered MCP Tools")
                table.add_column("Tool", style="cyan")
                table.add_column("Description", style="white")
                for t in mcp_tools.tools:
                    table.add_row(t.name, t.description or "")
                console.print(table)

            def print_prompts_table() -> None:
                table = Table(title="Available MCP Prompts")
                table.add_column("Slash Command", style="magenta", no_wrap=True)
                table.add_column("Parameters", style="yellow")
                table.add_column("Description", style="white")
                for p in mcp_prompts.prompts:
                    arg_names = ", ".join(arg.name for arg in (p.arguments or []))
                    table.add_row(f"/{p.name}", arg_names or "none", p.description or "")
                console.print(table)
                console.print("[dim]Usage example: /query_radar Memory leak in DuckDB joins[/dim]")

            def print_resources_table() -> None:
                table_res = Table(title="Available MCP Resources")
                table_res.add_column("URI", style="green", no_wrap=True)
                table_res.add_column("Description", style="white")
                for r in mcp_resources.resources:
                    table_res.add_row(str(r.uri), r.description or "No description")
                console.print(table_res)

                if mcp_templates.resource_templates:
                    table_tpl = Table(title="Available MCP Resource Templates")
                    table_tpl.add_column("URI Template", style="yellow", no_wrap=True)
                    table_tpl.add_column("Description", style="white")
                    for tpl in mcp_templates.resource_templates:
                        table_tpl.add_row(str(tpl.uri_template), tpl.description or "")
                    console.print(table_tpl)

            # Display header banner
            tool_names_str = ", ".join(f"[bold cyan]{t.name}[/bold cyan]" for t in mcp_tools.tools)
            prompt_names_str = ", ".join(f"[magenta]/{p.name}[/magenta]" for p in mcp_prompts.prompts)
            console.print(
                Panel(
                    f"[bold green]⚡ Tech Radar — Interactive MCP Host ⚡[/bold green]\n\n"
                    f"• Connected to Server: [cyan]{server_command}[/cyan] (Protocol: [green]{init_res.protocol_version}[/green])\n"
                    f"• Discovered MCP Tools ({len(mcp_tools.tools)}): {tool_names_str}\n"
                    f"• Discovered MCP Prompts ({len(mcp_prompts.prompts)}): {prompt_names_str}\n"
                    f"• LLM Engine: [magenta]{model_name}[/magenta]\n\n"
                    "[dim]Commands: 'exit' to stop | '/inspect' to see all | '/tools' | '/prompts' | '/resources' | '/clear' to reset[/dim]",
                    title="Model Context Protocol (MCP) Terminal Chat",
                    border_style="green",
                )
            )

            messages: list[Any] = [SystemMessage(content=SYSTEM_PROMPT)]

            while True:
                try:
                    user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]").strip()
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[yellow]Interrupted. Closing MCP session...[/yellow]")
                    break

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "q", "/exit", "/quit", "/q", ":q"):
                    console.print("[yellow]Exiting MCP chat host. Have a great day![/yellow]")
                    break

                if user_input.lower() in ("/clear", "clear"):
                    messages = [SystemMessage(content=SYSTEM_PROMPT)]
                    console.print("[green]Conversation memory cleared.[/green]")
                    continue

                if user_input.lower() in ("/tools", "tools"):
                    print_tools_table()
                    continue

                if user_input.lower() in ("/prompts", "prompts"):
                    print_prompts_table()
                    continue

                if user_input.lower() in ("/resources", "resources", "/resource"):
                    print_resources_table()
                    continue

                if user_input.lower() in ("/inspect", "inspect") or "--inspect" in user_input.lower():
                    print_tools_table()
                    console.print()
                    print_resources_table()
                    console.print()
                    print_prompts_table()
                    continue

                # Check if user invoked a registered MCP prompt directly (e.g. /query_radar or /triage_issue)
                triggered_prompt = None
                prompt_input_text = ""
                if user_input.startswith("/"):
                    parts = user_input[1:].split(" ", 1)
                    cmd_name = parts[0].strip()
                    rest = parts[1].strip() if len(parts) > 1 else ""

                    if cmd_name == "prompt" and rest:
                        sub_parts = rest.split(" ", 1)
                        cmd_name = sub_parts[0].strip()
                        rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

                    if cmd_name in prompts_map:
                        triggered_prompt = prompts_map[cmd_name]
                        prompt_input_text = rest

                if triggered_prompt:
                    # Parse arguments for the prompt
                    p_args: dict[str, str] = {}
                    expected_args = [arg.name for arg in (triggered_prompt.arguments or [])]

                    if "=" in prompt_input_text:
                        for token in prompt_input_text.split():
                            if "=" in token:
                                k, v = token.split("=", 1)
                                p_args[k.strip()] = v.strip().strip('"').strip("'")
                    elif len(expected_args) == 1 and prompt_input_text:
                        # Automatically assign the input text to the sole parameter
                        p_args[expected_args[0]] = prompt_input_text
                    elif len(expected_args) > 0 and not prompt_input_text:
                        # Interactive prompt for missing arguments
                        console.print(f"[cyan]MCP Prompt [bold]{triggered_prompt.name}[/bold] requires parameters:[/cyan]")
                        for arg_name in expected_args:
                            val = Prompt.ask(f"  Enter [yellow]{arg_name}[/yellow]").strip()
                            p_args[arg_name] = val

                    try:
                        console.print(f"[dim]⚡ Fetching rendered MCP prompt: [bold magenta]/{triggered_prompt.name}[/bold magenta]({p_args})...[/dim]")
                        rendered = await session.get_prompt(triggered_prompt.name, p_args)
                        for m in rendered.messages:
                            rendered_content = getattr(m.content, "text", str(m.content))
                            messages.append(HumanMessage(content=rendered_content))
                    except Exception as e:
                        console.print(f"[bold red]Failed to get prompt: {e}[/bold red]")
                        continue
                else:
                    messages.append(HumanMessage(content=user_input))

                # ReAct Tool Execution Loop (up to 5 iterations)
                for _ in range(5):
                    with console.status("[bold green]Agent thinking & invoking MCP tools...[/bold green]", spinner="dots"):
                        try:
                            response = await llm_with_tools.ainvoke(messages)
                        except Exception as e:
                            console.print(f"[bold red]LLM invocation error: {e}[/bold red]")
                            break

                    messages.append(response)

                    # If no tool calls, this is the final response
                    if not response.tool_calls:
                        content_str = extract_text(response.content)
                        console.print(
                            Panel(
                                Markdown(content_str),
                                title="[bold magenta]Tech Radar Assistant (via MCP)[/bold magenta]",
                                border_style="magenta",
                            )
                        )
                        break

                    # Execute each requested MCP tool via JSON-RPC
                    for call in response.tool_calls:
                        name = call["name"]
                        args = call["args"]
                        call_id = call.get("id") or str(uuid.uuid4())

                        console.print(f"[dim]⚡ [cyan]MCP Call:[/cyan] [bold]{name}[/bold]({json.dumps(args, default=str)})[/dim]")
                        try:
                            tool_result = await session.call_tool(name, args)
                            result_text = "\n".join(getattr(item, "text", str(item)) for item in tool_result.content)
                        except Exception as err:
                            result_text = f"MCP tool execution failed: {err}"

                        messages.append(
                            ToolMessage(content=result_text, tool_call_id=call_id, name=name)
                        )


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
        "--chat",
        action="store_true",
        help="Launch interactive conversational chat host with MCP tools.",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Inspect server capabilities (tools, resources, prompts) and exit.",
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
    parser.add_argument(
        "--model",
        default="gemini-3.5-flash-lite",
        help="Gemini model for conversational chat (default: gemini-3.5-flash-lite).",
    )

    parsed = parser.parse_args()

    if parsed.call:
        args_dict: dict[str, Any] = {}
        if parsed.call_args:
            try:
                args_dict = json.loads(parsed.call_args)
            except Exception:
                if "=" in parsed.call_args and parsed.call_args.count("=") == 1:
                    k, v = parsed.call_args.split("=", 1)
                    args_dict[k.strip()] = v.strip()
                else:
                    for part in parsed.call_args.split(","):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            args_dict[k.strip()] = v.strip()
        asyncio.run(execute_tool_call(parsed.command, parsed.args, parsed.call, args_dict))
    elif parsed.inspect:
        asyncio.run(inspect_server(parsed.command, parsed.args))
    else:
        # Default mode: interactive MCP conversational chat!
        asyncio.run(run_interactive_mcp_chat(parsed.command, parsed.args, model_name=parsed.model))


if __name__ == "__main__":
    main()
