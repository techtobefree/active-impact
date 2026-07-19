"""RSVP / self check-in state machine + event-leader designation.

Covers app/projects.py's RSVP surface: idempotent RSVP, the project_over wall on
both RSVP and self check-in, self check-in creating a participation + an RSVP row,
re-check-in after checkout while the project is still open, the organizer's
leader toggle (403 for non-organizers, 404 when the target never RSVP'd), and the
GET /rsvps roster shape (is_checked_in / has_participated).
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


def _make_over(project_id):
    """Force the project past starts_at + expected_minutes (over, not completed)."""
    db.query(
        "UPDATE projects SET starts_at = now() - make_interval(mins => expected_minutes + 60) "
        "WHERE id = %s",
        (project_id,),
    )


def _backdate(participation_id, minutes):
    """Pretend the volunteer checked in ``minutes`` ago (drives logged-hours math)."""
    db.query(
        "UPDATE participations SET checked_in_at = now() - make_interval(mins => %s) "
        "WHERE id = %s",
        (minutes, participation_id),
    )


# ---- RSVP: idempotent + detail flags ----------------------------------------

def test_rsvp_creates_and_is_idempotent(register):
    owner, _o, _ = register("owner_a")
    vol, v, _ = register("vol_a")
    proj = make_project(owner)

    # Before RSVP: my_rsvp is null, not over.
    detail = vol.get(f"/api/projects/{proj['id']}").json()
    assert detail["my_rsvp"] is None
    assert detail["is_over"] is False

    r = vol.post(f"/api/projects/{proj['id']}/rsvp")
    assert r.status_code == 200, r.text
    assert r.json()["my_rsvp"] == {"is_leader": False}

    # Second RSVP is a no-op (idempotent) -- still exactly one row.
    r2 = vol.post(f"/api/projects/{proj['id']}/rsvp")
    assert r2.status_code == 200, r2.text
    cnt = db.query_one(
        "SELECT COUNT(*) AS c FROM rsvps WHERE project_id=%s AND user_id=%s",
        (proj["id"], v["id"]),
    )["c"]
    assert cnt == 1


def test_rsvp_missing_project_404(register):
    vol, _v, _ = register("vol_b")
    r = vol.post("/api/projects/999999/rsvp")
    assert r.status_code == 404
    assert r.json()["detail"] == "not_found"


# ---- project_over blocks RSVP and self check-in -----------------------------

def test_project_over_blocks_rsvp(register):
    owner, _o, _ = register("owner_c")
    vol, _v, _ = register("vol_c")
    proj = make_project(owner)
    _make_over(proj["id"])

    detail = vol.get(f"/api/projects/{proj['id']}").json()
    assert detail["is_over"] is True

    r = vol.post(f"/api/projects/{proj['id']}/rsvp")
    assert r.status_code == 409
    assert r.json()["detail"] == "project_over"


def test_project_over_blocks_checkin(register):
    owner, _o, _ = register("owner_d")
    vol, _v, _ = register("vol_d")
    proj = make_project(owner)
    _make_over(proj["id"])

    r = vol.post(f"/api/projects/{proj['id']}/checkin")
    assert r.status_code == 409
    assert r.json()["detail"] == "project_over"


def test_completed_project_is_over(register):
    owner, _o, _ = register("owner_e")
    vol, _v, _ = register("vol_e")
    proj = make_project(owner)
    assert owner.post(f"/api/projects/{proj['id']}/close").status_code == 200

    detail = vol.get(f"/api/projects/{proj['id']}").json()
    assert detail["is_over"] is True
    assert vol.post(f"/api/projects/{proj['id']}/rsvp").status_code == 409
    assert vol.post(f"/api/projects/{proj['id']}/checkin").status_code == 409


# ---- self check-in: creates a participation + an RSVP -----------------------

def test_self_checkin_creates_participation_and_rsvp(register):
    owner, _o, _ = register("owner_f")
    vol, v, _ = register("vol_f")
    proj = make_project(owner)

    r = vol.post(f"/api/projects/{proj['id']}/checkin")
    assert r.status_code == 200, r.text
    detail = r.json()
    # An open participation now exists, and the RSVP row was created silently.
    assert detail["my_open_participation"] is not None
    assert detail["my_rsvp"] == {"is_leader": False}

    # The participation is pinned to the current (highest-version) waiver (I6).
    part = db.query_one(
        "SELECT waiver_id FROM participations WHERE project_id=%s AND user_id=%s",
        (proj["id"], v["id"]),
    )
    assert part["waiver_id"] == proj["waiver"]["id"]

    rsvp = db.query_one(
        "SELECT 1 FROM rsvps WHERE project_id=%s AND user_id=%s",
        (proj["id"], v["id"]),
    )
    assert rsvp is not None


def test_self_checkin_twice_while_open_409(register):
    owner, _o, _ = register("owner_g")
    vol, _v, _ = register("vol_g")
    proj = make_project(owner)

    assert vol.post(f"/api/projects/{proj['id']}/checkin").status_code == 200
    r = vol.post(f"/api/projects/{proj['id']}/checkin")
    assert r.status_code == 409
    assert r.json()["detail"] == "already_checked_in"


def test_self_checkin_missing_project_404(register):
    vol, _v, _ = register("vol_h")
    r = vol.post("/api/projects/999999/checkin")
    assert r.status_code == 404


# ---- re-check-in after checkout while still open ----------------------------

def test_recheckin_after_checkout(register):
    owner, _o, _ = register("owner_i")
    vol, _v, _ = register("vol_i")
    proj = make_project(owner)

    detail = vol.post(f"/api/projects/{proj['id']}/checkin").json()
    part_id = detail["my_open_participation"]["id"]
    assert vol.post(f"/api/participations/{part_id}/checkout").status_code == 200

    # After checkout the project is still open -> check in again is allowed, and
    # the original RSVP persists.
    r = vol.post(f"/api/projects/{proj['id']}/checkin")
    assert r.status_code == 200, r.text
    assert r.json()["my_open_participation"] is not None
    assert r.json()["my_rsvp"] == {"is_leader": False}


# ---- event-leader designation (organizer toggles the flag) ------------------

def test_organizer_toggles_leader(register):
    owner, _o, _ = register("owner_j")
    vol, v, _ = register("vol_j")
    proj = make_project(owner)
    assert vol.post(f"/api/projects/{proj['id']}/rsvp").status_code == 200

    r = owner.post(
        f"/api/projects/{proj['id']}/rsvps/{v['id']}/leader",
        json={"is_leader": True},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["user"]["id"] == v["id"]
    assert rows[0]["is_leader"] is True

    # The volunteer sees the designation on their own detail view.
    assert vol.get(f"/api/projects/{proj['id']}").json()["my_rsvp"] == {"is_leader": True}

    # Toggle back off.
    r2 = owner.post(
        f"/api/projects/{proj['id']}/rsvps/{v['id']}/leader",
        json={"is_leader": False},
    )
    assert r2.json()[0]["is_leader"] is False


def test_non_organizer_cannot_toggle_leader(register):
    owner, _o, _ = register("owner_k")
    vol, v, _ = register("vol_k")
    proj = make_project(owner)
    assert vol.post(f"/api/projects/{proj['id']}/rsvp").status_code == 200

    r = vol.post(
        f"/api/projects/{proj['id']}/rsvps/{v['id']}/leader",
        json={"is_leader": True},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "not_a_leader"


def test_toggle_leader_for_non_rsvp_404(register):
    owner, _o, _ = register("owner_l")
    vol, v, _ = register("vol_l")
    proj = make_project(owner)
    # vol never RSVP'd.
    r = owner.post(
        f"/api/projects/{proj['id']}/rsvps/{v['id']}/leader",
        json={"is_leader": True},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "not_found"


# ---- GET /rsvps roster ------------------------------------------------------

def test_rsvps_list_shape_and_state(register):
    owner, _o, _ = register("owner_m")
    vol1, v1, _ = register("vol_m1")
    vol2, v2, _ = register("vol_m2")
    proj = make_project(owner)

    # vol1 self-checks-in (open participation); vol2 only RSVPs.
    assert vol1.post(f"/api/projects/{proj['id']}/checkin").status_code == 200
    assert vol2.post(f"/api/projects/{proj['id']}/rsvp").status_code == 200

    r = owner.get(f"/api/projects/{proj['id']}/rsvps")
    assert r.status_code == 200, r.text
    rows = r.json()
    by_uid = {row["user"]["id"]: row for row in rows}

    assert set(by_uid) == {v1["id"], v2["id"]}
    # Shape check.
    row1 = by_uid[v1["id"]]
    assert set(row1) == {"user", "is_leader", "is_checked_in", "has_participated", "created_at"}
    assert set(row1["user"]) == {"id", "display_name"}

    assert row1["is_checked_in"] is True
    assert row1["has_participated"] is True

    row2 = by_uid[v2["id"]]
    assert row2["is_checked_in"] is False
    assert row2["has_participated"] is False

    # After vol1 checks out: no longer checked in, but has_participated stays true.
    part = vol1.get(f"/api/projects/{proj['id']}").json()["my_open_participation"]
    assert vol1.post(f"/api/participations/{part['id']}/checkout").status_code == 200
    rows = owner.get(f"/api/projects/{proj['id']}/rsvps").json()
    row1 = {row["user"]["id"]: row for row in rows}[v1["id"]]
    assert row1["is_checked_in"] is False
    assert row1["has_participated"] is True


def test_rsvps_list_forbidden_for_non_organizer(register):
    owner, _o, _ = register("owner_n")
    vol, _v, _ = register("vol_n")
    proj = make_project(owner)
    assert vol.post(f"/api/projects/{proj['id']}/rsvp").status_code == 200

    r = vol.get(f"/api/projects/{proj['id']}/rsvps")
    assert r.status_code == 403
    assert r.json()["detail"] == "not_a_leader"


def test_qr_checkin_appears_in_rsvps(register):
    """A QR (agree) check-in also lands the volunteer in the organizer's RSVP list."""
    owner, _o, _ = register("owner_o")
    vol, v, _ = register("vol_o")
    proj = make_project(owner)
    code = proj["checkin_code"]

    assert vol.post(f"/api/checkin/{code}/agree").status_code == 201
    rows = owner.get(f"/api/projects/{proj['id']}/rsvps").json()
    assert any(row["user"]["id"] == v["id"] for row in rows)


