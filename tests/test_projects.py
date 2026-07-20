"""Projects: create/detail/edit, versioned waivers, leaders, list scopes.

Covers app/projects.py: create seeds owner-leader + waiver v1 + the FIRST event;
a changed waiver INSERTs v2 leaving v1 untouched (I5); leader-only edits (403
not_a_leader); leader add/remove with the owner irremovable; list scopes (each
card embeds one event) + search + pagination; detail hiding the event checkin_code
from non-leaders. Event-scoped code/QR/close/roster live in test_events /
test_rsvp; the audit trail in test_audit.
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


def first_event(detail):
    return detail["events"][0]


def insert_participation(event_id, user_id, waiver_id, minutes_ago=0, open=True):
    """Insert a participation on an EVENT directly (checkin module is separate)."""
    checked_out = None if open else "now()"
    db.query(
        "INSERT INTO participations"
        "(event_id, user_id, waiver_id, checked_in_at, checked_out_at, minutes) "
        "VALUES (%s, %s, %s, now() - (%s * interval '1 minute'), "
        + (checked_out or "NULL")
        + ", %s)",
        (event_id, user_id, waiver_id, minutes_ago, None if open else 90),
    )


def _balance(uid):
    return db.query_one("SELECT balance FROM users WHERE id=%s", (uid,))["balance"]


# ---- create + detail --------------------------------------------------------

def test_create_seeds_leader_waiver_and_first_event(register):
    client, user, _ = register("owner")
    detail = make_project(client)
    # durable project fields
    assert detail["title"] == "Beach Cleanup"
    assert detail["description"] == ""
    assert detail["owner"] == {"id": user["id"], "display_name": "owner"}
    assert detail["image_ids"] == []
    assert [l["display_name"] for l in detail["leaders"]] == ["owner"]
    assert all("email" not in l and "username" not in l for l in detail["leaders"])
    assert detail["waiver"]["version"] == 1
    assert detail["waiver"]["text"] == DEFAULT_WAIVER
    assert detail["am_leader"] is True
    # the seeded first event carries the occurrence fields
    ev = first_event(detail)
    assert ev["location_text"] == "The Beach"
    assert ev["expected_minutes"] == 120
    assert ev["status"] == "open"
    assert ev["is_over"] is False
    assert ev["checked_in_count"] == 0
    assert ev["my_open_participation"] is None
    assert ev["my_hours_here"] == 0.0
    # checkin_code present for a leader; token_urlsafe(6) is 8 chars
    assert len(ev["checkin_code"]) == 8


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
    assert all("checkin_code" not in e for e in seen["events"])
    # ...but the leader still sees it on each event.
    mine = owner.get(f"/api/projects/{pid}").json()
    assert mine["am_leader"] is True
    assert all("checkin_code" in e for e in mine["events"])


def test_detail_my_hours_and_open_participation(register):
    owner, u, _ = register("owner")
    detail = make_project(owner, expected_minutes=120)
    pid, wid = detail["id"], detail["waiver"]["id"]
    eid = first_event(detail)["id"]
    # one closed 90-minute participation -> 1.5 hours
    insert_participation(eid, u["id"], wid, minutes_ago=90, open=False)
    # one currently-open participation
    insert_participation(eid, u["id"], wid, minutes_ago=10, open=True)
    ev = first_event(owner.get(f"/api/projects/{pid}").json())
    assert ev["my_hours_here"] == 1.5
    assert ev["my_open_participation"] is not None
    assert "checked_in_at" in ev["my_open_participation"]
    assert ev["checked_in_count"] == 1


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


def test_patch_updates_project_fields(register):
    client, _u, _ = register("owner")
    detail = make_project(client)
    pid = detail["id"]
    r = client.patch(f"/api/projects/{pid}", json={
        "title": "New Title", "description": "Bring gloves."})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "New Title"
    assert body["description"] == "Bring gloves."


def test_non_leader_patch_403(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    pid = make_project(owner)["id"]
    r = other.patch(f"/api/projects/{pid}", json={"title": "hijack"})
    assert r.status_code == 403 and r.json()["detail"] == "not_a_leader"


# ---- leaders ----------------------------------------------------------------

def test_add_and_remove_leader(register):
    owner, ou, _ = register("owner")
    co, cou, _ = register("colead")
    pid = make_project(owner)["id"]

    # add by email (case-insensitive)
    r = owner.post(f"/api/projects/{pid}/leaders", json={"email": "Colead@TEST.local"})
    assert r.status_code == 201
    assert {l["display_name"] for l in r.json()} == {"owner", "colead"}
    assert all("email" not in l for l in r.json())
    # the new leader now has leader powers
    assert co.patch(f"/api/projects/{pid}", json={"title": "Co edit"}).status_code == 200

    # remove the co-leader by user id
    r = owner.delete(f"/api/projects/{pid}/leaders/{cou['id']}")
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
                      json={"email": "colead@test.local"}).status_code == 403
    # unknown user
    r = owner.post(f"/api/projects/{pid}/leaders", json={"email": "ghost@test.local"})
    assert r.status_code == 404 and r.json()["detail"] == "user_not_found"
    # already a leader (the owner)
    r = owner.post(f"/api/projects/{pid}/leaders", json={"email": "owner@test.local"})
    assert r.status_code == 409 and r.json()["detail"] == "already_leader"


def test_owner_is_irremovable(register):
    owner, ou, _ = register("owner")
    pid = make_project(owner)["id"]
    r = owner.delete(f"/api/projects/{pid}/leaders/{ou['id']}")
    assert r.status_code == 409 and r.json()["detail"] == "cannot_remove_owner"


def test_remove_leader_errors(register):
    owner, ou, _ = register("owner")
    other, xu, _ = register("stranger")
    pid = make_project(owner)["id"]
    # non-leader cannot remove
    assert other.delete(f"/api/projects/{pid}/leaders/{ou['id']}").status_code == 403
    # unknown user id
    r = owner.delete(f"/api/projects/{pid}/leaders/999999")
    assert r.status_code == 404 and r.json()["detail"] == "user_not_found"
    # a real user who isn't a leader
    r = owner.delete(f"/api/projects/{pid}/leaders/{xu['id']}")
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


# ---- list scopes / search / pagination --------------------------------------

def test_list_scopes(register):
    owner, ou, _ = register("owner")
    other, xu, _ = register("stranger")
    up = make_project(owner, title="Upcoming Beach", starts_at=_future())["id"]
    past = make_project(owner, title="Old Park", starts_at=_past())["id"]
    theirs_detail = make_project(other, title="Their Thing", starts_at=_future(days=3))
    theirs = theirs_detail["id"]

    # upcoming (default): a project with a not-over event
    ids = [p["id"] for p in owner.get("/api/projects").json()]
    assert up in ids and past not in ids and theirs in ids

    # past: a project whose only event ended -- the complement of upcoming
    ids = [p["id"] for p in owner.get("/api/projects?scope=past").json()]
    assert past in ids and up not in ids

    # mine: owner's leaderships (and participations) only
    ids = [p["id"] for p in owner.get("/api/projects?scope=mine").json()]
    assert up in ids and past in ids and theirs not in ids

    # participation on another's event pulls their project into my "mine"
    theirs_eid = theirs_detail["events"][0]["id"]
    wid = theirs_detail["waiver"]["id"]
    insert_participation(theirs_eid, ou["id"], wid, minutes_ago=5, open=True)
    ids = [p["id"] for p in owner.get("/api/projects?scope=mine").json()]
    assert theirs in ids


def test_list_search_q(register):
    owner, _o, _ = register("owner")
    beach = make_project(owner, title="Beach Cleanup", location_text="Sandy Shore")["id"]
    park = make_project(owner, title="Park Planting", location_text="Green Park")["id"]
    # title match
    ids = [p["id"] for p in owner.get("/api/projects?q=beach").json()]
    assert ids == [beach]
    # event-location match
    ids = [p["id"] for p in owner.get("/api/projects?q=green").json()]
    assert ids == [park]


def test_list_pagination(register):
    owner, _o, _ = register("owner")
    p1 = make_project(owner, title="P1", starts_at=_future(days=1))["id"]
    p2 = make_project(owner, title="P2", starts_at=_future(days=2))["id"]
    p3 = make_project(owner, title="P3", starts_at=_future(days=3))["id"]
    # upcoming orders by the soonest not-over event ASC
    page1 = [p["id"] for p in owner.get("/api/projects?limit=2").json()]
    assert page1 == [p1, p2]
    page2 = [p["id"] for p in owner.get("/api/projects?limit=2&offset=2").json()]
    assert page2 == [p3]


def test_list_card_shape(register):
    owner, _o, _ = register("owner")
    detail = make_project(owner)
    pid = detail["id"]
    card = next(c for c in owner.get("/api/projects").json() if c["id"] == pid)
    assert set(card) == {"id", "title", "cover_image_id", "follower_count", "event"}
    assert card["title"] == "Beach Cleanup"
    assert card["follower_count"] == 0
    assert card["event"]["id"] == first_event(detail)["id"]
    assert card["event"]["location_text"] == "The Beach"


# ---- auth wall --------------------------------------------------------------

def test_auth_required(api, register):
    owner, _o, _ = register("owner")
    pid = make_project(owner)["id"]
    assert api.get("/api/projects").status_code == 401
    assert api.get(f"/api/projects/{pid}").status_code == 401
    assert api.post("/api/projects", json={}).status_code == 401
