"""Projects: create/detail/edit, versioned waivers, leaders, QR, close, roster.

Covers the full app/projects.py surface: create seeds owner-leader + waiver v1 +
code; a changed waiver INSERTs v2 leaving v1 untouched (I5); leader-only edits
(403 not_a_leader), project_not_open state guard; leader add/remove with the
owner irremovable; code regeneration; the QR SVG (leader only); list scopes +
search + pagination; detail hiding the checkin_code from non-leaders; and close
checking out everyone still on site.
"""
from datetime import datetime, timedelta, timezone

from app import db
from app.projects import DEFAULT_WAIVER


# ---- helpers ----------------------------------------------------------------

def _iso(dt):
    return dt.isoformat()


def _future(days=1):
    return _iso(datetime.now(timezone.utc) + timedelta(days=days))


def _past(days=2):
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


def make_project(client, title="Beach Cleanup", location_text="The Beach",
                 starts_at=None, expected_minutes=120, **extra):
    body = {
        "title": title,
        "location_text": location_text,
        "starts_at": starts_at or _future(),
        "expected_minutes": expected_minutes,
    }
    body.update(extra)
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def insert_participation(project_id, user_id, waiver_id, minutes_ago=0, open=True):
    """Insert a participation directly (checkin module is a separate build)."""
    checked_out = None if open else "now()"
    db.query(
        "INSERT INTO participations"
        "(project_id, user_id, waiver_id, checked_in_at, checked_out_at, minutes) "
        "VALUES (%s, %s, %s, now() - (%s * interval '1 minute'), "
        + (checked_out or "NULL")
        + ", %s)",
        (project_id, user_id, waiver_id, minutes_ago, None if open else 90),
    )


def _balance(uid):
    return db.query_one("SELECT balance FROM users WHERE id=%s", (uid,))["balance"]


# ---- create + detail --------------------------------------------------------

def test_create_seeds_leader_waiver_and_code(register):
    client, user, _ = register("owner")
    detail = make_project(client)
    # card fields present
    assert detail["title"] == "Beach Cleanup"
    assert detail["location_text"] == "The Beach"
    assert detail["expected_minutes"] == 120
    assert detail["status"] == "open"
    assert detail["checked_in_count"] == 0
    assert detail["owner"]["username"] == "owner"
    # detail-only fields
    assert detail["description"] == ""
    assert detail["image_ids"] == []
    assert detail["my_open_participation"] is None
    assert detail["my_hours_here"] == 0.0
    # owner is seeded as the sole leader
    assert [l["username"] for l in detail["leaders"]] == ["owner"]
    # waiver v1 defaults to the placeholder template
    assert detail["waiver"]["version"] == 1
    assert detail["waiver"]["text"] == DEFAULT_WAIVER
    assert detail["am_leader"] is True
    # checkin_code present for a leader; token_urlsafe(6) is 8 chars
    assert len(detail["checkin_code"]) == 8


def test_create_with_custom_waiver(register):
    client, _u, _ = register("owner")
    detail = make_project(client, waiver_text="Sign here, be careful.")
    assert detail["waiver"]["version"] == 1
    assert detail["waiver"]["text"] == "Sign here, be careful."


def test_create_blank_waiver_falls_back_to_default(register):
    client, _u, _ = register("owner")
    detail = make_project(client, waiver_text="   ")
    assert detail["waiver"]["text"] == DEFAULT_WAIVER


def test_create_validation_422(register):
    client, _u, _ = register("owner")
    # missing location_text
    assert client.post("/api/projects", json={
        "title": "x", "starts_at": _future(), "expected_minutes": 60}).status_code == 422
    # expected_minutes must be > 0
    assert client.post("/api/projects", json={
        "title": "x", "location_text": "y", "starts_at": _future(),
        "expected_minutes": 0}).status_code == 422
    # blank title
    assert client.post("/api/projects", json={
        "title": "   ", "location_text": "y", "starts_at": _future(),
        "expected_minutes": 60}).status_code == 422


