"""Append-only audit log (formerly app/events.py).

One immutable row per check-in / check-out, written in the SAME tx as the state
change it records (see docs/design/DOMAIN.md § Audit log). Rows are never updated
or deleted -- they are the source of truth for later reporting.

Renamed from ``app/events.py`` when the ``events`` name was reassigned to the new
service-project occurrence table; this writes the ``audit_log`` table, and every
row now carries both the occurrence (``event_id``) and its project (``project_id``).
"""
from __future__ import annotations

from psycopg.types.json import Json


def log(c, type, *, actor_user_id=None, subject_user_id=None, project_id=None,
        event_id=None, participation_id=None, minutes=None, tokens=None, meta=None):
    """Append ONE immutable row to the audit_log inside the caller's tx. Never updated/deleted."""
    c.execute(
        "INSERT INTO audit_log(type, actor_user_id, subject_user_id, project_id, "
        "event_id, participation_id, minutes, tokens, meta) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (type, actor_user_id, subject_user_id, project_id, event_id,
         participation_id, minutes, tokens, Json(meta or {})),
    )
