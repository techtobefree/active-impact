"""LOCATIONS.md — the address book the app builds itself.

Every address typed on an event becomes a location, matched on a normalized key,
and coordinates flow both ways so the SECOND event at a venue can match photos by
distance without anyone touching a map. Invariants L-I1…L-I6 live here.
"""
from datetime import datetime, timedelta, timezone

from app import db
from app.locations import normalize

from tests.test_events import make_project, _future
from tests.test_feed_matching import HERE, NEAR, _now_event, _set_coords


def _loc_of(event_id: int) -> dict | None:
    return db.query_one(
        "SELECT l.* FROM events e JOIN locations l ON l.id = e.location_id "
        "WHERE e.id = %s",
        (event_id,),
    )


def _suggest(client, q=None):
    url = "/api/locations" + (f"?q={q}" if q is not None else "")
    r = client.get(url)
    assert r.status_code == 200, r.text
    return r.json()


# ---- an address becomes a location (L-I1) -----------------------------------

def test_creating_a_project_remembers_its_address(register):
    client, _, _ = register("ana")
    p = make_project(client, location_text="Riverside Park, boathouse")
    loc = _loc_of(p["events"][0]["id"])
    assert loc is not None
    assert loc["label"] == "Riverside Park, boathouse"


def test_the_same_address_typed_differently_is_one_location(register):
    client, _, _ = register("ana")
    a = make_project(client, title="A", location_text="Riverside Park, Boathouse")
    b = make_project(client, title="B", location_text="  riverside   park,  boathouse ")
    assert _loc_of(a["events"][0]["id"])["id"] == _loc_of(b["events"][0]["id"])["id"]
    # …and the FIRST spelling is what people see offered back to them.
    assert _loc_of(b["events"][0]["id"])["label"] == "Riverside Park, Boathouse"


def test_a_different_address_is_a_different_location(register):
    client, _, _ = register("ana")
    a = make_project(client, title="A", location_text="Riverside Park, boathouse")
    b = make_project(client, title="B", location_text="Riverside Park, north gate")
    assert _loc_of(a["events"][0]["id"])["id"] != _loc_of(b["events"][0]["id"])["id"]


def test_normalize_is_the_documented_rule():
    assert normalize("  Riverside   Park,  Boathouse. ") == normalize("riverside park, boathouse")
    assert normalize("Depot") != normalize("Depot Annex")


def test_added_events_reuse_the_project_address(register):
    client, _, _ = register("ana")
    p = make_project(client, location_text="Community Center")
    r = client.post(f"/api/projects/{p['id']}/events", json={
        "location_text": "community center", "starts_at": _future(days=3),
        "expected_minutes": 60,
    })
    assert r.status_code == 201, r.text
    assert _loc_of(r.json()["id"])["id"] == _loc_of(p["events"][0]["id"])["id"]


# ---- coordinates flow both ways (L-I3, L-I4) --------------------------------

def test_an_event_teaches_its_location_where_it_is(register):
    client, _, _ = register("ana")
    p = make_project(client, location_text="The Depot", lat=HERE[0], lon=HERE[1])
    loc = _loc_of(p["events"][0]["id"])
    assert (round(loc["lat"], 4), round(loc["lon"], 4)) == HERE


def test_a_known_address_gives_the_next_event_its_coordinates(register):
    """L-I3, the payoff: the second event at a venue matches photos immediately."""
    client, _, _ = register("ana")
    make_project(client, title="First", location_text="The Depot",
                 lat=HERE[0], lon=HERE[1])
    starts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    second = make_project(client, title="Second", location_text="the depot",
                          starts_at=starts, expected_minutes=180)
    ev = second["events"][0]
    row = db.query_one("SELECT lat, lon FROM events WHERE id = %s", (ev["id"],))
    assert (round(row["lat"], 4), round(row["lon"], 4)) == HERE

    # …and that is enough for a passing guest's photo to find it (FEED.md §4).
    from app.matching import resolve_event
    guest = db.query_one(
        "INSERT INTO users(display_name, qr_token) VALUES ('Passer By', 'tok-loc-1') RETURNING id"
    )
    assert resolve_event(guest["id"], *NEAR) == (ev["id"], "nearby")


