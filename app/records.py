"""Service records: the anonymous-first social log (SERVICE_LOG.md §5, §6, §8, §9).

One record = one photo + one caption, authored by whoever you currently are
(guest or real). This is a STANDALONE layer -- it touches no tokens,
participations, projects, or the ledger. The photo reuses the polymorphic images
table (entity='service_record'); cheers are a single 🙌 per user; reports drive
light moderation (auto-hide at N distinct reporters).

See docs/design/API.md § Service records.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, field_validator

from app import db, serializers
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
    """One-shot create: the caption plus its (required) photo payload."""
    caption: str
    content_type: str
    data_base64: str

    @field_validator("caption")
    @classmethod
    def _v_caption(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= CAPTION_MAX):
            raise ValueError(f"caption must be 1-{CAPTION_MAX} characters")
        return v


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


def _author(user_id: int) -> dict:
    """The author row carrying the email flag (never emitted) for is_guest."""
    return db.query_one(
        "SELECT id, display_name, email FROM users WHERE id = %s", (user_id,)
    )


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
    photo pinned to it -- the check-in "insert then pin" pattern). Returns the
    record_card. 429 rate_limited past the per-hour cap."""
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

    with db.tx() as c:
        rec = c.execute(
            "INSERT INTO service_records(user_id, caption) VALUES (%s, %s) RETURNING *",
            (uid, body.caption),
        ).fetchone()
        img = c.execute(
            "INSERT INTO images(entity, entity_id, content_type, bytes, size, "
            "uploaded_by, is_primary) "
            "VALUES ('service_record', %s, %s, %s, %s, %s, true) RETURNING id",
            (rec["id"], body.content_type, data, len(data), uid),
        ).fetchone()

    cheer = {"cheer_count": 0, "i_cheered": False}
    return serializers.record_card(rec, user, cheer, img["id"])


# ---- feed / detail ----------------------------------------------------------

@router.get("/service_records")
def list_records(
    scope: str = Query("all"),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """record_card[] newest-first, hidden excluded. scope=all (default, global
    feed) or mine (the caller's own). Cheer state + photos are BATCHED by record
    id -- no N+1, like GET /projects."""
    uid = user["id"]
    if scope == "mine":
        rows = db.query(
            "SELECT * FROM service_records WHERE hidden = false AND user_id = %s "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            (uid, page.limit, page.offset),
        )
    else:  # all
        rows = db.query(
            "SELECT * FROM service_records WHERE hidden = false "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            (page.limit, page.offset),
        )
    if not rows:
        return []

    record_ids = [r["id"] for r in rows]
    author_ids = list({r["user_id"] for r in rows})
    authors = {
        a["id"]: a
        for a in db.query(
            "SELECT id, display_name, email FROM users WHERE id = ANY(%s)",
            (author_ids,),
        )
    }
    cheer = serializers.record_cheer_maps(record_ids, uid)
    photos = serializers.record_photo_maps(record_ids)
    return [
        serializers.record_card(r, authors[r["user_id"]], cheer[r["id"]], photos.get(r["id"]))
        for r in rows
    ]


@router.get("/service_records/{record_id}")
def get_record(record_id: int, user: dict = Depends(current_user)):
    """A single record (share target / detail). 404 if hidden or absent."""
    rec = _visible_record(record_id)
    cheer = serializers.record_cheer_maps([record_id], user["id"])[record_id]
    photo = serializers.cover_image_id("service_record", record_id)
    return serializers.record_card(rec, _author(rec["user_id"]), cheer, photo)


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
