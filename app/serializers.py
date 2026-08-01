"""Canonical read shapes, shared so every module emits identical JSON.

See docs/design/DOMAIN.md § Standard read shapes. These run supplementary
queries via the read pool; callers pass an already-fetched primary row.

A project CARD now embeds one relevant EVENT (occurrence). Per-event, per-user
action state (checked_in_count, my_rsvp, my_open_participation, my_hours_here) is
batched by event id via ``event_state_maps`` so the feed has no N+1.
"""
from __future__ import annotations

from app import db


def user_brief(uid: int | None) -> dict | None:
    """Public identity: id + display name only. Emails are private -- never here."""
    if uid is None:
        return None
    return db.query_one(
        "SELECT id, display_name FROM users WHERE id=%s", (uid,)
    )


def me_shape(row: dict) -> dict:
    """Private self view -- the ONLY shape that carries the email (and balance).

    ``is_guest`` (email IS NULL -- SERVICE_LOG.md §4) lets the UI show the "create
    an account to save your history" nudge. A guest's email serializes as JSON
    null; it is never emitted as the string "None".
    """
    shape = {k: row[k] for k in ("id", "email", "display_name", "bio", "balance", "created_at")}
    shape["is_guest"] = row["email"] is None
    # My personal QR handle. PRIVATE view only -- a code is handed out by its
    # owner (they show it), never scraped off a public profile (CHECKIN_PROOF.md §3).
    shape["qr_token"] = row["qr_token"]
    return shape


def user_public(row: dict) -> dict:
    """Public profile + volunteer stats. Email and balance are intentionally omitted."""
    uid = row["id"]
    minutes = db.query_one(
        "SELECT COALESCE(SUM(minutes),0) AS m FROM participations "
        "WHERE user_id=%s AND checked_out_at IS NOT NULL",
        (uid,),
    )["m"]
    earned = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS a FROM token_entries "
        "WHERE to_user_id=%s AND kind='earn'",
        (uid,),
    )["a"]
    # projects_joined counts distinct PROJECTS across the events I participated in.
    joined = db.query_one(
        "SELECT COUNT(DISTINCT e.project_id) AS c FROM participations p "
        "JOIN events e ON e.id = p.event_id "
        "WHERE p.user_id=%s AND p.checked_out_at IS NOT NULL",
        (uid,),
    )["c"]
    return {
        "id": uid,
        "display_name": row["display_name"],
        "bio": row["bio"],
        "created_at": row["created_at"],
        "hours_volunteered": round(int(minutes) / 60, 1),
        "tokens_earned": int(earned),
        "projects_joined": int(joined),
    }


def cover_image_id(entity: str, entity_id: int) -> int | None:
    """The entity's cover: its primary image, else the first by id."""
    r = db.query_one(
        "SELECT id FROM images WHERE entity=%s AND entity_id=%s "
        "ORDER BY is_primary DESC, id ASC LIMIT 1",
        (entity, entity_id),
    )
    return r["id"] if r else None


def follower_count(project_id: int) -> int:
    return int(
        db.query_one(
            "SELECT COUNT(*) AS c FROM follows WHERE project_id=%s", (project_id,)
        )["c"]
    )


# ---- events (occurrences) ---------------------------------------------------

def event_is_over(event: dict) -> bool:
    """Per-EVENT: completed OR now() past starts_at + expected_minutes.

    Uses the ``is_over`` column when the row was selected with it; otherwise
    computes it with one query.
    """
    if "is_over" in event and event["is_over"] is not None:
        return bool(event["is_over"])
    r = db.query_one(
        "SELECT (%s = 'completed' OR now() > (%s::timestamptz + "
        "make_interval(mins => %s))) AS over",
        (event["status"], event["starts_at"], event["expected_minutes"]),
    )
    return bool(r["over"])


def event_state_maps(event_ids: list[int], user_id: int) -> dict[int, dict]:
    """Batch per-event/per-user action state for a set of event ids (no N+1).

    Returns {event_id: {checked_in_count, my_rsvp, my_open_participation,
    my_hours_here}} -- exactly the fields event_card overlays onto an event row.
    """
    if not event_ids:
        return {}
    count_map = {
        r["event_id"]: r["c"]
        for r in db.query(
            "SELECT event_id, COUNT(*) AS c FROM participations "
            "WHERE event_id = ANY(%s) AND checked_out_at IS NULL GROUP BY event_id",
            (event_ids,),
        )
    }
    rsvp_map = {
        r["event_id"]: {"is_leader": r["is_leader"]}
        for r in db.query(
            "SELECT event_id, is_leader FROM rsvps "
            "WHERE user_id=%s AND event_id = ANY(%s)",
            (user_id, event_ids),
        )
    }
    open_map = {
        r["event_id"]: {
            "id": r["id"],
            "checked_in_at": r["checked_in_at"],
            # false = asserted ("I say I was here"), true = a peer scan
            # corroborated it (CHECKIN_PROOF.md §1).
            "attested": bool(r["attested"]),
        }
        for r in db.query(
            "SELECT DISTINCT ON (event_id) event_id, id, checked_in_at, attested "
            "FROM participations "
            "WHERE user_id=%s AND checked_out_at IS NULL AND event_id = ANY(%s) "
            "ORDER BY event_id",
            (user_id, event_ids),
        )
    }
    hours_map = {
        r["event_id"]: r["m"]
        for r in db.query(
            "SELECT event_id, COALESCE(SUM(minutes),0) AS m FROM participations "
            "WHERE user_id=%s AND checked_out_at IS NOT NULL AND event_id = ANY(%s) "
            "GROUP BY event_id",
            (user_id, event_ids),
        )
    }
    return {
        eid: {
            "checked_in_count": int(count_map.get(eid, 0)),
            "my_rsvp": rsvp_map.get(eid),
            "my_open_participation": open_map.get(eid),
            "my_hours_here": round(int(hours_map.get(eid, 0)) / 60, 1),
        }
        for eid in event_ids
    }


