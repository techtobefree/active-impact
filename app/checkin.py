"""Check-in / check-out — the waiver signature and the time sheet.

The QR encodes a URL, so the volunteer's native camera opens the PWA at
``#/c/{code}``; the frontend then drives these three endpoints:

- ``GET  /api/checkin/{code}``          resolve a scanned code -> event + project + waiver
- ``POST /api/checkin/{code}/agree``    the signature -> a new participation
- ``POST /api/participations/{id}/checkout``  close it, run the mint math

A check-in code belongs to an EVENT (occurrence). A participation is created by
agreeing to the event's project's *current* waiver version at check-in (its
``waiver_id`` is the signature — I6) and closed at check-out, when tokens are
minted from the (capped, half-up) elapsed minutes. Every token movement goes
through ``tokens.do_checkout`` inside a single ``db.tx()``.

See docs/design/API.md § Check-in and DOMAIN.md § Token accounting.
"""
from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends

from app import activity, audit, db, serializers
from app.auth import current_user
from app.deps import api_error
from app.projects import current_waiver, is_leader
from app.scan import has_attestation
from app.tokens import do_checkout

router = APIRouter()

_IS_OVER = (
    "(e.status = 'completed' OR "
    "now() > e.starts_at + make_interval(mins => e.expected_minutes))"
)


# ---- helpers ----------------------------------------------------------------

def _open_event_by_code(code: str) -> dict | None:
    """The event a scanned code resolves to — only while it is still open (I11)."""
    return db.query_one(
        f"SELECT *, {_IS_OVER} AS is_over FROM events e "
        "WHERE e.checkin_code = %s AND e.status = 'open'",
        (code,),
    )


def _my_open_participation(event_id: int, user_id: int) -> dict | None:
    row = db.query_one(
        "SELECT id, checked_in_at FROM participations "
        "WHERE event_id = %s AND user_id = %s AND checked_out_at IS NULL",
        (event_id, user_id),
    )
    return {"id": row["id"], "checked_in_at": row["checked_in_at"]} if row else None


# ---- resolve a scanned code -------------------------------------------------

@router.get("/checkin/{code}")
def resolve(code: str, user: dict = Depends(current_user)):
    """Resolve a scanned code -> {event, project card (embedding the event),
    current waiver, my open participation}."""
    event = _open_event_by_code(code)
    if not event:
        raise api_error(404, "invalid_code")
    pid = event["project_id"]
    project = db.query_one("SELECT * FROM projects WHERE id = %s", (pid,))
    state = serializers.event_state(event["id"], user["id"])
    event_card = serializers.event_card(event, state)
    return {
        "event": event_card,
        "project": serializers.project_card(project, event_card),
        "waiver": current_waiver(pid),
        "my_open_participation": state["my_open_participation"],
    }


# ---- agree = check-in (the signature) ---------------------------------------

@router.post("/checkin/{code}/agree", status_code=201)
def agree(code: str, user: dict = Depends(current_user)):
    """Sign the waiver: insert a participation pinned to the CURRENT waiver (I6).

    Leaders check in through this same endpoint (their lead screen has the code).
    One open participation per (event, user) is enforced by the partial unique
    index ``idx_participations_open`` -> a duplicate surfaces as 409.

    Knowing the event code is an ASSERTION, not proof of presence -- the code is
    on a sign anyone can photograph -- so this lands ``attested = false`` unless a
    peer already scanned this person here (CHECKIN_PROOF.md §5.4).
    """
    event = _open_event_by_code(code)
    if not event:
        raise api_error(404, "invalid_code")
    pid = event["project_id"]
    waiver = current_waiver(pid)
    attested = has_attestation(event["id"], user["id"])
    try:
        with db.tx() as c:
            # Ensure the volunteer appears in the organizer's RSVP list (idempotent).
            c.execute(
                "INSERT INTO rsvps(event_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT (event_id, user_id) DO NOTHING",
                (event["id"], user["id"]),
            )
            row = c.execute(
                "INSERT INTO participations(event_id, user_id, waiver_id, attested) "
                "VALUES (%s, %s, %s, %s) RETURNING *",
                (event["id"], user["id"], waiver["id"], attested),
            ).fetchone()
            audit.log(
                c, "check_in", actor_user_id=user["id"], subject_user_id=user["id"],
                project_id=pid, event_id=event["id"], participation_id=row["id"],
            )
            activity.record(c, "checked_in", user["id"], event_id=event["id"], project_id=pid)
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "already_checked_in")
    return row


# ---- check-out --------------------------------------------------------------

@router.post("/participations/{participation_id}/checkout")
def checkout(participation_id: int, user: dict = Depends(current_user)):
    """Close a participation and mint its tokens (self or a leader of the project)."""
    part = db.query_one(
        "SELECT p.id, p.user_id, p.event_id, p.checked_in_at, p.checked_out_at, "
        "       e.project_id, e.expected_minutes "
        "FROM participations p JOIN events e ON e.id = p.event_id "
        "WHERE p.id = %s",
        (participation_id,),
    )
    if not part:
        raise api_error(404, "not_found")
    if part["user_id"] != user["id"] and not is_leader(part["project_id"], user["id"]):
        raise api_error(403, "not_allowed")
    if part["checked_out_at"] is not None:
        raise api_error(409, "already_checked_out")

    with db.tx() as c:
        row = do_checkout(
            c,
            {
                "id": part["id"],
                "user_id": part["user_id"],
                "checked_in_at": part["checked_in_at"],
                "expected_minutes": part["expected_minutes"],
                "event_id": part["event_id"],
                "project_id": part["project_id"],
            },
            actor_user_id=user["id"],
        )
    if row is None:
        raise api_error(409, "already_checked_out")
    return row
