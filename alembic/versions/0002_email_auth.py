"""email auth

Auth moves from username+password to email+password: rename users.username ->
users.email and recreate the unique case-insensitive index under its new name.
No data rewrite -- pre-existing dev handles simply remain as non-email strings.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _user_columns() -> list[str]:
    return [c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")]


def upgrade() -> None:
    # Migration 0001 is a baseline that builds tables from the LIVE models
    # (Base.metadata.create_all), so a fresh database already has the email
    # schema when this revision runs. Only pre-existing databases -- created
    # when the models still said `username` -- need the rename.
    if "username" not in _user_columns():
        return
    op.drop_index("idx_users_username", table_name="users")
    op.alter_column("users", "username", new_column_name="email")
    op.create_index(
        "idx_users_email", "users", [sa.text("lower(email)")], unique=True
    )


def downgrade() -> None:
    if "email" not in _user_columns():
        return
    op.drop_index("idx_users_email", table_name="users")
    op.alter_column("users", "email", new_column_name="username")
    op.create_index(
        "idx_users_username", "users", [sa.text("lower(username)")], unique=True
    )