# ---- GET /projects cards carry per-user action state (no N+1) ----------------

def _card(client, project_id, scope="upcoming"):
    """Find this project's card in the GET /projects feed for the requesting user."""
    rows = client.get(f"/api/projects?scope={scope}").json()
    for row in rows:
        if row["id"] == project_id:
            return row
    return None


def test_list_cards_carry_action_state(register):
    owner, _o, _ = register("owner_list_a")
    vol, _v, _ = register("vol_list_a")
    proj = make_project(owner)
    pid = proj["id"]

    # Fresh card: the four fields exist and reflect an untouched project.
    card = _card(vol, pid)
    assert card is not None
    assert card["is_over"] is False
    assert card["my_rsvp"] is None
    assert card["my_open_participation"] is None
    assert card["my_hours_here"] == 0.0

    # After RSVP: the same project's card carries my_rsvp.
    assert vol.post(f"/api/projects/{pid}/rsvp").status_code == 200
    card = _card(vol, pid)
    assert card["my_rsvp"] == {"is_leader": False}
    assert card["my_open_participation"] is None

    # After check-in: the card carries my_open_participation.
    part = vol.post(f"/api/projects/{pid}/checkin").json()["my_open_participation"]
    card = _card(vol, pid)
    assert card["my_open_participation"] is not None
    assert card["my_open_participation"]["id"] == part["id"]

    # After a backdated checkout: my_hours_here reflects the logged time.
    _backdate(part["id"], 90)
    assert vol.post(f"/api/participations/{part['id']}/checkout").status_code == 200
    card = _card(vol, pid)
    assert card["my_open_participation"] is None
    assert card["my_hours_here"] == 1.5


def test_list_card_state_is_per_user(register):
    owner, _o, _ = register("owner_list_b")
    vol_a, _a, _ = register("vol_list_b_a")
    vol_b, _b, _ = register("vol_list_b_b")
    proj = make_project(owner)
    pid = proj["id"]

    assert vol_a.post(f"/api/projects/{pid}/rsvp").status_code == 200

    # vol_a sees their RSVP on the card; vol_b (who never RSVP'd) does not.
    assert _card(vol_a, pid)["my_rsvp"] == {"is_leader": False}
    assert _card(vol_b, pid)["my_rsvp"] is None


def test_list_card_is_over_reflects_backdated_project(register):
    owner, _o, _ = register("owner_list_c")
    vol, _v, _ = register("vol_list_c")
    proj = make_project(owner)
    pid = proj["id"]
    _make_over(pid)

    card = _card(vol, pid)
    assert card is not None
    assert card["is_over"] is True
