"""M1: registration, login, sessions, the current_user guard.

Auth is email + password; display_name is required at registration and is the
only public identity. Uses /api/tokens/ledger as a representative guarded
endpoint for the auth dependency (register/login responses already carry the
user, so no /me needed).
"""
from app import db


def test_register_returns_token_and_user(api):
    r = api.post("/api/auth/register", json={
        "email": "  Alice@Example.COM ", "password": "password123",
        "display_name": "Alice A"})
    assert r.status_code == 201
    data = r.json()
    assert data["token"]
    u = data["user"]
    assert u["email"] == "alice@example.com"      # trimmed + lowercased
    assert u["display_name"] == "Alice A"
    assert u["balance"] == 0
    assert "password_hash" not in u
    assert "username" not in u


def test_register_requires_display_name(api):
    r = api.post("/api/auth/register",
                 json={"email": "bob@example.com", "password": "password123"})
    assert r.status_code == 422


def test_duplicate_email_409(api):
    body = {"email": "carol@example.com", "password": "password123",
            "display_name": "Carol"}
    api.post("/api/auth/register", json=body)
    r = api.post("/api/auth/register", json=body)
    assert r.status_code == 409
    assert r.json()["detail"] == "email_taken"


def test_email_case_insensitive_duplicate(api):
    api.post("/api/auth/register", json={
        "email": "Dave@example.com", "password": "password123",
        "display_name": "Dave"})
    r = api.post("/api/auth/register", json={
        "email": "dave@EXAMPLE.com", "password": "password123",
        "display_name": "Dave 2"})
    assert r.status_code == 409
    assert r.json()["detail"] == "email_taken"


def test_display_names_need_not_be_unique(api):
    """Known MVP tradeoff: two accounts may share a display name."""
    r1 = api.post("/api/auth/register", json={
        "email": "sam1@example.com", "password": "password123",
        "display_name": "Sam"})
    r2 = api.post("/api/auth/register", json={
        "email": "sam2@example.com", "password": "password123",
        "display_name": "Sam"})
    assert r1.status_code == 201 and r2.status_code == 201


def test_login_ok_and_bad(api):
    api.post("/api/auth/register", json={
        "email": "erin@example.com", "password": "password123",
        "display_name": "Erin"})
    ok = api.post("/api/auth/login",
                  json={"email": "ERIN@Example.com", "password": "password123"})
    assert ok.status_code == 200 and ok.json()["token"]
    assert ok.json()["user"]["email"] == "erin@example.com"
    bad = api.post("/api/auth/login",
                   json={"email": "erin@example.com", "password": "wrongpass1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "invalid_credentials"


def test_login_unknown_user_same_error(api):
    r = api.post("/api/auth/login",
                 json={"email": "ghost@example.com", "password": "password123"})
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
    def reg(email, password="password123", display_name="Val"):
        return api.post("/api/auth/register", json={
            "email": email, "password": password, "display_name": display_name})

    # malformed emails -> 422
    assert reg("no-at-sign.example.com").status_code == 422
    assert reg("no-domain@").status_code == 422
    assert reg("@no-local.example.com").status_code == 422
    assert reg("no-tld@example").status_code == 422
    assert reg("white space@example.com").status_code == 422
    assert reg("two@@example.com").status_code == 422
    # over 254 chars -> 422
    assert reg("x" * 250 + "@e.com").status_code == 422
    # 254 chars exactly is accepted
    local = "x" * (254 - len("@example.com"))
    assert reg(f"{local}@example.com").status_code == 201
    # password bounds
    assert reg("shortpw@example.com", password="short").status_code == 422
    assert reg("longpw@example.com", password="x" * 73).status_code == 422
    # display_name bounds: blank and >60 are rejected, 60 exactly is fine
    assert reg("dn1@example.com", display_name="   ").status_code == 422
    assert reg("dn2@example.com", display_name="x" * 61).status_code == 422
    assert reg("dn3@example.com", display_name="x" * 60).status_code == 201
