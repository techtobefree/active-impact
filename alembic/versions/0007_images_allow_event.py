"""allow entity='event' on images

The images table has a CHECK constraint (ck_images_entity_valid) restricting
`entity` to the known owners. The events-decoupling let events own images too, so
widen it to include 'event'. Fresh (create_all) DBs already have the new model's
constraint; drop-and-recreate to the same definition there is harmless, so this
migration needs no shape guard.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE images DROP CONSTRAINT IF EXISTS ck_images_entity_valid")
    op.execute(
        "ALTER TABLE images ADD CONSTRAINT ck_images_entity_valid "
        "CHECK (entity IN ('project', 'catalog_item', 'event'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE images DROP CONSTRAINT IF EXISTS ck_images_entity_valid")
    op.execute(
        "ALTER TABLE images ADD CONSTRAINT ck_images_entity_valid "
        "CHECK (entity IN ('project', 'catalog_item'))"
    )
