"""M2: /me roundtrip, profile edits, public profiles + stats, auth + validation.

Covers every branch in app/users.py: the private self view (incl. balance),
PATCH of display_name/bio with updated_at bump, the public profile shape
(stats present, balance/password_hash absent), 404 for unknown users, 422
validation bounds, and the 401 auth wall (D12: profiles require a token).
"""
from app import db


def test_me_roundtrip(register):
    client, user, _token = register("alice", display_name="Alice A")
    r = client.get("/api/me")
    assert r.status_code == 200
    me = r.json()
    assert me["id"] == user["id"]
    assert me["username"] == "alice"
    assert me["display_name"] == "Alice A"
    assert me["bio"] == ""
    assert me["balance"] == 0
    assert "created_at" in me
    # Private-only fields never leak.
    assert "password_hash" not in me


def test_patch_display_name_and_bio(register):
    client, user, _token = register("bob")
    before = db.query_one("SELECT updated_at FROM users WHERE id=%s", (user["id"],))["updated_at"]
    r = client.patch("/api/me", json={"display_name": "Bob B", "bio": "I build trails."})
    assert r.status_code == 200
    me = r.json()
    assert me["display_name"] == "Bob B"
    assert me["bio"] == "I build trails."
    # Re-read confirms persistence and the updated_at bump.
    reread = client.get("/api/me").json()
    assert reread["display_name"] == "Bob B"
    assert reread["bio"] == "I build trails."
    after = db.query_one("SELECT updated_at FROM users WHERE id=%s", (user["id"],))["updated_at"]
    assert after >= before


def test_patch_partial_only_touches_given_field(register):
    client, _user, _token = register("carol", display_name="Carol C")
    client.patch("/api/me", json={"bio": "hello"})
    me = client.get("/api/me").json()
    assert me["bio"] == "hello"
    assert me["display_name"] == "Carol C"   # untouched
    # display_name-only patch leaves bio intact.
    client.patch("/api/me", json={"display_name": "Caroline"})
    me = client.get("/api/me").json()
    assert me["display_name"] == "Caroline"
    assert me["bio"] == "hello"


def test_patch_empty_body_is_noop(register):
    client, _user, _token = register("dave", display_name="Dave D")
    r = client.patch("/api/me", json={})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Dave D"


def test_public_profile_shape_and_stats(register):
    _client_a, _ua, _ = register("erin", display_name="Erin E")
    viewer, _uv, _ = register("viewer")
    r = viewer.get("/api/users/ERIN")   # lowercased lookup
    assert r.status_code == 200
    prof = r.json()
    assert prof["username"] == "erin"
    assert prof["display_name"] == "Erin E"
    assert prof["bio"] == ""
    assert "created_at" in prof
    # Stats present and zero for a brand-new user.
    assert prof["hours_volunteered"] == 0.0
    assert prof["tokens_earned"] == 0
    assert prof["projects_joined"] == 0
    # Private fields must NOT appear on a public profile.
    assert "balance" not in prof
    assert "password_hash" not in prof


def test_unknown_user_404(register):
    client, _user, _token = register("frank")
    r = client.get("/api/users/nobody")
    assert r.status_code == 404
    assert r.json()["detail"] == "not_found"


def test_validation_bounds_422(register):
    client, _user, _token = register("grace")
    # Empty display_name (after strip) is below the 1-char floor.
    assert client.patch("/api/me", json={"display_name": "   "}).status_code == 422
    # 61 chars exceeds the 60-char ceiling.
    assert client.patch("/api/me", json={"display_name": "x" * 61}).status_code == 422
    # bio over 10000 chars.
    assert client.patch("/api/me", json={"bio": "x" * 10001}).status_code == 422
    # Boundary values are accepted.
    assert client.patch("/api/me", json={"display_name": "x" * 60}).status_code == 200
    assert client.patch("/api/me", json={"bio": "x" * 10000}).status_code == 200


def test_auth_required(api, register):
    # No token on any of the three endpoints -> 401.
    assert api.get("/api/me").status_code == 401
    assert api.patch("/api/me", json={"bio": "hi"}).status_code == 401
    # Public-profile endpoint is behind the auth wall too (D12).
    register("harry")
    assert api.get("/api/users/harry").status_code == 401
