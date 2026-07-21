"""Service Log (SERVICE_LOG.md): guests, convert (attach/merge), records, cheers,
reports/auto-hide, feed scope, and the moderation + rate-limit floors.

A guest is a users row with email IS NULL; it reuses the whole session /
current_user path. Records are a standalone log (no tokens/projects/ledger). The
photo reuses the polymorphic images table (entity='service_record').
"""
import base64

from fastapi.testclient import TestClient

from app import db
from app.main import app

# A few bytes is a valid upload -- the endpoint checks size + declared
# content_type only, never the image internals (mirrors test_images.py).
TINY_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00"
TINY_B64 = base64.b64encode(TINY_PNG).decode()


# ---- helpers ----------------------------------------------------------------

def _client(token: str) -> TestClient:
    c = TestClient(app)
    c.headers.update({"Authorization": "Bearer " + token})
    return c


def _guest(api):
    """Bootstrap a fresh guest -> (authed_client, user, token)."""
    r = api.post("/api/auth/guest")
    assert r.status_code == 201, r.text
    data = r.json()
    return _client(data["token"]), data["user"], data["token"]


def _post_record(client, caption="Picked up litter", content_type="image/png",
                 data=TINY_B64):
    return client.post(
        "/api/service_records",
        json={"caption": caption, "content_type": content_type, "data_base64": data},
    )


# ---- guest bootstrap --------------------------------------------------------

def test_guest_bootstrap_returns_usable_session(api):
    r = api.post("/api/auth/guest")
    assert r.status_code == 201
    data = r.json()
    assert data["token"]
    u = data["user"]
    assert u["is_guest"] is True
    assert u["email"] is None            # JSON null, never the string "None"
    assert " " in u["display_name"]      # auto "Adjective Animal" handle
    # the token works on a guarded endpoint
    client = _client(data["token"])
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["is_guest"] is True


def test_guest_bootstrap_idempotent_with_valid_token(api):
    client, user, token = _guest(api)
    # a second call carrying the token returns the SAME session -- no spare guest
    r = client.post("/api/auth/guest")
    assert r.status_code == 201
    data = r.json()
    assert data["token"] == token
    assert data["user"]["id"] == user["id"]
    assert db.query_one("SELECT COUNT(*) AS c FROM users")["c"] == 1


def test_guest_bootstrap_without_token_mints_distinct(api):
    a = api.post("/api/auth/guest").json()
    b = api.post("/api/auth/guest").json()   # `api` sends no Authorization header
    assert a["user"]["id"] != b["user"]["id"]
    assert db.query_one("SELECT COUNT(*) AS c FROM users")["c"] == 2


def test_me_shape_is_guest_true_and_false(api, register):
    gc, _gu, _ = _guest(api)
    assert gc.get("/api/me").json()["is_guest"] is True
    rc, ru, _ = register("realuser")
    assert ru["is_guest"] is False                       # register response
    assert rc.get("/api/me").json()["is_guest"] is False  # /me


# ---- create record ----------------------------------------------------------

def test_records_require_auth(api):
    assert api.get("/api/service_records").status_code == 401
    assert _post_record(api).status_code == 401


def test_create_record_inserts_record_and_image_one_tx(api):
    client, user, _ = _guest(api)
    r = _post_record(client, caption="  Cleaned the park  ")
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["caption"] == "Cleaned the park"          # stripped
    assert card["author"]["id"] == user["id"]
    assert card["author"]["is_guest"] is True
    assert "email" not in card["author"]                  # never exposed
    assert card["cheer_count"] == 0 and card["i_cheered"] is False
    assert card["photo_image_id"]
    # the image row exists, is pinned to the record, and streams
    assert client.get(f"/api/images/{card['photo_image_id']}").status_code == 200
    img = db.query_one("SELECT entity, entity_id FROM images WHERE id=%s",
                       (card["photo_image_id"],))
    assert img["entity"] == "service_record" and img["entity_id"] == card["id"]
    assert db.query_one("SELECT COUNT(*) AS c FROM service_records")["c"] == 1


