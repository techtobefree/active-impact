"""claiming settles on the spot, and the price BURNS (T11, T12)

Two founder decisions land together because they rewrite the same rows.

T11 — a claim has no pending phase, so nothing accepts, declines or cancels it.
T12 — redemption destroys the tokens instead of paying the poster.

The data migration has to answer for claims caught mid-flight:

  accepted → redeemed   Same event under its true name. Tokens really did move
                        (as 'spend'), and those ledger rows are LEFT ALONE: the
                        ledger is append-only (I2), so history keeps saying what
                        actually happened rather than what we would now do.
  pending  → canceled   Nothing settled them and nothing ever can — the endpoint
                        that would have is gone. No tokens moved, so canceled is
                        the honest terminal state, stamped now.

GUARDED like 0002-0016: a no-op on a fresh create_all database.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes(table)}


def _is_nullable(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column and c["nullable"] for c in insp.get_columns(table))


def upgrade() -> None:
    # ---- T12: the ledger learns to burn ------------------------------------
    # A burn has a payer and no payee, so to_user_id stops being NOT NULL.
    if not _is_nullable("token_entries", "to_user_id"):
        op.alter_column("token_entries", "to_user_id",
                        existing_type=sa.Integer(), nullable=True)

    # Widen the kind CHECK, then pin the asymmetry: a missing recipient is a
    # burn and a burn is a missing recipient — nothing else may be half-formed.
    op.execute("ALTER TABLE token_entries DROP CONSTRAINT IF EXISTS ck_token_entries_kind_valid")
    op.execute("ALTER TABLE token_entries DROP CONSTRAINT IF EXISTS kind_valid")
    op.execute(
        "ALTER TABLE token_entries ADD CONSTRAINT ck_token_entries_kind_valid "
        "CHECK (kind IN ('earn', 'tip', 'spend', 'burn'))"
    )
    op.execute("ALTER TABLE token_entries DROP CONSTRAINT IF EXISTS ck_token_entries_burn_has_no_payee")
    op.execute(
        "ALTER TABLE token_entries ADD CONSTRAINT ck_token_entries_burn_has_no_payee "
        "CHECK ((kind = 'burn') = (to_user_id IS NULL))"
    )

    # ---- T11: claims are born settled --------------------------------------
    # Order matters: widen the CHECK, move the rows, then narrow onto the rows
    # as they now are. Doing it the other way round would fail on live data.
    op.execute("ALTER TABLE catalog_claims DROP CONSTRAINT IF EXISTS ck_catalog_claims_status_valid")
    op.execute("ALTER TABLE catalog_claims DROP CONSTRAINT IF EXISTS status_valid")
    op.execute(
        "ALTER TABLE catalog_claims ADD CONSTRAINT ck_catalog_claims_status_valid "
        "CHECK (status IN ('pending', 'accepted', 'redeemed', 'declined', 'canceled'))"
    )
    op.execute("UPDATE catalog_claims SET status = 'redeemed' WHERE status = 'accepted'")
    op.execute(
        "UPDATE catalog_claims SET status = 'canceled', decided_at = now() "
        "WHERE status = 'pending'"
    )
    op.execute("ALTER TABLE catalog_claims DROP CONSTRAINT ck_catalog_claims_status_valid")
    op.execute(
        "ALTER TABLE catalog_claims ADD CONSTRAINT ck_catalog_claims_status_valid "
        "CHECK (status IN ('redeemed', 'declined', 'canceled'))"
    )
    op.execute("ALTER TABLE catalog_claims ALTER COLUMN status SET DEFAULT 'redeemed'")

    # "One live claim per (item, claimant)" guarded a state that no longer
    # exists. Quantity is now the only bound on redeeming twice.
    if _has_index("catalog_claims", "idx_claims_pending"):
        op.drop_index("idx_claims_pending", table_name="catalog_claims")


def downgrade() -> None:
    # Burns cannot be expressed by the old schema and must not be silently
    # rewritten into transfers — there is no payee to invent. Refuse instead.
    bind = op.get_bind()
    burned = bind.execute(
        sa.text("SELECT count(*) FROM token_entries WHERE kind = 'burn'")
    ).scalar()
    if burned:
        raise RuntimeError(
            f"{burned} burn entries exist; downgrading would have to fabricate a "
            "recipient for each. Reverse them deliberately, then downgrade."
        )

    op.execute("ALTER TABLE token_entries DROP CONSTRAINT IF EXISTS ck_token_entries_burn_has_no_payee")
    op.execute("ALTER TABLE token_entries DROP CONSTRAINT IF EXISTS ck_token_entries_kind_valid")
    op.execute(
        "ALTER TABLE token_entries ADD CONSTRAINT ck_token_entries_kind_valid "
        "CHECK (kind IN ('earn', 'tip', 'spend'))"
    )
    if _is_nullable("token_entries", "to_user_id"):
        op.alter_column("token_entries", "to_user_id",
                        existing_type=sa.Integer(), nullable=False)

    op.execute("ALTER TABLE catalog_claims DROP CONSTRAINT IF EXISTS ck_catalog_claims_status_valid")
    op.execute("UPDATE catalog_claims SET status = 'accepted' WHERE status = 'redeemed'")
    op.execute(
        "ALTER TABLE catalog_claims ADD CONSTRAINT ck_catalog_claims_status_valid "
        "CHECK (status IN ('pending', 'accepted', 'declined', 'canceled'))"
    )
    op.execute("ALTER TABLE catalog_claims ALTER COLUMN status SET DEFAULT 'pending'")
    if not _has_index("catalog_claims", "idx_claims_pending"):
        op.create_index("idx_claims_pending", "catalog_claims",
                        ["item_id", "claimant_id"], unique=True,
                        postgresql_where=sa.text("status = 'pending'"))
