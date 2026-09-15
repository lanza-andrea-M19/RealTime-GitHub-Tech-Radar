import json
import logging
import sys
from typing import Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from src.common.config import settings
from src.common.db import get_db, verify_connection

console = Console()
logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 768


def get_embedding_model() -> GoogleGenerativeAIEmbeddings:
    """Returns the configured embedding model with fixed 768 dimensions."""
    if not settings.gemini_api_key:
        console.print("[bold red]Error: GEMINI_API_KEY is not set in .env[/bold red]")
        sys.exit(1)

    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        output_dimensionality=EMBEDDING_DIM,
        google_api_key=settings.gemini_api_key,
    )


def build_chunk_text(repo: str, issue_number: int, title: str, body: str | None, labels_raw: Any) -> str:
    """Formats an issue into a concise textual chunk optimized for semantic retrieval."""
    labels: list[str] = []
    if isinstance(labels_raw, list):
        labels = [str(l) for l in labels_raw]
    elif isinstance(labels_raw, str):
        try:
            parsed = json.loads(labels_raw)
            if isinstance(parsed, list):
                labels = [str(l) for l in parsed]
        except Exception:
            labels = []

    labels_str = ", ".join(labels) if labels else "None"
    body_snippet = (body or "").strip()[:1800]

    return f"Repository: {repo} | Issue #{issue_number}\nTitle: {title}\nLabels: {labels_str}\n\nContent:\n{body_snippet}"


def fetch_unindexed_issues(limit: int | None = None) -> list[dict[str, Any]]:
    """Retrieves issues from raw_github_events that have not yet been embedded."""
    query = """
    SELECT r.id, r.repo, r.issue_number, r.title, r.body, r.labels
    FROM raw_github_events r
    LEFT JOIN issue_embeddings e ON r.id = e.event_id
    WHERE e.id IS NULL
    ORDER BY r.upstream_created_at DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()


def store_embeddings_batch(records: list[dict[str, Any]]) -> int:
    """Stores generated embeddings into issue_embeddings."""
    if not records:
        return 0

    insert_query = """
    INSERT INTO issue_embeddings (
        event_id, repo, issue_number, title, chunk_text, embedding
    ) VALUES (
        %(event_id)s, %(repo)s, %(issue_number)s, %(title)s, %(chunk_text)s, %(embedding)s::vector
    )
    ON CONFLICT (event_id) DO NOTHING;
    """

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_query, records)
        conn.commit()

    return len(records)


import time

def index_all_issues(batch_size: int = 15, delay_between_batches: float = 3.0) -> int:
    """Generates embeddings for all unindexed issues in batches and stores them in PostgreSQL."""
    if not verify_connection():
        console.print("[bold red]Database connection failed. Ensure PostgreSQL container is running.[/bold red]")
        return 0

    unindexed = fetch_unindexed_issues()
    total_unindexed = len(unindexed)

    if total_unindexed == 0:
        console.print("[bold green]All issues are already indexed in pgvector![/bold green]")
        return 0

    console.print(f"[cyan]Found [bold]{total_unindexed}[/bold] unindexed issues. Generating embeddings in batches of {batch_size}...[/cyan]")
    embedder = get_embedding_model()
    indexed_count = 0

    for i in range(0, total_unindexed, batch_size):
        batch = unindexed[i : i + batch_size]
        chunks = [
            build_chunk_text(
                item["repo"],
                item["issue_number"],
                item["title"],
                item["body"],
                item["labels"],
            )
            for item in batch
        ]

        # Retry with exponential backoff on 429
        max_retries = 5
        for attempt in range(max_retries):
            try:
                vectors = embedder.embed_documents(chunks)
                records_to_save = []
                for item, chunk, vec in zip(batch, chunks, vectors):
                    records_to_save.append({
                        "event_id": item["id"],
                        "repo": item["repo"],
                        "issue_number": item["issue_number"],
                        "title": item["title"],
                        "chunk_text": chunk,
                        "embedding": vec,
                    })

                saved = store_embeddings_batch(records_to_save)
                indexed_count += saved
                console.print(f"  [green]Processed batch {i + 1} - {min(i + batch_size, total_unindexed)} of {total_unindexed} (saved {saved} embeddings).[/green]")
                time.sleep(delay_between_batches)
                break
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    wait_time = (attempt + 1) * 10
                    console.print(f"  [yellow]Rate limit reached. Waiting {wait_time}s before retrying attempt {attempt + 1}/{max_retries}...[/yellow]")
                    time.sleep(wait_time)
                else:
                    console.print(f"  [bold red]Error embedding batch {i + 1} - {min(i + batch_size, total_unindexed)}: {e}[/bold red]")
                    break

    console.print(f"[bold green]Indexing complete. Successfully stored {indexed_count} new embeddings.[/bold green]")
    return indexed_count



if __name__ == "__main__":
    index_all_issues()
