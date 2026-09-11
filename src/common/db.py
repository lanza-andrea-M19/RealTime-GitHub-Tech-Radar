from contextlib import contextmanager
from typing import Generator
import psycopg
from psycopg.rows import dict_row
from src.common.config import settings


@contextmanager
def get_db() -> Generator[psycopg.Connection, None, None]:
    """Provides a transactional database connection context."""
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def verify_connection() -> bool:
    """Verifies that the PostgreSQL database is reachable."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                res = cur.fetchone()
                return res is not None and res["?column?"] == 1
    except Exception:
        return False
