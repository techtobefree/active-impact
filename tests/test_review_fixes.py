"""Regression tests for the adversarial-review findings.

- Concurrent/double checkout must mint exactly once (the blocker).
- Tip with a bad catalog_item_id -> 404, not a 500 FK violation.
- PATCH catalog with an explicit null NOT-NULL field -> no-op, not a 500.
"""
import threading

from app import db, tokens


def _balance(uid):
    return db.query_one("SELECT balance FROM users WHERE id=%s", (uid,))["balance"]


def _open_participation(user_id, minutes_ago, expected_minutes=90, code="rev-code"):
    """A backdated OPEN participation (+ its project/waiver) via direct SQL."""
    with db.tx() as c:
        pr = c.execute(
            "INSERT INTO projects(owner_id,title,location_text,starts_at,expected_minutes,checkin_code) "
            "VALUES (%s,'P','L',now(),%s,%s) RETURNING id",
            (user_id, expected_minutes, code),
        ).fetchone()
        w = c.execute(
            "INSERT INTO waivers(project_id,version,text) VALUES (%s,1,'w') RETURNING id",
            (pr["id"],),
        ).fetchone()
        part = c.execute(
            "INSERT INTO participations(project_id,user_id,waiver_id,checked_in_at) "
            "VALUES (%s,%s,%s, now() - make_interval(mins => %s)) RETURNING id, checked_in_at",
            (pr["id"], user_id, w["id"], minutes_ago),
        ).fetchone()
    return {"id": part["id"], "user_id": user_id,
            "checked_in_at": part["checked_in_at"], "expected_minutes": expected_minutes}


def test_double_checkout_does_not_double_mint(register):
    _c, u, _t = register("revdbl")
    p = _open_participation(u["id"], 90, code="rev-code-1")
    with db.tx() as c:
        assert tokens.do_checkout(c, dict(p)) is not None
    with db.tx() as c:
        assert tokens.do_checkout(c, dict(p)) is None   # guarded: second mints nothing
    e = db.query_one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(amount),0) AS s "
        "FROM token_entries WHERE participation_id=%s AND kind='earn'", (p["id"],))
    assert e["c"] == 1 and int(e["s"]) == 2
    assert _balance(u["id"]) == 2                        # not 4


def test_concurrent_checkout_mints_once(register):
    _c, u, _t = register("revrace")
    p = _open_participation(u["id"], 90, code="rev-code-2")
    results = []

    def worker():
        with db.tx() as c:
            results.append("minted" if tokens.do_checkout(c, dict(p)) else "noop")

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == ["minted", "noop"]         # exactly one winner
    e = db.query_one(
        "SELECT COUNT(*) AS c FROM token_entries WHERE participation_id=%s AND kind='earn'", (p["id"],))
    assert e["c"] == 1
    assert _balance(u["id"]) == 2


def test_checkout_endpoint_second_call_409(register):
    client, u, _t = register("revapi")
    p = _open_participation(u["id"], 90, code="rev-code-3")
    r1 = client.post(f"/api/participations/{p['id']}/checkout")
    assert r1.status_code == 200 and r1.json()["tokens_awarded"] == 2
    r2 = client.post(f"/api/participations/{p['id']}/checkout")
    assert r2.status_code == 409 and r2.json()["detail"] == "already_checked_out"
    assert _balance(u["id"]) == 2


def test_tip_bad_catalog_item_id_is_404_not_500(register):
    ca, a, _ = register("revtipa")
    _cb, b, _ = register("revtipb")
    with db.tx() as c:
        tokens.mint(c, a["id"], 5)
    r = ca.post("/api/tokens/tip",
                json={"to_email": "revtipb@test.local", "amount": 1, "catalog_item_id": 999999})
    assert r.status_code == 404
    assert _balance(a["id"]) == 5 and _balance(b["id"]) == 0   # nothing moved


def test_patch_catalog_null_field_is_noop_not_500(register):
    client, _u, _ = register("revedit")
    item = client.post("/api/catalog",
                       json={"kind": "offer", "title": "Original", "price_tokens": 5}).json()
    r = client.patch(f"/api/catalog/{item['id']}", json={"title": None})
    assert r.status_code == 200
    assert r.json()["title"] == "Original"


def test_i2_ledger_written_only_by_tokens_module():
    """I2 (structural): only app/tokens.py may write token_entries or users.balance."""
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for f in sorted(app_dir.glob("*.py")):
        if f.name == "tokens.py":
            continue
        src = f.read_text().lower()
        if "insert into token_entries" in src:
            offenders.append(f"{f.name}: inserts token_entries")
        if "set balance" in src:  # e.g. "UPDATE users SET balance = balance + ..."
            offenders.append(f"{f.name}: writes users.balance")
    assert not offenders, offenders