def test_caption_validation(api):
    client, _, _ = _guest(api)
    assert _post_record(client, caption="   ").status_code == 422        # blank
    assert _post_record(client, caption="x" * 281).status_code == 422    # too long
    assert _post_record(client, caption="x" * 280).status_code == 201    # cap ok


def test_image_validation(api):
    client, _, _ = _guest(api)
    assert _post_record(client, content_type="application/pdf").status_code == 422
    assert _post_record(client, data="abc").status_code == 422           # bad base64
    big = base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode()
    assert _post_record(client, data=big).status_code == 413


def test_create_record_rate_limited(api):
    client, user, _ = _guest(api)
    # seed the per-hour cap directly, then the next create is refused
    db.query(
        "INSERT INTO service_records(user_id, caption) "
        "SELECT %s, 'seed' FROM generate_series(1, 20)",
        (user["id"],),
    )
    r = _post_record(client)
    assert r.status_code == 429 and r.json()["detail"] == "rate_limited"


# ---- feed -------------------------------------------------------------------

def test_feed_scope_and_hidden_exclusion(api):
    a_client, _a, _ = _guest(api)
    b_client, _b, _ = _guest(api)
    ra = _post_record(a_client, caption="A one").json()
    rb = _post_record(b_client, caption="B one").json()

    feed = a_client.get("/api/service_records").json()
    ids = [c["id"] for c in feed]
    assert set(ids) == {ra["id"], rb["id"]}
    assert ids[0] == rb["id"]                             # newest first

    mine = a_client.get("/api/service_records?scope=mine").json()
    assert [c["id"] for c in mine] == [ra["id"]]          # only my own

    db.query("UPDATE service_records SET hidden=true WHERE id=%s", (rb["id"],))
    feed2 = a_client.get("/api/service_records").json()
    assert [c["id"] for c in feed2] == [ra["id"]]         # hidden dropped


def test_feed_batches_cheer_state(api):
    a_client, _a, _ = _guest(api)
    b_client, _b, _ = _guest(api)
    r1 = _post_record(a_client, caption="one").json()
    r2 = _post_record(a_client, caption="two").json()
    b_client.post(f"/api/service_records/{r1['id']}/cheer")

    b_feed = {c["id"]: c for c in b_client.get("/api/service_records").json()}
    assert b_feed[r1["id"]]["i_cheered"] is True and b_feed[r1["id"]]["cheer_count"] == 1
    assert b_feed[r2["id"]]["i_cheered"] is False and b_feed[r2["id"]]["cheer_count"] == 0
    # photo is batched in too (record_photo_maps) -> each card streams its image
    assert b_feed[r1["id"]]["photo_image_id"] == r1["photo_image_id"]
    assert b_client.get(f"/api/images/{b_feed[r1['id']]['photo_image_id']}").status_code == 200
    # count is global; i_cheered is per-viewer
    a_feed = {c["id"]: c for c in a_client.get("/api/service_records").json()}
    assert a_feed[r1["id"]]["i_cheered"] is False and a_feed[r1["id"]]["cheer_count"] == 1


def test_get_record_detail_and_404(api):
    author, _, _ = _guest(api)
    rec = _post_record(author, caption="detail me").json()
    got = author.get(f"/api/service_records/{rec['id']}").json()
    assert got["id"] == rec["id"] and got["caption"] == "detail me"
    assert "email" not in got["author"]
    assert author.get("/api/service_records/999999").status_code == 404


# ---- cheer ------------------------------------------------------------------

