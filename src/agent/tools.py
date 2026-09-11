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
