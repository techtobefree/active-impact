"""rsvp and image primary

Adds RSVP / check-in modeling and a primary (cover) flag on images:

- ``rsvps`` table: one row per (project, user), with an ``is_leader`` designation
  flag (a pure marker, distinct from ``project_leaders`` organizers).
- ``images.is_primary``: marks the entity's cover image; the serializer picks the
  primary first, else the first by id.

Like 0002, migration 0001 is a create_all baseline from the CURRENT models, so a
FRESH database already has these objects when this revision runs. Guard every
step on existence (information_schema / inspector) so this no-ops on fresh DBs and
really applies on an already-migrated DB.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _columns(table: str) -> list[str]:
    return [c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)]


def upgrade() -> None:
    if not _has_table("rsvps"):
        op.create_table(
            "rsvps",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("is_leader", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_rsvps_project_id_projects", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_rsvps_user_id_users"),
            sa.PrimaryKeyConstraint("id", name="pk_rsvps"),
            sa.UniqueConstraint("project_id", "user_id", name="project_user"),
        )
        op.create_index("idx_rsvps_user", "rsvps", ["user_id"])

    if "is_primary" not in _columns("images"):
        op.add_column(
            "images",
            sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )


def downgrade() -> None:
    if "is_primary" in _columns("images"):
        op.drop_column("images", "is_primary")

    if _has_table("rsvps"):
        op.drop_index("idx_rsvps_user", table_name="rsvps")
        op.drop_table("rsvps")