def test_cheer_toggle_idempotent_and_count(api):
    a_client, _, _ = _guest(api)
    b_client, _, _ = _guest(api)
    rid = _post_record(a_client).json()["id"]

    assert b_client.post(f"/api/service_records/{rid}/cheer").json() == {
        "cheered": True, "cheer_count": 1}
    assert b_client.post(f"/api/service_records/{rid}/cheer").json() == {
        "cheered": True, "cheer_count": 1}          # idempotent add
    assert b_client.delete(f"/api/service_records/{rid}/cheer").json() == {
        "cheered": False, "cheer_count": 0}
    assert b_client.delete(f"/api/service_records/{rid}/cheer").json() == {
        "cheered": False, "cheer_count": 0}          # idempotent remove
    assert b_client.post("/api/service_records/999999/cheer").status_code == 404


# ---- report / auto-hide (guests may report) ---------------------------------

def test_report_autohide_at_three_distinct_reporters(api):
    author, _, _ = _guest(api)
    rid = _post_record(author).json()["id"]
    r0, _, _ = _guest(api)
    r1, _, _ = _guest(api)
    r2, _, _ = _guest(api)

    assert r0.post(f"/api/service_records/{rid}/report",
                   json={"reason": "spam"}).status_code == 204
    # a duplicate report by the same user does not count twice (idempotent)
    assert r0.post(f"/api/service_records/{rid}/report").status_code == 204
    assert author.get(f"/api/service_records/{rid}").status_code == 200

    assert r1.post(f"/api/service_records/{rid}/report").status_code == 204
    assert author.get(f"/api/service_records/{rid}").status_code == 200

    assert r2.post(f"/api/service_records/{rid}/report").status_code == 204  # 3rd distinct
    assert author.get(f"/api/service_records/{rid}").status_code == 404      # auto-hidden
    assert db.query_one("SELECT hidden FROM service_records WHERE id=%s",
                       (rid,))["hidden"] is True
    assert rid not in [c["id"] for c in
                       author.get("/api/service_records?scope=mine").json()]


# ---- delete (author only) ---------------------------------------------------

def test_author_only_delete_cascades(api):
    author, _, _ = _guest(api)
    other, _, _ = _guest(api)
    rec = _post_record(author).json()
    rid, img = rec["id"], rec["photo_image_id"]
    other.post(f"/api/service_records/{rid}/cheer")

    bad = other.delete(f"/api/service_records/{rid}")
    assert bad.status_code == 403 and bad.json()["detail"] == "not_yours"

    assert author.delete(f"/api/service_records/{rid}").status_code == 204
    assert author.get(f"/api/service_records/{rid}").status_code == 404
    # cheers cascade (FK); the polymorphic image is removed explicitly
    assert db.query_one("SELECT COUNT(*) AS c FROM cheers WHERE record_id=%s",
                       (rid,))["c"] == 0
    assert db.query_one("SELECT COUNT(*) AS c FROM images WHERE id=%s", (img,))["c"] == 0
    assert author.delete("/api/service_records/999999").status_code == 404


# ---- convert: attach (email free) -------------------------------------------

def test_convert_attach_keeps_id_and_records(api):
    client, guest_user, _ = _guest(api)
    rec = _post_record(client, caption="my log").json()

    r = client.post("/api/auth/convert", json={
        "email": "newby@example.com", "password": "password123", "display_name": "New B"})
    assert r.status_code == 200, r.text
    data = r.json()
    u = data["user"]
    assert u["id"] == guest_user["id"]                # SAME row
    assert u["is_guest"] is False and u["email"] == "newby@example.com"
    assert u["display_name"] == "New B"

    newc = _client(data["token"])
    mine = newc.get("/api/service_records?scope=mine").json()
    assert [c["id"] for c in mine] == [rec["id"]]     # records intact
    assert mine[0]["author"]["is_guest"] is False
    # the attached credentials now log in
    assert api.post("/api/auth/login", json={
        "email": "newby@example.com", "password": "password123"}).status_code == 200


# ---- convert: merge (email taken) -------------------------------------------

