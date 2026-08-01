"""FEED.md §5–§6 — the HTTP surface of the merged feed.

A logged service carries its event; a project card carries that event's two
latest photos; an event page carries its whole feed; a leader can pin an
event's coordinates. Invariants F-I1, F-I5, F-I6, F-I7, F-I8, F-I9 live here.
"""
import base64

from app import db

from tests.test_feed_matching import HERE, NEAR, _now_event, _set_coords
from tests.test_events import make_project

TINY_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00").decode()


# ---- helpers ----------------------------------------------------------------

def _post(client, caption="Picked up litter", **extra):
    body = {"caption": caption, "content_type": "image/png", "data_base64": TINY_B64}
    body.update(extra)
    return client.post("/api/service_records", json=body)

def _card(client, project_id, scope="upcoming"):
    for c in client.get(f"/api/projects?scope={scope}").json():
        if c["id"] == project_id:
            return c
    return None


# ---- creating a record on an event ------------------------------------------

def test_record_attaches_to_the_event_i_checked_into(register):
    client, user, _ = register("ana")
    p, ev = _now_event(client)
    client.post(f"/api/events/{ev['id']}/checkin")
    r = _post(client)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["event"] == {
        "id": ev["id"], "project_id": p["id"],
        "project_title": p["title"], "starts_at": rec["event"]["starts_at"],
    }


def test_record_attaches_by_gps_alone(register):
    """The guest case: never checked in, but standing at the event."""
    client, user, _ = register("ana")
    p, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    guest = _guest_client(client)
    r = _post(guest, lat=NEAR[0], lon=NEAR[1])
    assert r.status_code == 201, r.text
    assert r.json()["event"]["id"] == ev["id"]


def test_record_with_no_signal_is_saved_unattached(register):
    client, _, _ = register("ana")
    r = _post(client)
    assert r.status_code == 201
    assert r.json()["event"] is None


def test_explicit_event_id_wins(register):
    client, _, _ = register("ana")
    _, checked_into = _now_event(client, title="Checked into")
    p2, chosen = _now_event(client, title="Chosen")
    client.post(f"/api/events/{checked_into['id']}/checkin")
    r = _post(client, event_id=chosen["id"])
    assert r.json()["event"]["id"] == chosen["id"]


def test_explicit_unknown_event_is_rejected(register):
    client, _, _ = register("ana")
    r = _post(client, event_id=999999)
    assert r.status_code == 404
    assert r.json()["detail"] == "event_not_found"


def test_coordinates_are_never_served(register):
    """F-I5: a caption is public; the author's coordinates are not."""
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    rec = _post(client, lat=NEAR[0], lon=NEAR[1]).json()
    body = str(rec)
    assert "lat" not in body and "lon" not in body and "match_reason" not in body
    detail = client.get(f"/api/service_records/{rec['id']}").json()
    assert "lat" not in str(detail) and "match_reason" not in str(detail)
    # …and the row really did store them (they are matching inputs, not fiction)
    row = db.query_one("SELECT lat, lon, match_reason FROM service_records WHERE id=%s", (rec["id"],))
    assert row["lat"] is not None and row["match_reason"] == "nearby"


# ---- geo bootstrap (F-I7) ---------------------------------------------------

def test_a_matched_record_geolocates_its_event(register):
    """The first checked-in volunteer's phone pins the event for everyone after."""
    client, _, _ = register("ana")
    p, ev = _now_event(client)
    client.post(f"/api/events/{ev['id']}/checkin")
    _post(client, lat=HERE[0], lon=HERE[1])
    row = db.query_one("SELECT lat, lon FROM events WHERE id=%s", (ev["id"],))
    assert (round(row["lat"], 4), round(row["lon"], 4)) == HERE
    # …so a passing guest with no check-in now matches by distance alone
    guest = _guest_client(client)
    assert _post(guest, lat=NEAR[0], lon=NEAR[1]).json()["event"]["id"] == ev["id"]


def test_bootstrap_never_overwrites_a_leaders_coordinates(register):
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    client.post(f"/api/events/{ev['id']}/checkin")
    _post(client, lat=41.5, lon=-70.0)
    row = db.query_one("SELECT lat, lon FROM events WHERE id=%s", (ev["id"],))
    assert (round(row["lat"], 4), round(row["lon"], 4)) == HERE


