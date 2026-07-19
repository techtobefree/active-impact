"""events audit log

Adds the append-only ``events`` table: one immutable row per check-in /
check-out, written in the SAME tx as the state change so an event can never
exist without its change or vice-versa. Rows are never updated or deleted.

Like 0002/0003, migration 0001 is a create_all baseline from the CURRENT models,
so a FRESH database already has this table when this revision runs. Guard on
existence (information_schema / inspector) so this no-ops on fresh DBs and really
applies on an already-migrated DB.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("events"):
        op.create_table(
            "events",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("type", sa.Text(), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("subject_user_id", sa.Integer(), nullable=True),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("participation_id", sa.Integer(), nullable=True),
            sa.Column("minutes", sa.Integer(), nullable=True),
            sa.Column("tokens", sa.Integer(), nullable=True),
            sa.Column("meta", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_events_actor_user_id_users"),
            sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], name="fk_events_subject_user_id_users"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_events_project_id_projects", ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["participation_id"], ["participations.id"], name="fk_events_participation_id_participations", ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name="pk_events"),
        )
        op.create_index("idx_events_type", "events", ["type", "created_at"])
        op.create_index("idx_events_project", "events", ["project_id"])
        op.create_index("idx_events_subject", "events", ["subject_user_id"])


def downgrade() -> None:
    if _has_table("events"):
        op.drop_index("idx_events_subject", table_name="events")
        op.drop_index("idx_events_project", table_name="events")
        op.drop_index("idx_events_type", table_name="events")
        op.drop_table("events")
