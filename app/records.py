"""Service records: one logged act of service (SERVICE_LOG.md + FEED.md).

One record = one photo + one caption, authored by whoever you currently are
(guest or real). It touches no tokens, participations, or the ledger -- but it
BELONGS TO AN EVENT (FEED.md F1): app/matching.py resolves which one from the
author's check-in, RSVP, or GPS + time, and the event's project card then carries
the photo. Nothing matched means ``event_id IS NULL`` -- the record is saved
anyway, as its author's own log entry, until they attach it.

The photo reuses the polymorphic images table (entity='service_record'); cheers
are a single 🙌 per user; reports drive light moderation (auto-hide at N distinct
reporters).

See docs/design/API.md § Service records.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, field_validator

from app import db, matching, serializers
from app.auth import current_user
from app.deps import Page, api_error, pagination
from app.images import decode_image_payload

router = APIRouter()

CAPTION_MAX = 280
# Spam floor (§9): at most this many records per author per rolling hour.
RATE_LIMIT_PER_HOUR = 20
# Moderation (§9): this many DISTINCT reporters auto-hide a record.
HIDE_THRESHOLD = 3


# ---- request bodies ---------------------------------------------------------

class RecordCreate(BaseModel):
    """One-shot create: the caption, its (required) photo, and where I am.

    ``event_id`` states the event outright (logging from an event page or after
    picking one); ``lat``/``lon`` are the device's position, used only to work out
    which event this was (FEED.md §4). All three are optional -- a bare caption +
    photo still posts, it just may land unattached.
    """
    caption: str
    content_type: str
    data_base64: str
    event_id: int | None = None
    lat: float | None = None
    lon: float | None = None

    @field_validator("caption")
    @classmethod
    def _v_caption(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= CAPTION_MAX):
            raise ValueError(f"caption must be 1-{CAPTION_MAX} characters")
        return v

    @field_validator("lat")
    @classmethod
    def _v_lat(cls, v: float | None) -> float | None:
        if v is not None and not (-90 <= v <= 90):
            raise ValueError("lat must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def _v_lon(cls, v: float | None) -> float | None:
        if v is not None and not (-180 <= v <= 180):
            raise ValueError("lon must be between -180 and 180")
        return v


class RecordAttach(BaseModel):
    """Attach, re-attach, or (with null) detach a record -- the author's remedy
    when the auto-match guessed wrong or found nothing."""
    event_id: int | None = None


class ReportIn(BaseModel):
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def _v_reason(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 1000:
            raise ValueError("reason must be at most 1000 characters")
        return v


# ---- helpers ----------------------------------------------------------------

def _visible_record(record_id: int) -> dict:
    """A record that exists and is not hidden, else 404 (the public view)."""
    row = db.query_one(
        "SELECT * FROM service_records WHERE id = %s AND hidden = false",
        (record_id,),
    )
    if not row:
        raise api_error(404, "not_found")
    return row


def _cheer_count(record_id: int) -> int:
    return int(
        db.query_one(
            "SELECT COUNT(*) AS c FROM cheers WHERE record_id = %s", (record_id,)
        )["c"]
    )


# ---- create -----------------------------------------------------------------

@router.post("/service_records", status_code=201)
def create_record(body: RecordCreate, user: dict = Depends(current_user)):
    """Insert a record + its image in ONE tx (record first for the id, then the
    photo pinned to it -- the check-in "insert then pin" pattern), having first
    worked out WHICH EVENT this was (FEED.md §4). Returns the record_card.
    429 rate_limited past the per-hour cap."""
    uid = user["id"]
    recent = db.query_one(
        "SELECT COUNT(*) AS c FROM service_records "
        "WHERE user_id = %s AND created_at > now() - interval '1 hour'",
        (uid,),
    )["c"]
    if recent >= RATE_LIMIT_PER_HOUR:
        raise api_error(429, "rate_limited")

    # Validate the photo (content-type + base64 + 10 MB) before opening the tx.
    data = decode_image_payload(body.content_type, body.data_base64)

    try:
        event_id, reason = matching.resolve_event(uid, body.lat, body.lon, body.event_id)
    except LookupError:
        raise api_error(404, "event_not_found")

    with db.tx() as c:
        rec = c.execute(
            "INSERT INTO service_records(user_id, caption, event_id, lat, lon, match_reason) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (uid, body.caption, event_id, body.lat, body.lon, reason),
        ).fetchone()
        img = c.execute(
            "INSERT INTO images(entity, entity_id, content_type, bytes, size, "
            "uploaded_by, is_primary) "
            "VALUES ('service_record', %s, %s, %s, %s, %s, true) RETURNING id",
            (rec["id"], body.content_type, data, len(data), uid),
        ).fetchone()
        # A record that found its event by other means also geolocates it, once.
        if event_id is not None and reason != "nearby":
            matching.bootstrap_coords(c, event_id, body.lat, body.lon)

    cheer = {"cheer_count": 0, "i_cheered": False}
    event = serializers.record_event_maps([event_id]).get(event_id)
    return serializers.record_card(rec, user, cheer, img["id"], event)


@router.patch("/service_records/{record_id}")
def attach_record(
    record_id: int, body: RecordAttach, user: dict = Depends(current_user)
):
    """Author-only: put this record on an event (or take it off, with null).

    The remedy for a wrong guess and the way an unattached record finds its home.
    Targets are bounded exactly like an explicit create: an event still collecting
    photos, or one the author has actually been to (matching.may_attach)."""
    rec = db.query_one("SELECT * FROM service_records WHERE id = %s", (record_id,))
    if not rec:
        raise api_error(404, "not_found")
    if rec["user_id"] != user["id"]:
        raise api_error(403, "not_yours")

    event_id = body.event_id
    if event_id is not None:
        try:
            if not matching.may_attach(user["id"], event_id):
                raise api_error(409, "event_not_attachable")
        except LookupError:
            raise api_error(404, "event_not_found")

    with db.tx() as c:
        rec = c.execute(
            "UPDATE service_records SET event_id = %s, match_reason = %s "
            "WHERE id = %s RETURNING *",
            (event_id, "explicit" if event_id is not None else None, record_id),
        ).fetchone()
    return serializers.record_cards([rec], user["id"])[0]


# ---- feed / detail ----------------------------------------------------------

@router.get("/service_records")
def list_records(
    scope: str = Query("all"),
    event_id: int | None = Query(default=None),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """record_card[] newest-first, hidden excluded.

    scope=all (default) · mine (the caller's own, attached or not) · unattached
    (the caller's own that matched no event -- their loose log entries).
    ``event_id`` narrows any scope to ONE event's feed, which is what the event
    page renders. Everything is BATCHED by record id -- no N+1, like GET /projects.
    """
    uid = user["id"]
    where = ["hidden = false"]
    params: list = []
    if scope == "mine":
        where.append("user_id = %s")
        params.append(uid)
    elif scope == "unattached":
        where.append("user_id = %s AND event_id IS NULL")
        params.append(uid)
    if event_id is not None:
        where.append("event_id = %s")
        params.append(event_id)
    params += [page.limit, page.offset]
    rows = db.query(
        "SELECT * FROM service_records WHERE " + " AND ".join(where)
        + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
        params,
    )
    return serializers.record_cards(rows, uid)


@router.get("/service_records/{record_id}")
def get_record(record_id: int, user: dict = Depends(current_user)):
    """A single record (share target / detail). 404 if hidden or absent."""
    rec = _visible_record(record_id)
    return serializers.record_cards([rec], user["id"])[0]


# ---- delete (author only) ---------------------------------------------------

@router.delete("/service_records/{record_id}", status_code=204)
def delete_record(record_id: int, user: dict = Depends(current_user)):
    """Author-only hard delete. cheers/reports cascade via FK; the image is
    polymorphic (no FK), so it is removed explicitly in the same tx."""
    rec = db.query_one("SELECT * FROM service_records WHERE id = %s", (record_id,))
    if not rec:
        raise api_error(404, "not_found")
    if rec["user_id"] != user["id"]:
        raise api_error(403, "not_yours")
    with db.tx() as c:
        c.execute(
            "DELETE FROM images WHERE entity = 'service_record' AND entity_id = %s",
            (record_id,),
        )
        c.execute("DELETE FROM service_records WHERE id = %s", (record_id,))
    return Response(status_code=204)


# ---- cheer (toggle) ---------------------------------------------------------

@router.post("/service_records/{record_id}/cheer")
def cheer(record_id: int, user: dict = Depends(current_user)):
    """Add my 🙌. Idempotent (ON CONFLICT DO NOTHING). Returns the fresh count."""
    _visible_record(record_id)
    with db.tx() as c:
        c.execute(
            "INSERT INTO cheers(record_id, user_id) VALUES (%s, %s) "
            "ON CONFLICT (record_id, user_id) DO NOTHING",
            (record_id, user["id"]),
        )
    return {"cheered": True, "cheer_count": _cheer_count(record_id)}


@router.delete("/service_records/{record_id}/cheer")
def uncheer(record_id: int, user: dict = Depends(current_user)):
    """Remove my 🙌. Idempotent. Returns the fresh count."""
    _visible_record(record_id)
    with db.tx() as c:
        c.execute(
            "DELETE FROM cheers WHERE record_id = %s AND user_id = %s",
            (record_id, user["id"]),
        )
    return {"cheered": False, "cheer_count": _cheer_count(record_id)}


# ---- report (moderation-light) ----------------------------------------------

@router.post("/service_records/{record_id}/report", status_code=204)
def report(
    record_id: int,
    body: ReportIn | None = None,
    user: dict = Depends(current_user),
):
    """File a report (idempotent per user). At HIDE_THRESHOLD distinct reporters
    the record auto-hides (dropped from every feed)."""
    _visible_record(record_id)
    reason = body.reason if body else None
    with db.tx() as c:
        c.execute(
            "INSERT INTO reports(record_id, user_id, reason) VALUES (%s, %s, %s) "
            "ON CONFLICT (record_id, user_id) DO NOTHING",
            (record_id, user["id"], reason),
        )
        distinct = c.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM reports WHERE record_id = %s",
            (record_id,),
        ).fetchone()["c"]
        if distinct >= HIDE_THRESHOLD:
            c.execute(
                "UPDATE service_records SET hidden = true WHERE id = %s", (record_id,)
            )
    return Response(status_code=204)
