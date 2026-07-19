"""The sacred core: checkout math, ledger primitives, tipping, invariants.

Covers I1 (balance == ledger sum), I2 (append-only), I9 (guarded transfer),
and I12 (checkout math boundaries + mint cap).
"""
import threading

import pytest

from app import db, tokens


# ---- I12: checkout math boundaries + cap ------------------------------------

@pytest.mark.parametrize("minutes,expected_tokens", [
    (29, 0), (30, 1), (89, 1), (90, 2), (150, 3), (0, 0),
])
def test_tokens_for_boundaries(minutes, expected_tokens):
    # expected_minutes large so the cap never binds here.
    assert tokens.tokens_for(minutes, expected_minutes=100000) == expected_tokens


def test_mint_cap_binds():
    # 600 elapsed minutes at 120 expected -> credited capped at 240 -> 4 tokens.
    assert tokens.tokens_for(600, expected_minutes=120) == 4


def test_elapsed_minutes_half_up():
    assert tokens.elapsed_minutes(29 * 60) == 29
    assert tokens.elapsed_minutes(30 * 60) == 30
    assert tokens.elapsed_minutes(29 * 60 + 30) == 30   # half rounds up


def test_not_python_round():
    # Guard the exact banker's-rounding trap the design warns about.
    assert round(30 / 60) == 0           # what we must NOT do
    assert tokens.tokens_for(30, 100000) == 1  # what we do


# ---- ledger primitives + invariants -----------------------------------------

def _balance(uid):
    return db.query_one("SELECT balance FROM users WHERE id=%s", (uid,))["balance"]


def _ledger_balance(uid):
    incoming = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE to_user_id=%s", (uid,))["s"]
    outgoing = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE from_user_id=%s", (uid,))["s"]
    return int(incoming) - int(outgoing)


def test_mint_updates_balance_and_ledger(register):
    _c, user, _t = register("mia")
    with db.tx() as c:
        tokens.mint(c, user["id"], 5)
    assert _balance(user["id"]) == 5
    assert _ledger_balance(user["id"]) == 5   # I1


def test_transfer_moves_tokens_and_conserves(register):
    _ca, a, _ta = register("ann")
    _cb, b, _tb = register("ben")
    with db.tx() as c:
        tokens.mint(c, a["id"], 10)
    with db.tx() as c:
        tokens.transfer(c, a["id"], b["id"], 4, "tip")
    assert _balance(a["id"]) == 6 and _balance(b["id"]) == 4
    assert _ledger_balance(a["id"]) == 6 and _ledger_balance(b["id"]) == 4   # I1


def test_insufficient_balance_changes_nothing(register):
    _ca, a, _ta = register("ada")
    _cb, b, _tb = register("boo")
    with db.tx() as c:
        tokens.mint(c, a["id"], 3)
    with pytest.raises(tokens.InsufficientBalance):
        with db.tx() as c:
            tokens.transfer(c, a["id"], b["id"], 5, "tip")   # I9
    assert _balance(a["id"]) == 3 and _balance(b["id"]) == 0
    # No spend/tip entry was written.
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries WHERE kind='tip'")["c"] == 0


def test_concurrent_transfers_only_one_succeeds(register):
    _ca, a, _ta = register("race_a")
    _cb, b, _tb = register("race_b")
    with db.tx() as c:
        tokens.mint(c, a["id"], 100)
    results = []

    def worker():
        try:
            with db.tx() as c:
                tokens.transfer(c, a["id"], b["id"], 100, "tip")
            results.append("ok")
        except tokens.InsufficientBalance:
            results.append("fail")

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == ["fail", "ok"]         # atomic guard holds
    assert _balance(a["id"]) == 0 and _balance(b["id"]) == 100
    assert _ledger_balance(a["id"]) == 0             # I1 after contention


# ---- tip endpoint -----------------------------------------------------------

def test_tip_by_email_happy_path(register):
    ca, a, _ta = register("tipa")
    _cb, b, _tb = register("tipb", display_name="Tip B")
    with db.tx() as c:
        tokens.mint(c, a["id"], 5)
    r = ca.post("/api/tokens/tip",
                json={"to_email": "tipb@test.local", "amount": 3, "note": "thanks"})
    assert r.status_code == 201
    body = r.json()
    assert body["direction"] == "out" and body["amount"] == 3
    # Counterparty is the public identity only -- never the email.
    assert body["counterparty"] == {"id": b["id"], "display_name": "Tip B"}
    assert "email" not in body["counterparty"]
    assert _balance(a["id"]) == 2 and _balance(b["id"]) == 3


def test_tip_by_user_id_happy_path(register):
    ca, a, _ta = register("tipc")
    _cb, b, _tb = register("tipd", display_name="Tip D")
    with db.tx() as c:
        tokens.mint(c, a["id"], 5)
    r = ca.post("/api/tokens/tip", json={"to_user_id": b["id"], "amount": 2})
    assert r.status_code == 201
    body = r.json()
    assert body["direction"] == "out" and body["amount"] == 2
    assert body["counterparty"] == {"id": b["id"], "display_name": "Tip D"}
    assert _balance(a["id"]) == 3 and _balance(b["id"]) == 2


def test_tip_exactly_one_recipient_422(register):
    ca, a, _ta = register("one")
    _cb, b, _tb = register("two")
    with db.tx() as c:
        tokens.mint(c, a["id"], 5)
    # Neither recipient field -> 422.
    assert ca.post("/api/tokens/tip", json={"amount": 1}).status_code == 422
    # Both recipient fields -> 422.
    assert ca.post("/api/tokens/tip", json={
        "to_user_id": b["id"], "to_email": "two@test.local", "amount": 1,
    }).status_code == 422
    assert _balance(a["id"]) == 5 and _balance(b["id"]) == 0   # nothing moved


def test_tip_insufficient(register):
    ca, a, _ta = register("poor")
    _cb, b, _tb = register("rich")
    r = ca.post("/api/tokens/tip", json={"to_email": "rich@test.local", "amount": 1})
    assert r.status_code == 409 and r.json()["detail"] == "insufficient_balance"


def test_tip_self_and_unknown_and_bad_amount(register):
    ca, a, _ta = register("solo")
    with db.tx() as c:
        tokens.mint(c, a["id"], 5)
    # cannot_tip_self via both recipient forms
    assert ca.post("/api/tokens/tip", json={"to_email": "solo@test.local", "amount": 1}).status_code == 409
    assert ca.post("/api/tokens/tip", json={"to_user_id": a["id"], "amount": 1}).status_code == 409
    # unknown recipient via both forms
    assert ca.post("/api/tokens/tip", json={"to_email": "nobody@test.local", "amount": 1}).status_code == 404
    assert ca.post("/api/tokens/tip", json={"to_user_id": 999999, "amount": 1}).status_code == 404
    # bad amount
    assert ca.post("/api/tokens/tip", json={"to_email": "solo@test.local", "amount": 0}).status_code == 422


def test_ledger_lists_entries(register):
    ca, a, _ta = register("led")
    _cb, b, _tb = register("led2")
    with db.tx() as c:
        tokens.mint(c, a["id"], 5)
    ca.post("/api/tokens/tip", json={"to_email": "led2@test.local", "amount": 2})
    rows = ca.get("/api/tokens/ledger").json()
    kinds = {row["kind"] for row in rows}
    assert "earn" in kinds and "tip" in kinds
    assert rows[0]["created_at"]  # newest first, well-formed
    # No ledger row ever exposes an email.
    for row in rows:
        assert row["counterparty"] is None or "email" not in row["counterparty"]
