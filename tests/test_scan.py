"""Peer check-in — the ATTESTED layer (CHECKIN_PROOF.md).

The button says "I was here"; a scan says "someone else's code says we were both
here". These tests own the proof invariants: I13 (a sighting names two different
users and is unique per event/scanner/subject, so a re-scan is a no-op), I14 (a
scan NEVER creates a participation for the subject -- that would forge their
waiver signature -- while the scanner's own always pins a real waiver, I6), and
I15 (``participations.attested`` is set at insert and flipped by a later scan only
on a still-open participation).
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


def _make_event_over(event_id):
    db.query(
        "UPDATE events SET starts_at = now() - make_interval(mins => expected_minutes + 60) "
        "WHERE id = %s",
        (event_id,),
    )


def qr_token(client):
    return client.get("/api/me").json()["qr_token"]


def _participation(event_id, user_id):
    return db.query_one(
        "SELECT * FROM participations WHERE event_id=%s AND user_id=%s "
        "ORDER BY id DESC LIMIT 1",
        (event_id, user_id),
    )


def _attestations(event_id):
    return db.query(
        "SELECT * FROM attestations WHERE event_id=%s ORDER BY id", (event_id,)
    )


def _setup(register):
    """A project with one open event, its organizer, and two volunteers.

    ``ana`` is the one who is already there and shows her code; ``ben`` arrives
    and scans it.
    """
    owner, _ou, _ = register("owner")
    detail = make_project(owner)
    ev = first_event(detail)
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    return owner, detail, ev, (ana, ana_u), (ben, ben_u)


# ---- identity: the permanent handle a personal QR carries (§3) --------------

def test_me_carries_a_qr_token(register):
    ana, _a, _ = register("ana")
    me = ana.get("/api/me").json()
    assert me["qr_token"]
    assert isinstance(me["qr_token"], str) and len(me["qr_token"]) >= 8
    # Stable across calls -- it is an identity, not a session artefact.
    assert ana.get("/api/me").json()["qr_token"] == me["qr_token"]


def test_qr_tokens_are_distinct_per_user(register):
    ana, _a, _ = register("ana")
    ben, _b, _ = register("ben")
    assert qr_token(ana) != qr_token(ben)


def test_guests_get_a_qr_token_too(api):
    """Guests are first-class (SERVICE_LOG.md §4) -- they can be scanned."""
    tok = api.post("/api/auth/guest").json()["token"]
    me = api.get("/api/me", headers={"Authorization": "Bearer " + tok}).json()
    assert me["is_guest"] is True
    assert me["qr_token"]


def test_qr_token_survives_guest_attach(api):
    """ATTACH (email free): the same row gains credentials -- the handle persists."""
    tok = api.post("/api/auth/guest").json()["token"]
    h = {"Authorization": "Bearer " + tok}
    before = api.get("/api/me", headers=h).json()
    r = api.post("/api/auth/convert", headers=h,
                 json={"email": "fresh@test.local", "password": "password123"})
    assert r.status_code == 200, r.text
    after = api.get("/api/me", headers={"Authorization": "Bearer " + r.json()["token"]}).json()
    assert after["id"] == before["id"]
    assert after["qr_token"] == before["qr_token"]


def test_public_profile_never_leaks_a_qr_token(register):
    """The code is handed out by its owner, never scraped from a profile."""
    ana, ana_u, _ = register("ana")
    ben, _b, _ = register("ben")
    body = ben.get(f"/api/users/{ana_u['id']}").json()
    assert "qr_token" not in body


# ---- my personal QR for an event (§5.2) -------------------------------------

def test_attendee_can_fetch_their_event_qr(register):
    _owner, _d, ev, (ana, _au), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/rsvp")
    r = ana.get(f"/api/events/{ev['id']}/my-qr.svg")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in r.content


def test_checked_in_attendee_can_fetch_their_event_qr(register):
    _owner, _d, ev, (ana, _au), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    assert ana.get(f"/api/events/{ev['id']}/my-qr.svg").status_code == 200


def test_non_attendee_cannot_fetch_an_event_qr(register):
    _owner, _d, ev, _ana, (ben, _bu) = _setup(register)
    r = ben.get(f"/api/events/{ev['id']}/my-qr.svg")
    assert r.status_code == 403 and r.json()["detail"] == "not_attending"


def test_my_qr_missing_event_404(register):
    ana, _a, _ = register("ana")
    assert ana.get("/api/events/999999/my-qr.svg").status_code == 404


def test_personal_qr_url_shape():
    """The QR is a plain URL: a person + an event, nothing else (P2/P3)."""
    from app.scan import personal_qr_url

    assert personal_qr_url("https", "impact.example", "AbC-123", 42) == (
        "https://impact.example/#/s/AbC-123/42"
    )


# ---- resolving a scanned personal QR ----------------------------------------

def test_resolve_personal_qr(register):
    _owner, detail, ev, (ana, ana_u), (ben, _bu) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")

    r = ben.get(f"/api/scan/{qr_token(ana)}/{ev['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["person"]["id"] == ana_u["id"]
    assert body["person"]["display_name"] == "ana"
    assert body["event"]["id"] == ev["id"]
    assert body["project"]["id"] == detail["id"]
    assert body["waiver"]["id"] == detail["waiver"]["id"]
    assert "text" in body["waiver"]
    assert body["my_open_participation"] is None
    assert body["already_attested"] is False
    # The person's own code is never echoed back to the scanner.
    assert "qr_token" not in body["person"]


def test_resolve_unknown_token_404(register):
    _owner, _d, ev, _ana, (ben, _bu) = _setup(register)
    r = ben.get(f"/api/scan/not-a-real-token/{ev['id']}")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_qr"


def test_resolve_closed_event_404(register):
    owner, _d, ev, (ana, _au), (ben, _bu) = _setup(register)
    owner.post(f"/api/events/{ev['id']}/close")
    r = ben.get(f"/api/scan/{qr_token(ana)}/{ev['id']}")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_qr"


def test_resolve_missing_event_404(register):
    _owner, _d, _ev, (ana, _au), (ben, _bu) = _setup(register)
    r = ben.get(f"/api/scan/{qr_token(ana)}/999999")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_qr"


# ---- confirm: one scan records BOTH people (§5.3) ---------------------------

def test_confirm_records_both_people(register):
    _owner, detail, ev, (ana, ana_u), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")          # ana is already on site

    r = ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["attested"] is True
    assert body["person"]["id"] == ana_u["id"]

    # The sighting itself, with its direction preserved (P6).
    rows = _attestations(ev["id"])
    assert len(rows) == 1
    assert rows[0]["scanner_user_id"] == ben_u["id"]
    assert rows[0]["subject_user_id"] == ana_u["id"]

    # BOTH participations are now attested.
    ben_p = _participation(ev["id"], ben_u["id"])
    ana_p = _participation(ev["id"], ana_u["id"])
    assert ben_p is not None and ben_p["attested"] is True
    assert ana_p is not None and ana_p["attested"] is True
    assert body["participation"]["id"] == ben_p["id"]

    # I6 still holds for the scanner's freshly-created participation.
    w = db.query_one("SELECT project_id FROM waivers WHERE id=%s", (ben_p["waiver_id"],))
    assert w["project_id"] == detail["id"]


def test_confirm_puts_the_scanner_on_the_rsvp_list(register):
    _owner, _d, ev, (ana, ana_u), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    rows = db.query("SELECT user_id FROM rsvps WHERE event_id=%s", (ev["id"],))
    assert {r["user_id"] for r in rows} == {ana_u["id"], ben_u["id"]}


def test_confirm_upgrades_an_already_asserted_scanner(register):
    """Ben checked in with the button first, then scanned. No second participation."""
    _owner, _d, ev, (ana, _au), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/events/{ev['id']}/checkin")
    before = _participation(ev["id"], ben_u["id"])
    assert before["attested"] is False                    # the button is an assertion

    r = ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert r.status_code == 201, r.text
    after = _participation(ev["id"], ben_u["id"])
    assert after["id"] == before["id"]                    # upgraded, not duplicated
    assert after["attested"] is True
    assert db.query_one(
        "SELECT COUNT(*) AS c FROM participations WHERE event_id=%s AND user_id=%s",
        (ev["id"], ben_u["id"]),
    )["c"] == 1


# ---- I14: a scan never forges the subject's waiver signature ----------------

def test_scanning_someone_not_checked_in_creates_no_participation_for_them(register):
    _owner, _d, ev, (ana, ana_u), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/rsvp")              # RSVP'd only -- never agreed

    r = ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert r.status_code == 201, r.text

    # The sighting is stored (P8) ...
    assert len(_attestations(ev["id"])) == 1
    # ... and the scanner is checked in ...
    assert _participation(ev["id"], ben_u["id"])["attested"] is True
    # ... but NOTHING was signed on ana's behalf (I14).
    assert _participation(ev["id"], ana_u["id"]) is None


def test_pending_attestation_upgrades_the_subject_on_later_checkin(register):
    """P8 / §5.4: scanned at 09:00, taps Check in at 09:05 -> already attested."""
    _owner, _d, ev, (ana, ana_u), (ben, _bu) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/rsvp")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert _participation(ev["id"], ana_u["id"]) is None

    assert ana.post(f"/api/events/{ev['id']}/checkin").status_code == 200
    assert _participation(ev["id"], ana_u["id"])["attested"] is True


def test_pending_attestation_upgrades_a_later_code_agree(register):
    """The same catch-up applies to the event-code path, not just the button."""
    _owner, _d, ev, (ana, ana_u), (ben, _bu) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/rsvp")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")

    r = ana.post(f"/api/checkin/{ev['checkin_code']}/agree")
    assert r.status_code == 201, r.text
    assert _participation(ev["id"], ana_u["id"])["attested"] is True


# ---- I13: idempotency, self-scan, and the two-different-users rule ----------

def test_rescanning_the_same_person_is_a_noop_not_an_error(register):
    _owner, _d, ev, (ana, _au), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    first = ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert first.status_code == 201
    second = ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert second.status_code == 201, second.text
    assert len(_attestations(ev["id"])) == 1
    assert db.query_one(
        "SELECT COUNT(*) AS c FROM participations WHERE event_id=%s AND user_id=%s",
        (ev["id"], ben_u["id"]),
    )["c"] == 1


def test_scanning_your_own_code_409(register):
    _owner, _d, ev, (ana, _au), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    r = ana.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert r.status_code == 409 and r.json()["detail"] == "self_scan"
    assert _attestations(ev["id"]) == []


def test_resolving_your_own_code_is_flagged(register):
    """Resolve stays 200 so the UI can explain it kindly rather than 404."""
    _owner, _d, ev, (ana, ana_u), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    body = ana.get(f"/api/scan/{qr_token(ana)}/{ev['id']}").json()
    assert body["person"]["id"] == ana_u["id"]
    assert body["is_self"] is True


def test_the_reverse_scan_is_a_separate_sighting(register):
    """Ben scans Ana, then Ana scans Ben: two rows, both directions kept."""
    _owner, _d, ev, (ana, ana_u), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    ana.post(f"/api/scan/{qr_token(ben)}/{ev['id']}/confirm")
    rows = _attestations(ev["id"])
    assert len(rows) == 2
    assert {(r["scanner_user_id"], r["subject_user_id"]) for r in rows} == {
        (ben_u["id"], ana_u["id"]),
        (ana_u["id"], ben_u["id"]),
    }


def test_confirm_on_an_over_event_409(register):
    _owner, _d, ev, (ana, _au), (ben, _bu) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    _make_event_over(ev["id"])
    r = ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    assert r.status_code == 409 and r.json()["detail"] == "event_over"
    assert _attestations(ev["id"]) == []


def test_confirm_unknown_token_404(register):
    _owner, _d, ev, _ana, (ben, _bu) = _setup(register)
    r = ben.post(f"/api/scan/nope/{ev['id']}/confirm")
    assert r.status_code == 404 and r.json()["detail"] == "invalid_qr"


# ---- I15: attested is pinned to the participation, not the person ----------

def test_a_closed_participation_is_not_retroactively_attested(register):
    """A sighting during today's shift must not vouch for one closed earlier."""
    _owner, _d, ev, (ana, ana_u), (ben, _bu) = _setup(register)
    first = ana.post(f"/api/events/{ev['id']}/checkin").json()
    pid = first["my_open_participation"]["id"]
    assert ana.post(f"/api/participations/{pid}/checkout").status_code == 200

    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")

    closed = db.query_one("SELECT attested FROM participations WHERE id=%s", (pid,))
    assert closed["attested"] is False


