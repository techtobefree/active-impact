"""decouple events from service projects

Re-architects a service project into a durable umbrella with MANY events
(occurrences). The event-specific columns move off ``projects`` onto a new
``events`` domain table; ``participations`` and ``rsvps`` repoint from
``project_id`` to ``event_id``; the append-only check-in/out log (old ``events``
table) is renamed to ``audit_log`` and gains an ``event_id`` alongside its
``project_id``.

GUARDED like 0002-0005: migration 0001 is a ``create_all`` baseline from the
CURRENT (already-new-shape) models, so a FRESH database is created directly in the
new shape. We detect that by the absence of ``projects.starts_at`` and NO-OP.
Only an OLD-shape database (one that reached 0005 before this change) still has
``projects.starts_at`` and gets the real transform below, which builds a 1:1
project<->event map from the existing per-project event columns.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _columns(table: str) -> list[str]:
    return [c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)]


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    # Fresh (create_all) DBs are already the new shape -> nothing to transform.
    if "starts_at" not in _columns("projects"):
        return

    # 1. Rename the append-only audit log: events -> audit_log (+ its indexes/pk).
    if _has_table("events") and not _has_table("audit_log"):
        op.execute("ALTER TABLE events RENAME CONSTRAINT pk_events TO pk_audit_log")
        op.execute("ALTER INDEX idx_events_type RENAME TO idx_audit_log_type")
        op.execute("ALTER INDEX idx_events_project RENAME TO idx_audit_log_project")
        op.execute("ALTER INDEX idx_events_subject RENAME TO idx_audit_log_subject")
        op.rename_table("events", "audit_log")

    # 2. Create the new events DOMAIN table (one occurrence of a project).
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("expected_minutes", sa.Integer(), nullable=False),
        sa.Column("location_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("checkin_code", sa.Text(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        # Bare tokens: naming_convention prefixes check names with "ck_events_".
        sa.CheckConstraint("expected_minutes > 0", name="expected_minutes_pos"),
        sa.CheckConstraint("status IN ('open', 'completed')", name="status_valid"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_events_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.UniqueConstraint("checkin_code", name="uq_events_checkin_code"),
    )
    op.create_index("idx_events_project", "events", ["project_id"])
    op.create_index("idx_events_starts", "events", ["starts_at"])

    # 3. One event per existing project (1:1 map at migration time).
    op.execute(
        "INSERT INTO events(project_id, starts_at, expected_minutes, location_text, "
        "checkin_code, status, created_at, updated_at) "
        "SELECT id, starts_at, expected_minutes, location_text, checkin_code, status, "
        "created_at, now() FROM projects"
    )

    # 4. participations: project_id -> event_id.
    op.add_column("participations", sa.Column("event_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE participations p SET event_id = e.id FROM events e WHERE e.project_id = p.project_id")
    op.alter_column("participations", "event_id", nullable=False)
    op.create_foreign_key("fk_participations_event_id_events", "participations", "events", ["event_id"], ["id"], ondelete="CASCADE")
    op.drop_index("idx_participations_open", table_name="participations")  # (project_id, user_id)
    op.create_index(
        "idx_participations_open", "participations", ["event_id", "user_id"],
        unique=True, postgresql_where=sa.text("checked_out_at IS NULL"),
    )
    op.create_index("idx_participations_event", "participations", ["event_id"])
    op.drop_index("idx_participations_project", table_name="participations")
    op.drop_constraint("fk_participations_project_id_projects", "participations", type_="foreignkey")
    op.drop_column("participations", "project_id")

    # 5. rsvps: project_id -> event_id.
    op.add_column("rsvps", sa.Column("event_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE rsvps r SET event_id = e.id FROM events e WHERE e.project_id = r.project_id")
    op.alter_column("rsvps", "event_id", nullable=False)
    op.create_foreign_key("fk_rsvps_event_id_events", "rsvps", "events", ["event_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("event_user", "rsvps", ["event_id", "user_id"])
    op.drop_constraint("project_user", "rsvps", type_="unique")
    op.drop_constraint("fk_rsvps_project_id_projects", "rsvps", type_="foreignkey")
    op.drop_column("rsvps", "project_id")

    # 6. audit_log gains event_id (keep project_id for project-level reporting).
    op.add_column("audit_log", sa.Column("event_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE audit_log a SET event_id = e.id FROM events e WHERE e.project_id = a.project_id")
    op.create_foreign_key("fk_audit_log_event_id_events", "audit_log", "events", ["event_id"], ["id"], ondelete="SET NULL")

    # 7. Drop the moved columns off projects.
    op.drop_constraint("uq_projects_checkin_code", "projects", type_="unique")
    op.drop_index("idx_projects_starts", table_name="projects")
    # Bare tokens: the Base naming_convention prefixes check constraints with
    # "ck_projects_" -> the real names ck_projects_expected_minutes_pos / _status_valid.
    op.drop_constraint("expected_minutes_pos", "projects", type_="check")
    op.drop_constraint("status_valid", "projects", type_="check")
    op.drop_column("projects", "checkin_code")
    op.drop_column("projects", "starts_at")
    op.drop_column("projects", "expected_minutes")
    op.drop_column("projects", "location_text")
    op.drop_column("projects", "status")


def downgrade() -> None:
    # Already old shape -> nothing to reverse.
    if "starts_at" in _columns("projects"):
        return

    # D4. participations: event_id -> project_id (read events before it is dropped).
    op.add_column("participations", sa.Column("project_id", sa.Integer(), nullable=True))
    op.execute("UPDATE participations p SET project_id = e.project_id FROM events e WHERE e.id = p.event_id")
    op.alter_column("participations", "project_id", nullable=False)
    op.create_foreign_key("fk_participations_project_id_projects", "participations", "projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_index("idx_participations_project", "participations", ["project_id"])
    op.drop_index("idx_participations_open", table_name="participations")  # (event_id, user_id)
    op.create_index(
        "idx_participations_open", "participations", ["project_id", "user_id"],
        unique=True, postgresql_where=sa.text("checked_out_at IS NULL"),
    )
    op.drop_index("idx_participations_event", table_name="participations")
    op.drop_constraint("fk_participations_event_id_events", "participations", type_="foreignkey")
    op.drop_column("participations", "event_id")

    # D5. rsvps: event_id -> project_id.
    op.add_column("rsvps", sa.Column("project_id", sa.Integer(), nullable=True))
    op.execute("UPDATE rsvps r SET project_id = e.project_id FROM events e WHERE e.id = r.event_id")
    op.alter_column("rsvps", "project_id", nullable=False)
    op.create_foreign_key("fk_rsvps_project_id_projects", "rsvps", "projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("project_user", "rsvps", ["project_id", "user_id"])
    op.drop_constraint("event_user", "rsvps", type_="unique")
    op.drop_constraint("fk_rsvps_event_id_events", "rsvps", type_="foreignkey")
    op.drop_column("rsvps", "event_id")

    # D6. audit_log: drop event_id (project_id stays).
    op.drop_constraint("fk_audit_log_event_id_events", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "event_id")

    # D7. Re-add the moved columns onto projects from the 1:1 event (lowest id).
    op.add_column("projects", sa.Column("starts_at", TS, nullable=True))
    op.add_column("projects", sa.Column("expected_minutes", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("location_text", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=True))
    op.add_column("projects", sa.Column("checkin_code", sa.Text(), nullable=True))
    op.execute(
        "UPDATE projects p SET starts_at = e.starts_at, expected_minutes = e.expected_minutes, "
        "location_text = e.location_text, status = e.status, checkin_code = e.checkin_code "
        "FROM (SELECT DISTINCT ON (project_id) project_id, starts_at, expected_minutes, "
        "location_text, status, checkin_code FROM events ORDER BY project_id, id) e "
        "WHERE e.project_id = p.id"
    )
    # Projects with no events at all get safe placeholders so NOT NULL can hold.
    op.execute(
        "UPDATE projects SET starts_at = COALESCE(starts_at, now()), "
        "expected_minutes = COALESCE(expected_minutes, 60), "
        "location_text = COALESCE(location_text, ''), "
        "status = COALESCE(status, 'open'), "
        "checkin_code = COALESCE(checkin_code, 'legacy-' || id::text) "
        "WHERE starts_at IS NULL OR expected_minutes IS NULL OR location_text IS NULL "
        "OR status IS NULL OR checkin_code IS NULL"
    )
    op.alter_column("projects", "starts_at", nullable=False)
    op.alter_column("projects", "expected_minutes", nullable=False)
    op.alter_column("projects", "location_text", nullable=False)
    op.alter_column("projects", "status", nullable=False)
    op.alter_column("projects", "checkin_code", nullable=False)
    op.create_unique_constraint("uq_projects_checkin_code", "projects", ["checkin_code"])
    op.create_index("idx_projects_starts", "projects", ["starts_at"])
    # Bare tokens: naming_convention prefixes these with "ck_projects_".
    op.create_check_constraint("expected_minutes_pos", "projects", "expected_minutes > 0")
    op.create_check_constraint("status_valid", "projects", "status IN ('open', 'completed')")

    # D3. Drop the events DOMAIN table (all backfills that read it are done).
    op.drop_table("events")

    # D1. Rename the audit log back: audit_log -> events (+ its indexes/pk).
    op.execute("ALTER TABLE audit_log RENAME CONSTRAINT pk_audit_log TO pk_events")
    op.execute("ALTER INDEX idx_audit_log_type RENAME TO idx_events_type")
    op.execute("ALTER INDEX idx_audit_log_project RENAME TO idx_events_project")
    op.execute("ALTER INDEX idx_audit_log_subject RENAME TO idx_events_subject")
    op.rename_table("audit_log", "events")
