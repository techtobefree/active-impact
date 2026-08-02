"""social: user_follows, blocks, activities, notification watermark

Person -> person following, a public activity projection, one-way blocks that
keep the follow, and the two user columns notifications are derived from
(SOCIAL.md). GUARDED like 0002-0011: a no-op on a fresh create_all database, real
work on an existing one.

NO BACK-FILL of activities, deliberately (SOCIAL.md §2): inventing rows for
months-old check-ins would open everybody's first Following feed with stale
"just checked in" items. The stream starts now.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # (a) person -> person. Distinct from `follows` (person -> project).
    if not _has_table("user_follows"):
        op.create_table(
            "user_follows",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("follower_id", sa.Integer(), nullable=False),
            sa.Column("followee_id", sa.Integer(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint("follower_id <> followee_id", name="ck_user_follows_not_self"),
            sa.ForeignKeyConstraint(["follower_id"], ["users.id"], name="fk_user_follows_follower_id_users", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["followee_id"], ["users.id"], name="fk_user_follows_followee_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_user_follows"),
            sa.UniqueConstraint("follower_id", "followee_id", name="uq_user_follows"),
        )
        op.create_index("idx_user_follows_followee", "user_follows", ["followee_id"])

    # (b) "may not see my activity" -- never touches user_follows (S4).
    if not _has_table("blocks"):
        op.create_table(
            "blocks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("blocker_id", sa.Integer(), nullable=False),
            sa.Column("blocked_id", sa.Integer(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint("blocker_id <> blocked_id", name="ck_blocks_not_self"),
            sa.ForeignKeyConstraint(["blocker_id"], ["users.id"], name="fk_blocks_blocker_id_users", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["blocked_id"], ["users.id"], name="fk_blocks_blocked_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_blocks"),
            sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks"),
        )
        op.create_index("idx_blocks_blocked", "blocks", ["blocked_id"])

    # (c) the public projection. CASCADE on every subject: a feed must never
    #     point at something that has been deleted.
    if not _has_table("activities"):
        op.create_table(
            "activities",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("event_id", sa.BigInteger(), nullable=True),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("record_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint("kind IN ('logged', 'rsvp', 'checked_in')", name="ck_activities_kind_valid"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_activities_user_id_users", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_activities_event_id_events", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_activities_project_id_projects", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["record_id"], ["service_records.id"], name="fk_activities_record_id_service_records", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_activities"),
        )
        op.create_index("idx_activities_user", "activities", ["user_id", "id"])
        op.create_index("idx_activities_created", "activities", ["created_at"])

    # (d) what notifications are derived from. seen_at NULL = never opened, so an
    #     existing user's first badge counts everything since they started
    #     following -- which is nothing, because (c) is not back-filled.
    if not _has_column("users", "notifications_seen_at"):
        op.add_column("users", sa.Column("notifications_seen_at", TS, nullable=True))
    if not _has_column("users", "notify_activity"):
        op.add_column("users", sa.Column(
            "notify_activity", sa.Boolean(), server_default=sa.text("true"), nullable=False))


def downgrade() -> None:
    for col in ("notify_activity", "notifications_seen_at"):
        if _has_column("users", col):
            op.drop_column("users", col)
    for tbl in ("activities", "blocks", "user_follows"):
        if _has_table(tbl):
            op.drop_table(tbl)