def event_state(event_id: int, user_id: int) -> dict:
    """Single-event convenience wrapper over event_state_maps."""
    return event_state_maps([event_id], user_id)[event_id]


def event_card(event: dict | None, state: dict) -> dict | None:
    """Per-EVENT read shape embedded in project cards / detail.

    ``event`` is an events row (ideally selected with ``... AS is_over``);
    ``state`` supplies checked_in_count + the my_* fields (from ``event_state`` /
    ``event_state_maps``). Returns None for a null event (a project with none).
    """
    if event is None:
        return None
    st = state
    return {
        "id": event["id"],
        "starts_at": event["starts_at"],
        "location_text": event["location_text"],
        "expected_minutes": event["expected_minutes"],
        "status": event["status"],
        "is_over": event_is_over(event),
        "cover_image_id": cover_image_id("event", event["id"]),
        "checked_in_count": int(st["checked_in_count"]),
        "my_rsvp": st["my_rsvp"],
        "my_open_participation": st["my_open_participation"],
        "my_hours_here": st["my_hours_here"],
    }


def event_detail(event: dict | None, state: dict, am_leader: bool) -> dict | None:
    """event_card plus the leader-only checkin_code -- the per-event detail shape.

    Used inside project detail and returned by POST /projects/{id}/events and the
    event-scoped endpoints.
    """
    card = event_card(event, state)
    if card is not None:
        eid = event["id"]
        card["cover_image_id"] = cover_image_id("event", eid)
        card["image_ids"] = [
            r["id"]
            for r in db.query(
                "SELECT id FROM images WHERE entity='event' AND entity_id=%s "
                "ORDER BY id",
                (eid,),
            )
        ]
        if am_leader:
            card["checkin_code"] = event["checkin_code"]
    return card


def project_card(project: dict, event: dict | None) -> dict:
    """Feed card: the durable project + ONE embedded event_card (or null).

    ``event`` is the already-assembled event_card dict for the relevant occurrence
    (soonest not-over for `upcoming`, most-recent for `past`), or None.
    """
    pid = project["id"]
    return {
        "id": pid,
        "title": project["title"],
        "cover_image_id": cover_image_id("project", pid),
        "follower_count": follower_count(pid),
        "event": event,
    }


def item_card(row: dict) -> dict:
    iid = row["id"]
    return {
        "id": iid,
        "kind": row["kind"],
        "title": row["title"],
        "price_tokens": row["price_tokens"],
        "quantity": row["quantity"],
        "status": row["status"],
        "cover_image_id": cover_image_id("catalog_item", iid),
        "poster": user_brief(row["poster_id"]),
        "created_at": row["created_at"],
    }


# ---- service log (records) --------------------------------------------------

def record_cheer_maps(record_ids: list[int], user_id: int) -> dict[int, dict]:
    """Batch cheer_count + i_cheered for a set of record ids (no N+1, like the
    event feed). Returns {record_id: {cheer_count, i_cheered}}."""
    if not record_ids:
        return {}
    counts = {
        r["record_id"]: r["c"]
        for r in db.query(
            "SELECT record_id, COUNT(*) AS c FROM cheers "
            "WHERE record_id = ANY(%s) GROUP BY record_id",
            (record_ids,),
        )
    }
    mine = {
        r["record_id"]
        for r in db.query(
            "SELECT record_id FROM cheers WHERE user_id = %s AND record_id = ANY(%s)",
            (user_id, record_ids),
        )
    }
    return {
        rid: {"cheer_count": int(counts.get(rid, 0)), "i_cheered": rid in mine}
        for rid in record_ids
    }


def record_photo_maps(record_ids: list[int]) -> dict[int, int | None]:
    """Batch each record's photo (its cover image id) so the feed has no N+1.

    A record has exactly one image; this still applies the cover rule (primary
    else first by id) for consistency with cover_image_id.
    """
    if not record_ids:
        return {}
    rows = db.query(
        "SELECT DISTINCT ON (entity_id) entity_id, id FROM images "
        "WHERE entity = 'service_record' AND entity_id = ANY(%s) "
        "ORDER BY entity_id, is_primary DESC, id ASC",
        (record_ids,),
    )
    return {r["entity_id"]: r["id"] for r in rows}


def record_card(record: dict, author: dict, cheer: dict, photo_image_id: int | None) -> dict:
    """The feed/detail read shape for a service record.

    ``author`` must carry ``email`` (to derive ``is_guest``) -- the email itself
    is NEVER exposed. ``cheer`` is one entry from record_cheer_maps.
    """
    return {
        "id": record["id"],
        "author": {
            "id": author["id"],
            "display_name": author["display_name"],
            "is_guest": author["email"] is None,
        },
        "caption": record["caption"],
        "photo_image_id": photo_image_id,
        "created_at": record["created_at"],
        "cheer_count": int(cheer["cheer_count"]),
        "i_cheered": bool(cheer["i_cheered"]),
    }
