"""Event RSVP / self check-in / leader designation / close / roster.

Everything that happens AT an occurrence is event-scoped now (app/events.py):
idempotent RSVP, the event_over wall on RSVP and self check-in, self check-in
creating a participation + RSVP, re-check-in after checkout, the organizer's
leader toggle + GET /rsvps roster, closing the event (check out + mint), and the
per-event/per-user card state carried on GET /projects (card["event"]).
"""
from datetime import datetime, timedelta, timezone

from app import db


# ---- helpers ----------------------------------------------------------------

def _future(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def make_project(client, expected_minutes=120, **extra):
    body = {
        "title": "Beach Cleanup",
        "location_text": "The Beach",
        "starts_at": _future(),
        "expected_minutes": expected_minutes,
    }
    body.update(extra)
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def first_event(detail):
    return detail["events"][0]


def insert_participation(event_id, user_id, waiver_id, minutes_ago=0, open=True):
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


def _make_event_over(event_id):
    """Force an event past starts_at + expected_minutes (over, not completed)."""
    db.query(
        "UPDATE events SET starts_at = now() - make_interval(mins => expected_minutes + 60) "
        "WHERE id = %s",
        (event_id,),
    )


def _backdate(participation_id, minutes):
    db.query(
        "UPDATE participations SET checked_in_at = now() - make_interval(mins => %s) "
        "WHERE id = %s",
        (minutes, participation_id),
    )


# ---- RSVP: idempotent + detail flags ----------------------------------------

def test_rsvp_creates_and_is_idempotent(register):
    owner, _o, _ = register("owner_a")
    vol, v, _ = register("vol_a")
    eid = first_event(make_project(owner))["id"]

    detail = vol.get(f"/api/events/{eid}").json()
    assert detail["my_rsvp"] is None
    assert detail["is_over"] is False

    r = vol.post(f"/api/events/{eid}/rsvp")
    assert r.status_code == 200, r.text
    assert r.json()["my_rsvp"] == {"is_leader": False}

    r2 = vol.post(f"/api/events/{eid}/rsvp")
    assert r2.status_code == 200, r2.text
    cnt = db.query_one(
        "SELECT COUNT(*) AS c FROM rsvps WHERE event_id=%s AND user_id=%s",
        (eid, v["id"]),
    )["c"]
    assert cnt == 1


def test_rsvp_missing_event_404(register):
    vol, _v, _ = register("vol_b")
    r = vol.post("/api/events/999999/rsvp")
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


# ---- event_over blocks RSVP and self check-in -------------------------------

def test_event_over_blocks_rsvp(register):
    owner, _o, _ = register("owner_c")
    vol, _v, _ = register("vol_c")
    eid = first_event(make_project(owner))["id"]
    _make_event_over(eid)

    assert vol.get(f"/api/events/{eid}").json()["is_over"] is True
    r = vol.post(f"/api/events/{eid}/rsvp")
    assert r.status_code == 409 and r.json()["detail"] == "event_over"


def test_event_over_blocks_checkin(register):
    owner, _o, _ = register("owner_d")
    vol, _v, _ = register("vol_d")
    eid = first_event(make_project(owner))["id"]
    _make_event_over(eid)

    r = vol.post(f"/api/events/{eid}/checkin")
    assert r.status_code == 409 and r.json()["detail"] == "event_over"


def test_completed_event_is_over(register):
    owner, _o, _ = register("owner_e")
    vol, _v, _ = register("vol_e")
    eid = first_event(make_project(owner))["id"]
    assert owner.post(f"/api/events/{eid}/close").status_code == 200

    assert vol.get(f"/api/events/{eid}").json()["is_over"] is True
    assert vol.post(f"/api/events/{eid}/rsvp").status_code == 409
    assert vol.post(f"/api/events/{eid}/checkin").status_code == 409


# ---- self check-in: creates a participation + an RSVP -----------------------

def test_self_checkin_creates_participation_and_rsvp(register):
    owner, _o, _ = register("owner_f")
    vol, v, _ = register("vol_f")
    proj = make_project(owner)
    eid = first_event(proj)["id"]

    r = vol.post(f"/api/events/{eid}/checkin")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["my_open_participation"] is not None
    assert detail["my_rsvp"] == {"is_leader": False}

    part = db.query_one(
        "SELECT waiver_id FROM participations WHERE event_id=%s AND user_id=%s",
        (eid, v["id"]),
    )
    assert part["waiver_id"] == proj["waiver"]["id"]

    rsvp = db.query_one(
        "SELECT 1 FROM rsvps WHERE event_id=%s AND user_id=%s", (eid, v["id"])
    )
    assert rsvp is not None


def test_self_checkin_twice_while_open_409(register):
    owner, _o, _ = register("owner_g")
    vol, _v, _ = register("vol_g")
    eid = first_event(make_project(owner))["id"]

    assert vol.post(f"/api/events/{eid}/checkin").status_code == 200
    r = vol.post(f"/api/events/{eid}/checkin")
    assert r.status_code == 409 and r.json()["detail"] == "already_checked_in"


def test_self_checkin_missing_event_404(register):
    vol, _v, _ = register("vol_h")
    assert vol.post("/api/events/999999/checkin").status_code == 404


def test_recheckin_after_checkout(register):
    owner, _o, _ = register("owner_i")
    vol, _v, _ = register("vol_i")
    eid = first_event(make_project(owner))["id"]

    detail = vol.post(f"/api/events/{eid}/checkin").json()
    part_id = detail["my_open_participation"]["id"]
    assert vol.post(f"/api/participations/{part_id}/checkout").status_code == 200

    r = vol.post(f"/api/events/{eid}/checkin")
    assert r.status_code == 200, r.text
    assert r.json()["my_open_participation"] is not None
    assert r.json()["my_rsvp"] == {"is_leader": False}


# ---- event-leader designation (organizer toggles the flag) ------------------

def test_organizer_toggles_leader(register):
    owner, _o, _ = register("owner_j")
    vol, v, _ = register("vol_j")
    eid = first_event(make_project(owner))["id"]
    assert vol.post(f"/api/events/{eid}/rsvp").status_code == 200

    r = owner.post(f"/api/events/{eid}/rsvps/{v['id']}/leader", json={"is_leader": True})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["user"]["id"] == v["id"]
    assert rows[0]["is_leader"] is True

    assert vol.get(f"/api/events/{eid}").json()["my_rsvp"] == {"is_leader": True}

    r2 = owner.post(f"/api/events/{eid}/rsvps/{v['id']}/leader", json={"is_leader": False})
    assert r2.json()[0]["is_leader"] is False


def test_non_organizer_cannot_toggle_leader(register):
    owner, _o, _ = register("owner_k")
    vol, v, _ = register("vol_k")
    eid = first_event(make_project(owner))["id"]
    assert vol.post(f"/api/events/{eid}/rsvp").status_code == 200

    r = vol.post(f"/api/events/{eid}/rsvps/{v['id']}/leader", json={"is_leader": True})
    assert r.status_code == 403 and r.json()["detail"] == "not_a_leader"


def test_toggle_leader_for_non_rsvp_404(register):
    owner, _o, _ = register("owner_l")
    vol, v, _ = register("vol_l")
    eid = first_event(make_project(owner))["id"]
    r = owner.post(f"/api/events/{eid}/rsvps/{v['id']}/leader", json={"is_leader": True})
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


# ---- GET /events/{id}/rsvps roster ------------------------------------------

def test_rsvps_list_shape_and_state(register):
    owner, _o, _ = register("owner_m")
    vol1, v1, _ = register("vol_m1")
    vol2, v2, _ = register("vol_m2")
    eid = first_event(make_project(owner))["id"]

    assert vol1.post(f"/api/events/{eid}/checkin").status_code == 200
    assert vol2.post(f"/api/events/{eid}/rsvp").status_code == 200

    rows = owner.get(f"/api/events/{eid}/rsvps").json()
    by_uid = {row["user"]["id"]: row for row in rows}
    assert set(by_uid) == {v1["id"], v2["id"]}

    row1 = by_uid[v1["id"]]
    assert set(row1) == {"user", "is_leader", "is_checked_in", "has_participated",
                         "is_attested", "created_at"}
    assert set(row1["user"]) == {"id", "display_name"}
    assert row1["is_checked_in"] is True
    assert row1["has_participated"] is True
    # Nobody scanned anybody here — the button is an assertion (CHECKIN_PROOF.md §1).
    assert row1["is_attested"] is False

    row2 = by_uid[v2["id"]]
    assert row2["is_checked_in"] is False
    assert row2["has_participated"] is False
    assert row2["is_attested"] is False

    part = vol1.get(f"/api/events/{eid}").json()["my_open_participation"]
    assert vol1.post(f"/api/participations/{part['id']}/checkout").status_code == 200
    rows = owner.get(f"/api/events/{eid}/rsvps").json()
    row1 = {row["user"]["id"]: row for row in rows}[v1["id"]]
    assert row1["is_checked_in"] is False
    assert row1["has_participated"] is True


def test_rsvps_list_forbidden_for_non_organizer(register):
    owner, _o, _ = register("owner_n")
    vol, _v, _ = register("vol_n")
    eid = first_event(make_project(owner))["id"]
    assert vol.post(f"/api/events/{eid}/rsvp").status_code == 200

    r = vol.get(f"/api/events/{eid}/rsvps")
    assert r.status_code == 403 and r.json()["detail"] == "not_a_leader"


def test_qr_checkin_appears_in_rsvps(register):
    owner, _o, _ = register("owner_o")
    vol, v, _ = register("vol_o")
    ev = first_event(make_project(owner))
    eid, code = ev["id"], ev["checkin_code"]

    assert vol.post(f"/api/checkin/{code}/agree").status_code == 201
    rows = owner.get(f"/api/events/{eid}/rsvps").json()
    assert any(row["user"]["id"] == v["id"] for row in rows)


# ---- close (event lifecycle) ------------------------------------------------

def test_close_empty_event_ok(register):
    client, _u, _ = register("owner_close_a")
    eid = first_event(make_project(client))["id"]
    r = client.post(f"/api/events/{eid}/close")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_close_checks_out_open_participations(register):
    owner, _o, _ = register("owner_close_b")
    vol, vu, _ = register("volunteer_close_b")
    detail = make_project(owner, expected_minutes=120)
    eid, wid = first_event(detail)["id"], detail["waiver"]["id"]
    insert_participation(eid, vu["id"], wid, minutes_ago=90, open=True)

    assert owner.post(f"/api/events/{eid}/close").status_code == 200

    part = db.query_one("SELECT * FROM participations WHERE event_id=%s", (eid,))
    assert part["checked_out_at"] is not None
    assert part["minutes"] >= 90
    assert part["tokens_awarded"] == 2          # 90 min -> 2 tokens
    assert _balance(vu["id"]) == 2              # minted at checkout

    roster = owner.get(f"/api/events/{eid}/roster").json()
    assert roster["checked_in_count"] == 0


def test_close_non_leader_403_and_double_close_409(register):
    owner, _o, _ = register("owner_close_c")
    other, _x, _ = register("stranger_close_c")
    eid = first_event(make_project(owner))["id"]
    assert other.post(f"/api/events/{eid}/close").status_code == 403
    assert owner.post(f"/api/events/{eid}/close").status_code == 200
    r = owner.post(f"/api/events/{eid}/close")
    assert r.status_code == 409 and r.json()["detail"] == "event_not_open"


# ---- roster -----------------------------------------------------------------

def test_roster_leader_only(register):
    owner, _o, _ = register("owner_roster")
    vol, vu, _ = register("volunteer_roster")
    other, _x, _ = register("stranger_roster")
    detail = make_project(owner)
    eid, wid = first_event(detail)["id"], detail["waiver"]["id"]
    insert_participation(eid, vu["id"], wid, minutes_ago=10, open=True)

    roster = owner.get(f"/api/events/{eid}/roster").json()
    assert roster["checked_in_count"] == 1
    assert len(roster["participations"]) == 1
    row = roster["participations"][0]
    assert row["user"] == {"id": vu["id"], "display_name": "volunteer_roster"}
    assert "id" in row and row["checked_out_at"] is None

    assert other.get(f"/api/events/{eid}/roster").status_code == 403


# ---- GET /projects cards carry per-user action state (no N+1) ----------------

def _card(client, project_id, scope="upcoming"):
    for row in client.get(f"/api/projects?scope={scope}").json():
        if row["id"] == project_id:
            return row
    return None


def test_list_cards_carry_action_state(register):
    owner, _o, _ = register("owner_list_a")
    vol, _v, _ = register("vol_list_a")
    proj = make_project(owner)
    pid, eid = proj["id"], first_event(proj)["id"]

    ev = _card(vol, pid)["event"]
    assert ev["is_over"] is False
    assert ev["my_rsvp"] is None
    assert ev["my_open_participation"] is None
    assert ev["my_hours_here"] == 0.0

    assert vol.post(f"/api/events/{eid}/rsvp").status_code == 200
    ev = _card(vol, pid)["event"]
    assert ev["my_rsvp"] == {"is_leader": False}
    assert ev["my_open_participation"] is None

    part = vol.post(f"/api/events/{eid}/checkin").json()["my_open_participation"]
    ev = _card(vol, pid)["event"]
    assert ev["my_open_participation"] is not None
    assert ev["my_open_participation"]["id"] == part["id"]

    _backdate(part["id"], 90)
    assert vol.post(f"/api/participations/{part['id']}/checkout").status_code == 200
    ev = _card(vol, pid)["event"]
    assert ev["my_open_participation"] is None
    assert ev["my_hours_here"] == 1.5


def test_list_card_state_is_per_user(register):
    owner, _o, _ = register("owner_list_b")
    vol_a, _a, _ = register("vol_list_b_a")
    vol_b, _b, _ = register("vol_list_b_b")
    proj = make_project(owner)
    pid, eid = proj["id"], first_event(proj)["id"]

    assert vol_a.post(f"/api/events/{eid}/rsvp").status_code == 200
    assert _card(vol_a, pid)["event"]["my_rsvp"] == {"is_leader": False}
    assert _card(vol_b, pid)["event"]["my_rsvp"] is None


def test_list_card_is_over_reflects_backdated_event(register):
    owner, _o, _ = register("owner_list_c")
    vol, _v, _ = register("vol_list_c")
    proj = make_project(owner)
    pid, eid = proj["id"], first_event(proj)["id"]
    _make_event_over(eid)

    assert _card(vol, pid, scope="upcoming") is None
    card = _card(vol, pid, scope="past")
    assert card is not None
    assert card["event"]["is_over"] is True