def test_detail_404(register):
    client, _u, _ = register("owner")
    r = client.get("/api/projects/999")
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


def test_detail_hides_checkin_code_from_non_leader(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    detail = make_project(owner)
    pid = detail["id"]
    seen = other.get(f"/api/projects/{pid}").json()
    assert seen["am_leader"] is False
    assert "checkin_code" not in seen
    # ...but the leader still sees it.
    mine = owner.get(f"/api/projects/{pid}").json()
    assert "checkin_code" in mine and mine["am_leader"] is True


def test_detail_my_hours_and_open_participation(register):
    owner, u, _ = register("owner")
    detail = make_project(owner, expected_minutes=120)
    pid, wid = detail["id"], detail["waiver"]["id"]
    # one closed 90-minute participation -> 1.5 hours
    insert_participation(pid, u["id"], wid, minutes_ago=90, open=False)
    # one currently-open participation
    insert_participation(pid, u["id"], wid, minutes_ago=10, open=True)
    seen = owner.get(f"/api/projects/{pid}").json()
    assert seen["my_hours_here"] == 1.5
    assert seen["my_open_participation"] is not None
    assert "checked_in_at" in seen["my_open_participation"]
    assert seen["checked_in_count"] == 1


# ---- edit + waiver versioning (I5) ------------------------------------------

def test_waiver_edit_creates_v2_leaving_v1_untouched(register):
    client, _u, _ = register("owner")
    detail = make_project(client)
    pid = detail["id"]
    v1_text = detail["waiver"]["text"]

    r = client.patch(f"/api/projects/{pid}", json={"waiver_text": "New terms v2."})
    assert r.status_code == 200
    assert r.json()["waiver"]["version"] == 2
    assert r.json()["waiver"]["text"] == "New terms v2."

    rows = db.query(
        "SELECT version, text FROM waivers WHERE project_id=%s ORDER BY version", (pid,))
    assert len(rows) == 2
    assert rows[0]["version"] == 1 and rows[0]["text"] == v1_text  # untouched
    assert rows[1]["version"] == 2 and rows[1]["text"] == "New terms v2."


def test_waiver_unchanged_creates_no_new_version(register):
    client, _u, _ = register("owner")
    detail = make_project(client, waiver_text="Same text.")
    pid = detail["id"]
    # Re-submit identical text + change another field.
    r = client.patch(f"/api/projects/{pid}",
                     json={"waiver_text": "Same text.", "title": "Renamed"})
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"
    assert r.json()["waiver"]["version"] == 1
    assert db.query_one(
        "SELECT COUNT(*) AS c FROM waivers WHERE project_id=%s", (pid,))["c"] == 1


def test_patch_updates_fields(register):
    client, _u, _ = register("owner")
    detail = make_project(client)
    pid = detail["id"]
    new_start = _future(days=5)
    r = client.patch(f"/api/projects/{pid}", json={
        "title": "New Title", "location_text": "Elsewhere",
        "expected_minutes": 60, "starts_at": new_start, "description": "Bring gloves."})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "New Title"
    assert body["location_text"] == "Elsewhere"
    assert body["expected_minutes"] == 60
    assert body["description"] == "Bring gloves."


def test_non_leader_patch_403(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]
    r = other.patch(f"/api/projects/{pid}", json={"title": "hijack"})
    assert r.status_code == 403 and r.json()["detail"] == "not_a_leader"


def test_patch_completed_project_409(register):
    client, _u, _ = register("owner")
    pid = make_project(client)["id"]
    assert client.post(f"/api/projects/{pid}/close").status_code == 200
    r = client.patch(f"/api/projects/{pid}", json={"title": "too late"})
    assert r.status_code == 409 and r.json()["detail"] == "project_not_open"


# ---- leaders ----------------------------------------------------------------

def test_add_and_remove_leader(register):
    owner, _o, _ = register("owner")
    co, cou, _ = register("colead")
    pid = make_project(owner)["id"]

    r = owner.post(f"/api/projects/{pid}/leaders", json={"username": "colead"})
    assert r.status_code == 201
    assert {l["username"] for l in r.json()} == {"owner", "colead"}
    # the new leader now has leader powers
    assert co.patch(f"/api/projects/{pid}", json={"title": "Co edit"}).status_code == 200

    # remove the co-leader
    r = owner.delete(f"/api/projects/{pid}/leaders/colead")
    assert r.status_code == 204
    # they lose leader powers
    assert co.patch(f"/api/projects/{pid}", json={"title": "x"}).status_code == 403


def test_add_leader_errors(register):
    owner, _o, _ = register("owner")
    register("colead")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]

    # non-leader cannot add
    assert other.post(f"/api/projects/{pid}/leaders",
                      json={"username": "colead"}).status_code == 403
    # unknown user
    r = owner.post(f"/api/projects/{pid}/leaders", json={"username": "ghost"})
    assert r.status_code == 404 and r.json()["detail"] == "user_not_found"
    # already a leader (the owner)
    r = owner.post(f"/api/projects/{pid}/leaders", json={"username": "owner"})
    assert r.status_code == 409 and r.json()["detail"] == "already_leader"


