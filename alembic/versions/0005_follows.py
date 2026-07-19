"""follows

Adds the ``follows`` table: one row per (user, project) a user follows, so the
feed can show a follow button + follower count without touching RSVP/check-in.

Like 0002/0003/0004, migration 0001 is a create_all baseline from the CURRENT
models, so a FRESH database already has this table when this revision runs. Guard
on existence (inspector.has_table) so this no-ops on fresh DBs and really applies
on an already-migrated DB.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("follows"):
        op.create_table(
            "follows",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_follows_user_id_users", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_follows_project_id_projects", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_follows"),
            sa.UniqueConstraint("user_id", "project_id", name="uq_follow"),
        )
        op.create_index("idx_follows_project", "follows", ["project_id"])


def downgrade() -> None:
    if _has_table("follows"):
        op.drop_index("idx_follows_project", table_name="follows")
        op.drop_table("follows")
