"""Postgres access layer for Active Impact.

One connection pool, a ``query``/``query_one`` helper for reads, a ``tx()``
context manager for atomic writes, and an idempotent schema ``init`` applied on
every container boot (``python -m app.db --init``).

DATABASE_URL defaults to the dev-compose socket so local runs and pytest need no
env setup.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgres://postgres:postgres@localhost:5433/impact"
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Lazily-opened pool. Every pooled connection uses dict rows.
pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"row_factory": dict_row},
)


def _ensure_open() -> None:
    if pool.closed:
        pool.open()


def query(sql: str, params: object = None) -> list[dict]:
    """Run a read (or write) statement and return all rows as dicts."""
    _ensure_open()
    with pool.connection() as conn:
        cur = conn.execute(sql, params or ())
        return cur.fetchall() if cur.description else []


def query_one(sql: str, params: object = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


@contextmanager
def tx():
    """Yield a connection wrapped in a single transaction.

    Every token movement and multi-statement mutation goes through this so the
    write and its ledger/audit rows commit or roll back together.
    """
    _ensure_open()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def init() -> None:
    """Apply schema.sql. Idempotent -- safe to run on every boot.

    Uses a ClientCursor connection so the multi-statement file (including the
    dollar-quoted DO blocks) executes in one PQexec call.
    """
    schema = SCHEMA_PATH.read_text()
    with psycopg.connect(
        DATABASE_URL, autocommit=True, cursor_factory=psycopg.ClientCursor
    ) as conn:
        conn.execute(schema)
    print("Active Impact schema applied.")


if __name__ == "__main__":
    if "--init" in sys.argv:
        init()
    else:
        print("usage: python -m app.db --init")