def test_owner_is_irremovable(register):
    owner, _o, _ = register("owner")
    pid = make_project(owner)["id"]
    r = owner.delete(f"/api/projects/{pid}/leaders/owner")
    assert r.status_code == 409 and r.json()["detail"] == "cannot_remove_owner"


def test_remove_leader_errors(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]
    # non-leader cannot remove
    assert other.delete(f"/api/projects/{pid}/leaders/owner").status_code == 403
    # unknown username
    r = owner.delete(f"/api/projects/{pid}/leaders/ghost")
    assert r.status_code == 404 and r.json()["detail"] == "user_not_found"
    # a real user who isn't a leader
    r = owner.delete(f"/api/projects/{pid}/leaders/stranger")
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


# ---- code + QR --------------------------------------------------------------

def test_regenerate_code_changes_it(register):
    client, _u, _ = register("owner")
    detail = make_project(client)
    pid, old_code = detail["id"], detail["checkin_code"]
    r = client.post(f"/api/projects/{pid}/code/regenerate")
    assert r.status_code == 200
    new_code = r.json()["checkin_code"]
    assert new_code != old_code and len(new_code) == 8
    # detail reflects the new code
    assert client.get(f"/api/projects/{pid}").json()["checkin_code"] == new_code


def test_regenerate_non_leader_403(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]
    assert other.post(f"/api/projects/{pid}/code/regenerate").status_code == 403


