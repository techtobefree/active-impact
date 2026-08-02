"""Invites: "come to this", addressed to one person (SOCIAL.md §5b).

The project page's Invite button. You may invite exactly the people in your
follow graph -- those you follow, plus those who follow you (S12). That is both
what was asked for and the anti-abuse boundary: an endpoint that took arbitrary
user ids would be a notification-blast weapon.

An invite is DIRECTED, so it is deliberately not public activity (S13): it
creates no ``activities`` row and appears in nobody's feed. It reaches the
invitee's notifications, and their phone if they have turned it on.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import db, push, serializers, social
from app.auth import current_user
from app.deps import Page, api_error, pagination

router = APIRouter()

# Everyone in my follow graph, in either direction, minus anyone who has blocked
# me (S15: a block means "stop reaching me", and an invite is reaching them).
# (No DISTINCT needed, and it would fight the ORDER BY: there is no join here,
# so following each other still yields exactly one row for that person.)
_GRAPH = """
    SELECT u.id, u.display_name, u.email
    FROM users u
    WHERE u.id <> %s
      AND (u.id IN (SELECT followee_id FROM user_follows WHERE follower_id = %s)
        OR u.id IN (SELECT follower_id FROM user_follows WHERE followee_id = %s))
      AND NOT EXISTS (SELECT 1 FROM blocks b
                      WHERE b.blocker_id = u.id AND b.blocked_id = %s)
"""


class InviteIn(BaseModel):
    user_ids: list[int]


def _project(project_id: int) -> dict:
    row = db.query_one("SELECT id, title FROM projects WHERE id = %s", (project_id,))
    if not row:
        raise api_error(404, "not_found")
    return row


@router.get("/projects/{project_id}/invitable")
def invitable(
    project_id: int, page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """Who I can invite here, each flagged with whether I already have.

    Both directions of the graph, because "people I follow" and "people who
    follow me" are both people I know -- and either is somebody I might want at a
    service project.
    """
    _project(project_id)
    rows = db.query(
        _GRAPH + " ORDER BY lower(u.display_name) ASC, u.id ASC LIMIT %s OFFSET %s",
        (user["id"], user["id"], user["id"], user["id"], page.limit, page.offset),
    )
    already = {
        r["invitee_id"]
        for r in db.query(
            "SELECT invitee_id FROM invites WHERE project_id = %s AND inviter_id = %s",
            (project_id, user["id"]),
        )
    }
    cards = social.person_cards(rows, user["id"])
    for c in cards:
        c["invited"] = c["id"] in already
    return cards


@router.post("/projects/{project_id}/invite")
def invite(project_id: int, body: InviteIn, user: dict = Depends(current_user)):
    """Invite people from my graph. Returns how many invitations were actually new.

    Anyone outside the graph, anyone who blocked me, and anyone I already invited
    is silently skipped rather than erroring: a stale picker must never make the
    button fail at somebody.
    """
    project = _project(project_id)
    if not body.user_ids:
        return {"invited": 0}

    allowed = {
        r["id"]
        for r in db.query(
            _GRAPH + " AND u.id = ANY(%s)",
            (user["id"], user["id"], user["id"], user["id"], list(set(body.user_ids))),
        )
    }
    if not allowed:
        return {"invited": 0}

    with db.tx() as c:
        rows = c.execute(
            "INSERT INTO invites(project_id, inviter_id, invitee_id) "
            "SELECT %s, %s, x FROM unnest(%s::int[]) AS x "
            "ON CONFLICT (project_id, inviter_id, invitee_id) DO NOTHING "
            "RETURNING invitee_id",
            (project_id, user["id"], list(allowed)),
        ).fetchall()

    # Only the NEW ones are news (S16) -- and the buzz goes out after the commit,
    # like every other push.
    for r in rows:
        push.send_invite(user, r["invitee_id"], project)
    return {"invited": len(rows)}


# ---- as a notification source (S14) -----------------------------------------

def for_user(user_id: int, limit: int, offset: int) -> list[dict]:
    """Invitations addressed to me, newest first, as notification cards.

    Shaped like an activity_card so one renderer draws both -- ``kind`` is
    ``invited``, the actor is whoever invited me, and the target is the project.
    """
    rows = db.query(
        "SELECT i.id, i.created_at, i.project_id, p.title AS project_title, "
        "       u.id AS actor_id, u.display_name, u.email "
        "FROM invites i "
        "JOIN projects p ON p.id = i.project_id "
        "JOIN users u ON u.id = i.inviter_id "
        "WHERE i.invitee_id = %s "
        "ORDER BY i.created_at DESC, i.id DESC LIMIT %s OFFSET %s",
        (user_id, limit, offset),
    )
    return [
        {
            "id": f"invite-{r['id']}",
            "kind": "invited",
            "actor": {
                "id": r["actor_id"],
                "display_name": r["display_name"],
                "is_guest": r["email"] is None,
            },
            "created_at": r["created_at"],
            "event": None,
            "record": None,
            "project": {"id": r["project_id"], "title": r["project_title"]},
        }
        for r in rows
    ]


def unread_count(user: dict) -> int:
    """Invitations since I last opened the bell. Same watermark as activity, so
    the badge stays one number over two sources."""
    if not user.get("notify_activity", True):
        return 0
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM invites WHERE invitee_id = %s "
        "AND (%s::timestamptz IS NULL OR created_at > %s::timestamptz)",
        (user["id"], user["notifications_seen_at"], user["notifications_seen_at"]),
    )
    return int(row["c"])
