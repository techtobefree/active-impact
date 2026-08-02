"""Invites: "come to this", addressed to one person (SOCIAL.md §5b).

The Invite button, on a project page and on an event page. You may invite exactly
the people in your follow graph -- those you follow, plus those who follow you
(S12). That is both what was asked for and the anti-abuse boundary: an endpoint
that took arbitrary user ids would be a notification-blast weapon.

An invite is DIRECTED, so it is deliberately not public activity (S13): it
creates no ``activities`` row and appears in nobody's feed. It reaches the
invitee's notifications, and their phone if they have turned it on.

**Two scopes.** From a project page it means "come to this project"; from an
event page it means "come on Saturday". Different messages, so different
invitations -- and the notification links to whichever one was meant.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import db, push, social
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


def _event(event_id: int) -> dict:
    """The event plus its project's title -- everything a notification needs."""
    row = db.query_one(
        "SELECT e.id, e.project_id, e.starts_at, p.title "
        "FROM events e JOIN projects p ON p.id = e.project_id WHERE e.id = %s",
        (event_id,),
    )
    if not row:
        raise api_error(404, "not_found")
    return row


def _graph(user_id: int, only: list[int] | None = None, page: Page | None = None) -> list[dict]:
    sql, params = _GRAPH, [user_id, user_id, user_id, user_id]
    if only is not None:
        sql += " AND u.id = ANY(%s)"
        params.append(list(set(only)))
    if page is not None:
        sql += " ORDER BY lower(u.display_name) ASC, u.id ASC LIMIT %s OFFSET %s"
        params += [page.limit, page.offset]
    return db.query(sql, params)


def _invitable(user: dict, page: Page, where: str, params: tuple) -> list[dict]:
    """The picker's list: my graph, each flagged with whether I already invited
    them TO THIS THING (a project invite does not mark an event invite as done)."""
    rows = _graph(user["id"], page=page)
    already = {
        r["invitee_id"]
        for r in db.query(
            f"SELECT invitee_id FROM invites WHERE inviter_id = %s AND {where}",
            (user["id"],) + params,
        )
    }
    cards = social.person_cards(rows, user["id"])
    for c in cards:
        c["invited"] = c["id"] in already
    return cards


def _create(user: dict, invitee_ids: list[int], project_id: int, event_id: int | None) -> list[int]:
    """Insert the new invitations, returning who is actually newly invited.

    Anyone outside the graph, anyone who blocked me, and anyone already invited to
    this same thing is silently skipped -- a stale picker must never make the
    button fail at somebody.
    """
    if not invitee_ids:
        return []
    allowed = [r["id"] for r in _graph(user["id"], only=invitee_ids)]
    if not allowed:
        return []
    with db.tx() as c:
        rows = c.execute(
            "INSERT INTO invites(project_id, event_id, inviter_id, invitee_id) "
            "SELECT %s, %s, %s, x FROM unnest(%s::int[]) AS x "
            "ON CONFLICT DO NOTHING RETURNING invitee_id",
            (project_id, event_id, user["id"], allowed),
        ).fetchall()
    return [r["invitee_id"] for r in rows]


# ---- project-scoped ---------------------------------------------------------

@router.get("/projects/{project_id}/invitable")
def project_invitable(
    project_id: int, page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """Who I can invite to this project, and who I already have."""
    _project(project_id)
    return _invitable(user, page, "project_id = %s AND event_id IS NULL", (project_id,))


@router.post("/projects/{project_id}/invite")
def project_invite(project_id: int, body: InviteIn, user: dict = Depends(current_user)):
    project = _project(project_id)
    invited = _create(user, body.user_ids, project_id, None)
    for invitee_id in invited:
        push.send_invite(user, invitee_id, {
            "title": project["title"], "url": f"#/projects/{project_id}",
        })
    return {"invited": len(invited)}


# ---- event-scoped -----------------------------------------------------------

@router.get("/events/{event_id}/invitable")
def event_invitable(
    event_id: int, page: Page = Depends(pagination), user: dict = Depends(current_user)
):
    """Who I can invite to this occurrence. Independent of project invitations:
    somebody already invited to the project can still be asked to come Saturday."""
    _event(event_id)
    return _invitable(user, page, "event_id = %s", (event_id,))


@router.post("/events/{event_id}/invite")
def event_invite(event_id: int, body: InviteIn, user: dict = Depends(current_user)):
    event = _event(event_id)
    invited = _create(user, body.user_ids, event["project_id"], event_id)
    for invitee_id in invited:
        push.send_invite(user, invitee_id, {
            "title": event["title"], "url": f"#/events/{event_id}",
        })
    return {"invited": len(invited)}


# ---- as a notification source (S14) -----------------------------------------

def for_user(user_id: int, limit: int, offset: int) -> list[dict]:
    """Invitations addressed to me, newest first, as notification cards.

    Shaped like an activity_card so one renderer draws both: ``kind`` is
    ``invited``, the actor is whoever invited me, and the target is the EVENT
    when there was one, otherwise the project -- so the card links where the
    invitation actually pointed.
    """
    rows = db.query(
        "SELECT i.id, i.created_at, i.project_id, i.event_id, e.starts_at, "
        "       p.title AS project_title, u.id AS actor_id, u.display_name, u.email "
        "FROM invites i "
        "JOIN projects p ON p.id = i.project_id "
        "LEFT JOIN events e ON e.id = i.event_id "
        "JOIN users u ON u.id = i.inviter_id "
        "WHERE i.invitee_id = %s "
        "ORDER BY i.created_at DESC, i.id DESC LIMIT %s OFFSET %s",
        (user_id, limit, offset),
    )
    out = []
    for r in rows:
        card = {
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
            "project": None,
        }
        if r["event_id"] is not None:
            card["event"] = {
                "id": r["event_id"],
                "project_id": r["project_id"],
                "project_title": r["project_title"],
                "starts_at": r["starts_at"],
            }
        else:
            card["project"] = {"id": r["project_id"], "title": r["project_title"]}
        out.append(card)
    return out


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
