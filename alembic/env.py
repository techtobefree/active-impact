"""Alembic environment. Reads DATABASE_URL and targets the ORM models' metadata."""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the repo root importable so `app.models` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    """SQLAlchemy URL from DATABASE_URL, forced onto the psycopg (v3) driver."""
    url = os.environ.get("DATABASE_URL", "postgres://postgres:postgres@localhost:5433/impact")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


config.set_main_option("sqlalchemy.url", _db_url())


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
