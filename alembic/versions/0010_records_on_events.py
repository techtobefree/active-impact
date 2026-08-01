"""one feed: service_records.event_id/lat/lon/match_reason + events.lat/lon

The merge of the two feeds (FEED.md F1/F5). A service record now belongs to the
EVENT it was logged at, and an event can carry coordinates so a record's GPS can
find it.

GUARDED like 0002-0009: migration 0001 is a ``create_all`` baseline from the
CURRENT models, so a FRESH database already has every column below. Each step
checks first and is a no-op there, doing real work only on an existing (<=0009)
database.

Existing production records keep ``event_id IS NULL`` -- FEED.md §8 deliberately
does NOT back-fill: guessing an association for a photo taken weeks ago, from a
location we never recorded, would be inventing data. Their authors can attach
them by hand (PATCH /service_records/{id}).

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    # (a) Where an event actually is. NULL = unknown (never matches by distance).
    if not _has_column("events", "lat"):
        op.add_column("events", sa.Column("lat", sa.Double(), nullable=True))
    if not _has_column("events", "lon"):
        op.add_column("events", sa.Column("lon", sa.Double(), nullable=True))

    # (b) The merge itself. SET NULL, never CASCADE -- deleting an event must not
    #     delete the photos people took there.
    if not _has_column("service_records", "event_id"):
        op.add_column("service_records", sa.Column("event_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            "fk_service_records_event_id_events",
            "service_records", "events", ["event_id"], ["id"], ondelete="SET NULL",
        )

    # (c) The matching inputs. NEVER served in any read shape (FEED.md F6).
    for col in ("lat", "lon"):
        if not _has_column("service_records", col):
            op.add_column("service_records", sa.Column(col, sa.Double(), nullable=True))
    if not _has_column("service_records", "match_reason"):
        op.add_column("service_records", sa.Column("match_reason", sa.Text(), nullable=True))

    # (d) One event's feed, newest-first -- the read every project card runs.
    if not _has_index("service_records", "idx_service_records_event"):
        op.create_index(
            "idx_service_records_event", "service_records",
            ["event_id", sa.text("created_at DESC")],
        )


def downgrade() -> None:
    if _has_index("service_records", "idx_service_records_event"):
        op.drop_index("idx_service_records_event", table_name="service_records")
    for col in ("match_reason", "lon", "lat"):
        if _has_column("service_records", col):
            op.drop_column("service_records", col)
    if _has_column("service_records", "event_id"):
        op.drop_constraint("fk_service_records_event_id_events", "service_records", type_="foreignkey")
        op.drop_column("service_records", "event_id")
    for col in ("lon", "lat"):
        if _has_column("events", col):
            op.drop_column("events", col)
