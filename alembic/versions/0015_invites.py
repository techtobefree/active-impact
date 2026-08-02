"""invites: "come to this", addressed to one person

The project page's Invite button, wired up (SOCIAL.md §5b). A directed message,
deliberately NOT public activity — it creates no activities row and appears in
no feed, only in the invitee's notifications.

GUARDED like 0002-0014: a no-op on a fresh create_all database.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("invites"):
        op.create_table(
            "invites",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("inviter_id", sa.Integer(), nullable=False),
            sa.Column("invitee_id", sa.Integer(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint("inviter_id <> invitee_id", name="ck_invites_not_self"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"],
                                    name="fk_invites_project_id_projects", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["inviter_id"], ["users.id"],
                                    name="fk_invites_inviter_id_users", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invitee_id"], ["users.id"],
                                    name="fk_invites_invitee_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_invites"),
            sa.UniqueConstraint("project_id", "inviter_id", "invitee_id", name="uq_invites"),
        )
        op.create_index("idx_invites_invitee", "invites", ["invitee_id", "id"])


def downgrade() -> None:
    if _has_table("invites"):
        op.drop_table("invites")
