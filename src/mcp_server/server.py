import json
import logging
from typing import Any
from mcp.server.mcpserver import MCPServer

from src.services.radar_core import (
    run_readonly_sql,
    fetch_table_schema,
    run_vector_search,
    get_monitored_repos_summary,
    get_database_stats,
    fetch_recent_issues_for_repo,
    DEFAULT_MONITORED_REPOSITORIES,
)

logger = logging.getLogger(__name__)

# Initialize MCP Server
app = MCPServer(
    name="tech-radar",
    description="Real-Time GitHub Tech Radar & Early Warning System MCP Server",
    version="1.0.0",
)


# ==========================================
# MCP TOOLS
# ==========================================


@app.tool(description="Executes a read-only SELECT or WITH SQL query against the PostgreSQL database.")
def execute_readonly_sql(query: str, max_rows: int = 50) -> str:
    """Executes a read-only SELECT or WITH SQL query against the PostgreSQL database.

    Available tables:
    - raw_github_events (id, repo, issue_number, title, author, state, labels, body, comments_count, upstream_created_at, upstream_updated_at, payload, ingested_at)
    - issue_embeddings (id, event_id, repo, issue_number, title, chunk_text, embedding)

    Parameters:
    - query: SQL query string strictly starting with SELECT or WITH.
    - max_rows: Maximum number of rows to return (default: 50).
    """
    return run_readonly_sql(query=query, max_rows=max_rows)


@app.tool(description="Performs semantic similarity vector search across GitHub issues using pgvector cosine distance.")
def semantic_vector_search(query: str, repo: str | None = None, top_k: int = 5) -> str:
    """Performs semantic similarity vector search across issue descriptions and bodies using pgvector embeddings.

    Ideal for conceptual queries, finding bugs by symptoms/error messages, memory leaks, performance regressions, workarounds, or searching across languages.

    Parameters:
    - query: Natural language description, bug symptoms, error message, or technical topic to search.
    - repo: Optional repository filter (e.g. 'duckdb/duckdb', 'pola-rs/polars', 'pydantic/pydantic').
    - top_k: Number of semantically relevant issues to retrieve (default: 5, max: 20).
    """
    return run_vector_search(query=query, repo=repo, top_k=top_k)


@app.tool(description="Retrieves the column names, data types, and nullability for database tables.")
def get_table_schema(table_name: str = "raw_github_events") -> str:
    """Retrieves the column names, data types, and comments for a specified database table.

    Parameters:
    - table_name: Database table name to inspect (e.g. 'raw_github_events' or 'issue_embeddings').
    """
    return fetch_table_schema(table_name=table_name)


@app.tool(description="Fetches recent GitHub issues via REST API and persists them into the database.")
def ingest_github_issues(repositories: list[str] | None = None, limit_per_repo: int = 30) -> str:
    """Fetches the latest issues from monitored or specified GitHub repositories and stores them in PostgreSQL.

    Parameters:
    - repositories: Optional list of repositories in 'owner/repo' format. Defaults to monitored repos.
    - limit_per_repo: Number of issues to fetch per repository (default: 30, max: 100).
    """
    try:
        from src.ingestion.github_poller import GitHubIssuePoller

        poller = GitHubIssuePoller()
        target_repos = repositories or DEFAULT_MONITORED_REPOSITORIES
        summary = poller.run(repos=target_repos, limit_per_repo=min(limit_per_repo, 100))
        return json.dumps(
            {
                "status": "success",
                "processed_repositories": summary,
                "total_issues_processed": sum(summary.values()),
            },
            indent=2,
        )
    except Exception as e:
        return f"Ingestion error: {e}"


@app.tool(description="Generates pgvector embeddings for unindexed issues using Gemini embedding model.")
def index_embeddings(batch_size: int = 15) -> str:
    """Generates 768-dimensional vector embeddings with Gemini for all pending unindexed issues in PostgreSQL.

    Parameters:
    - batch_size: Number of issues to embed in each batch (default: 15).
    """
    try:
        from src.embeddings.indexer import index_all_issues

        count = index_all_issues(batch_size=batch_size)
        return json.dumps(
            {
                "status": "success",
                "newly_indexed_issues": count,
            },
            indent=2,
        )
    except Exception as e:
        return f"Indexing error: {e}"


# ==========================================
# MCP RESOURCES & RESOURCE TEMPLATES
# ==========================================


@app.resource("techradar://repositories")
def list_repositories() -> str:
    """Provides a list of monitored repositories and their issue counts."""
    return get_monitored_repos_summary()


@app.resource("techradar://stats")
def show_database_stats() -> str:
    """Provides global counts of ingested events and vector embeddings."""
    return get_database_stats()


@app.resource("techradar://schema/{table_name}")
def inspect_table_schema(table_name: str) -> str:
    """Provides schema information for a specific database table."""
    return fetch_table_schema(table_name)


@app.resource("techradar://issues/{owner}/{repo_name}")
def get_recent_issues(owner: str, repo_name: str) -> str:
    """Retrieves recent issues stored in the database for the given repository."""
    repo = f"{owner}/{repo_name}"
    return fetch_recent_issues_for_repo(repo=repo, limit=10)


# ==========================================
# MCP PROMPTS
# ==========================================


@app.prompt(description="Guides the assistant to answer questions using Tech Radar SQL and vector tools.")
def query_radar(question: str) -> str:
    """Generates an orchestration prompt for querying Tech Radar repository telemetry.

    Parameters:
    - question: The user's query or investigation topic.
    """
    return f"""You are the Tech Radar Assistant, an expert AI engineer monitoring open-source library reliability.

User Query: "{question}"

Instructions:
1. Determine the best retrieval strategy:
   - For quantitative questions (e.g. counts, top issues by comments, filtering by date/state/author): Use the `execute_readonly_sql` tool on `raw_github_events`.
   - For conceptual, bug symptoms, error messages, memory leaks, or workarounds: Use `semantic_vector_search`.
   - For complex questions: Combine both tools (run SQL filter and semantic search).
2. Synthesize key technical findings clearly: issue title, severity, symptoms, status (open/closed), and any discussed workarounds or causes.
3. For every issue referenced, always include the direct GitHub URL: `https://github.com/<owner>/<repo>/issues/<number>`.
"""


@app.prompt(description="Triage an incoming GitHub issue to extract severity, affected versions, and workarounds.")
def triage_issue(issue_title: str, issue_body: str) -> str:
    """Generates a structured prompt to triage and classify an incoming GitHub issue.

    Parameters:
    - issue_title: The title of the issue.
    - issue_body: The body/content of the issue.
    """
    return f"""Analyze the following GitHub issue and classify it according to Tech Radar triage guidelines:

Title: {issue_title}
Body:
{issue_body}

Output Requirements:
1. Classification: Choose one of [Critical Bug/Crash, Security/CVE, Breaking Change, Noise/Question].
2. Affected Versions: Extract any mentioned library versions or environments.
3. Root Cause / Symptoms: Summarize what breaks and under what conditions.
4. Suggested Workaround: Extract any temporary fix, patch, or downgrade advice mentioned.
"""