# ---- reading one event's feed -----------------------------------------------

def test_event_id_filter_returns_only_that_events_records(register):
    client, _, _ = register("ana")
    _, a = _now_event(client, title="A")
    _, b = _now_event(client, title="B")
    _post(client, caption="at A", event_id=a["id"])
    _post(client, caption="at B", event_id=b["id"])
    rows = client.get(f"/api/service_records?event_id={a['id']}").json()
    assert [r["caption"] for r in rows] == ["at A"]


def test_unattached_scope_is_mine_only(register):
    ana, _, _ = register("ana")
    ben, _, _ = register("ben")
    _post(ana, caption="ana loose")
    _post(ben, caption="ben loose")
    rows = ana.get("/api/service_records?scope=unattached").json()
    assert [r["caption"] for r in rows] == ["ana loose"]


def test_mine_scope_includes_attached_and_unattached(register):
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    _post(client, caption="attached", event_id=ev["id"])
    _post(client, caption="loose")
    rows = client.get("/api/service_records?scope=mine").json()
    assert {r["caption"] for r in rows} == {"attached", "loose"}


# ---- attaching after the fact (F-I8) ----------------------------------------

def test_author_can_attach_an_unattached_record(register):
    client, _, _ = register("ana")
    p, ev = _now_event(client)
    rec = _post(client).json()
    assert rec["event"] is None
    r = client.patch(f"/api/service_records/{rec['id']}", json={"event_id": ev["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["event"]["id"] == ev["id"]
    assert db.query_one("SELECT match_reason FROM service_records WHERE id=%s",
                        (rec["id"],))["match_reason"] == "explicit"


def test_author_can_detach(register):
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    rec = _post(client, event_id=ev["id"]).json()
    r = client.patch(f"/api/service_records/{rec['id']}", json={"event_id": None})
    assert r.status_code == 200
    assert r.json()["event"] is None


def test_only_the_author_may_attach(register):
    ana, _, _ = register("ana")
    ben, _, _ = register("ben")
    _, ev = _now_event(ana)
    rec = _post(ana).json()
    r = ben.patch(f"/api/service_records/{rec['id']}", json={"event_id": ev["id"]})
    assert r.status_code == 403
    assert r.json()["detail"] == "not_yours"


def test_attaching_to_an_unknown_event_is_rejected(register):
    client, _, _ = register("ana")
    rec = _post(client).json()
    r = client.patch(f"/api/service_records/{rec['id']}", json={"event_id": 999999})
    assert r.status_code == 404
    assert r.json()["detail"] == "event_not_found"


# ---- the project card carries the photos (F-I6) -----------------------------

def test_project_card_carries_the_two_latest_records(register):
    client, _, _ = register("ana")
    p, ev = _now_event(client)
    for cap in ("first", "second", "third"):
        _post(client, caption=cap, event_id=ev["id"])
    card = _card(client, p["id"])
    assert [r["caption"] for r in card["event"]["records"]] == ["third", "second"]
    assert card["event"]["record_count"] == 3


def test_project_card_only_carries_its_own_events_records(register):
    client, _, _ = register("ana")
    p, shown = _now_event(client, title="Shown")
    other, _ = _now_event(client, title="Other")
    _post(client, caption="elsewhere", event_id=other["events"][0]["id"])
    _post(client, caption="here", event_id=shown["id"])
    card = _card(client, p["id"])
    assert [r["caption"] for r in card["event"]["records"]] == ["here"]


def test_a_project_with_no_records_carries_an_empty_list(register):
    client, _, _ = register("ana")
    p = make_project(client)
    assert _card(client, p["id"])["event"]["records"] == []


def test_hidden_records_vanish_from_the_card(register):
    """F-I9: three reports and the photo leaves every surface, cards included."""
    ana, _, _ = register("ana")
    p, ev = _now_event(ana)
    rec = _post(ana, caption="reported", event_id=ev["id"]).json()
    for name in ("ben", "cara", "dee"):
        other, _, _ = register(name)
        other.post(f"/api/service_records/{rec['id']}/report", json={})
    card = _card(ana, p["id"])
    assert card["event"]["records"] == []
    assert card["event"]["record_count"] == 0
    assert ana.get(f"/api/service_records?event_id={ev['id']}").json() == []


# ---- an event keeps its photos when it dies (F-I1) --------------------------

def test_deleting_an_event_detaches_its_records_instead_of_deleting_them(register):
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    rec = _post(client, caption="survives", event_id=ev["id"]).json()
    db.query("DELETE FROM events WHERE id = %s", (ev["id"],))
    still = client.get(f"/api/service_records/{rec['id']}")
    assert still.status_code == 200
    assert still.json()["caption"] == "survives"
    assert still.json()["event"] is None


# ---- candidates (the "posting to…" preview + picker) ------------------------

def test_candidates_returns_the_match_and_the_ranked_list(register):
    client, user, _ = register("ana")
    p, ev = _now_event(client)
    client.post(f"/api/events/{ev['id']}/checkin")
    other, _ = _now_event(client, title="Other")
    r = client.get("/api/events/candidates")
    assert r.status_code == 200
    data = r.json()
    assert data["match"]["event_id"] == ev["id"]
    assert data["match"]["reason"] == "checked_in"
    assert data["match"]["project_title"] == p["title"]
    ids = [c["event_id"] for c in data["candidates"]]
    assert ids[0] == ev["id"] and other["events"][0]["id"] in ids


def test_candidates_reports_distance_when_gps_is_sent(register):
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    guest = _guest_client(client)
    data = guest.get(f"/api/events/candidates?lat={NEAR[0]}&lon={NEAR[1]}").json()
    cand = data["candidates"][0]
    assert cand["reason"] == "nearby"
    assert 0.5 < cand["distance_km"] < 2.0


def test_candidates_is_empty_when_nothing_is_happening(register):
    client, _, _ = register("ana")
    make_project(client)  # starts tomorrow — outside the window
    data = client.get("/api/events/candidates").json()
    assert data["match"] is None and data["candidates"] == []


# ---- a leader can fix an event ----------------------------------------------

def test_leader_can_edit_an_event_and_pin_its_coordinates(register):
    client, _, _ = register("ana")
    _, ev = _now_event(client)
    r = client.patch(f"/api/events/{ev['id']}", json={
        "location_text": "The North Gate", "expected_minutes": 45,
        "lat": HERE[0], "lon": HERE[1],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["location_text"] == "The North Gate"
    assert body["expected_minutes"] == 45
    assert (round(body["lat"], 4), round(body["lon"], 4)) == HERE


def test_non_leader_cannot_edit_an_event(register):
    ana, _, _ = register("ana")
    ben, _, _ = register("ben")
    _, ev = _now_event(ana)
    r = ben.patch(f"/api/events/{ev['id']}", json={"location_text": "Hijacked"})
    assert r.status_code == 403
    assert r.json()["detail"] == "not_a_leader"


def test_event_coordinates_can_be_set_at_creation(register):
    client, _, _ = register("ana")
    p = make_project(client, lat=HERE[0], lon=HERE[1])
    ev = client.get(f"/api/events/{p['events'][0]['id']}").json()
    assert (round(ev["lat"], 4), round(ev["lon"], 4)) == HERE


def test_added_events_can_carry_coordinates(register):
    client, _, _ = register("ana")
    p = make_project(client)
    from tests.test_events import _future
    r = client.post(f"/api/projects/{p['id']}/events", json={
        "location_text": "Depot", "starts_at": _future(days=2),
        "expected_minutes": 60, "lat": HERE[0], "lon": HERE[1],
    })
    assert r.status_code == 201, r.text
    assert (round(r.json()["lat"], 4), round(r.json()["lon"], 4)) == HERE


# ---- a guest client on the same app -----------------------------------------

def _guest_client(existing):
    """A second, anonymous identity (SERVICE_LOG.md §4) sharing the test app."""
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    data = c.post("/api/auth/guest").json()
    c.headers.update({"Authorization": "Bearer " + data["token"]})
    return c
