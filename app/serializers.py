"""Canonical read shapes, shared so every module emits identical JSON.

See docs/design/DOMAIN.md § Standard read shapes. These run supplementary
queries via the read pool; callers pass an already-fetched primary row.
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
    """Private self view -- the ONLY shape that carries the email (and balance)."""
    return {k: row[k] for k in ("id", "email", "display_name", "bio", "balance", "created_at")}


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
    joined = db.query_one(
        "SELECT COUNT(DISTINCT project_id) AS c FROM participations "
        "WHERE user_id=%s AND checked_out_at IS NOT NULL",
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
    r = db.query_one(
        "SELECT id FROM images WHERE entity=%s AND entity_id=%s ORDER BY id LIMIT 1",
        (entity, entity_id),
    )
    return r["id"] if r else None


def project_card(row: dict) -> dict:
    pid = row["id"]
    cnt = db.query_one(
        "SELECT COUNT(*) AS c FROM participations "
        "WHERE project_id=%s AND checked_out_at IS NULL",
        (pid,),
    )["c"]
    return {
        "id": pid,
        "title": row["title"],
        "location_text": row["location_text"],
        "starts_at": row["starts_at"],
        "expected_minutes": row["expected_minutes"],
        "status": row["status"],
        "cover_image_id": cover_image_id("project", pid),
        "checked_in_count": int(cnt),
        "owner": user_brief(row["owner_id"]),
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