def test_a_location_never_forgets_a_position_it_already_knew(register):
    """L-I4: the first coordinates win; a later blank or different event cannot
    silently move a venue."""
    client, _, _ = register("ana")
    make_project(client, title="First", location_text="The Depot",
                 lat=HERE[0], lon=HERE[1])
    p2 = make_project(client, title="Second", location_text="The Depot",
                      lat=41.5, lon=-70.0)
    loc = _loc_of(p2["events"][0]["id"])
    assert (round(loc["lat"], 4), round(loc["lon"], 4)) == HERE


def test_a_logged_photo_teaches_the_location_too(register):
    """FEED.md's bootstrap writes through: the venue learns, not just this event."""
    import base64
    client, _, _ = register("ana")
    p, ev = _now_event(client, location_text="The Depot")
    client.post(f"/api/events/{ev['id']}/checkin")
    client.post("/api/service_records", json={
        "caption": "Sorted boxes", "content_type": "image/png",
        "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00").decode(),
        "lat": HERE[0], "lon": HERE[1],
    })
    loc = _loc_of(ev["id"])
    assert (round(loc["lat"], 4), round(loc["lon"], 4)) == HERE


# ---- editing an event (L-I6) ------------------------------------------------

def test_editing_an_address_relinks_the_event(register):
    client, _, _ = register("ana")
    p = make_project(client, location_text="Old Hall")
    ev = p["events"][0]["id"]
    before = _loc_of(ev)["id"]
    r = client.patch(f"/api/events/{ev}", json={"location_text": "New Hall"})
    assert r.status_code == 200, r.text
    after = _loc_of(ev)
    assert after["id"] != before
    assert after["label"] == "New Hall"
    # The old location still exists for anyone else using it.
    assert db.query_one("SELECT 1 FROM locations WHERE id = %s", (before,)) is not None


# ---- suggestions (L-I5) -----------------------------------------------------

def test_suggestions_match_case_and_spacing_insensitively(register):
    client, _, _ = register("ana")
    make_project(client, location_text="Riverside Park, boathouse")
    labels = [s["label"] for s in _suggest(client, "RIVERSIDE")]
    assert "Riverside Park, boathouse" in labels


def test_suggestions_rank_prefix_matches_first(register):
    client, _, _ = register("ana")
    make_project(client, title="A", location_text="Park Street Depot")
    make_project(client, title="B", location_text="Riverside Park")
    labels = [s["label"] for s in _suggest(client, "park")]
    assert labels[0] == "Park Street Depot"   # starts with it
    assert "Riverside Park" in labels          # contains it


def test_suggestions_rank_the_most_used_first(register):
    client, _, _ = register("ana")
    make_project(client, title="Rare", location_text="Depot Annex")
    for i in range(3):
        make_project(client, title=f"Common {i}", location_text="Depot Main")
    rows = _suggest(client, "depot")
    assert rows[0]["label"] == "Depot Main"
    assert rows[0]["event_count"] == 3
    assert rows[1]["event_count"] == 1


def test_suggestions_with_no_query_offer_the_venues_in_use(register):
    client, _, _ = register("ana")
    make_project(client, location_text="Community Center")
    assert [s["label"] for s in _suggest(client)] == ["Community Center"]


def test_suggestions_never_expose_coordinates(register):
    """L-I5: a venue's position is a matching input, not a published field."""
    client, _, _ = register("ana")
    make_project(client, location_text="The Depot", lat=HERE[0], lon=HERE[1])
    rows = _suggest(client, "depot")
    assert rows and set(rows[0]) == {"id", "label", "event_count"}


def test_suggestions_are_capped(register):
    client, _, _ = register("ana")
    for i in range(12):
        make_project(client, title=f"P{i}", location_text=f"Hall {i}")
    assert len(_suggest(client, "hall")) <= 10


def test_a_guest_can_read_suggestions(api):
    """Guests create projects too — the field must help them the same way."""
    token = api.post("/api/auth/guest").json()["token"]
    r = api.get("/api/locations", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
