"""Service projects: the durable umbrella + its events (occurrences).

A PROJECT is the persistent thing (title, description, organizers, versioned
waivers, images, follows). Each time it actually runs it is an EVENT with its own
date, place, check-in code, and open/completed status (see app/events.py). Project
creation seeds the owner as a leader, waiver version 1, and the FIRST event.

Leaders manage the project (edit, co-leaders, add events) and every event under
it. The per-occurrence surface (rsvp, check-in, roster, close, QR, code) lives in
app/events.py. See docs/design/API.md § Projects and DOMAIN.md.
"""
from __future__ import annotations

import secrets
from datetime import datetime

import psycopg
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator

from app import activity, db, locations, serializers
from app.auth import current_user
from app.deps import Page, api_error, pagination

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

# A not-over event: still open AND now() has not passed starts_at + expected.
# (Complement of is_over.) Uses the alias ``e`` -- every query below binds it.
_NOT_OVER = (
    "(e.status <> 'completed' AND "
    "now() <= e.starts_at + make_interval(mins => e.expected_minutes))"
)
# is_over as a selectable expression (alias ``e``).
_IS_OVER_EXPR = (
    "(e.status = 'completed' OR "
    "now() > e.starts_at + make_interval(mins => e.expected_minutes))"
)


# ---- request bodies ---------------------------------------------------------

