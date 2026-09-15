from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class RouteDecision(BaseModel):
    """Structured decision on how to handle the incoming user prompt."""
    intent: Literal["sql", "vector", "hybrid", "direct"] = Field(
        description=(
            "The execution route: "
            "'sql' for aggregations, counts, exact status, dates, or structured filters; "
            "'vector' for conceptual bugs, crash symptoms, error messages, or workarounds; "
            "'hybrid' when both exact date/repo filtering and semantic text search are required; "
            "'direct' for general chat, greetings, or questions not needing repository data."
        )
    )
    repo_filter: str | None = Field(
        default=None,
        description="Target repository if mentioned or implied (e.g. 'duckdb/duckdb', 'pola-rs/polars', 'pydantic/pydantic')."
    )
    search_query: str | None = Field(
        default=None,
        description="Refined keyword or semantic search phrase for vector similarity search."
    )
    reasoning: str = Field(
        description="Brief explanation of why this route was selected."
    )


class AgentState(BaseModel):
    """The state passed between nodes in the Tech Radar LangGraph workflow."""
    messages: Annotated[list[BaseMessage], add_messages]
    route: str = "direct"
    repo_filter: str | None = None
    search_query: str | None = None
    sql_query: str | None = None
    retrieved_context: list[str] = Field(default_factory=list)