def test_button_checkin_is_asserted_not_attested(register):
    _owner, _d, ev, (ana, ana_u), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    assert _participation(ev["id"], ana_u["id"])["attested"] is False


def test_code_agree_is_asserted_not_attested(register):
    _owner, _d, ev, (ana, ana_u), _ben = _setup(register)
    ana.post(f"/api/checkin/{ev['checkin_code']}/agree")
    assert _participation(ev["id"], ana_u["id"])["attested"] is False


# ---- read shapes ------------------------------------------------------------

def test_roster_exposes_attested(register):
    owner, _d, ev, (ana, ana_u), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    roster = owner.get(f"/api/events/{ev['id']}/roster").json()
    by_user = {p["user"]["id"]: p for p in roster["participations"]}
    assert by_user[ana_u["id"]]["attested"] is True
    assert by_user[ben_u["id"]]["attested"] is True


def test_roster_shows_self_reported_as_not_attested(register):
    owner, _d, ev, (ana, ana_u), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    roster = owner.get(f"/api/events/{ev['id']}/roster").json()
    assert roster["participations"][0]["attested"] is False


def test_rsvp_list_exposes_is_attested(register):
    owner, _d, ev, (ana, ana_u), (ben, ben_u) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/rsvp")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    rows = owner.get(f"/api/events/{ev['id']}/rsvps").json()
    by_user = {r["user"]["id"]: r for r in rows}
    # Ana never checked in, but she was seen -- the organizer can tell.
    assert by_user[ana_u["id"]]["is_attested"] is True
    assert by_user[ana_u["id"]]["is_checked_in"] is False
    assert by_user[ben_u["id"]]["is_attested"] is True


