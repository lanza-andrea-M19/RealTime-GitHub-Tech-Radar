from pydantic import BaseModel, Field
from langchain_core.tools import tool

from src.services.radar_core import (
    run_readonly_sql,
    fetch_table_schema,
    run_vector_search,
)


class SQLQueryInput(BaseModel):
    query: str = Field(
        description="A read-only PostgreSQL SELECT query to run against the tech radar database."
    )


class TableSchemaInput(BaseModel):
    table_name: str = Field(
        default="raw_github_events",
        description="The name of the database table to inspect.",
    )


class VectorSearchInput(BaseModel):
    query: str = Field(
        description="The natural language concept, error message, bug symptom, or topic to search semantically."
    )
    repo: str | None = Field(
        default=None,
        description="Optional repository filter (e.g. 'duckdb/duckdb', 'pola-rs/polars', 'pydantic/pydantic').",
    )
    top_k: int = Field(
        default=5,
        description="Number of semantically relevant issues to retrieve (default 5, max 10).",
    )


@tool(args_schema=SQLQueryInput)
def execute_readonly_sql(query: str) -> str:
    """Executes a read-only SELECT query against the PostgreSQL database.

    Useful to find issues by repository, keywords, labels, status, author, or dates.
    """
    return run_readonly_sql(query)


@tool(args_schema=TableSchemaInput)
def get_table_schema(table_name: str = "raw_github_events") -> str:
    """Retrieves the column names, data types, and comments for a specified database table."""
    return fetch_table_schema(table_name)


@tool(args_schema=VectorSearchInput)
def semantic_vector_search(query: str, repo: str | None = None, top_k: int = 5) -> str:
    """Performs semantic similarity vector search across issue descriptions and bodies using pgvector embeddings.

    Ideal for conceptual queries, finding bugs by symptoms/error messages, workarounds, or searching across languages.
    """
    return run_vector_search(query=query, repo=repo, top_k=top_k)
