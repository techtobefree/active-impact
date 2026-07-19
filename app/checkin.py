"""Check-in / check-out — the waiver signature and the time sheet.

The QR encodes a URL, so the volunteer's native camera opens the PWA at
``#/c/{code}``; the frontend then drives these three endpoints:

- ``GET  /api/checkin/{code}``          resolve a scanned code -> project + waiver
- ``POST /api/checkin/{code}/agree``    the signature -> a new participation
- ``POST /api/participations/{id}/checkout``  close it, run the mint math

A participation is created by agreeing to the *current* waiver version at
check-in (its ``waiver_id`` is the signature — I6) and closed at check-out, when
tokens are minted from the (capped, half-up) elapsed minutes. Every token
movement goes through ``tokens.do_checkout`` inside a single ``db.tx()``.

See docs/design/API.md § Check-in and DOMAIN.md § Token accounting.
"""
from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends

from app import db, serializers
from app.auth import current_user
from app.deps import api_error
from app.tokens import do_checkout

router = APIRouter()


# ---- helpers ----------------------------------------------------------------

def _open_project_by_code(code: str) -> dict | None:
    """The project a scanned code resolves to — only while it is still open (I11)."""
    return db.query_one(
        "SELECT * FROM projects WHERE checkin_code = %s AND status = 'open'",
        (code,),
    )


def _current_waiver(project_id: int) -> dict | None:
    """The latest (highest-version) waiver for a project."""
    return db.query_one(
        "SELECT id, version, text FROM waivers WHERE project_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (project_id,),
    )


def _my_open_participation(project_id: int, user_id: int) -> dict | None:
    row = db.query_one(
        "SELECT id, checked_in_at FROM participations "
        "WHERE project_id = %s AND user_id = %s AND checked_out_at IS NULL",
        (project_id, user_id),
    )
    return {"id": row["id"], "checked_in_at": row["checked_in_at"]} if row else None


def _is_leader(project_id: int, user_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM project_leaders WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    ) is not None


# ---- resolve a scanned code -------------------------------------------------

@router.get("/checkin/{code}")
def resolve(code: str, user: dict = Depends(current_user)):
    """Resolve a scanned code -> {project card, current waiver, my open participation}."""
    project = _open_project_by_code(code)
    if not project:
        raise api_error(404, "invalid_code")
    return {
        "project": serializers.project_card(project),
        "waiver": _current_waiver(project["id"]),
        "my_open_participation": _my_open_participation(project["id"], user["id"]),
    }


# ---- agree = check-in (the signature) ---------------------------------------

@router.post("/checkin/{code}/agree", status_code=201)
def agree(code: str, user: dict = Depends(current_user)):
    """Sign the waiver: insert a participation pinned to the CURRENT waiver (I6).

    Leaders check in through this same endpoint (their lead screen has the code).
    One open participation per (project, user) is enforced by the partial unique
    index ``idx_participations_open`` -> a duplicate surfaces as 409.
    """
    project = _open_project_by_code(code)
    if not project:
        raise api_error(404, "invalid_code")
    waiver = _current_waiver(project["id"])
    try:
        with db.tx() as c:
            row = c.execute(
                "INSERT INTO participations(project_id, user_id, waiver_id) "
                "VALUES (%s, %s, %s) RETURNING *",
                (project["id"], user["id"], waiver["id"]),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "already_checked_in")
    return row


# ---- check-out --------------------------------------------------------------

@router.post("/participations/{participation_id}/checkout")
def checkout(participation_id: int, user: dict = Depends(current_user)):
    """Close a participation and mint its tokens (self or a leader of the project)."""
    part = db.query_one(
        "SELECT p.id, p.user_id, p.project_id, p.checked_in_at, p.checked_out_at, "
        "       pr.expected_minutes "
        "FROM participations p JOIN projects pr ON pr.id = p.project_id "
        "WHERE p.id = %s",
        (participation_id,),
    )
    if not part:
        raise api_error(404, "not_found")
    if part["user_id"] != user["id"] and not _is_leader(part["project_id"], user["id"]):
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
            },
        )
    if row is None:
        raise api_error(409, "already_checked_out")
    return row
