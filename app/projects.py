"""Impact projects: CRUD, leaders, versioned waivers, QR code, and roster.

A project is anything with a time and a place. Creating one seeds the owner as a
leader and waiver version 1 (default template unless custom text is supplied).
Leaders may edit the project, manage co-leaders, show the check-in QR, close it,
and read the roster. See docs/design/API.md § Projects and DOMAIN.md.
"""
from __future__ import annotations

import io
import secrets
from datetime import datetime

import psycopg
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from app import db, serializers
from app.auth import current_user
from app.deps import Page, api_error, pagination
from app.tokens import do_checkout

router = APIRouter()

# Placeholder waiver — deliberately NOT legal advice. Project owners are expected
# to replace it with something appropriate before running real events.
DEFAULT_WAIVER = (
    "By checking in to this project you acknowledge that you are volunteering at "
    "your own risk, release the organizers and Active Impact from liability for "
    "any injury or loss, and agree to follow the safety instructions of the "
    "project leaders. (Placeholder template -- not legal advice. Replace with a "
    "waiver appropriate to your project and jurisdiction before running real "
    "events.)"
)


# ---- request bodies ---------------------------------------------------------

class ProjectCreate(BaseModel):
    title: str
    description: str | None = None
    location_text: str
    starts_at: datetime
    expected_minutes: int = Field(gt=0)
    waiver_text: str | None = None

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 120):
            raise ValueError("title must be 1-120 characters")
        return v

    @field_validator("location_text")
    @classmethod
    def _v_location(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 200):
            raise ValueError("location must be 1-200 characters")
        return v

    @field_validator("description", "waiver_text")
    @classmethod
    def _v_long_text(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("text too long")
        return v


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    location_text: str | None = None
    starts_at: datetime | None = None
    expected_minutes: int | None = Field(default=None, gt=0)
    waiver_text: str | None = None

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (1 <= len(v) <= 120):
            raise ValueError("title must be 1-120 characters")
        return v

    @field_validator("location_text")
    @classmethod
    def _v_location(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (1 <= len(v) <= 200):
            raise ValueError("location must be 1-200 characters")
        return v

    @field_validator("description", "waiver_text")
    @classmethod
    def _v_long_text(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("text too long")
        return v


class AddLeaderIn(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().lower()


class LeaderFlagIn(BaseModel):
    is_leader: bool


# ---- helpers ----------------------------------------------------------------

def _new_code() -> str:
    return secrets.token_urlsafe(6)


def _get_project(project_id: int) -> dict | None:
    return db.query_one("SELECT * FROM projects WHERE id = %s", (project_id,))


def _is_leader(project_id: int, user_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM project_leaders WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    ) is not None


def _leaders(project_id: int) -> list[dict]:
    rows = db.query(
        "SELECT user_id FROM project_leaders WHERE project_id = %s "
        "ORDER BY added_at, user_id",
        (project_id,),
    )
    return [serializers.user_brief(r["user_id"]) for r in rows]


def _current_waiver(project_id: int) -> dict | None:
    return db.query_one(
        "SELECT id, version, text FROM waivers WHERE project_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (project_id,),
    )


def _is_over(row: dict) -> bool:
    """A project is over when completed OR now() is past starts_at + expected_minutes."""
    if row["status"] == "completed":
        return True
    r = db.query_one(
        "SELECT (now() > (%s::timestamptz + make_interval(mins => %s))) AS over",
        (row["starts_at"], row["expected_minutes"]),
    )
    return bool(r["over"])


def _my_rsvp(project_id: int, user_id: int) -> dict | None:
    r = db.query_one(
        "SELECT is_leader FROM rsvps WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    )
    return {"is_leader": r["is_leader"]} if r else None


def _rsvps(project_id: int) -> list[dict]:
    """Everyone who RSVP'd, oldest first, with their check-in / participation state."""
    rows = db.query(
        "SELECT user_id, is_leader, created_at FROM rsvps WHERE project_id = %s "
        "ORDER BY created_at, user_id",
        (project_id,),
    )
    out = []
    for r in rows:
        uid = r["user_id"]
        is_checked_in = db.query_one(
            "SELECT 1 FROM participations WHERE project_id = %s AND user_id = %s "
            "AND checked_out_at IS NULL",
            (project_id, uid),
        ) is not None
        has_participated = db.query_one(
            "SELECT 1 FROM participations WHERE project_id = %s AND user_id = %s",
            (project_id, uid),
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


def _detail(row: dict, user_id: int) -> dict:
    """Full project detail from a fetched projects row, from user_id's view."""
    pid = row["id"]
    am_leader = _is_leader(pid, user_id)

    image_ids = [
        r["id"]
        for r in db.query(
            "SELECT id FROM images WHERE entity = 'project' AND entity_id = %s "
            "ORDER BY id",
            (pid,),
        )
    ]

    my_open = db.query_one(
        "SELECT id, checked_in_at FROM participations "
        "WHERE project_id = %s AND user_id = %s AND checked_out_at IS NULL",
        (pid, user_id),
    )
    my_minutes = db.query_one(
        "SELECT COALESCE(SUM(minutes), 0) AS m FROM participations "
        "WHERE project_id = %s AND user_id = %s AND checked_out_at IS NOT NULL",
        (pid, user_id),
    )["m"]

    out = serializers.project_card(row)
    out.update(
        {
            "description": row["description"],
            "image_ids": image_ids,
            "primary_image_id": serializers.cover_image_id("project", pid),
            "leaders": _leaders(pid),
            "waiver": _current_waiver(pid),
            "am_leader": am_leader,
            "is_over": _is_over(row),
            "my_rsvp": _my_rsvp(pid, user_id),
            "my_open_participation": (
                {"id": my_open["id"], "checked_in_at": my_open["checked_in_at"]}
                if my_open
                else None
            ),
            "my_hours_here": round(int(my_minutes) / 60, 1),
        }
    )
    if am_leader:
        out["checkin_code"] = row["checkin_code"]
    return out


# ---- list -------------------------------------------------------------------

@router.get("/projects")
def list_projects(
    scope: str = Query("upcoming"),
    q: str | None = Query(default=None),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """project_card[] for a scope. upcoming (default, ASC), past (DESC), mine (DESC)."""
    params: list = []
    where: list[str] = []

    if scope == "mine":
        where.append(
            "(id IN (SELECT project_id FROM participations WHERE user_id = %s) "
            "OR id IN (SELECT project_id FROM project_leaders WHERE user_id = %s))"
        )
        params += [user["id"], user["id"]]
        order = "starts_at DESC, id DESC"
    elif scope == "past":
        where.append(
            "NOT (status = 'open' AND starts_at >= now() - interval '12 hours')"
        )
        order = "starts_at DESC, id DESC"
    else:  # upcoming (default)
        where.append("status = 'open' AND starts_at >= now() - interval '12 hours'")
        order = "starts_at ASC, id ASC"

    if q:
        where.append(
            "(title ILIKE %s OR description ILIKE %s OR location_text ILIKE %s)"
        )
        like = f"%{q}%"
        params += [like, like, like]

    sql = (
        "SELECT * FROM projects WHERE "
        + " AND ".join(where)
        + f" ORDER BY {order} LIMIT %s OFFSET %s"
    )
    params += [page.limit, page.offset]
    rows = db.query(sql, params)
    cards = [serializers.project_card(r) for r in rows]
    if not rows:
        return cards

    pids = [r["id"] for r in rows]
    uid = user["id"]

    over_map = {
        r["id"]: r["is_over"]
        for r in db.query(
            "SELECT id, (status = 'completed' "
            "OR now() > starts_at + make_interval(mins => expected_minutes)) "
            "AS is_over FROM projects WHERE id = ANY(%s)",
            (pids,),
        )
    }
    rsvp_map = {
        r["project_id"]: {"is_leader": r["is_leader"]}
        for r in db.query(
            "SELECT project_id, is_leader FROM rsvps "
            "WHERE user_id = %s AND project_id = ANY(%s)",
            (uid, pids),
        )
    }
    open_map = {
        r["project_id"]: {"id": r["id"], "checked_in_at": r["checked_in_at"]}
        for r in db.query(
            "SELECT DISTINCT ON (project_id) project_id, id, checked_in_at "
            "FROM participations "
            "WHERE user_id = %s AND checked_out_at IS NULL AND project_id = ANY(%s) "
            "ORDER BY project_id",
            (uid, pids),
        )
    }
    hours_map = {
        r["project_id"]: r["m"]
        for r in db.query(
            "SELECT project_id, COALESCE(SUM(minutes), 0) AS m FROM participations "
            "WHERE user_id = %s AND checked_out_at IS NOT NULL AND project_id = ANY(%s) "
            "GROUP BY project_id",
            (uid, pids),
        )
    }

    for card in cards:
        cid = card["id"]
        card["is_over"] = over_map[cid]
        card["my_rsvp"] = rsvp_map.get(cid)
        card["my_open_participation"] = open_map.get(cid)
        card["my_hours_here"] = round(int(hours_map.get(cid, 0)) / 60, 1)
    return cards


# ---- create -----------------------------------------------------------------

@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, user: dict = Depends(current_user)):
    waiver_text = (
        body.waiver_text
        if (body.waiver_text and body.waiver_text.strip())
        else DEFAULT_WAIVER
    )
    with db.tx() as c:
        proj = c.execute(
            "INSERT INTO projects"
            "(owner_id, title, description, location_text, starts_at, "
            " expected_minutes, checkin_code) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                user["id"],
                body.title,
                body.description or "",
                body.location_text,
                body.starts_at,
                body.expected_minutes,
                _new_code(),
            ),
        ).fetchone()
        pid = proj["id"]
        c.execute(
            "INSERT INTO project_leaders(project_id, user_id) VALUES (%s, %s)",
            (pid, user["id"]),
        )
        c.execute(
            "INSERT INTO waivers(project_id, version, text) VALUES (%s, 1, %s)",
            (pid, waiver_text),
        )
    row = _get_project(pid)
    return _detail(row, user["id"])


# ---- detail -----------------------------------------------------------------

@router.get("/projects/{project_id}")
def get_project(project_id: int, user: dict = Depends(current_user)):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    return _detail(row, user["id"])


# ---- edit -------------------------------------------------------------------

@router.patch("/projects/{project_id}")
def update_project(
    project_id: int, body: ProjectUpdate, user: dict = Depends(current_user)
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    if row["status"] != "open":
        raise api_error(409, "project_not_open")

    data = body.model_dump(exclude_unset=True)
    # Column fields to update (drop waiver, drop explicit nulls -> those are no-ops
    # since the columns are NOT NULL). Keys come from the fixed model field set.
    set_fields = {
        k: v
        for k, v in data.items()
        if k != "waiver_text" and v is not None
    }
    new_waiver = data.get("waiver_text")

    with db.tx() as c:
        if set_fields:
            sets = ", ".join(f"{k} = %s" for k in set_fields)
            params = list(set_fields.values()) + [project_id]
            c.execute(
                f"UPDATE projects SET {sets}, updated_at = now() WHERE id = %s",
                params,
            )
        if new_waiver is not None and new_waiver.strip():
            cur = c.execute(
                "SELECT version, text FROM waivers WHERE project_id = %s "
                "ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            # I5: never mutate an existing waiver row -- a changed text inserts n+1.
            if cur is None or new_waiver != cur["text"]:
                next_version = (cur["version"] + 1) if cur else 1
                c.execute(
                    "INSERT INTO waivers(project_id, version, text) "
                    "VALUES (%s, %s, %s)",
                    (project_id, next_version, new_waiver),
                )

    return _detail(_get_project(project_id), user["id"])


# ---- close ------------------------------------------------------------------

@router.post("/projects/{project_id}/close")
def close_project(project_id: int, user: dict = Depends(current_user)):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    if row["status"] != "open":
        raise api_error(409, "project_not_open")

    with db.tx() as c:
        c.execute(
            "UPDATE projects SET status = 'completed', updated_at = now() "
            "WHERE id = %s",
            (project_id,),
        )
        # Check out everyone still on site, in the same tx (capped mint each).
        open_parts = c.execute(
            "SELECT p.id, p.user_id, p.checked_in_at, pr.expected_minutes "
            "FROM participations p JOIN projects pr ON pr.id = p.project_id "
            "WHERE p.project_id = %s AND p.checked_out_at IS NULL",
            (project_id,),
        ).fetchall()
        for part in open_parts:
            do_checkout(c, part)

    return _detail(_get_project(project_id), user["id"])


# ---- leaders ----------------------------------------------------------------

@router.post("/projects/{project_id}/leaders", status_code=201)
def add_leader(
    project_id: int, body: AddLeaderIn, user: dict = Depends(current_user)
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    target = db.query_one(
        "SELECT id FROM users WHERE lower(email) = %s", (body.email,)
    )
    if not target:
        raise api_error(404, "user_not_found")
    try:
        with db.tx() as c:
            c.execute(
                "INSERT INTO project_leaders(project_id, user_id) VALUES (%s, %s)",
                (project_id, target["id"]),
            )
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "already_leader")
    return _leaders(project_id)


@router.delete("/projects/{project_id}/leaders/{user_id}", status_code=204)
def remove_leader(
    project_id: int, user_id: int, user: dict = Depends(current_user)
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    target = db.query_one("SELECT id FROM users WHERE id = %s", (user_id,))
    if not target:
        raise api_error(404, "user_not_found")
    if target["id"] == row["owner_id"]:
        raise api_error(409, "cannot_remove_owner")
    with db.tx() as c:
        cur = c.execute(
            "DELETE FROM project_leaders WHERE project_id = %s AND user_id = %s",
            (project_id, target["id"]),
        )
        removed = cur.rowcount
    if not removed:
        raise api_error(404, "not_found")
    return Response(status_code=204)


# ---- RSVP / self check-in ---------------------------------------------------

@router.post("/projects/{project_id}/rsvp")
def rsvp(project_id: int, user: dict = Depends(current_user)):
    """RSVP to a project any time it is not over. Idempotent."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if _is_over(row):
        raise api_error(409, "project_over")
    with db.tx() as c:
        c.execute(
            "INSERT INTO rsvps(project_id, user_id) VALUES (%s, %s) "
            "ON CONFLICT (project_id, user_id) DO NOTHING",
            (project_id, user["id"]),
        )
    return _detail(_get_project(project_id), user["id"])


@router.post("/projects/{project_id}/checkin")
def self_checkin(project_id: int, user: dict = Depends(current_user)):
    """Self-service check-in (no QR, no waiver screen): ensure an RSVP, then
    create a participation pinned to the CURRENT waiver (I6). Silent waiver pin."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if _is_over(row):
        raise api_error(409, "project_over")
    waiver = _current_waiver(project_id)
    try:
        with db.tx() as c:
            c.execute(
                "INSERT INTO rsvps(project_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT (project_id, user_id) DO NOTHING",
                (project_id, user["id"]),
            )
            c.execute(
                "INSERT INTO participations(project_id, user_id, waiver_id) "
                "VALUES (%s, %s, %s)",
                (project_id, user["id"], waiver["id"]),
            )
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "already_checked_in")
    return _detail(_get_project(project_id), user["id"])


# ---- RSVP roster + event-leader designation ---------------------------------

@router.get("/projects/{project_id}/rsvps")
def list_rsvps(project_id: int, user: dict = Depends(current_user)):
    """The organizer's view of everyone who RSVP'd. Leaders (am_leader) only."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    return _rsvps(project_id)


@router.post("/projects/{project_id}/rsvps/{user_id}/leader")
def set_rsvp_leader(
    project_id: int,
    user_id: int,
    body: LeaderFlagIn,
    user: dict = Depends(current_user),
):
    """Toggle a RSVP'd volunteer's event-leader designation. Organizer only."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    with db.tx() as c:
        cur = c.execute(
            "UPDATE rsvps SET is_leader = %s WHERE project_id = %s AND user_id = %s",
            (body.is_leader, project_id, user_id),
        )
        if cur.rowcount == 0:
            raise api_error(404, "not_found")
    return _rsvps(project_id)


# ---- check-in code + QR -----------------------------------------------------

@router.post("/projects/{project_id}/code/regenerate")
def regenerate_code(project_id: int, user: dict = Depends(current_user)):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    code = _new_code()
    with db.tx() as c:
        c.execute(
            "UPDATE projects SET checkin_code = %s, updated_at = now() WHERE id = %s",
            (code, project_id),
        )
    return {"checkin_code": code}


@router.get("/projects/{project_id}/qr.svg")
def project_qr(
    project_id: int, request: Request, user: dict = Depends(current_user)
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    host = request.headers.get("host", "")
    url = f"{request.url.scheme}://{host}/#/c/{row['checkin_code']}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# ---- roster -----------------------------------------------------------------

@router.get("/projects/{project_id}/roster")
def roster(
    project_id: int,
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    rows = db.query(
        "SELECT * FROM participations WHERE project_id = %s "
        "ORDER BY checked_in_at DESC, id DESC LIMIT %s OFFSET %s",
        (project_id, page.limit, page.offset),
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
        "WHERE project_id = %s AND checked_out_at IS NULL",
        (project_id,),
    )["c"]
    return {
        "participations": participations,
        "checked_in_count": int(checked_in_count),
    }
