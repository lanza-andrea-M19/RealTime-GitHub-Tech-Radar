import json
import logging
import re
from typing import Any

from src.common.db import get_db, verify_connection

logger = logging.getLogger(__name__)

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|CREATE)\b",
    re.IGNORECASE,
)

DEFAULT_MONITORED_REPOSITORIES = [
    "duckdb/duckdb",
    "pola-rs/polars",
    "pydantic/pydantic",
]


def run_readonly_sql(query: str, max_rows: int = 50) -> str:
    """Executes a read-only SELECT or WITH SQL query against the PostgreSQL database."""
    clean_query = query.strip().rstrip(";")

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
                rows = cur.fetchmany(max_rows)
                if not rows:
                    return "Query returned 0 results."
                # Format nicely as JSON string
                formatted_rows = [dict(r) for r in rows]
                return json.dumps(formatted_rows, indent=2, default=str)
    except Exception as e:
        return f"Database query error: {e}"


def fetch_table_schema(table_name: str = "raw_github_events") -> str:
    """Retrieves column names, data types, and nullability for a specified table."""
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


def run_vector_search(query: str, repo: str | None = None, top_k: int = 5) -> str:
    """Performs semantic similarity vector search across issue embeddings using pgvector cosine distance."""
    if not verify_connection():
        return "Error: Database is offline. Ensure PostgreSQL Docker container is running."

    try:
        from src.embeddings.indexer import get_embedding_model

        embedder = get_embedding_model()
        query_vec = embedder.embed_query(query)
    except Exception as e:
        return f"Error generating query embedding: {e}"

    top_k = min(max(1, top_k), 20)
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


def get_monitored_repos_summary() -> str:
    """Returns a status summary of monitored repositories and issue counts."""
    repos = DEFAULT_MONITORED_REPOSITORIES
    if not verify_connection():
        lines = [f"- {repo} (Database offline, cannot read counts)" for repo in repos]
        return "Monitored Repositories:\n" + "\n".join(lines)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT repo, COUNT(*) as count,
                           COUNT(*) FILTER (WHERE state = 'open') as open_count,
                           COUNT(*) FILTER (WHERE state = 'closed') as closed_count
                    FROM raw_github_events
                    GROUP BY repo
                    ORDER BY repo;
                    """
                )
                rows = cur.fetchall()
                if not rows:
                    return "No repositories have ingested issues yet. Default targets: " + ", ".join(repos)

                lines = ["Monitored Repositories:"]
                for r in rows:
                    lines.append(
                        f"- {r['repo']}: {r['count']} total issues ({r['open_count']} open, {r['closed_count']} closed)"
                    )
                return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving repository summary: {e}"


def get_database_stats() -> str:
    """Returns total counts of raw GitHub events and vector embeddings."""
    if not verify_connection():
        return "Error: Database is offline."

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as raw_count FROM raw_github_events;")
                raw_count = cur.fetchone()["raw_count"]

                cur.execute("SELECT COUNT(*) as embed_count FROM issue_embeddings;")
                embed_count = cur.fetchone()["embed_count"]

                return json.dumps(
                    {
                        "total_raw_events": raw_count,
                        "total_vector_embeddings": embed_count,
                        "database_status": "online",
                    },
                    indent=2,
                )
    except Exception as e:
        return f"Error retrieving database stats: {e}"


def fetch_recent_issues_for_repo(repo: str, limit: int = 10) -> str:
    """Returns the most recent issues for a given repository."""
    if not verify_connection():
        return "Error: Database is offline."

    query = """
    SELECT repo, issue_number, title, state, author, comments_count, upstream_created_at
    FROM raw_github_events
    WHERE repo = %s
    ORDER BY upstream_created_at DESC
    LIMIT %s;
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (repo, limit))
                rows = cur.fetchall()
                if not rows:
                    return f"No issues found in database for repository '{repo}'."
                formatted = [dict(r) for r in rows]
                return json.dumps(formatted, indent=2, default=str)
    except Exception as e:
        return f"Error fetching issues for {repo}: {e}"
