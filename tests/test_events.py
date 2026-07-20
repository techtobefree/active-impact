"""Events (occurrences): a service project has MANY events.

Covers the decoupled domain: POST /projects/{id}/events (leader-only) adds an
occurrence; project detail lists all events (each with is_over); GET /events/{id}
returns event detail + its project summary + waiver; and the feed scopes embed the
right event -- upcoming shows the SOONEST not-over event, past shows a project
whose only event ended.
"""
from datetime import datetime, timedelta, timezone

from app import db


def _future(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


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


def add_event(client, project_id, location_text="North Field", starts_at=None,
              expected_minutes=90):
    r = client.post(
        f"/api/projects/{project_id}/events",
        json={
            "location_text": location_text,
            "starts_at": starts_at or _future(days=2),
            "expected_minutes": expected_minutes,
        },
    )
    return r


def _make_event_over(event_id):
    """Force an event past starts_at + expected_minutes (over, not completed)."""
    db.query(
        "UPDATE events SET starts_at = now() - make_interval(mins => expected_minutes + 60) "
        "WHERE id = %s",
        (event_id,),
    )


def _card(client, project_id, scope="upcoming"):
    for c in client.get(f"/api/projects?scope={scope}").json():
        if c["id"] == project_id:
            return c
    return None


# ---- create seeds the first event -------------------------------------------

def test_create_seeds_first_event(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner)
    assert len(proj["events"]) == 1
    ev = proj["events"][0]
    assert ev["location_text"] == "The Beach"
    assert ev["expected_minutes"] == 120
    assert ev["status"] == "open"
    assert ev["is_over"] is False
    assert ev["checked_in_count"] == 0
    assert ev["my_rsvp"] is None
    assert ev["my_open_participation"] is None
    assert ev["my_hours_here"] == 0.0
    assert len(ev["checkin_code"]) == 8   # leader sees the code


# ---- add another occurrence (leader only) -----------------------------------

def test_add_event_leader_only_and_shape(register):
    owner, _o, _ = register("owner")
    other, _x, _ = register("stranger")
    proj = make_project(owner)
    pid = proj["id"]

    assert add_event(other, pid).status_code == 403   # not a leader

    r = add_event(owner, pid, location_text="North Field", expected_minutes=90)
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["location_text"] == "North Field"
    assert ev["expected_minutes"] == 90
    assert ev["status"] == "open"
    assert ev["is_over"] is False
    assert len(ev["checkin_code"]) == 8

    detail = owner.get(f"/api/projects/{pid}").json()
    assert len(detail["events"]) == 2


def test_add_event_missing_project_404(register):
    owner, _o, _ = register("owner")
    assert add_event(owner, 999999).status_code == 404


# ---- GET /events/{id} : detail + project summary + waiver -------------------

def test_event_detail_endpoint(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner)
    ev = proj["events"][0]

    body = owner.get(f"/api/events/{ev['id']}").json()
    assert body["id"] == ev["id"]
    assert body["location_text"] == "The Beach"
    assert body["status"] == "open"
    assert body["project"]["id"] == proj["id"]
    assert body["project"]["title"] == "Beach Cleanup"
    assert body["waiver"]["version"] == 1
    assert "text" in body["waiver"]
    assert body["am_leader"] is True
    assert "checkin_code" in body

    other, _x, _ = register("stranger")
    seen = other.get(f"/api/events/{ev['id']}").json()
    assert seen["am_leader"] is False
    assert "checkin_code" not in seen

    assert owner.get("/api/events/999999").status_code == 404


# ---- feed scopes: which event a card embeds ---------------------------------

def test_two_events_upcoming_and_past_shows_soonest_upcoming(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner, starts_at=_future(days=3))   # event A: upcoming
    pid = proj["id"]
    a_eid = proj["events"][0]["id"]
    b = add_event(owner, pid, starts_at=_future(days=1)).json()
    _make_event_over(b["id"])                               # event B: now past

    # Under upcoming, the project appears once, embedding the only not-over event.
    card = _card(owner, pid, scope="upcoming")
    assert card is not None
    assert card["event"]["id"] == a_eid
    assert card["event"]["is_over"] is False
    # ...and NOT under past, because it still has a not-over event.
    assert _card(owner, pid, scope="past") is None


def test_upcoming_card_shows_soonest_not_over_event(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner, starts_at=_future(days=5))   # A: day 5
    pid = proj["id"]
    b = add_event(owner, pid, starts_at=_future(days=2)).json()   # B: day 2 (sooner)

    card = _card(owner, pid, scope="upcoming")
    assert card["event"]["id"] == b["id"]                  # soonest not-over wins


def test_project_only_event_ended_lists_under_past(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner)
    pid = proj["id"]
    eid = proj["events"][0]["id"]
    _make_event_over(eid)

    assert _card(owner, pid, scope="upcoming") is None
    card = _card(owner, pid, scope="past")
    assert card is not None
    assert card["event"]["id"] == eid
    assert card["event"]["is_over"] is True


def test_past_card_shows_most_recent_event(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner, starts_at=_future(days=1))
    pid = proj["id"]
    older = proj["events"][0]["id"]
    newer = add_event(owner, pid, starts_at=_future(days=2)).json()["id"]
    # End both; the past card embeds the most-recent (newer) event.
    _make_event_over(older)
    _make_event_over(newer)

    card = _card(owner, pid, scope="past")
    assert card is not None
    assert card["event"]["id"] == newer


def test_project_detail_splits_upcoming_and_past_events(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner, starts_at=_future(days=2))
    pid = proj["id"]
    past_eid = proj["events"][0]["id"]
    upcoming_eid = add_event(owner, pid, starts_at=_future(days=4)).json()["id"]
    _make_event_over(past_eid)

    events = owner.get(f"/api/projects/{pid}").json()["events"]
    assert len(events) == 2
    by_id = {e["id"]: e for e in events}
    assert by_id[past_eid]["is_over"] is True
    assert by_id[upcoming_eid]["is_over"] is False
    # not-over events are listed before over ones.
    assert events[0]["id"] == upcoming_eid
    assert events[1]["id"] == past_eid
