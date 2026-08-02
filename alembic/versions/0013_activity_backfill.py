"""organizing is activity, and back-fill everything that already happened

Two changes, both in service of one bug: a project's organizer — the very person
you tap on from a project page — had an empty activity stream.

(a) Widen ``activities.kind`` to include ``created_project`` and
    ``scheduled_event``. Starting a project is the most visible thing an organizer
    does, and it was not being recorded at all.

(b) BACK-FILL from what is already in the database. 0012 deliberately did not,
    on the reasoning that stale items would top everybody's feed — that was
    wrong: rows are back-filled with their ORIGINAL timestamps, so they sort into
    the past exactly where they belong. The real risk was the notification badge,
    and that is handled by stamping every existing user's watermark to now().

Idempotent: every insert is guarded by NOT EXISTS on the same (user, kind,
subject), so re-running adds nothing and this migration is safe alongside live
activity written since 0012.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KINDS = "('logged', 'rsvp', 'checked_in', 'created_project', 'scheduled_event')"


def upgrade() -> None:
    # (a) the two organizer kinds.
    #
    # Both names are dropped on purpose. 0012 passed already-prefixed constraint
    # names to op.create_table, and the metadata naming convention prefixed them
    # AGAIN -- so a MIGRATED database ended up with
    # ck_activities_ck_activities_kind_valid while a FRESH create_all database
    # (models.py names it "kind_valid") has ck_activities_kind_valid. Dropping
    # both and adding one converges the two paths, which matters the next time
    # anyone runs autogenerate against either.
    op.execute("ALTER TABLE activities DROP CONSTRAINT IF EXISTS ck_activities_ck_activities_kind_valid")
    op.execute("ALTER TABLE activities DROP CONSTRAINT IF EXISTS ck_activities_kind_valid")
    op.execute(
        f"ALTER TABLE activities ADD CONSTRAINT ck_activities_kind_valid CHECK (kind IN {KINDS})"
    )
    # The same double-prefixing hit the other two 0012 tables. Same fix, same
    # reason -- the constraints behave identically either way, but the NAMES must
    # match the models or every future diff shows phantom changes.
    for tbl, name in (("user_follows", "not_self"), ("blocks", "not_self")):
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS ck_{tbl}_ck_{tbl}_{name}")
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS ck_{tbl}_{name}")
    op.execute("ALTER TABLE user_follows ADD CONSTRAINT ck_user_follows_not_self "
               "CHECK (follower_id <> followee_id)")
    op.execute("ALTER TABLE blocks ADD CONSTRAINT ck_blocks_not_self "
               "CHECK (blocker_id <> blocked_id)")

    bind = op.get_bind()

    # (b) back-fill, oldest facts first. Each carries the timestamp of the thing
    #     that actually happened, so the history sorts itself.

    # Starting a project — paired with its FIRST event, the one it announced.
    bind.execute(sa.text("""
        INSERT INTO activities (user_id, kind, event_id, project_id, created_at)
        SELECT p.owner_id, 'created_project', first_ev.id, p.id, p.created_at
        FROM projects p
        LEFT JOIN LATERAL (
            SELECT e.id FROM events e WHERE e.project_id = p.id ORDER BY e.id LIMIT 1
        ) first_ev ON true
        WHERE NOT EXISTS (
            SELECT 1 FROM activities a
            WHERE a.user_id = p.owner_id AND a.kind = 'created_project' AND a.project_id = p.id
        )
    """))

    # Later events only: the first one was announced by the project itself.
    bind.execute(sa.text("""
        INSERT INTO activities (user_id, kind, event_id, project_id, created_at)
        SELECT p.owner_id, 'scheduled_event', e.id, e.project_id, e.created_at
        FROM events e
        JOIN projects p ON p.id = e.project_id
        WHERE e.id <> (SELECT MIN(e2.id) FROM events e2 WHERE e2.project_id = p.id)
          AND NOT EXISTS (
            SELECT 1 FROM activities a
            WHERE a.kind = 'scheduled_event' AND a.event_id = e.id
        )
    """))

    # Photos.
    bind.execute(sa.text("""
        INSERT INTO activities (user_id, kind, event_id, project_id, record_id, created_at)
        SELECT s.user_id, 'logged', s.event_id, e.project_id, s.id, s.created_at
        FROM service_records s
        LEFT JOIN events e ON e.id = s.event_id
        WHERE NOT EXISTS (
            SELECT 1 FROM activities a WHERE a.kind = 'logged' AND a.record_id = s.id
        )
    """))

    # Check-ins: one per participation, at the moment they arrived.
    bind.execute(sa.text("""
        INSERT INTO activities (user_id, kind, event_id, project_id, created_at)
        SELECT pa.user_id, 'checked_in', pa.event_id, e.project_id, pa.checked_in_at
        FROM participations pa
        JOIN events e ON e.id = pa.event_id
        WHERE NOT EXISTS (
            SELECT 1 FROM activities a
            WHERE a.kind = 'checked_in' AND a.user_id = pa.user_id
              AND a.event_id = pa.event_id AND a.created_at = pa.checked_in_at
        )
    """))

    # RSVPs — but ONLY where the person never checked in. Live, a check-in
    # silently ensures an rsvp row and stays silent about it; the history cannot
    # tell the two apart, so this mirrors that rule rather than double-announcing.
    bind.execute(sa.text("""
        INSERT INTO activities (user_id, kind, event_id, project_id, created_at)
        SELECT r.user_id, 'rsvp', r.event_id, e.project_id, r.created_at
        FROM rsvps r
        JOIN events e ON e.id = r.event_id
        WHERE NOT EXISTS (
            SELECT 1 FROM participations pa
            WHERE pa.event_id = r.event_id AND pa.user_id = r.user_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM activities a
            WHERE a.kind = 'rsvp' AND a.user_id = r.user_id AND a.event_id = r.event_id
        )
    """))

    # Nobody should open the app to a badge counting months of back-filled
    # history. Only users who have never opened the bell (the watermark is the
    # feature's own "I have seen up to here", and 0012 shipped it NULL).
    bind.execute(sa.text(
        "UPDATE users SET notifications_seen_at = now() WHERE notifications_seen_at IS NULL"
    ))


def downgrade() -> None:
    # The narrower CHECK cannot be added while rows violate it, so the organizer
    # kinds go first. (The back-filled rows of the OLD kinds stay: they describe
    # things that really happened, and 0012's shape holds them fine.)
    op.execute("ALTER TABLE activities DROP CONSTRAINT IF EXISTS ck_activities_kind_valid")
    op.execute("DELETE FROM activities WHERE kind IN ('created_project', 'scheduled_event')")
    op.execute(
        "ALTER TABLE activities ADD CONSTRAINT ck_activities_kind_valid "
        "CHECK (kind IN ('logged', 'rsvp', 'checked_in'))"
    )
