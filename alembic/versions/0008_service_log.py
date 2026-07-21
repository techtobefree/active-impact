"""service log: nullable auth, service_records/cheers/reports, images entity

Adds the Service Log layer (SERVICE_LOG.md §4-§9):
  (a) users.email + users.password_hash become NULLABLE (email IS NULL ⇔ guest);
  (b) new tables service_records, cheers, reports;
  (c) widen ck_images_entity_valid to include 'service_record'.

GUARDED like 0002-0007: migration 0001 is a ``create_all`` baseline from the
CURRENT models, so a FRESH database already has every bit of this. Each step is
written to be a harmless no-op on that fresh shape and to do the real work only on
an existing (<=0007) database:
  (a) ``ALTER COLUMN ... DROP NOT NULL`` is idempotent -- a no-op on an
      already-nullable column;
  (b) guarded by ``has_table`` -> skipped when the baseline already built it;
  (c) drop+recreate the CHECK to the widened definition (mirrors 0007) -- dropping
      and recreating an identical constraint on a fresh DB is harmless.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    # (a) Nullable auth columns (guest = email/password_hash both NULL). DROP NOT
    # NULL is idempotent, so this is a no-op on the already-nullable fresh baseline.
    op.alter_column("users", "email", existing_type=sa.Text(), nullable=True)
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)

    # (b) The service-log tables. Skip any the create_all baseline already built.
    if not _has_table("service_records"):
        op.create_table(
            "service_records",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("caption", sa.Text(), nullable=False),
            sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_service_records_user_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_service_records"),
        )
        op.create_index("idx_service_records_created", "service_records", [sa.text("created_at DESC")])
        op.create_index("idx_service_records_user", "service_records", ["user_id"])
    if not _has_table("cheers"):
        op.create_table(
            "cheers",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("record_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["record_id"], ["service_records.id"], name="fk_cheers_record_id_service_records", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_cheers_user_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_cheers"),
            sa.UniqueConstraint("record_id", "user_id", name="uq_cheer"),
        )
    if not _has_table("reports"):
        op.create_table(
            "reports",
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("record_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["record_id"], ["service_records.id"], name="fk_reports_record_id_service_records", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_reports_user_id_users", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="pk_reports"),
            sa.UniqueConstraint("record_id", "user_id", name="uq_report"),
        )

    # (c) Widen the images entity CHECK to allow 'service_record' (mirrors 0007).
    op.execute("ALTER TABLE images DROP CONSTRAINT IF EXISTS ck_images_entity_valid")
    op.execute(
        "ALTER TABLE images ADD CONSTRAINT ck_images_entity_valid "
        "CHECK (entity IN ('project', 'catalog_item', 'event', 'service_record'))"
    )


def downgrade() -> None:
    # (c) Narrow the images CHECK back (any service_record rows must be gone first).
    op.execute("ALTER TABLE images DROP CONSTRAINT IF EXISTS ck_images_entity_valid")
    op.execute(
        "ALTER TABLE images ADD CONSTRAINT ck_images_entity_valid "
        "CHECK (entity IN ('project', 'catalog_item', 'event'))"
    )

    # (b) Drop the service-log tables (children before parent).
    if _has_table("reports"):
        op.drop_table("reports")
    if _has_table("cheers"):
        op.drop_table("cheers")
    if _has_table("service_records"):
        op.drop_table("service_records")

    # (a) Restore NOT NULL (valid only when no guest rows remain).
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    op.alter_column("users", "email", existing_type=sa.Text(), nullable=False)
