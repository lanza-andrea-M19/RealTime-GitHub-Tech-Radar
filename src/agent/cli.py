import os
import sys
from typing import Any
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage

from src.common.config import settings
from src.agent.tools import execute_readonly_sql, get_table_schema

console = Console()

SYSTEM_PROMPT = """You are Tech Radar Assistant, an expert AI engineer monitoring open-source repositories and GitHub issues.
You have access to a PostgreSQL database containing issues from monitored repositories (such as duckdb/duckdb, pola-rs/polars, pydantic/pydantic).

Database Guidelines:
1. When asked about issues or events, write read-only PostgreSQL queries using the `execute_readonly_sql` tool.
2. If unsure about column names or types, inspect the table schema using `get_table_schema`.
3. In queries:
   - Match text case-insensitively using `ILIKE` (e.g. `title ILIKE '%memory%'` or `body ILIKE '%leak%'`).
   - Filter by `repo` whenever the user asks about a specific library (e.g. `repo = 'pola-rs/polars'`).
   - Always retrieve `repo`, `issue_number`, `title`, `state`, and `upstream_created_at`.
   - Order results by `upstream_created_at DESC` and limit to a reasonable number (e.g., `LIMIT 5`).
4. In responses:
   - Respond in Italian if the user speaks Italian.
   - Provide concrete insights: issue title, status, summary of the problem, and relevant labels.
   - Always format GitHub issue links as: `https://github.com/<repo>/issues/<issue_number>`.
   - Use clear bullet points and markdown tables where appropriate.
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


class TechRadarAgent:
    def __init__(self):
        if not settings.gemini_api_key:
            console.print(
                "[bold red]Error: GEMINI_API_KEY is not configured in .env[/bold red]\n"
                "Please get a free API key at [link=https://aistudio.google.com/]https://aistudio.google.com/[/link] and add it to your .env file."
            )
            sys.exit(1)

        # Configure LangSmith if enabled
        if settings.langchain_tracing_v2 and settings.langchain_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
            os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

        self.tools = {
            "execute_readonly_sql": execute_readonly_sql,
            "get_table_schema": get_table_schema,
        }

        self.llm = ChatGoogleGenerativeAI(
            model="gemini-3.5-flash-lite",
            google_api_key=settings.gemini_api_key,
        ).bind_tools(list(self.tools.values()))

    def query(self, user_question: str) -> str:
        """Executes a user question through the ReAct tool calling loop."""
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_question),
        ]

        # ReAct loop (max 6 iterations to prevent infinite loops)
        for _ in range(6):
            response = self.llm.invoke(messages)
            messages.append(response)

            # If no tools called, we have the final synthesis
            if not response.tool_calls:
                return extract_text(response.content)

            # Execute tool calls
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_id = tool_call.get("id", "tool_call_id")

                tool_fn = self.tools.get(tool_name)
                if tool_fn:
                    try:
                        result = tool_fn.invoke(tool_args)
                    except Exception as err:
                        result = f"Tool execution error: {err}"
                else:
                    result = f"Error: Tool '{tool_name}' is not recognized."

                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tool_id)
                )

        return "Raggiunto il numero massimo di iterazioni senza una risposta finale."


def ask_agent(agent: TechRadarAgent, question: str):
    """Executes a single user query against the agent and prints the formatted response."""
    console.print(f"\n[bold cyan]User:[/bold cyan] {question}")
    with console.status("[bold green]Agent researching database...[/bold green]", spinner="dots"):
        try:
            answer = agent.query(question)
            console.print("\n[bold magenta]Tech Radar Assistant:[/bold magenta]")
            console.print(Markdown(answer))
        except Exception as e:
            console.print(f"[bold red]Error during execution: {e}[/bold red]")


def interactive_cli():
    """Runs an interactive conversational CLI loop."""
    console.print(
        Panel(
            "[bold green]Tech Stack Radar & Dependency Early-Warning System[/bold green]\n"
            "Interactive CLI Agent powered by LangChain & Gemini",
            border_style="green",
        )
    )

    agent = TechRadarAgent()
    console.print("[dim]Type your question or 'exit' / 'quit' to close.[/dim]\n")

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]Question[/bold cyan]")
            if user_input.strip().lower() in ("exit", "quit", "q"):
                console.print("[yellow]Exiting. Have a great day![/yellow]")
                break
            if not user_input.strip():
                continue

            ask_agent(agent, user_input)
            console.print("-" * 60)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user. Exiting...[/yellow]")
            break


if __name__ == "__main__":
    interactive_cli()
