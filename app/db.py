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
    """Apply pending migrations (alembic upgrade head). Idempotent -- safe on every boot.

    Alembic reads DATABASE_URL (see alembic/env.py), so this targets whichever DB
    the app/tests point at. The schema itself is defined by the ORM models in
    app/models.py and evolved via migrations in alembic/versions/.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    print("Active Impact migrations applied (alembic upgrade head).")


if __name__ == "__main__":
    if "--init" in sys.argv:
        init()
    else:
        print("usage: python -m app.db --init")