def test_convert_merge_repoints_and_retires_guest(api, register):
    real_client, real_user, _ = register("realmerge")
    real_rec = _post_record(real_client, caption="real log").json()

    guest_client, guest_user, _ = _guest(api)
    guest_rec = _post_record(guest_client, caption="guest log").json()
    guest_img = guest_rec["photo_image_id"]
    guest_client.post(f"/api/service_records/{real_rec['id']}/cheer")

    # wrong password -> 401, and the guest is left intact
    bad = guest_client.post("/api/auth/convert", json={
        "email": "realmerge@test.local", "password": "wrongpass1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "invalid_credentials"
    assert db.query_one("SELECT COUNT(*) AS c FROM users WHERE id=%s",
                       (guest_user["id"],))["c"] == 1

    ok = guest_client.post("/api/auth/convert", json={
        "email": "realmerge@test.local", "password": "password123"})
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["user"]["id"] == real_user["id"]      # the EXISTING account
    assert data["user"]["is_guest"] is False

    # guest retired; its data re-pointed to the existing account
    assert db.query_one("SELECT COUNT(*) AS c FROM users WHERE id=%s",
                       (guest_user["id"],))["c"] == 0
    assert db.query_one("SELECT user_id FROM service_records WHERE id=%s",
                       (guest_rec["id"],))["user_id"] == real_user["id"]
    assert db.query_one("SELECT uploaded_by FROM images WHERE id=%s",
                       (guest_img,))["uploaded_by"] == real_user["id"]
    assert db.query_one("SELECT user_id FROM cheers WHERE record_id=%s",
                       (real_rec["id"],))["user_id"] == real_user["id"]
    # a fresh session for the existing account sees BOTH logs as mine
    merged = _client(data["token"])
    mine = {c["id"] for c in merged.get("/api/service_records?scope=mine").json()}
    assert mine == {real_rec["id"], guest_rec["id"]}


def test_convert_merge_resolves_cheer_collision(api, register):
    real_client, real_user, _ = register("realcol")
    guest_client, guest_user, _ = _guest(api)
    author_client, _, _ = _guest(api)
    rec = _post_record(author_client).json()
    # BOTH the real account and the guest cheered the same record
    real_client.post(f"/api/service_records/{rec['id']}/cheer")
    guest_client.post(f"/api/service_records/{rec['id']}/cheer")

    ok = guest_client.post("/api/auth/convert", json={
        "email": "realcol@test.local", "password": "password123"})
    assert ok.status_code == 200, ok.text
    # the UNIQUE(record_id,user_id) collision resolves to a single surviving cheer
    assert db.query_one("SELECT COUNT(*) AS c FROM cheers WHERE record_id=%s",
                       (rec["id"],))["c"] == 1
    assert db.query_one("SELECT user_id FROM cheers WHERE record_id=%s",
                       (rec["id"],))["user_id"] == real_user["id"]


def test_non_guest_cannot_convert(api, register):
    real_client, _, _ = register("realreject")
    r = real_client.post("/api/auth/convert", json={
        "email": "other@example.com", "password": "password123"})
    assert r.status_code == 409 and r.json()["detail"] == "not_a_guest"


def test_convert_validation(api):
    client, _, _ = _guest(api)
    assert client.post("/api/auth/convert", json={
        "email": "bad-email", "password": "password123"}).status_code == 422
    assert client.post("/api/auth/convert", json={
        "email": "ok@example.com", "password": ""}).status_code == 422


# ---- register / login unchanged (still require a real email) ----------------

def test_register_login_still_require_email(api):
    assert api.post("/api/auth/register", json={
        "password": "password123", "display_name": "NoEmail"}).status_code == 422
    reg = api.post("/api/auth/register", json={
        "email": "still@example.com", "password": "password123", "display_name": "Still"})
    assert reg.status_code == 201 and reg.json()["user"]["is_guest"] is False
    login = api.post("/api/auth/login", json={
        "email": "still@example.com", "password": "password123"})
    assert login.status_code == 200 and login.json()["user"]["is_guest"] is False
