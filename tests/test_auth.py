"""M1: registration, login, sessions, the current_user guard.

Uses /api/tokens/ledger as a representative guarded endpoint for the auth
dependency (register/login responses already carry the user, so no /me needed).
"""
from app import db


def test_register_returns_token_and_user(api):
    r = api.post("/api/auth/register", json={"username": "Alice", "password": "password123"})
    assert r.status_code == 201
    data = r.json()
    assert data["token"]
    u = data["user"]
    assert u["username"] == "alice"          # normalized lowercase
    assert u["display_name"] == "alice"      # defaults to username
    assert u["balance"] == 0
    assert "password_hash" not in u


def test_register_custom_display_name(api):
    r = api.post("/api/auth/register",
                 json={"username": "bob", "password": "password123", "display_name": "Bob B"})
    assert r.json()["user"]["display_name"] == "Bob B"


def test_duplicate_username_409(api):
    api.post("/api/auth/register", json={"username": "carol", "password": "password123"})
    r = api.post("/api/auth/register", json={"username": "carol", "password": "password123"})
    assert r.status_code == 409
    assert r.json()["detail"] == "username_taken"


def test_username_case_insensitive_duplicate(api):
    api.post("/api/auth/register", json={"username": "Dave", "password": "password123"})
    r = api.post("/api/auth/register", json={"username": "dave", "password": "password123"})
    assert r.status_code == 409


def test_login_ok_and_bad(api):
    api.post("/api/auth/register", json={"username": "erin", "password": "password123"})
    ok = api.post("/api/auth/login", json={"username": "ERIN", "password": "password123"})
    assert ok.status_code == 200 and ok.json()["token"]
    bad = api.post("/api/auth/login", json={"username": "erin", "password": "wrongpass1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "invalid_credentials"


def test_login_unknown_user_same_error(api):
    r = api.post("/api/auth/login", json={"username": "ghost", "password": "password123"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_credentials"


def test_guard_requires_token(api):
    r = api.get("/api/tokens/ledger")
    assert r.status_code == 401 and r.json()["detail"] == "auth_required"


def test_logout_revokes_token(register):
    client, _user, token = register("frank")
    assert client.get("/api/tokens/ledger").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    # Old token now rejected.
    assert client.get("/api/tokens/ledger").status_code == 401


def test_expired_session_rejected(register):
    client, _user, token = register("grace")
    db.query("UPDATE sessions SET expires_at = now() - interval '1 day' WHERE token = %s", (token,))
    r = client.get("/api/tokens/ledger")
    assert r.status_code == 401 and r.json()["detail"] == "invalid_token"


def test_validation_bounds(api):
    assert api.post("/api/auth/register", json={"username": "ab", "password": "password123"}).status_code == 422
    assert api.post("/api/auth/register", json={"username": "okname", "password": "short"}).status_code == 422
    assert api.post("/api/auth/register", json={"username": "bad name", "password": "password123"}).status_code == 422
