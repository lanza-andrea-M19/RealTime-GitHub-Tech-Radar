import re
from typing import Any
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from src.common.db import get_db, verify_connection


class SQLQueryInput(BaseModel):
    query: str = Field(
        description="A read-only PostgreSQL SELECT query to run against the tech radar database."
    )


class TableSchemaInput(BaseModel):
    table_name: str = Field(
        default="raw_github_events",
        description="The name of the database table to inspect.",
    )


FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|CREATE)\b",
    re.IGNORECASE,
)


@tool(args_schema=SQLQueryInput)
def execute_readonly_sql(query: str) -> str:
    """Executes a read-only SELECT query against the PostgreSQL database.

    Useful to find issues by repository, keywords, labels, status, author, or dates.
    """
    clean_query = query.strip().rstrip(";")

    # Security check: strictly read-only
    if not clean_query.lower().startswith(("select", "with")):
        return "Error: Only SELECT or WITH queries are permitted."

    if FORBIDDEN_SQL_PATTERN.search(clean_query):
        return "Error: Destructive or data-modifying SQL statements are forbidden."

    if not verify_connection():
        return "Error: Database is offline. Ensure PostgreSQL Docker container is running."

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(clean_query)
                rows = cur.fetchmany(50)
                if not rows:
                    return "Query returned 0 results."
                return str(rows)
    except Exception as e:
        return f"Database query error: {e}"


@tool(args_schema=TableSchemaInput)
def get_table_schema(table_name: str = "raw_github_events") -> str:
    """Retrieves the column names, data types, and comments for a specified database table."""
    if not verify_connection():
        return "Error: Database is offline. Ensure PostgreSQL Docker container is running."

    query = """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = %s
    ORDER BY ordinal_position;
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (table_name,))
                rows = cur.fetchall()
                if not rows:
                    return f"Table '{table_name}' does not exist or has no columns."
                formatted = "\n".join([f"- {r['column_name']} ({r['data_type']})" for r in rows])
                return f"Schema for {table_name}:\n{formatted}"
    except Exception as e:
        return f"Error retrieving schema: {e}"


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


@tool(args_schema=VectorSearchInput)
def semantic_vector_search(query: str, repo: str | None = None, top_k: int = 5) -> str:
    """Performs semantic similarity vector search across issue descriptions and bodies using pgvector embeddings.

    Ideal for conceptual queries, finding bugs by symptoms/error messages, workarounds, or searching across languages.
    """
    if not verify_connection():
        return "Error: Database is offline. Ensure PostgreSQL Docker container is running."

    try:
        from src.embeddings.indexer import get_embedding_model
        embedder = get_embedding_model()
        query_vec = embedder.embed_query(query)
    except Exception as e:
        return f"Error generating query embedding: {e}"

    top_k = min(max(1, top_k), 10)
    params: dict[str, Any] = {"vec": query_vec, "top_k": top_k}
    where_clauses = []
    if repo:
        where_clauses.append("e.repo = %(repo)s")
        params["repo"] = repo

    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql_query = f"""
    SELECT 
        e.repo,
        e.issue_number,
        e.title,
        ROUND((1 - (e.embedding <=> %(vec)s::vector))::numeric, 4) AS similarity_score,
        r.state,
        r.author,
        r.upstream_created_at,
        e.chunk_text
    FROM issue_embeddings e
    JOIN raw_github_events r ON e.event_id = r.id
    {where_str}
    ORDER BY e.embedding <=> %(vec)s::vector
    LIMIT %(top_k)s;
    """

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_query, params)
                rows = cur.fetchall()

                if not rows:
                    return f"No semantically similar issues found for query: '{query}'."

                formatted_results = []
                for r in rows:
                    formatted_results.append(
                        f"• [{r['repo']}#{r['issue_number']}] {r['title']}\n"
                        f"  Similarity: {r['similarity_score']} | State: {r['state']} | Date: {r['upstream_created_at']}\n"
                        f"  URL: https://github.com/{r['repo']}/issues/{r['issue_number']}\n"
                        f"  Snippet: {r['chunk_text'][:250]}..."
                    )
                return "\n\n".join(formatted_results)
    except Exception as e:
        return f"Database vector search error: {e}"

