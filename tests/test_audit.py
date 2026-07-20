"""Audit log: every check-in and check-out is an INDEPENDENT immutable row.

(Renamed from tests/test_events.py when the ``events`` table was reassigned to the
service-project occurrence domain; this now reads the ``audit_log`` table.)

Each row is written in the SAME tx as the state change it records (a check_in next
to the participation insert; a check_out only on the real checkout transition) and
carries both the occurrence (``event_id``) and its project (``project_id``).
Re-check-in appends fresh rows; a double checkout adds nothing; a 0-minute
checkout still logs.
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
    """The one event seeded with a freshly created project."""
    return detail["events"][0]


def _backdate(participation_id, minutes):
    db.query(
        "UPDATE participations SET checked_in_at = now() - make_interval(mins => %s) "
        "WHERE id = %s",
        (minutes, participation_id),
    )


def _audit(**where):
    sql = "SELECT * FROM audit_log"
    params = []
    if where:
        clauses = []
        for k, v in where.items():
            clauses.append(f"{k} = %s")
            params.append(v)
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    return db.query(sql, params)


# ---- check-in logging -------------------------------------------------------

def test_agree_logs_one_check_in(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner)
    ev = first_event(proj)
    code, eid, pid = ev["checkin_code"], ev["id"], proj["id"]
    vol, vu, _ = register("vol")
    part = vol.post(f"/api/checkin/{code}/agree").json()

    rows = _audit(type="check_in", subject_user_id=vu["id"])
    assert len(rows) == 1
    e = rows[0]
    assert e["actor_user_id"] == vu["id"]
    assert e["subject_user_id"] == vu["id"]
    assert e["participation_id"] == part["id"]
    assert e["event_id"] == eid == part["event_id"]
    assert e["project_id"] == pid
    assert e["minutes"] is None and e["tokens"] is None


def test_self_checkin_logs_one_check_in(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner)
    ev = first_event(proj)
    eid, pid = ev["id"], proj["id"]
    vol, vu, _ = register("vol")
    r = vol.post(f"/api/events/{eid}/checkin")
    assert r.status_code == 200, r.text

    rows = _audit(type="check_in", subject_user_id=vu["id"])
    assert len(rows) == 1
    assert rows[0]["actor_user_id"] == vu["id"]
    assert rows[0]["event_id"] == eid
    assert rows[0]["project_id"] == pid
    assert rows[0]["participation_id"] is not None


# ---- check-out logging ------------------------------------------------------

def test_self_checkout_logs_check_out(register):
    owner, _o, _ = register("owner")
    proj = make_project(owner, expected_minutes=120)
    ev = first_event(proj)
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{ev['checkin_code']}/agree").json()
    _backdate(p["id"], 90)
    assert vol.post(f"/api/participations/{p['id']}/checkout").status_code == 200

    rows = _audit(type="check_out")
    assert len(rows) == 1
    e = rows[0]
    assert e["actor_user_id"] == vu["id"]       # self
    assert e["subject_user_id"] == vu["id"]
    assert e["participation_id"] == p["id"]
    assert e["event_id"] == ev["id"]
    assert e["project_id"] == proj["id"]
    assert e["minutes"] == 90
    assert e["tokens"] == 2


def test_leader_checkout_logs_actor_leader_subject_volunteer(register):
    owner, ou, _ = register("owner")
    proj = make_project(owner, expected_minutes=120)
    ev = first_event(proj)
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{ev['checkin_code']}/agree").json()
    _backdate(p["id"], 30)
    assert owner.post(f"/api/participations/{p['id']}/checkout").status_code == 200

    rows = _audit(type="check_out")
    assert len(rows) == 1
    e = rows[0]
    assert e["actor_user_id"] == ou["id"]       # the leader performed it
    assert e["subject_user_id"] == vu["id"]     # the volunteer it is about
    assert e["event_id"] == ev["id"]
    assert e["project_id"] == proj["id"]
    assert e["tokens"] == 1


def test_close_event_logs_check_out_for_each_open_participation(register):
    owner, ou, _ = register("owner")
    proj = make_project(owner, expected_minutes=120)
    ev = first_event(proj)
    code, eid = ev["checkin_code"], ev["id"]

    vols = []
    for name in ("a", "b", "c"):
        vc, vu, _ = register(name)
        p = vc.post(f"/api/checkin/{code}/agree").json()
        _backdate(p["id"], 60)
        vols.append((vu, p))

    assert owner.post(f"/api/events/{eid}/close").status_code == 200

    rows = _audit(type="check_out")
    assert len(rows) == 3
    subjects = {r["subject_user_id"] for r in rows}
    assert subjects == {vu["id"] for vu, _ in vols}
    # the closer is the actor on every row; each carries the event + project
    assert all(r["actor_user_id"] == ou["id"] for r in rows)
    assert all(r["event_id"] == eid and r["project_id"] == proj["id"] for r in rows)


def test_full_cycle_yields_independent_rows(register):
    owner, _o, _ = register("owner")
    ev = first_event(make_project(owner, expected_minutes=120))
    code = ev["checkin_code"]
    vol, vu, _ = register("vol")

    p1 = vol.post(f"/api/checkin/{code}/agree").json()
    _backdate(p1["id"], 90)
    assert vol.post(f"/api/participations/{p1['id']}/checkout").status_code == 200
    p2 = vol.post(f"/api/checkin/{code}/agree").json()

    rows = _audit(subject_user_id=vu["id"])
    assert [r["type"] for r in rows] == ["check_in", "check_out", "check_in"]
    # INDEPENDENT rows: distinct ids, and the two check-ins are distinct parts.
    assert len({r["id"] for r in rows}) == 3
    assert rows[0]["participation_id"] == p1["id"]
    assert rows[2]["participation_id"] == p2["id"]
    assert p2["id"] != p1["id"]


def test_double_checkout_does_not_add_second_check_out(register):
    owner, _o, _ = register("owner")
    ev = first_event(make_project(owner))
    vol, _v, _ = register("vol")
    p = vol.post(f"/api/checkin/{ev['checkin_code']}/agree").json()
    assert vol.post(f"/api/participations/{p['id']}/checkout").status_code == 200
    # already checked out -> 409, and NO second check_out row
    assert vol.post(f"/api/participations/{p['id']}/checkout").status_code == 409

    rows = _audit(type="check_out")
    assert len(rows) == 1


def test_zero_minute_checkout_still_logs_check_out(register):
    owner, _o, _ = register("owner")
    ev = first_event(make_project(owner))
    vol, vu, _ = register("vol")
    p = vol.post(f"/api/checkin/{ev['checkin_code']}/agree").json()
    # immediate checkout: 0 minutes, 0 tokens, but STILL an audit row.
    body = vol.post(f"/api/participations/{p['id']}/checkout").json()
    assert body["minutes"] == 0 and body["tokens_awarded"] == 0

    rows = _audit(type="check_out")
    assert len(rows) == 1
    assert rows[0]["minutes"] == 0
    assert rows[0]["tokens"] == 0
    assert rows[0]["subject_user_id"] == vu["id"]
