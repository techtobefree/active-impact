"""Events: the per-occurrence surface of a service project.

An EVENT is one dated/located session of a project (app/projects.py owns the
durable umbrella). Everything that happens *at* an occurrence lives here: RSVP,
self check-in, the organizer's roster + event-leader designation, closing the
event (check out everyone, mint), and the check-in code + QR.

"Leader" throughout means a ``project_leaders`` organizer of the event's project
(resolved event -> project_id). See docs/design/API.md § Events and DOMAIN.md.

(The append-only check-in/out audit trail is app/audit.py -> the ``audit_log``
table; not to be confused with this ``events`` domain table.)
"""
from __future__ import annotations

import io

import psycopg
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app import audit, db, serializers
from app.auth import current_user
from app.deps import Page, api_error, pagination
from app.projects import current_waiver, is_leader, new_code
from app.tokens import do_checkout

router = APIRouter()

_IS_OVER = (
    "(e.status = 'completed' OR "
    "now() > e.starts_at + make_interval(mins => e.expected_minutes))"
)


class LeaderFlagIn(BaseModel):
    is_leader: bool


# ---- helpers ----------------------------------------------------------------

def _get_event(event_id: int) -> dict | None:
    """The event row plus a computed is_over flag."""
    return db.query_one(
        f"SELECT *, {_IS_OVER} AS is_over FROM events e WHERE e.id = %s", (event_id,)
    )


def _event_rsvps(event_id: int) -> list[dict]:
    """Everyone who RSVP'd to this event, oldest first, with check-in state."""
    rows = db.query(
        "SELECT user_id, is_leader, created_at FROM rsvps WHERE event_id = %s "
        "ORDER BY created_at, user_id",
        (event_id,),
    )
    out = []
    for r in rows:
        uid = r["user_id"]
        is_checked_in = db.query_one(
            "SELECT 1 FROM participations WHERE event_id = %s AND user_id = %s "
            "AND checked_out_at IS NULL",
            (event_id, uid),
        ) is not None
        has_participated = db.query_one(
            "SELECT 1 FROM participations WHERE event_id = %s AND user_id = %s",
            (event_id, uid),
        ) is not None
        out.append(
            {
                "user": serializers.user_brief(uid),
                "is_leader": r["is_leader"],
                "is_checked_in": is_checked_in,
                "has_participated": has_participated,
                "created_at": r["created_at"],
            }
        )
    return out


def _event_response(event_id: int, user_id: int) -> dict:
    """The GET /events/{id} shape: event_detail + project summary + waiver + state."""
    ev = _get_event(event_id)
    pid = ev["project_id"]
    am_leader = is_leader(pid, user_id)
    state = serializers.event_state(event_id, user_id)
    detail = serializers.event_detail(ev, state, am_leader)
    proj = db.query_one("SELECT id, title FROM projects WHERE id = %s", (pid,))
    detail["project"] = {
        "id": pid,
        "title": proj["title"],
        "cover_image_id": serializers.cover_image_id("project", pid),
    }
    detail["waiver"] = current_waiver(pid)
    detail["am_leader"] = am_leader
    return detail


def _require_leader(event_id: int, user_id: int) -> dict:
    """Fetch the event or 404; require the caller to lead its project or 403."""
    ev = _get_event(event_id)
    if not ev:
        raise api_error(404, "not_found")
    if not is_leader(ev["project_id"], user_id):
        raise api_error(403, "not_a_leader")
    return ev


# ---- event detail -----------------------------------------------------------

@router.get("/events/{event_id}")
def get_event(event_id: int, user: dict = Depends(current_user)):
    if not _get_event(event_id):
        raise api_error(404, "not_found")
    return _event_response(event_id, user["id"])


# ---- RSVP / self check-in ---------------------------------------------------

@router.post("/events/{event_id}/rsvp")
def rsvp(event_id: int, user: dict = Depends(current_user)):
    """RSVP to an event any time it is not over. Idempotent."""
    ev = _get_event(event_id)
    if not ev:
        raise api_error(404, "not_found")
    if ev["is_over"]:
        raise api_error(409, "event_over")
    with db.tx() as c:
        c.execute(
            "INSERT INTO rsvps(event_id, user_id) VALUES (%s, %s) "
            "ON CONFLICT (event_id, user_id) DO NOTHING",
            (event_id, user["id"]),
        )
    return _event_response(event_id, user["id"])


