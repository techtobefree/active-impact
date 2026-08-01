"""check-in proof: users.qr_token, participations.attested, attestations

Adds the attested layer (CHECKIN_PROOF.md §5.1):
  (a) users.qr_token -- the permanent opaque handle a personal QR carries;
  (b) participations.attested -- false = asserted, true = corroborated by a scan;
  (c) new table attestations -- the append-only sightings.

GUARDED like 0002-0008: migration 0001 is a ``create_all`` baseline from the
CURRENT models, so a FRESH database already has every bit of this. Each step is a
harmless no-op on that fresh shape and does the real work only on an existing
(<=0008) database:
  (a) added NULLABLE, backfilled row-by-row with the SAME generator the app uses
      (``secrets.token_urlsafe(8)`` -- not a SQL expression, so a migrated token is
      indistinguishable from a minted one), then made NOT NULL + UNIQUE. On a
      fresh DB the column already exists and every row already has a value, so the
      guard skips straight past;
  (b) ``add_column`` guarded by a column check; the server_default fills existing
      rows with false, which is exactly right -- every check-in that predates this
      migration WAS an assertion;
  (c) guarded by ``has_table``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-01
"""
import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # (a) users.qr_token -- add nullable, backfill, then constrain.
    if not _has_column("users", "qr_token"):
        op.add_column("users", sa.Column("qr_token", sa.Text(), nullable=True))
        bind = op.get_bind()
        rows = bind.execute(sa.text("SELECT id FROM users WHERE qr_token IS NULL")).fetchall()
        for (uid,) in rows:
            # Retry on the (vanishingly unlikely) UNIQUE collision rather than
            # letting a whole deploy fail on a dice roll.
            while True:
                try:
                    with bind.begin_nested():
                        bind.execute(
                            sa.text("UPDATE users SET qr_token = :t WHERE id = :i"),
                            {"t": secrets.token_urlsafe(8), "i": uid},
                        )
                    break
                except sa.exc.IntegrityError:
                    continue
        op.alter_column("users", "qr_token", existing_type=sa.Text(), nullable=False)
        op.create_unique_constraint("uq_users_qr_token", "users", ["qr_token"])

    # (b) participations.attested -- existing rows are assertions, hence false.
    if not _has_column("participations", "attested"):
        op.add_column(
            "participations",
            sa.Column("attested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )

    # (c) The append-only sightings table.
    if not _has_table("attestations"):
        op.create_table(
            "attestations",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("event_id", sa.BigInteger(), nullable=False),
            sa.Column("scanner_user_id", sa.Integer(), nullable=False),
            sa.Column("subject_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint("scanner_user_id <> subject_user_id", name="ck_attestations_not_self"),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_attestations_event_id_events", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["scanner_user_id"], ["users.id"], name="fk_attestations_scanner_user_id_users"),
            sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], name="fk_attestations_subject_user_id_users"),
            sa.PrimaryKeyConstraint("id", name="pk_attestations"),
            sa.UniqueConstraint("event_id", "scanner_user_id", "subject_user_id", name="uq_attestations_event_scanner_subject"),
        )
        op.create_index("idx_attestations_event", "attestations", ["event_id"])
        op.create_index("idx_attestations_subject", "attestations", ["subject_user_id"])


def downgrade() -> None:
    if _has_table("attestations"):
        op.drop_table("attestations")
    if _has_column("participations", "attested"):
        op.drop_column("participations", "attested")
    if _has_column("users", "qr_token"):
        op.drop_constraint("uq_users_qr_token", "users", type_="unique")
        op.drop_column("users", "qr_token")
