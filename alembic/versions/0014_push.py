"""push: app_keys (the VAPID pair) and push_subscriptions (one per device)

Web Push (PUSH.md). No config knobs: the VAPID pair is minted by the app on
first use and kept in ``app_keys``, because it must stay STABLE — it is baked
into every subscription a browser holds.

GUARDED like 0002-0013: a no-op on a fresh create_all database.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("app_keys"):
        op.create_table(
            "app_keys",
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("name", name="pk_app_keys"),
        )
    if not _has_table("push_subscriptions"):
        op.create_table(
            "push_subscriptions",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("endpoint", sa.Text(), nullable=False),
            sa.Column("p256dh", sa.Text(), nullable=False),
            sa.Column("auth", sa.Text(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                    name="fk_push_subscriptions_user_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_push_subscriptions"),
            sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
        )
        op.create_index("idx_push_subscriptions_user", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    for tbl in ("push_subscriptions", "app_keys"):
        if _has_table(tbl):
            op.drop_table(tbl)
