"""Pytest harness: a real Postgres test database, truncated between tests.

Point tests at an isolated DB via TEST_DATABASE_URL (parallel agents each use a
distinct name). The app pool binds to the same URL, so requests hit the test DB.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

os.environ.setdefault(
    "TEST_DATABASE_URL", "postgres://postgres:postgres@localhost:5433/impact_test"
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import psycopg  # noqa: E402
import pytest  # noqa: E402


def _ensure_test_db() -> None:
    parts = urlsplit(os.environ["DATABASE_URL"])
    dbname = parts.path.lstrip("/")
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (dbname,)
        ).fetchone()
        if not exists:
            # Force UTF-8 (matches the postgres:16-alpine prod image) regardless of
            # the cluster's default encoding, so text columns decode to str.
            conn.execute(
                f'CREATE DATABASE "{dbname}" TEMPLATE template0 '
                "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
            )


_ensure_test_db()

from app import db as _db  # noqa: E402

_db.init()

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

# Child-first order so RESTART IDENTITY CASCADE is clean.
_TABLES = [
    "token_entries", "catalog_claims", "catalog_items", "participations",
    "waivers", "project_leaders", "projects", "images", "sessions", "users",
]


@pytest.fixture(autouse=True)
def _clean():
    _db.query("TRUNCATE " + ", ".join(_TABLES) + " RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def api():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def register(api):
    """Return a factory: register(name) -> (authed_client, user, token).

    Builds email f"{name}@test.local" and defaults display_name to the name,
    so call sites keep passing short handles.
    """
    def _register(name="alice", password="password123", display_name=None):
        body = {
            "email": f"{name}@test.local",
            "password": password,
            "display_name": display_name or name,
        }
        r = api.post("/api/auth/register", json=body)
        r.raise_for_status()
        data = r.json()
        client = TestClient(app)
        client.headers.update({"Authorization": "Bearer " + data["token"]})
        return client, data["user"], data["token"]

    return _register
