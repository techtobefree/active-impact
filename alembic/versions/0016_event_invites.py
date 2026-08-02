"""invites can target one EVENT, not just the project

"Come on Saturday" is a different message from "come to this project", so the
Invite button on an event page records a different invitation (SOCIAL.md §5b).

The single UNIQUE is replaced by two PARTIAL uniques: Postgres treats NULLs as
distinct, so one UNIQUE over a nullable event_id would let the same
project-level invitation be inserted repeatedly. Same pattern the codebase
already uses for idx_participations_open and idx_claims_pending.

GUARDED like 0002-0015: a no-op on a fresh create_all database.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    if not _has_column("invites", "event_id"):
        op.add_column("invites", sa.Column("event_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key("fk_invites_event_id_events", "invites", "events",
                              ["event_id"], ["id"], ondelete="CASCADE")

    # The old whole-table UNIQUE cannot express "per event OR per project".
    op.execute("ALTER TABLE invites DROP CONSTRAINT IF EXISTS uq_invites")
    if not _has_index("invites", "idx_invites_project_unique"):
        op.create_index("idx_invites_project_unique", "invites",
                        ["project_id", "inviter_id", "invitee_id"],
                        unique=True, postgresql_where=sa.text("event_id IS NULL"))
    if not _has_index("invites", "idx_invites_event_unique"):
        op.create_index("idx_invites_event_unique", "invites",
                        ["event_id", "inviter_id", "invitee_id"],
                        unique=True, postgresql_where=sa.text("event_id IS NOT NULL"))


def downgrade() -> None:
    for name in ("idx_invites_event_unique", "idx_invites_project_unique"):
        if _has_index("invites", name):
            op.drop_index(name, table_name="invites")
    if _has_column("invites", "event_id"):
        # Event-scoped invitations cannot survive a schema that has no event.
        op.execute("DELETE FROM invites WHERE event_id IS NOT NULL")
        op.drop_constraint("fk_invites_event_id_events", "invites", type_="foreignkey")
        op.drop_column("invites", "event_id")
    op.execute("ALTER TABLE invites ADD CONSTRAINT uq_invites "
               "UNIQUE (project_id, inviter_id, invitee_id)")
