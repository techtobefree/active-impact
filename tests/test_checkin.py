"""Check-in / check-out: code resolution, the waiver signature, and the mint.

Owns the check-in invariants: I11 (a code resolves only to an OPEN project),
I6 (a participation pins the CURRENT waiver version — and a fresh check-in after
a waiver edit pins the NEW version), I3 (one open participation per project/user,
re-check-in allowed after checkout), I12 (the 29/30/90-minute boundaries and the
mint cap 600@120 -> 4), and I1 (a zero-minute checkout mints nothing and leaves
balance == ledger sum).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import db


# ---- helpers ----------------------------------------------------------------

def _future(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def make_project(client, expected_minutes=120, waiver_text=None, **extra):
    body = {
        "title": "Beach Cleanup",
        "location_text": "The Beach",
        "starts_at": _future(),
        "expected_minutes": expected_minutes,
    }
    if waiver_text is not None:
        body["waiver_text"] = waiver_text
    body.update(extra)
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _backdate(participation_id, minutes):
    """Pretend the volunteer checked in ``minutes`` ago (I12 mint boundaries)."""
    db.query(
        "UPDATE participations SET checked_in_at = now() - make_interval(mins => %s) "
        "WHERE id = %s",
        (minutes, participation_id),
    )


def _balance(uid):
    return db.query_one("SELECT balance FROM users WHERE id=%s", (uid,))["balance"]


def _ledger_sum(uid):
    inflow = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE to_user_id=%s",
        (uid,),
    )["s"]
    outflow = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE from_user_id=%s",
        (uid,),
    )["s"]
    return int(inflow) - int(outflow)


# ---- resolve a scanned code (I11) -------------------------------------------

def test_resolve_valid_code(register):
    owner, _o, _ = register("owner")
    detail = make_project(owner)
    code = detail["checkin_code"]

    vol, _v, _ = register("vol")
    r = vol.get(f"/api/checkin/{code}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project"]["id"] == detail["id"]
    assert body["project"]["title"] == "Beach Cleanup"
    assert body["waiver"]["version"] == 1
    assert body["waiver"]["id"] == detail["waiver"]["id"]
    assert "text" in body["waiver"]
    assert body["my_open_participation"] is None


def test_resolve_invalid_code_404(register):
    vol, _v, _ = register("vol")
    r = vol.get("/api/checkin/does-not-exist")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_code"


def test_resolve_closed_project_is_invalid_code(register):
    owner, _o, _ = register("owner")
    detail = make_project(owner)
    code = detail["checkin_code"]
    assert owner.post(f"/api/projects/{detail['id']}/close").status_code == 200
    # I11: a completed project's code no longer resolves.
    r = owner.get(f"/api/checkin/{code}")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_code"


def test_resolve_shows_my_open_participation(register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    vol, _v, _ = register("vol")
    part = vol.post(f"/api/checkin/{code}/agree").json()
    body = vol.get(f"/api/checkin/{code}").json()
    assert body["my_open_participation"]["id"] == part["id"]
    assert "checked_in_at" in body["my_open_participation"]


# ---- agree = check-in, pinned to the current waiver (I6) ---------------------

def test_agree_pins_current_waiver(register):
    owner, _o, _ = register("owner")
    detail = make_project(owner)
    code, current_wid = detail["checkin_code"], detail["waiver"]["id"]

    vol, vu, _ = register("vol")
    r = vol.post(f"/api/checkin/{code}/agree")
    assert r.status_code == 201, r.text
    part = r.json()
    assert part["waiver_id"] == current_wid
    assert part["user_id"] == vu["id"]
    assert part["project_id"] == detail["id"]
    assert part["checked_out_at"] is None
    assert part["minutes"] is None and part["tokens_awarded"] is None
    # I6: the pinned waiver belongs to this participation's project.
    w = db.query_one("SELECT project_id FROM waivers WHERE id=%s", (part["waiver_id"],))
    assert w["project_id"] == detail["id"]


def test_agree_after_waiver_edit_pins_new_version(register):
    owner, _o, _ = register("owner")
    detail = make_project(owner)
    code, pid, v1_id = detail["checkin_code"], detail["id"], detail["waiver"]["id"]

    vol, _v, _ = register("vol")
    p1 = vol.post(f"/api/checkin/{code}/agree").json()
    assert p1["waiver_id"] == v1_id
    # Check out so a re-check-in is allowed.
    assert vol.post(f"/api/participations/{p1['id']}/checkout").status_code == 200

    # Leader edits the waiver text -> version 2 (I5).
    upd = owner.patch(f"/api/projects/{pid}", json={"waiver_text": "New terms v2."})
    v2_id = upd.json()["waiver"]["id"]
    assert v2_id != v1_id

    # I6: the fresh check-in pins the NEW version, not the one signed before.
    p2 = vol.post(f"/api/checkin/{code}/agree").json()
    assert p2["waiver_id"] == v2_id


def test_agree_invalid_code_404(register):
    vol, _v, _ = register("vol")
    r = vol.post("/api/checkin/nope/agree")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_code"


def test_agree_closed_project_404(register):
    owner, _o, _ = register("owner")
    detail = make_project(owner)
    code = detail["checkin_code"]
    owner.post(f"/api/projects/{detail['id']}/close")
    r = owner.post(f"/api/checkin/{code}/agree")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_code"


def test_leader_checks_in_through_agree(register):
    owner, ou, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    # The leader has the code on their lead screen and signs the same way.
    r = owner.post(f"/api/checkin/{code}/agree")
    assert r.status_code == 201
    assert r.json()["user_id"] == ou["id"]


# ---- one open per (project, user), re-check-in after checkout (I3) ----------

def test_duplicate_agree_409(register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    vol, _v, _ = register("vol")
    assert vol.post(f"/api/checkin/{code}/agree").status_code == 201
    r = vol.post(f"/api/checkin/{code}/agree")
    assert r.status_code == 409 and r.json()["detail"] == "already_checked_in"


def test_recheckin_allowed_after_checkout(register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    vol, _v, _ = register("vol")
    p1 = vol.post(f"/api/checkin/{code}/agree").json()
    assert vol.post(f"/api/participations/{p1['id']}/checkout").status_code == 200
    r = vol.post(f"/api/checkin/{code}/agree")
    assert r.status_code == 201
    assert r.json()["id"] != p1["id"]


# ---- checkout permissions ---------------------------------------------------

def test_checkout_by_self(register):
    owner, _o, _ = register("owner")
    code = make_project(owner, expected_minutes=120)["checkin_code"]
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    _backdate(p["id"], 90)
    r = vol.post(f"/api/participations/{p['id']}/checkout")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked_out_at"] is not None
    assert body["minutes"] == 90
    assert body["tokens_awarded"] == 2
    assert _balance(vu["id"]) == 2


def test_checkout_by_leader(register):
    owner, _o, _ = register("owner")
    code = make_project(owner, expected_minutes=120)["checkin_code"]
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    _backdate(p["id"], 30)
    r = owner.post(f"/api/participations/{p['id']}/checkout")
    assert r.status_code == 200, r.text
    assert r.json()["tokens_awarded"] == 1
    assert _balance(vu["id"]) == 1


def test_checkout_by_stranger_403(register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    vol, vu, _ = register("vol")
    stranger, _s, _ = register("stranger")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    r = stranger.post(f"/api/participations/{p['id']}/checkout")
    assert r.status_code == 403 and r.json()["detail"] == "not_allowed"
    # nothing minted, still open
    assert _balance(vu["id"]) == 0
    assert db.query_one(
        "SELECT checked_out_at FROM participations WHERE id=%s", (p["id"],)
    )["checked_out_at"] is None


def test_checkout_missing_404(register):
    vol, _v, _ = register("vol")
    r = vol.post("/api/participations/999999/checkout")
    assert r.status_code == 404 and r.json()["detail"] == "not_found"


def test_checkout_already_checked_out_409(register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    vol, _v, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    assert vol.post(f"/api/participations/{p['id']}/checkout").status_code == 200
    r = vol.post(f"/api/participations/{p['id']}/checkout")
    assert r.status_code == 409 and r.json()["detail"] == "already_checked_out"


# ---- mint math: half-up boundaries + cap (I12) ------------------------------

@pytest.mark.parametrize("minutes,tokens", [(29, 0), (30, 1), (90, 2)])
def test_mint_boundaries(register, minutes, tokens):
    owner, _o, _ = register("owner")
    code = make_project(owner, expected_minutes=120)["checkin_code"]
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    _backdate(p["id"], minutes)
    body = vol.post(f"/api/participations/{p['id']}/checkout").json()
    # actual elapsed is stored truthfully; tokens are the half-up hours.
    assert body["minutes"] == minutes
    assert body["tokens_awarded"] == tokens
    assert _balance(vu["id"]) == tokens


def test_mint_cap_backdated_checkout(register):
    # expected 120 -> credit capped at 240 min -> 4 tokens even after 600 min.
    owner, _o, _ = register("owner")
    code = make_project(owner, expected_minutes=120)["checkin_code"]
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    _backdate(p["id"], 600)
    body = vol.post(f"/api/participations/{p['id']}/checkout").json()
    assert body["minutes"] == 600            # stored truthfully
    assert body["tokens_awarded"] == 4       # NOT 10 — cap protects the supply
    assert _balance(vu["id"]) == 4


# ---- zero-minute checkout mints nothing; balance == ledger sum (I1) ---------

def test_zero_minute_checkout_no_earn_row(register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    # immediate checkout: < 30s elapsed -> 0 minutes, 0 tokens
    body = vol.post(f"/api/participations/{p['id']}/checkout").json()
    assert body["minutes"] == 0
    assert body["tokens_awarded"] == 0
    # I4: minutes + tokens set even though both are zero
    assert body["checked_out_at"] is not None
    # NO 'earn' ledger row was written for a zero mint
    earned = db.query_one(
        "SELECT COUNT(*) AS c FROM token_entries WHERE to_user_id=%s AND kind='earn'",
        (vu["id"],),
    )["c"]
    assert earned == 0
    # I1: balance agrees with the ledger sum (both zero)
    assert _balance(vu["id"]) == 0
    assert _ledger_sum(vu["id"]) == 0


def test_earn_row_matches_balance_after_mint(register):
    # I1 with a real mint: exactly one earn row of the awarded amount.
    owner, _o, _ = register("owner")
    code = make_project(owner, expected_minutes=120)["checkin_code"]
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{code}/agree").json()
    _backdate(p["id"], 90)
    body = vol.post(f"/api/participations/{p['id']}/checkout").json()
    rows = db.query(
        "SELECT amount, participation_id FROM token_entries "
        "WHERE to_user_id=%s AND kind='earn'",
        (vu["id"],),
    )
    assert len(rows) == 1
    assert rows[0]["amount"] == body["tokens_awarded"] == 2
    assert rows[0]["participation_id"] == p["id"]
    assert _balance(vu["id"]) == _ledger_sum(vu["id"]) == 2


# ---- auth wall --------------------------------------------------------------

def test_auth_required(api, register):
    owner, _o, _ = register("owner")
    code = make_project(owner)["checkin_code"]
    assert api.get(f"/api/checkin/{code}").status_code == 401
    assert api.post(f"/api/checkin/{code}/agree").status_code == 401
    assert api.post("/api/participations/1/checkout").status_code == 401