@router.post("/events/{event_id}/checkin")
def self_checkin(event_id: int, user: dict = Depends(current_user)):
    """Self-service check-in (no QR): ensure an RSVP, then create a participation
    pinned to the event's project's CURRENT waiver (I6). Silent waiver pin."""
    ev = _get_event(event_id)
    if not ev:
        raise api_error(404, "not_found")
    if ev["is_over"]:
        raise api_error(409, "event_over")
    pid = ev["project_id"]
    waiver = current_waiver(pid)
    try:
        with db.tx() as c:
            c.execute(
                "INSERT INTO rsvps(event_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT (event_id, user_id) DO NOTHING",
                (event_id, user["id"]),
            )
            part = c.execute(
                "INSERT INTO participations(event_id, user_id, waiver_id) "
                "VALUES (%s, %s, %s) RETURNING id",
                (event_id, user["id"], waiver["id"]),
            ).fetchone()
            audit.log(
                c, "check_in", actor_user_id=user["id"], subject_user_id=user["id"],
                project_id=pid, event_id=event_id, participation_id=part["id"],
            )
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "already_checked_in")
    return _event_response(event_id, user["id"])


# ---- RSVP roster + event-leader designation ---------------------------------

@router.get("/events/{event_id}/rsvps")
def list_rsvps(event_id: int, user: dict = Depends(current_user)):
    """The organizer's view of everyone who RSVP'd. Leaders only."""
    _require_leader(event_id, user["id"])
    return _event_rsvps(event_id)


@router.post("/events/{event_id}/rsvps/{user_id}/leader")
def set_rsvp_leader(
    event_id: int,
    user_id: int,
    body: LeaderFlagIn,
    user: dict = Depends(current_user),
):
    """Toggle a RSVP'd volunteer's event-leader designation. Organizer only."""
    _require_leader(event_id, user["id"])
    with db.tx() as c:
        cur = c.execute(
            "UPDATE rsvps SET is_leader = %s WHERE event_id = %s AND user_id = %s",
            (body.is_leader, event_id, user_id),
        )
        if cur.rowcount == 0:
            raise api_error(404, "not_found")
    return _event_rsvps(event_id)


# ---- close ------------------------------------------------------------------

@router.post("/events/{event_id}/close")
def close_event(event_id: int, user: dict = Depends(current_user)):
    """Leader: complete the event and check out everyone still on site (mint)."""
    ev = _require_leader(event_id, user["id"])
    if ev["status"] != "open":
        raise api_error(409, "event_not_open")
    with db.tx() as c:
        c.execute(
            "UPDATE events SET status = 'completed', updated_at = now() WHERE id = %s",
            (event_id,),
        )
        # Check out everyone still on site, in the same tx (capped mint each).
        open_parts = c.execute(
            "SELECT p.id, p.user_id, p.checked_in_at, p.event_id, "
            "       e.project_id, e.expected_minutes "
            "FROM participations p JOIN events e ON e.id = p.event_id "
            "WHERE p.event_id = %s AND p.checked_out_at IS NULL",
            (event_id,),
        ).fetchall()
        for part in open_parts:
            do_checkout(c, part, actor_user_id=user["id"])
    return _event_response(event_id, user["id"])


# ---- check-in code + QR -----------------------------------------------------

@router.post("/events/{event_id}/code/regenerate")
def regenerate_code(event_id: int, user: dict = Depends(current_user)):
    _require_leader(event_id, user["id"])
    code = new_code()
    with db.tx() as c:
        c.execute(
            "UPDATE events SET checkin_code = %s, updated_at = now() WHERE id = %s",
            (code, event_id),
        )
    return {"checkin_code": code}


@router.get("/events/{event_id}/qr.svg")
def event_qr(event_id: int, request: Request, user: dict = Depends(current_user)):
    ev = _require_leader(event_id, user["id"])
    host = request.headers.get("host", "")
    url = f"{request.url.scheme}://{host}/#/c/{ev['checkin_code']}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# ---- roster -----------------------------------------------------------------

@router.get("/events/{event_id}/roster")
def roster(
    event_id: int,
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    _require_leader(event_id, user["id"])
    rows = db.query(
        "SELECT * FROM participations WHERE event_id = %s "
        "ORDER BY checked_in_at DESC, id DESC LIMIT %s OFFSET %s",
        (event_id, page.limit, page.offset),
    )
    participations = [
        {
            "id": r["id"],
            "user": serializers.user_brief(r["user_id"]),
            "checked_in_at": r["checked_in_at"],
            "checked_out_at": r["checked_out_at"],
            "minutes": r["minutes"],
            "tokens_awarded": r["tokens_awarded"],
        }
        for r in rows
    ]
    checked_in_count = db.query_one(
        "SELECT COUNT(*) AS c FROM participations "
        "WHERE event_id = %s AND checked_out_at IS NULL",
        (event_id,),
    )["c"]
    return {
        "participations": participations,
        "checked_in_count": int(checked_in_count),
    }
