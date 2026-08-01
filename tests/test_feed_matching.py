"""FEED.md §4 — which event was this service logged at?

The merge of the two feeds rests entirely on this guess being defensible, so the
priority order (explicit > checked_in > participated > rsvp > nearby > none), the
live time window, and the distance bound are all asserted here. Invariants
F-I2, F-I3, F-I4, F-I7 live in this file.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.matching import MAX_MATCH_KM, candidate_rows, reason_for, resolve_event

from tests.test_events import make_project

# Two points ~1.1 km apart (0.01° of latitude), and one ~110 km away.
HERE = (40.7128, -74.0060)
NEAR = (40.7228, -74.0060)
FAR = (41.7128, -74.0060)


# ---- helpers ----------------------------------------------------------------

def _now_event(client, title="Now Project", minutes_ago=30, expected=120, **extra):
    """A project whose only event started `minutes_ago` and runs `expected` mins."""
    starts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    p = make_project(client, title=title, starts_at=starts, expected_minutes=expected, **extra)
    return p, p["events"][0]


def _set_coords(event_id, lat, lon):
    db.query("UPDATE events SET lat = %s, lon = %s WHERE id = %s", (lat, lon, event_id))


def _shift(event_id, **delta):
    """Move an event's start time by a timedelta (to leave/enter the window)."""
    db.query(
        "UPDATE events SET starts_at = starts_at + %s WHERE id = %s",
        (timedelta(**delta), event_id),
    )


# ---- the live window (F-I2) -------------------------------------------------

def test_no_candidates_no_match(register):
    """Nothing happening anywhere -> unattached, not a wild guess."""
    client, user, _ = register("ana")
    assert resolve_event(user["id"], *HERE) == (None, None)


def test_geo_matches_an_event_in_progress(register):
    client, user, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    assert resolve_event(user["id"], *NEAR) == (ev["id"], "nearby")


def test_geo_ignores_an_event_that_ended_long_ago(register):
    """Six hours of grace after the expected end, then the event stops collecting."""
    client, user, _ = register("ana")
    _, ev = _now_event(client, minutes_ago=30, expected=60)
    _set_coords(ev["id"], *HERE)
    _shift(ev["id"], hours=-8)  # ended ~7h ago -> past WINDOW_AFTER
    assert resolve_event(user["id"], *NEAR) == (None, None)


def test_geo_matches_an_event_that_ended_within_the_grace(register):
    """Posting the photo on the couch that evening still finds the event."""
    client, user, _ = register("ana")
    _, ev = _now_event(client, minutes_ago=30, expected=60)
    _set_coords(ev["id"], *HERE)
    _shift(ev["id"], hours=-3)  # ended ~2.5h ago -> inside the grace
    assert resolve_event(user["id"], *NEAR) == (ev["id"], "nearby")


def test_geo_ignores_an_event_that_is_days_away(register):
    client, user, _ = register("ana")
    _, ev = _now_event(client, minutes_ago=-60 * 24 * 3)  # starts in 3 days
    _set_coords(ev["id"], *HERE)
    assert resolve_event(user["id"], *NEAR) == (None, None)


def test_geo_matches_an_event_about_to_start(register):
    """Two hours of lead-in: people arrive and start early."""
    client, user, _ = register("ana")
    _, ev = _now_event(client, minutes_ago=-60)  # starts in an hour
    _set_coords(ev["id"], *HERE)
    assert resolve_event(user["id"], *NEAR) == (ev["id"], "nearby")


def test_a_completed_event_still_collects_photos(register):
    """A leader who closed the event ten minutes ago must still get its photos."""
    client, user, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    db.query("UPDATE events SET status = 'completed' WHERE id = %s", (ev["id"],))
    assert resolve_event(user["id"], *NEAR) == (ev["id"], "nearby")


# ---- the distance bound (F-I4) ----------------------------------------------

def test_geo_never_matches_beyond_the_bound(register):
    client, user, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    assert resolve_event(user["id"], *FAR) == (None, None)  # ~110 km


def test_geo_needs_coordinates_on_both_sides(register):
    client, user, _ = register("ana")
    _, ev = _now_event(client)  # event has no coordinates
    assert resolve_event(user["id"], *NEAR) == (None, None)
    _set_coords(ev["id"], *HERE)
    assert resolve_event(user["id"], None, None) == (None, None)  # no device GPS


def test_geo_picks_the_nearest_of_several(register):
    client, user, _ = register("ana")
    _, far_ev = _now_event(client, title="Far", minutes_ago=45)
    _, near_ev = _now_event(client, title="Near", minutes_ago=15)
    _set_coords(far_ev["id"], 40.7328, -74.0060)   # ~2.2 km from NEAR
    _set_coords(near_ev["id"], 40.7238, -74.0060)  # ~0.1 km from NEAR
    assert resolve_event(user["id"], *NEAR) == (near_ev["id"], "nearby")


def test_max_match_km_is_the_documented_bound():
    assert MAX_MATCH_KM == 5.0


# ---- priority order (F-I3) --------------------------------------------------