class ProjectCreate(BaseModel):
    """Create a project AND its first event in one call."""
    title: str
    description: str | None = None
    waiver_text: str | None = None
    # first event
    location_text: str
    starts_at: datetime
    expected_minutes: int = Field(gt=0)
    lat: float | None = None  # optional: where the event actually is (FEED.md F5)
    lon: float | None = None

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
    """Edit the durable project: title / description / waiver only.

    Event-specific fields (schedule, location, code, status) are edited per event,
    not here.
    """
    title: str | None = None
    description: str | None = None
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

    @field_validator("description", "waiver_text")
    @classmethod
    def _v_long_text(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("text too long")
        return v


class EventCreate(BaseModel):
    """Add another occurrence of an existing project."""
    location_text: str
    starts_at: datetime
    expected_minutes: int = Field(gt=0)
    lat: float | None = None  # optional: where it actually is (FEED.md F5)
    lon: float | None = None

    @field_validator("location_text")
    @classmethod
    def _v_location(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 200):
            raise ValueError("location must be 1-200 characters")
        return v


class AddLeaderIn(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().lower()


# ---- helpers ----------------------------------------------------------------

def new_code() -> str:
    return secrets.token_urlsafe(6)


def _get_project(project_id: int) -> dict | None:
    return db.query_one("SELECT * FROM projects WHERE id = %s", (project_id,))


def is_leader(project_id: int, user_id: int) -> bool:
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


def current_waiver(project_id: int) -> dict | None:
    return db.query_one(
        "SELECT id, version, text FROM waivers WHERE project_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (project_id,),
    )


def _is_following(project_id: int, user_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM follows WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    ) is not None


def insert_event(c, project_id: int, location_text: str, starts_at, expected_minutes: int,
                 lat: float | None = None, lon: float | None = None) -> dict:
    """Insert one event (occurrence) with a fresh check-in code, status 'open'.

    The address is remembered as a ``location`` (LOCATIONS.md) and, when that
    venue already knows where it is, this event inherits its coordinates -- so
    photos logged there attach themselves with nobody touching a map.
    """
    location_id, lat, lon = locations.apply_to_event(c, location_text, lat, lon)
    return c.execute(
        "INSERT INTO events(project_id, location_text, location_id, starts_at, "
        "expected_minutes, lat, lon, checkin_code, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open') RETURNING *",
        (project_id, location_text, location_id, starts_at, expected_minutes,
         lat, lon, new_code()),
    ).fetchone()


def _events_for_project(project_id: int) -> list[dict]:
    """All events of a project, each with an is_over flag, chronological."""
    return db.query(
        f"SELECT *, {_IS_OVER_EXPR} AS is_over FROM events e "
        "WHERE e.project_id = %s ORDER BY e.starts_at, e.id",
        (project_id,),
    )


def _detail(project_row: dict, user_id: int) -> dict:
    """Full project detail: durable fields + its events (upcoming ASC, then past DESC)."""
    pid = project_row["id"]
    am_leader = is_leader(pid, user_id)

    image_ids = [
        r["id"]
        for r in db.query(
            "SELECT id FROM images WHERE entity = 'project' AND entity_id = %s "
            "ORDER BY id",
            (pid,),
        )
    ]

    events = _events_for_project(pid)
    state = serializers.event_state_maps([e["id"] for e in events], user_id)
    upcoming = sorted(
        (e for e in events if not e["is_over"]), key=lambda e: (e["starts_at"], e["id"])
    )
    past = sorted(
        (e for e in events if e["is_over"]),
        key=lambda e: (e["starts_at"], e["id"]),
        reverse=True,
    )
    event_details = [
        serializers.event_detail(e, state[e["id"]], am_leader)
        for e in (*upcoming, *past)
    ]

    return {
        "id": pid,
        "title": project_row["title"],
        "description": project_row["description"],
        "owner": serializers.user_brief(project_row["owner_id"]),
        "leaders": _leaders(pid),
        "image_ids": image_ids,
        "cover_image_id": serializers.cover_image_id("project", pid),
        "primary_image_id": serializers.cover_image_id("project", pid),
        "waiver": current_waiver(pid),
        "am_leader": am_leader,
        "is_following": _is_following(pid, user_id),
        "follower_count": serializers.follower_count(pid),
        "events": event_details,
    }


# ---- list -------------------------------------------------------------------

@router.get("/projects")
def list_projects(
    scope: str = Query("upcoming"),
    q: str | None = Query(default=None),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """project_card[] for a scope, each embedding ONE relevant event.

    - upcoming (default): projects with >=1 not-over event; card shows the SOONEST
      not-over event; ordered by that event ASC.
    - past: projects with NO not-over event; card shows the most-recent event (or
      null); ordered by that event DESC.
    - mine: projects I lead OR have an rsvp/participation in; DESC. Card prefers the
      soonest not-over event, else the most recent.
    """
    uid = user["id"]
    params: list = []

    if scope == "mine":
        base = f"""
            SELECT p.id AS project_id, ce.event_id AS event_id
            FROM projects p
            LEFT JOIN LATERAL (
                SELECT e.id AS event_id FROM events e WHERE e.project_id = p.id
                ORDER BY {_NOT_OVER} DESC,
                         CASE WHEN {_NOT_OVER} THEN e.starts_at END ASC NULLS LAST,
                         e.starts_at DESC, e.id DESC
                LIMIT 1
            ) ce ON true
            WHERE (
                p.id IN (SELECT project_id FROM project_leaders WHERE user_id = %s)
                OR p.id IN (SELECT e2.project_id FROM events e2
                            JOIN participations pa ON pa.event_id = e2.id
                            WHERE pa.user_id = %s)
                OR p.id IN (SELECT e2.project_id FROM events e2
                            JOIN rsvps r ON r.event_id = e2.id WHERE r.user_id = %s)
            )
        """
        params += [uid, uid, uid]
        order = "ORDER BY p.created_at DESC, p.id DESC"
    elif scope == "past":
        base = f"""
            SELECT p.id AS project_id, pe.event_id AS event_id
            FROM projects p
            LEFT JOIN LATERAL (
                SELECT e.id AS event_id, e.starts_at FROM events e
                WHERE e.project_id = p.id ORDER BY e.starts_at DESC, e.id DESC LIMIT 1
            ) pe ON true
            WHERE NOT EXISTS (
                SELECT 1 FROM events e WHERE e.project_id = p.id AND {_NOT_OVER}
            )
        """
        order = "ORDER BY pe.starts_at DESC NULLS LAST, p.id DESC"
    else:  # upcoming
        base = f"""
            SELECT p.id AS project_id, se.event_id AS event_id
            FROM projects p
            JOIN LATERAL (
                SELECT e.id AS event_id, e.starts_at FROM events e
                WHERE e.project_id = p.id AND {_NOT_OVER}
                ORDER BY e.starts_at ASC, e.id ASC LIMIT 1
            ) se ON true
            WHERE true
        """
        order = "ORDER BY se.starts_at ASC, p.id ASC"

    if q:
        base += (
            " AND (p.title ILIKE %s OR p.description ILIKE %s OR EXISTS ("
            "SELECT 1 FROM events eq WHERE eq.project_id = p.id "
            "AND eq.location_text ILIKE %s))"
        )
        like = f"%{q}%"
        params += [like, like, like]

    sql = base + " " + order + " LIMIT %s OFFSET %s"
    params += [page.limit, page.offset]
    rows = db.query(sql, params)
    if not rows:
        return []

    proj_ids = [r["project_id"] for r in rows]
    event_ids = [r["event_id"] for r in rows if r["event_id"] is not None]

    proj_map = {
        p["id"]: p
        for p in db.query("SELECT * FROM projects WHERE id = ANY(%s)", (proj_ids,))
    }
    event_map = {
        e["id"]: e
        for e in db.query(
            f"SELECT *, {_IS_OVER_EXPR} AS is_over FROM events e WHERE e.id = ANY(%s)",
            (event_ids,),
        )
    } if event_ids else {}
    state = serializers.event_state_maps(event_ids, uid)

    cards = []
    for r in rows:
        eid = r["event_id"]
        ev = event_map.get(eid) if eid is not None else None
        ev_card = serializers.event_card(ev, state[eid]) if ev is not None else None
        cards.append(serializers.project_card(proj_map[r["project_id"]], ev_card))
    return cards


# ---- create -----------------------------------------------------------------

@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, user: dict = Depends(current_user)):
    """Create a project + owner-leader + waiver v1 + the FIRST event, in one tx."""
    waiver_text = (
        body.waiver_text
        if (body.waiver_text and body.waiver_text.strip())
        else DEFAULT_WAIVER
    )
    with db.tx() as c:
        proj = c.execute(
            "INSERT INTO projects(owner_id, title, description) "
            "VALUES (%s, %s, %s) RETURNING id",
            (user["id"], body.title, body.description or ""),
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
        ev = insert_event(c, pid, body.location_text, body.starts_at, body.expected_minutes,
                          body.lat, body.lon)
        # Starting a project is the most visible thing an organizer does — and it
        # announces the first event too, so scheduling that one is not news again.
        activity.record(c, "created_project", user["id"], event_id=ev["id"], project_id=pid)
    return _detail(_get_project(pid), user["id"])


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
    if not is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")

    data = body.model_dump(exclude_unset=True)
    set_fields = {
        k: v for k, v in data.items() if k != "waiver_text" and v is not None
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


# ---- add an event -----------------------------------------------------------

@router.post("/projects/{project_id}/events", status_code=201)
def add_event(
    project_id: int, body: EventCreate, user: dict = Depends(current_user)
):
    """Leader: schedule another occurrence of this project. Returns event_detail."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not is_leader(project_id, user["id"]):
        raise api_error(403, "not_a_leader")
    with db.tx() as c:
        ev = insert_event(
            c, project_id, body.location_text, body.starts_at, body.expected_minutes,
            body.lat, body.lon,
        )
        activity.record(c, "scheduled_event", user["id"], event_id=ev["id"], project_id=project_id)
    state = serializers.event_state(ev["id"], user["id"])
    return serializers.event_detail(ev, state, am_leader=True)


# ---- leaders ----------------------------------------------------------------

@router.post("/projects/{project_id}/leaders", status_code=201)
def add_leader(
    project_id: int, body: AddLeaderIn, user: dict = Depends(current_user)
):
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    if not is_leader(project_id, user["id"]):
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
    if not is_leader(project_id, user["id"]):
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


# ---- follow (project-scoped) ------------------------------------------------

@router.post("/projects/{project_id}/follow")
def follow_project(project_id: int, user: dict = Depends(current_user)):
    """Follow a project. Idempotent (ON CONFLICT DO NOTHING). Returns fresh count."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    with db.tx() as c:
        c.execute(
            "INSERT INTO follows(project_id, user_id) VALUES (%s, %s) "
            "ON CONFLICT (user_id, project_id) DO NOTHING",
            (project_id, user["id"]),
        )
    return {
        "is_following": True,
        "follower_count": serializers.follower_count(project_id),
    }


@router.delete("/projects/{project_id}/follow")
def unfollow_project(project_id: int, user: dict = Depends(current_user)):
    """Unfollow a project. Idempotent. Returns 200 with the fresh follower count."""
    row = _get_project(project_id)
    if not row:
        raise api_error(404, "not_found")
    with db.tx() as c:
        c.execute(
            "DELETE FROM follows WHERE project_id = %s AND user_id = %s",
            (project_id, user["id"]),
        )
    return {
        "is_following": False,
        "follower_count": serializers.follower_count(project_id),
    }
