"""Social: following people, their activity, blocking, notifications (SOCIAL.md).

Person -> person. Following decides whose activity reaches my feed and my
notifications and nothing else; blocking is a one-way visibility mute that
deliberately LEAVES the follow in place (S4), because the founder asked for a
blocked person to stay a follower and be unblockable.

Every activity read here goes through ``app/activity.py``, which owns the single
visibility rule. Notifications are derived from a watermark, so there is no
fan-out table and the badge cannot disagree with the list it opens.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app import activity, db
from app.auth import current_user
from app.deps import Page, api_error, pagination

router = APIRouter()


# ---- helpers ----------------------------------------------------------------

def _require_user(user_id: int) -> dict:
    row = db.query_one("SELECT id FROM users WHERE id = %s", (user_id,))
    if not row:
        raise api_error(404, "not_found")
    return row


def follower_count(user_id: int) -> int:
    return int(db.query_one(
        "SELECT COUNT(*) AS c FROM user_follows WHERE followee_id = %s", (user_id,)
    )["c"])


def following_count(user_id: int) -> int:
    return int(db.query_one(
        "SELECT COUNT(*) AS c FROM user_follows WHERE follower_id = %s", (user_id,)
    )["c"])


def is_following(follower_id: int, followee_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM user_follows WHERE follower_id = %s AND followee_id = %s",
        (follower_id, followee_id),
    ) is not None


def is_blocked(blocker_id: int, blocked_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM blocks WHERE blocker_id = %s AND blocked_id = %s",
        (blocker_id, blocked_id),
    ) is not None


def person_cards(rows: list[dict], viewer_id: int) -> list[dict]:
    """person_card[] -- identity plus MY relationship to each of them, batched.

    ``is_following`` = do I follow them; ``is_blocked`` = have I blocked them
    (the flag the followers list needs to render Block vs Unblock). Never an email.
    """
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    followed = {
        f["followee_id"] for f in db.query(
            "SELECT followee_id FROM user_follows WHERE follower_id = %s AND followee_id = ANY(%s)",
            (viewer_id, ids),
        )
    }
    blocked = {
        b["blocked_id"] for b in db.query(
            "SELECT blocked_id FROM blocks WHERE blocker_id = %s AND blocked_id = ANY(%s)",
            (viewer_id, ids),
        )
    }
    return [
        {
            "id": r["id"],
            "display_name": r["display_name"],
            "is_guest": r["email"] is None,
            "is_following": r["id"] in followed,
            "is_blocked": r["id"] in blocked,
        }
        for r in rows
    ]


# ---- follow -----------------------------------------------------------------

@router.post("/users/{user_id}/follow")
def follow(user_id: int, user: dict = Depends(current_user)):
    """Follow a person. Idempotent, like every other follow in the app."""
    _require_user(user_id)
    if user_id == user["id"]:
        raise api_error(409, "cannot_follow_self")
    with db.tx() as c:
        c.execute(
            "INSERT INTO user_follows(follower_id, followee_id) VALUES (%s, %s) "
            "ON CONFLICT (follower_id, followee_id) DO NOTHING",
            (user["id"], user_id),
        )
    return {"is_following": True, "follower_count": follower_count(user_id)}


@router.delete("/users/{user_id}/follow")
def unfollow(user_id: int, user: dict = Depends(current_user)):
    _require_user(user_id)
    with db.tx() as c:
        c.execute(
            "DELETE FROM user_follows WHERE follower_id = %s AND followee_id = %s",
            (user["id"], user_id),
        )
    return {"is_following": False, "follower_count": follower_count(user_id)}


@router.get("/users/{user_id}/followers")
def followers(
    user_id: int, page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """Who follows this person, newest first. On my own list every row carries
    ``is_blocked`` -- this is the screen the Block control lives on."""
    _require_user(user_id)
    rows = db.query(
        "SELECT u.id, u.display_name, u.email FROM user_follows f "
        "JOIN users u ON u.id = f.follower_id WHERE f.followee_id = %s "
        "ORDER BY f.created_at DESC, f.id DESC LIMIT %s OFFSET %s",
        (user_id, page.limit, page.offset),
    )
    return person_cards(rows, user["id"])


@router.get("/users/{user_id}/following")
def following(
    user_id: int, page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    _require_user(user_id)
    rows = db.query(
        "SELECT u.id, u.display_name, u.email FROM user_follows f "
        "JOIN users u ON u.id = f.followee_id WHERE f.follower_id = %s "
        "ORDER BY f.created_at DESC, f.id DESC LIMIT %s OFFSET %s",
        (user_id, page.limit, page.offset),
    )
    return person_cards(rows, user["id"])


# ---- block ------------------------------------------------------------------

@router.post("/users/{user_id}/block")
def block(user_id: int, user: dict = Depends(current_user)):
    """Stop this person seeing what I do. Their follow is deliberately untouched
    (S4/S-I3): the founder's ask was that they remain a follower."""
    _require_user(user_id)
    if user_id == user["id"]:
        raise api_error(409, "cannot_block_self")
    with db.tx() as c:
        c.execute(
            "INSERT INTO blocks(blocker_id, blocked_id) VALUES (%s, %s) "
            "ON CONFLICT (blocker_id, blocked_id) DO NOTHING",
            (user["id"], user_id),
        )
    return {"is_blocked": True}


@router.delete("/users/{user_id}/block")
def unblock(user_id: int, user: dict = Depends(current_user)):
    """Let them see my activity again -- everything comes back exactly (S-I4),
    because blocking only ever filtered reads; nothing was deleted."""
    _require_user(user_id)
    with db.tx() as c:
        c.execute(
            "DELETE FROM blocks WHERE blocker_id = %s AND blocked_id = %s",
            (user["id"], user_id),
        )
    return {"is_blocked": False}


# ---- activity ---------------------------------------------------------------

@router.get("/users/{user_id}/activity")
def user_activity(
    user_id: int, page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """One person's activity -- their page IS this list. A viewer they blocked
    gets an empty stream: no error, no banner, simply nothing to see."""
    _require_user(user_id)
    rows = activity.by_user(user_id, user["id"], page.limit, page.offset)
    return activity.cards(rows, user["id"])


@router.get("/feed/following")
def following_feed(
    page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """The Following tab: what the people I follow have done. Never my own."""
    rows = activity.following(user["id"], page.limit, page.offset)
    return activity.cards(rows, user["id"])


# ---- notifications ----------------------------------------------------------

@router.get("/notifications")
def notifications(
    page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """Notifiable activity from my followees + how much of it is new.

    ``unread`` is derived from my watermark (S6), so it always agrees with the
    list below it -- there is nothing to keep in sync because there is no second
    copy of anything.
    """
    rows = activity.following(user["id"], page.limit, page.offset, kinds=activity.NOTIFIABLE)
    return {
        "unread": activity.unread_count(user),
        "items": activity.cards(rows, user["id"]),
    }


@router.post("/notifications/seen")
def mark_seen(user: dict = Depends(current_user)):
    """Everything up to now is read. The items stay readable -- seen is a
    watermark, not a delete."""
    with db.tx() as c:
        c.execute(
            "UPDATE users SET notifications_seen_at = now() WHERE id = %s", (user["id"],)
        )
    return {"unread": 0}
