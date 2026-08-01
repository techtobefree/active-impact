"""locations: the address book the app builds itself

Every address typed on an event upserts a ``locations`` row (LOCATIONS.md), and
events link to it. Existing events are BACK-FILLED here -- unlike FEED.md's
records, this is not guesswork: the address is right there on the row, so the
same normalization that will run from now on runs once over the history and the
list starts out populated with the venues already in use.

GUARDED like 0002-0010: a no-op on a fresh create_all database, real work on an
existing one.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.TIMESTAMP(timezone=True)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("locations"):
        op.create_table(
            "locations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column("norm", sa.Text(), nullable=False),
            sa.Column("lat", sa.Double(), nullable=True),
            sa.Column("lon", sa.Double(), nullable=True),
            sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id", name="pk_locations"),
            sa.UniqueConstraint("norm", name="uq_locations_norm"),
        )

    if not _has_column("events", "location_id"):
        op.add_column("events", sa.Column("location_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_events_location_id_locations",
            "events", "locations", ["location_id"], ["id"], ondelete="SET NULL",
        )
    if not _has_index("events", "idx_events_location"):
        op.create_index("idx_events_location", "events", ["location_id"])

    # Back-fill from the addresses already on file. The normalization matches
    # app/locations.normalize: lowercase, collapse whitespace, strip edge
    # punctuation. btrim's character set is the same " .,;:-" the app uses.
    bind = op.get_bind()
    norm = "btrim(lower(regexp_replace(location_text, '\\s+', ' ', 'g')), ' .,;:-')"
    bind.execute(sa.text(
        f"INSERT INTO locations (label, norm) "
        f"SELECT DISTINCT ON ({norm}) btrim(location_text), {norm} "
        f"FROM events WHERE {norm} <> '' "
        f"ORDER BY {norm}, id "          # the earliest spelling becomes the label
        f"ON CONFLICT (norm) DO NOTHING"
    ))
    bind.execute(sa.text(
        f"UPDATE events e SET location_id = l.id "
        f"FROM locations l WHERE l.norm = {norm} AND e.location_id IS NULL"
    ))
    # A back-filled venue inherits coordinates from any event that has them
    # (FEED.md/LOCATIONS.md L4) -- the earliest such event wins, and NULL stays
    # NULL when nothing knows.
    bind.execute(sa.text(
        "UPDATE locations l SET lat = src.lat, lon = src.lon "
        "FROM (SELECT DISTINCT ON (location_id) location_id, lat, lon FROM events "
        "      WHERE location_id IS NOT NULL AND lat IS NOT NULL ORDER BY location_id, id) src "
        "WHERE l.id = src.location_id AND l.lat IS NULL"
    ))


def downgrade() -> None:
    if _has_index("events", "idx_events_location"):
        op.drop_index("idx_events_location", table_name="events")
    if _has_column("events", "location_id"):
        op.drop_constraint("fk_events_location_id_locations", "events", type_="foreignkey")
        op.drop_column("events", "location_id")
    if _has_table("locations"):
        op.drop_table("locations")
