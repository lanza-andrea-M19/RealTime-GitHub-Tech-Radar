import os
import sys
import uuid
from typing import Any
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver

from src.common.config import settings
from src.common.db import verify_connection
from src.agent.graph import build_tech_radar_graph

console = Console()


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


class TechRadarMultiNodeAgent:
    """Multi-node stateful agent compiled with LangGraph and PostgreSQL checkpointer."""

    def __init__(self, thread_id: str | None = None):
        if not settings.gemini_api_key:
            console.print(
                "[bold red]Error: GEMINI_API_KEY is not configured in .env[/bold red]\n"
                "Please get a free API key at https://aistudio.google.com/ and add it to your .env file."
            )
            sys.exit(1)

        if not verify_connection():
            console.print(
                "[bold red]Error: PostgreSQL database is offline.[/bold red]\n"
                "Start the container with: docker compose -f docker/docker-compose.yml up -d postgres"
            )
            sys.exit(1)

        # Configure LangSmith if enabled
        if settings.langchain_tracing_v2 and settings.langchain_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
            os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

        self.thread_id = thread_id or str(uuid.uuid4())[:8]
        self._checkpointer_cm = PostgresSaver.from_conn_string(settings.database_url)
        self.checkpointer = self._checkpointer_cm.__enter__()
        self.checkpointer.setup()
        self.app = build_tech_radar_graph(checkpointer=self.checkpointer)

    def close(self):
        try:
            self._checkpointer_cm.__exit__(None, None, None)
        except Exception:
            pass

    def query(self, user_question: str) -> tuple[str, str, str | None]:
        """Runs the question through the LangGraph workflow and returns answer, route, and SQL query (if any)."""
        config = {"configurable": {"thread_id": self.thread_id}}
        inputs = {"messages": [HumanMessage(content=user_question)]}

        result = self.app.invoke(inputs, config=config)
        last_message = result["messages"][-1]
        answer = extract_text(last_message.content)
        route = result.get("route", "direct")
        sql_query = result.get("sql_query")

        return answer, route, sql_query


def ask_agent(agent: TechRadarMultiNodeAgent, question: str):
    """Executes a single user query against the multi-node agent and prints the formatted response."""
    console.print(f"\n[bold cyan]User:[/bold cyan] {question}")

    with console.status("[bold green]Agent routing and retrieving intelligence...[/bold green]", spinner="dots"):
        try:
            answer, route, sql_query = agent.query(question)
        except Exception as e:
            console.print(f"[bold red]Error during execution: {e}[/bold red]")
            return

    # Visual indicators of multi-node execution
    route_colors = {
        "sql": "bold blue",
        "vector": "bold magenta",
        "hybrid": "bold yellow",
        "direct": "bold green",
    }
    color = route_colors.get(route, "cyan")
    console.print(f"[dim]⚡ Pipeline Route:[/dim] [{color}]{route.upper()}[/{color}]  [dim]| Session Thread:[/dim] [cyan]{agent.thread_id}[/cyan]")

    if sql_query:
        console.print(f"[dim]Generated SQL:[/dim] [bright_black]{sql_query}[/bright_black]")

    console.print("\n[bold magenta]Tech Radar Assistant:[/bold magenta]")
    console.print(Markdown(answer))


def interactive_cli():
    """Runs an interactive conversational CLI loop with PostgreSQL persistence."""
    console.print(
        Panel(
            "[bold green]Tech Stack Radar & Dependency Early-Warning System[/bold green]\n"
            "Stateful Multi-Node LangGraph Agent with pgvector & PostgreSQL Checkpointing",
            border_style="green",
        )
    )

    agent = TechRadarMultiNodeAgent()
    console.print(f"[dim]Active session thread: [bold cyan]{agent.thread_id}[/bold cyan][/dim]")
    console.print("[dim]Commands: 'exit'/'quit' to close | '/new' to start a new persistent session.[/dim]\n")

    try:
        while True:
            try:
                user_input = Prompt.ask("[bold cyan]Question[/bold cyan]")
                clean_input = user_input.strip()

                if clean_input.lower() in ("exit", "quit", "q"):
                    console.print("[yellow]Exiting. Session saved in PostgreSQL. Have a great day![/yellow]")
                    break

                if clean_input.lower() == "/new":
                    agent.thread_id = str(uuid.uuid4())[:8]
                    console.print(f"[green]Started new session thread: [bold cyan]{agent.thread_id}[/bold cyan][/green]\n")
                    continue

                if not clean_input:
                    continue

                ask_agent(agent, clean_input)
                console.print("-" * 70)
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted by user. Exiting...[/yellow]")
                break
    finally:
        agent.close()


if __name__ == "__main__":
    interactive_cli()