def test_qr_svg_leader_only(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]
    r = owner.get(f"/api/projects/{pid}/qr.svg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in r.content
    # non-leader forbidden
    assert other.get(f"/api/projects/{pid}/qr.svg").status_code == 403


# ---- list scopes / search / pagination --------------------------------------

def test_list_scopes(register):
    owner, ou, _ = register("owner")
    other, xu, _ = register("stranger")
    up = make_project(owner, title="Upcoming Beach", starts_at=_future())["id"]
    past = make_project(owner, title="Old Park", starts_at=_past())["id"]
    theirs = make_project(other, title="Their Thing", starts_at=_future(days=3))["id"]

    # upcoming (default): only the future, still-open project
    ids = [p["id"] for p in owner.get("/api/projects").json()]
    assert up in ids and past not in ids and theirs in ids

    # past: the rest (started > 12h ago)
    ids = [p["id"] for p in owner.get("/api/projects?scope=past").json()]
    assert past in ids and up not in ids

    # mine: owner's leaderships (and participations) only
    ids = [p["id"] for p in owner.get("/api/projects?scope=mine").json()]
    assert up in ids and past in ids and theirs not in ids

    # participation pulls another's project into my "mine" (I: participations ∪ leaderships)
    wid = owner.get(f"/api/projects/{theirs}").json()["waiver"]["id"]
    insert_participation(theirs, ou["id"], wid, minutes_ago=5, open=True)
    ids = [p["id"] for p in owner.get("/api/projects?scope=mine").json()]
    assert theirs in ids


def test_list_search_q(register):
    owner, _o, _ = register("owner")
    beach = make_project(owner, title="Beach Cleanup", location_text="Sandy Shore")["id"]
    park = make_project(owner, title="Park Planting", location_text="Green Park")["id"]
    # title match
    ids = [p["id"] for p in owner.get("/api/projects?q=beach").json()]
    assert ids == [beach]
    # location match
    ids = [p["id"] for p in owner.get("/api/projects?q=green").json()]
    assert ids == [park]


def test_list_pagination(register):
    owner, _o, _ = register("owner")
    p1 = make_project(owner, title="P1", starts_at=_future(days=1))["id"]
    p2 = make_project(owner, title="P2", starts_at=_future(days=2))["id"]
    p3 = make_project(owner, title="P3", starts_at=_future(days=3))["id"]
    # upcoming orders by starts_at ASC
    page1 = [p["id"] for p in owner.get("/api/projects?limit=2").json()]
    assert page1 == [p1, p2]
    page2 = [p["id"] for p in owner.get("/api/projects?limit=2&offset=2").json()]
    assert page2 == [p3]


# ---- close ------------------------------------------------------------------

def test_close_empty_project_ok(register):
    client, _u, _ = register("owner")
    pid = make_project(client)["id"]
    r = client.post(f"/api/projects/{pid}/close")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_close_checks_out_open_participations(register):
    owner, _o, _ = register("owner")
    vol, vu, _ = register("volunteer")
    detail = make_project(owner, expected_minutes=120)
    pid, wid = detail["id"], detail["waiver"]["id"]
    # a volunteer checked in 90 minutes ago and never left
    insert_participation(pid, vu["id"], wid, minutes_ago=90, open=True)

    r = owner.post(f"/api/projects/{pid}/close")
    assert r.status_code == 200

    part = db.query_one("SELECT * FROM participations WHERE project_id=%s", (pid,))
    assert part["checked_out_at"] is not None
    assert part["minutes"] >= 90
    assert part["tokens_awarded"] == 2          # 90 min -> 2 tokens
    assert _balance(vu["id"]) == 2              # minted at checkout

    # roster now shows nobody checked in
    roster = owner.get(f"/api/projects/{pid}/roster").json()
    assert roster["checked_in_count"] == 0


def test_close_non_leader_403_and_double_close_409(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]
    assert other.post(f"/api/projects/{pid}/close").status_code == 403
    assert owner.post(f"/api/projects/{pid}/close").status_code == 200
    r = owner.post(f"/api/projects/{pid}/close")
    assert r.status_code == 409 and r.json()["detail"] == "project_not_open"


# ---- roster -----------------------------------------------------------------

def test_roster_leader_only(register):
    owner, _o, _ = register("owner")
    vol, vu, _ = register("volunteer")
    other, _x, _ = register("stranger")
    detail = make_project(owner)
    pid, wid = detail["id"], detail["waiver"]["id"]
    insert_participation(pid, vu["id"], wid, minutes_ago=10, open=True)

    roster = owner.get(f"/api/projects/{pid}/roster").json()
    assert roster["checked_in_count"] == 1
    assert len(roster["participations"]) == 1
    row = roster["participations"][0]
    assert row["user"]["username"] == "volunteer"
    assert "id" in row and row["checked_out_at"] is None

    # non-leader forbidden
    assert other.get(f"/api/projects/{pid}/roster").status_code == 403


# ---- auth wall --------------------------------------------------------------

def test_auth_required(api, register):
    owner, _o, _ = register("owner")
    pid = make_project(owner)["id"]
    assert api.get("/api/projects").status_code == 401
    assert api.get(f"/api/projects/{pid}").status_code == 401
    assert api.post("/api/projects", json={}).status_code == 401
