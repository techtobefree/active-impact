"""Public activity: what someone did, for the people who follow them (SOCIAL.md).

Three actions are public (S2): ``logged`` a service, ``rsvp``'d to an event, and
``checked_in``. Each writes ONE ``activities`` row inside the same transaction as
the action itself, so a feed item can never exist without its action, nor an
action without its item.

Kept separate from ``app/audit.py`` on purpose (S3): an audit row is a reporting
record that must never be reshaped for display, while an activity row is public
and is deleted with its subject.

**The visibility rule lives here, once** (§3): *someone I have blocked never sees
my activity*. Every read of this table -- a profile, the Following feed, the
notification count -- goes through ``VISIBLE_TO``, because a rule enforced in
three places is a rule that will one day be enforced in two.
"""
from __future__ import annotations

from app import db, serializers

# Kinds worth a notification (S7): somebody turning up somewhere. A logged photo
# is ambient -- it shows in the feed without pinging anyone.
NOTIFIABLE = ("rsvp", "checked_in")

# The one visibility clause. Binds ONE param: the viewer's id. Written as a
# fragment so every caller composes the identical rule.
VISIBLE_TO = (
    "NOT EXISTS (SELECT 1 FROM blocks b "
    "            WHERE b.blocker_id = a.user_id AND b.blocked_id = %s)"
)


def record(c, kind: str, user_id: int, *, event_id=None, project_id=None, record_id=None) -> None:
    """Append one activity row inside the caller's tx. Never updated or deleted.

    The project is derived from the event when the caller has not already got it
    to hand, so no call site has to look it up just to fill in a column.
    """
    c.execute(
        "INSERT INTO activities(user_id, kind, event_id, project_id, record_id) "
        "VALUES (%s, %s, %s, "
        "        COALESCE(%s, (SELECT project_id FROM events WHERE id = %s)), %s)",
        (user_id, kind, event_id, project_id, event_id, record_id),
    )


# ---- reads ------------------------------------------------------------------

def _rows(where: str, params: list, limit: int, offset: int) -> list[dict]:
    return db.query(
        f"SELECT a.* FROM activities a WHERE {where} "
        "ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s",
        params + [limit, offset],
    )


def by_user(user_id: int, viewer_id: int, limit: int, offset: int) -> list[dict]:
    """One person's activity, as seen by ``viewer_id`` (their page).

    A viewer they have blocked sees an empty stream -- not an error, not a
    "you are blocked" banner: it simply looks like there is nothing to see.
    """
    return _rows(f"a.user_id = %s AND {VISIBLE_TO}", [user_id, viewer_id], limit, offset)


def following(
    viewer_id: int, limit: int, offset: int,
    kinds: tuple[str, ...] | None = None, q: str | None = None,
) -> list[dict]:
    """Everything the people I follow have done. Never my own (S-I6).

    ``q`` matches the person or the project they did it at -- the home screen's
    search box stays put on every tab, so it has to mean something on this one.
    Applied AFTER the visibility rule, never instead of it: a filter must not
    become a hole.
    """
    where = (
        "a.user_id IN (SELECT followee_id FROM user_follows WHERE follower_id = %s) "
        f"AND {VISIBLE_TO}"
    )
    params = [viewer_id, viewer_id]
    if kinds:
        where += " AND a.kind = ANY(%s)"
        params.append(list(kinds))
    if q:
        where += (
            " AND (EXISTS (SELECT 1 FROM users au WHERE au.id = a.user_id "
            "              AND au.display_name ILIKE %s)"
            "   OR EXISTS (SELECT 1 FROM projects ap WHERE ap.id = a.project_id "
            "              AND ap.title ILIKE %s))"
        )
        like = f"%{q}%"
        params += [like, like]
    return _rows(where, params, limit, offset)


def unread_count(viewer: dict) -> int:
    """Notifiable activity from my followees since I last opened the bell (S6).

    Derived, never stored: no fan-out table to backfill, and the badge cannot
    disagree with the list it opens. The switch is off ⇒ nothing is unread.
    """
    if not viewer.get("notify_activity", True):
        return 0
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM activities a "
        "WHERE a.user_id IN (SELECT followee_id FROM user_follows WHERE follower_id = %s) "
        f"AND a.kind = ANY(%s) AND {VISIBLE_TO} "
        "AND (%s::timestamptz IS NULL OR a.created_at > %s::timestamptz)",
        (viewer["id"], list(NOTIFIABLE), viewer["id"],
         viewer["notifications_seen_at"], viewer["notifications_seen_at"]),
    )
    return int(row["c"])


def cards(rows: list[dict], viewer_id: int) -> list[dict]:
    """activity_card[] for a set of rows, fully batched (no N+1).

    A ``logged`` activity embeds its whole record_card, so the Following feed
    shows the photo itself rather than a sentence about a photo.
    """
    if not rows:
        return []
    actors = {
        a["id"]: a
        for a in db.query(
            "SELECT id, display_name, email FROM users WHERE id = ANY(%s)",
            (list({r["user_id"] for r in rows}),),
        )
    }
    events = serializers.record_event_maps([r["event_id"] for r in rows])
    record_ids = [r["record_id"] for r in rows if r["record_id"] is not None]
    records = {}
    if record_ids:
        recs = db.query(
            "SELECT * FROM service_records WHERE id = ANY(%s) AND hidden = false",
            (record_ids,),
        )
        records = {r["id"]: c for r, c in zip(recs, serializers.record_cards(recs, viewer_id))}
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "actor": {
                "id": actors[r["user_id"]]["id"],
                "display_name": actors[r["user_id"]]["display_name"],
                "is_guest": actors[r["user_id"]]["email"] is None,
            },
            "created_at": r["created_at"],
            "event": events.get(r["event_id"]),
            "record": records.get(r["record_id"]),
        }
        for r in rows
    ]