def test_open_participation_beats_a_nearer_event(register):
    """Standing at an event beats being close to a different one."""
    client, user, _ = register("ana")
    _, mine = _now_event(client, title="Mine")
    _, other = _now_event(client, title="Other")
    _set_coords(mine["id"], 41.0, -74.0)      # far from the phone
    _set_coords(other["id"], *HERE)           # right next to the phone
    client.post(f"/api/events/{mine['id']}/checkin")
    assert resolve_event(user["id"], *NEAR) == (mine["id"], "checked_in")


def test_checked_out_participation_still_wins_over_geo(register):
    client, user, _ = register("ana")
    _, mine = _now_event(client, title="Mine")
    _, other = _now_event(client, title="Other")
    _set_coords(other["id"], *HERE)
    r = client.post(f"/api/events/{mine['id']}/checkin")
    pid = r.json()["my_open_participation"]["id"]
    client.post(f"/api/participations/{pid}/checkout")
    assert resolve_event(user["id"], *NEAR) == (mine["id"], "participated")


def test_open_participation_beats_a_checked_out_one(register):
    client, user, _ = register("ana")
    _, first = _now_event(client, title="First", minutes_ago=90)
    _, second = _now_event(client, title="Second", minutes_ago=10)
    r = client.post(f"/api/events/{first['id']}/checkin")
    client.post(f"/api/participations/{r.json()['my_open_participation']['id']}/checkout")
    client.post(f"/api/events/{second['id']}/checkin")
    assert resolve_event(user["id"], None, None) == (second["id"], "checked_in")


def test_rsvp_matches_when_nothing_stronger_does(register):
    client, user, _ = register("ana")
    _, ev = _now_event(client)
    client.post(f"/api/events/{ev['id']}/rsvp")
    assert resolve_event(user["id"], None, None) == (ev["id"], "rsvp")


def test_participation_beats_an_rsvp_elsewhere(register):
    client, user, _ = register("ana")
    _, rsvped = _now_event(client, title="RSVP'd")
    _, joined = _now_event(client, title="Joined")
    client.post(f"/api/events/{rsvped['id']}/rsvp")
    client.post(f"/api/events/{joined['id']}/checkin")
    assert resolve_event(user["id"], None, None) == (joined["id"], "checked_in")


def test_rsvp_beats_a_geo_match_elsewhere(register):
    client, user, _ = register("ana")
    _, rsvped = _now_event(client, title="RSVP'd")
    _, near = _now_event(client, title="Near")
    _set_coords(near["id"], *HERE)
    client.post(f"/api/events/{rsvped['id']}/rsvp")
    assert resolve_event(user["id"], *NEAR) == (rsvped["id"], "rsvp")


def test_someone_elses_checkin_does_not_match_me(register):
    """The signals are per-author — Ben's check-in is not Ana's evidence."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    _, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    assert resolve_event(ben_u["id"], None, None) == (None, None)


# ---- explicit (F-I2's exception) --------------------------------------------

def test_explicit_beats_everything(register):
    client, user, _ = register("ana")
    _, checked_into = _now_event(client, title="Checked into")
    _, chosen = _now_event(client, title="Chosen")
    client.post(f"/api/events/{checked_into['id']}/checkin")
    assert resolve_event(user["id"], *NEAR, explicit_event_id=chosen["id"]) == (
        chosen["id"], "explicit",
    )


def test_explicit_may_name_an_event_outside_the_window(register):
    """A deliberate choice is trusted over the clock — but only for an event I was at."""
    client, user, _ = register("ana")
    _, ev = _now_event(client)
    client.post(f"/api/events/{ev['id']}/checkin")
    _shift(ev["id"], days=-30)
    assert resolve_event(user["id"], None, None, explicit_event_id=ev["id"]) == (
        ev["id"], "explicit",
    )


def test_explicit_rejects_an_unknown_event(register):
    client, user, _ = register("ana")
    with pytest.raises(LookupError):
        resolve_event(user["id"], None, None, explicit_event_id=999999)


def test_explicit_falls_back_when_the_event_is_stale_and_not_mine(register):
    """Naming a long-dead event I never attended is a stale client, not a choice."""
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    _, ev = _now_event(ana)
    _shift(ev["id"], days=-30)
    assert resolve_event(ben_u["id"], None, None, explicit_event_id=ev["id"]) == (None, None)


# ---- an unlocated event has an UNKNOWN distance, not a confident one --------

def test_an_event_without_coordinates_reports_no_distance(register):
    """Postgres GREATEST/LEAST ignore NULLs, so the obvious clamp turned an
    unlocated event into "20015 km away" (acos(-1)). Distance must stay NULL."""
    client, user, _ = register("ana")
    _, ev = _now_event(client)  # no coordinates
    rows = candidate_rows(user["id"], *HERE)
    assert [r["distance_km"] for r in rows] == [None]
    assert reason_for(rows[0]) is None


def test_no_device_gps_reports_no_distance(register):
    client, user, _ = register("ana")
    _, ev = _now_event(client)
    _set_coords(ev["id"], *HERE)
    rows = candidate_rows(user["id"], None, None)
    assert [r["distance_km"] for r in rows] == [None]
