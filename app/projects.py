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
    username: str

    @field_validator("username")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().lower()


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
            "leaders": _leaders(pid),
            "waiver": _current_waiver(pid),
            "am_leader": am_leader,
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
    return [serializers.project_card(r) for r in rows]


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
        "SELECT id FROM users WHERE lower(username) = %s", (body.username,)
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


@router.delete("/projects/{project_id}/leaders/{username}", status_code=204)
def remove_leader(
    project_id: int, username: str, user: dict = Depends(current_user)
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not _is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    target = db.query_one(
        "SELECT id FROM users WHERE lower(username) = %s",
        (username.strip().lower(),),
    )
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
