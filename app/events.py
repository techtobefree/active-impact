"""Append-only events audit log.

One immutable row per check-in / check-out, written in the SAME tx as the state
change it records (see docs/design/DOMAIN.md § Events). Rows are never updated or
deleted -- they are the source of truth for later reporting.
"""
from __future__ import annotations

from psycopg.types.json import Json


def log(c, type, *, actor_user_id=None, subject_user_id=None, project_id=None,
        participation_id=None, minutes=None, tokens=None, meta=None):
    """Append ONE immutable row to the events log inside the caller's tx. Never updated/deleted."""
    c.execute(
        "INSERT INTO events(type, actor_user_id, subject_user_id, project_id, participation_id, minutes, tokens, meta) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (type, actor_user_id, subject_user_id, project_id, participation_id, minutes, tokens, Json(meta or {})),
    )