def test_my_open_participation_carries_attested(register):
    _owner, _d, ev, (ana, _au), (ben, _bu) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    detail = ana.get(f"/api/events/{ev['id']}").json()
    assert detail["my_open_participation"]["attested"] is True


def test_resolve_reports_already_attested(register):
    _owner, _d, ev, (ana, _au), (ben, _bu) = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm")
    body = ben.get(f"/api/scan/{qr_token(ana)}/{ev['id']}").json()
    assert body["already_attested"] is True
    assert body["my_open_participation"]["attested"] is True


# ---- a guest can do all of this ---------------------------------------------

def test_a_guest_can_scan_and_be_scanned(api, register):
    _owner, _d, ev, (ana, ana_u), _ben = _setup(register)
    ana.post(f"/api/events/{ev['id']}/checkin")

    gtok = api.post("/api/auth/guest").json()["token"]
    h = {"Authorization": "Bearer " + gtok}
    guest_id = api.get("/api/me", headers=h).json()["id"]

    r = api.post(f"/api/scan/{qr_token(ana)}/{ev['id']}/confirm", headers=h)
    assert r.status_code == 201, r.text
    assert _participation(ev["id"], guest_id)["attested"] is True
    assert _participation(ev["id"], ana_u["id"])["attested"] is True


# ---- auth wall --------------------------------------------------------------

def test_auth_required(api, register):
    _owner, _d, ev, (ana, _au), _ben = _setup(register)
    tok = qr_token(ana)
    assert api.get(f"/api/scan/{tok}/{ev['id']}").status_code == 401
    assert api.post(f"/api/scan/{tok}/{ev['id']}/confirm").status_code == 401
    assert api.get(f"/api/events/{ev['id']}/my-qr.svg").status_code == 401
