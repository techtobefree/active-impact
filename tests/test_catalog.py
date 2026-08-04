"""Catalog items + claims: price rules, claim gating, settlement, invariants.

Owns catalog invariants I7 (a claim is born settled and never transitions), I8
(priced claim <-> exactly one 'burn' entry with no payee), I10 (only active
in-quantity offers claim; quantity 0 -> closed). Touches I1 (balance == ledger
sum) and I1b (supply = mints - burns).
"""
import pytest

from app import db, main, tokens


# ---- helpers ----------------------------------------------------------------

def _fund(uid, amount):
    """Give a user tokens via the sacred primitive, as the task setup prescribes."""
    with db.tx() as c:
        tokens.mint(c, uid, amount)


def _balance(uid):
    return db.query_one("SELECT balance FROM users WHERE id=%s", (uid,))["balance"]


def _ledger_balance(uid):
    inc = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE to_user_id=%s",
        (uid,),
    )["s"]
    out = db.query_one(
        "SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE from_user_id=%s",
        (uid,),
    )["s"]
    return int(inc) - int(out)


def _offer(client, price=5, quantity=None, title="Widget"):
    body = {"kind": "offer", "title": title, "price_tokens": price}
    if quantity is not None:
        body["quantity"] = quantity
    r = client.post("/api/catalog", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _need(client, title="Help me move"):
    r = client.post("/api/catalog", json={"kind": "need", "title": title})
    assert r.status_code == 201, r.text
    return r.json()


# ---- create: offer/need price rules -----------------------------------------

def test_create_offer_requires_price(register):
    ca, a, _ = register("poster_a")
    item = _offer(ca, price=0, title="Free sticker")   # 0 is a valid free offer
    assert item["kind"] == "offer" and item["price_tokens"] == 0
    assert item["status"] == "active"
    # my_claim / burn tally present for the poster's own detail view
    assert item["my_claim"] is None
    assert item["redeemed_count"] == 0 and item["burned_tokens"] == 0


def test_offer_without_price_is_price_required(register):
    ca, a, _ = register("poster_b")
    r = ca.post("/api/catalog", json={"kind": "offer", "title": "No price"})
    assert r.status_code == 422 and r.json()["detail"] == "price_required"


def test_need_forbids_price(register):
    ca, a, _ = register("poster_c")
    r = ca.post("/api/catalog", json={"kind": "need", "title": "Nope", "price_tokens": 3})
    assert r.status_code == 422 and r.json()["detail"] == "price_on_need"


def test_need_created_unpriced(register):
    ca, a, _ = register("poster_d")
    need = _need(ca)
    assert need["kind"] == "need" and need["price_tokens"] is None


def test_create_validation_422(register):
    ca, a, _ = register("poster_e")
    assert ca.post("/api/catalog", json={"kind": "bogus", "title": "x"}).status_code == 422
    assert ca.post("/api/catalog", json={"kind": "offer", "title": "", "price_tokens": 1}).status_code == 422
    assert ca.post("/api/catalog", json={"kind": "offer", "title": "x", "price_tokens": -1}).status_code == 422
    assert ca.post("/api/catalog", json={"kind": "offer", "title": "x", "price_tokens": 1, "quantity": 0}).status_code == 422


# ---- list -------------------------------------------------------------------

def test_list_filters_and_newest_first(register):
    ca, a, _ = register("lister")
    _offer(ca, title="Alpha offer")
    _need(ca, title="Beta need")
    _offer(ca, title="Gamma offer")

    allrows = ca.get("/api/catalog").json()
    assert [r["title"] for r in allrows] == ["Gamma offer", "Beta need", "Alpha offer"]  # newest first

    offers = ca.get("/api/catalog?kind=offer").json()
    assert {r["title"] for r in offers} == {"Alpha offer", "Gamma offer"}

    needs = ca.get("/api/catalog?kind=need").json()
    assert [r["title"] for r in needs] == ["Beta need"]

    hits = ca.get("/api/catalog?q=gamma").json()
    assert [r["title"] for r in hits] == ["Gamma offer"]


def test_list_mine_and_status_and_pagination(register):
    ca, a, _ = register("owner1")
    cb, b, _ = register("owner2")
    o1 = _offer(ca, title="Mine one")
    _offer(ca, title="Mine two")
    _offer(cb, title="Theirs")

    mine = ca.get("/api/catalog?mine=1").json()
    assert {r["title"] for r in mine} == {"Mine one", "Mine two"}

    # closed items are hidden by the default active filter
    ca.patch(f"/api/catalog/{o1['id']}", json={"status": "closed"})
    active = ca.get("/api/catalog?mine=1").json()
    assert {r["title"] for r in active} == {"Mine two"}
    closed = ca.get("/api/catalog?mine=1&status=closed").json()
    assert {r["title"] for r in closed} == {"Mine one"}

    page = ca.get("/api/catalog?mine=1&status=closed&limit=1&offset=0").json()
    assert len(page) == 1


# ---- detail -----------------------------------------------------------------

def test_detail_404(register):
    ca, a, _ = register("det")
    assert ca.get("/api/catalog/9999").status_code == 404


def test_detail_redeemed_count_poster_only(register):
    cp, p, _ = register("shopkeep")
    cc, c, _ = register("buyer")
    _fund(c["id"], 10)
    item = _offer(cp, price=2)
    cc.post(f"/api/catalog/{item['id']}/claim")

    # The poster's number is what they gave away, not what they are owed (T5).
    poster_view = cp.get(f"/api/catalog/{item['id']}").json()
    assert poster_view["redeemed_count"] == 1
    assert poster_view["burned_tokens"] == 2
    assert poster_view["my_claim"] is None

    buyer_view = cc.get(f"/api/catalog/{item['id']}").json()
    assert "redeemed_count" not in buyer_view             # poster-only field
    assert "burned_tokens" not in buyer_view
    assert buyer_view["my_claim"]["status"] == "redeemed"


# ---- patch ------------------------------------------------------------------

def test_patch_not_yours_403(register):
    cp, p, _ = register("ownerp")
    cx, x, _ = register("intruder")
    item = _offer(cp)
    r = cx.patch(f"/api/catalog/{item['id']}", json={"title": "Hacked"})
    assert r.status_code == 403 and r.json()["detail"] == "not_yours"


def test_patch_updates_fields(register):
    cp, p, _ = register("editor")
    item = _offer(cp, price=5, title="Old")
    r = cp.patch(f"/api/catalog/{item['id']}", json={"title": "New", "quantity": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "New" and body["quantity"] == 3


def test_patch_price_does_not_touch_existing_claim(register):
    cp, p, _ = register("pricer")
    cc, c, _ = register("shopper")
    _fund(c["id"], 20)
    item = _offer(cp, price=5, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    assert claim["price_tokens"] == 5

    # Settlement already happened at the snapshot price (5), not the later one.
    assert _balance(c["id"]) == 15

    # Poster raises the price AFTER the claim exists.
    cp.patch(f"/api/catalog/{item['id']}", json={"price_tokens": 10})
    # Snapshot on the claim is unchanged, and so is the balance it produced.
    still = cc.get(f"/api/catalog/{item['id']}").json()["my_claim"]
    assert still["price_tokens"] == 5
    assert _balance(c["id"]) == 15


# ---- claim gating (I10) -----------------------------------------------------

def test_claim_need_not_claimable(register):
    cp, p, _ = register("needer")
    cc, c, _ = register("helper")
    need = _need(cp)
    r = cc.post(f"/api/catalog/{need['id']}/claim")
    assert r.status_code == 409 and r.json()["detail"] == "not_claimable"


def test_claim_own_item(register):
    cp, p, _ = register("selfclaim")
    item = _offer(cp)
    r = cp.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 409 and r.json()["detail"] == "own_item"


def test_claim_closed_item(register):
    cp, p, _ = register("closer")
    cc, c, _ = register("late")
    item = _offer(cp)
    cp.patch(f"/api/catalog/{item['id']}", json={"status": "closed"})
    r = cc.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 409 and r.json()["detail"] == "item_closed"


def test_claim_missing_item_404(register):
    cc, c, _ = register("ghost")
    assert cc.post("/api/catalog/9999/claim").status_code == 404


def test_claim_twice_is_two_redemptions(register):
    """The old "one live claim per item" rule guarded the pending state. With no
    pending state, redeeming twice is simply redeeming twice -- and it burns
    twice. Quantity is the poster's bound, not a per-person cap."""
    cp, p, _ = register("dupshop")
    cc, c, _ = register("dupbuyer")
    _fund(c["id"], 10)
    item = _offer(cp, price=1, quantity=3)
    assert cc.post(f"/api/catalog/{item['id']}/claim").status_code == 201
    assert cc.post(f"/api/catalog/{item['id']}/claim").status_code == 201

    assert _balance(c["id"]) == 8
    assert db.query_one(
        "SELECT COUNT(*) AS c FROM token_entries WHERE kind='burn' AND from_user_id=%s",
        (c["id"],),
    )["c"] == 2
    it = db.query_one("SELECT quantity FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 1


def test_claim_without_the_tokens_is_refused_and_changes_nothing(register):
    cp, p, _ = register("bigseller")
    cc, c, _ = register("brokebuyer")
    _fund(c["id"], 5)
    item = _offer(cp, price=100, quantity=1)

    r = cc.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 409 and r.json()["detail"] == "insufficient_balance"

    # No claim, no burn, no decrement: the whole tx rolled back (I9).
    assert db.query_one("SELECT COUNT(*) AS c FROM catalog_claims")["c"] == 0
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries WHERE kind='burn'")["c"] == 0
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 1 and it["status"] == "active"
    assert _balance(c["id"]) == 5


# ---- settlement happens AT the claim (T11) ----------------------------------

def test_claim_burns_the_price_and_closes_at_zero(register):
    cp, p, _ = register("seller")
    cc, c, _ = register("payer")
    _fund(c["id"], 10)
    item = _offer(cp, price=3, quantity=1)

    r = cc.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 201
    body = r.json()
    # Born settled: no pending phase to pass through (T11).
    assert body["status"] == "redeemed" and body["decided_at"] is not None

    # Exactly one burn entry, tagged with the claim, going NOWHERE (I8, T12).
    burns = db.query(
        "SELECT * FROM token_entries WHERE kind='burn' AND claim_id=%s", (body["id"],)
    )
    assert len(burns) == 1
    assert burns[0]["amount"] == 3
    assert burns[0]["from_user_id"] == c["id"]
    assert burns[0]["to_user_id"] is None
    assert burns[0]["catalog_item_id"] == item["id"]

    # Last unit consumed -> quantity 0 and item auto-closed (I10).
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 0 and it["status"] == "closed"

    # The claimant paid; the poster was NOT paid (T4).
    assert _balance(c["id"]) == 7 and _balance(p["id"]) == 0
    assert _ledger_balance(c["id"]) == 7 and _ledger_balance(p["id"]) == 0


def test_burned_tokens_leave_circulation(register):
    """I1b: supply is mints minus burns. A redemption shrinks the total -- it
    does not move it sideways to the poster."""
    cp, p, _ = register("shrink_poster")
    cc, c, _ = register("shrink_buyer")
    _fund(c["id"], 10)
    item = _offer(cp, price=4)
    cc.post(f"/api/catalog/{item['id']}/claim")

    minted = db.query_one("SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE kind='earn'")["s"]
    burned = db.query_one("SELECT COALESCE(SUM(amount),0) AS s FROM token_entries WHERE kind='burn'")["s"]
    held = db.query_one("SELECT COALESCE(SUM(balance),0) AS s FROM users")["s"]
    assert int(minted) == 10 and int(burned) == 4
    assert int(held) == int(minted) - int(burned) == 6


def test_claim_price_zero_no_ledger_entry(register):
    cp, p, _ = register("freebie")
    cc, c, _ = register("taker")
    item = _offer(cp, price=0, quantity=2)

    r = cc.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 201 and r.json()["status"] == "redeemed"

    # Nothing to burn, so no ledger row at all (I8).
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries")["c"] == 0
    # Quantity decremented but not exhausted -> still active.
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 1 and it["status"] == "active"


def test_claim_unlimited_quantity_stays_open(register):
    cp, p, _ = register("unlim")
    cc, c, _ = register("unlimbuyer")
    item = _offer(cp, price=0)   # quantity None = unlimited
    cc.post(f"/api/catalog/{item['id']}/claim")
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] is None and it["status"] == "active"


def test_last_unit_goes_to_whoever_claims_first(register):
    cp, p, _ = register("oneseller")
    c1, u1, _ = register("firstbuyer")
    c2, u2, _ = register("secondbuyer")
    _fund(u1["id"], 10)
    _fund(u2["id"], 10)
    item = _offer(cp, price=1, quantity=1)

    assert c1.post(f"/api/catalog/{item['id']}/claim").status_code == 201
    r = c2.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 409 and r.json()["detail"] == "item_closed"
    # The one who missed out kept their tokens.
    assert _balance(u1["id"]) == 9 and _balance(u2["id"]) == 10


def test_the_poster_has_no_veto(register):
    """T6/T11: a listing is binding until withdrawn, so the endpoints that let a
    poster stand in the way are gone -- not merely unused."""
    cp, p, _ = register("veto_poster")
    cc, c, _ = register("veto_claimant")
    _fund(c["id"], 10)
    item = _offer(cp, price=2, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    # Gone from the routing table, not merely guarded. (The HTTP call answers
    # 405: an unknown POST falls through to the static mount, which serves GET.)
    routes = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert not [p for p in routes if p.endswith(("/accept", "/decline", "/cancel"))]
    for verb, client in (("accept", cp), ("decline", cp), ("cancel", cc)):
        assert client.post(f"/api/claims/{claim['id']}/{verb}").status_code >= 400

    # Withdrawing the listing is the poster's real power, and it is forward-only.
    assert cp.patch(f"/api/catalog/{item['id']}", json={"status": "closed"}).status_code == 200
    row = db.query_one("SELECT status FROM catalog_claims WHERE id=%s", (claim["id"],))
    assert row["status"] == "redeemed"


def test_claims_are_never_updated(register):
    """I7: one row, written once. decided_at is stamped at insert."""
    cp, p, _ = register("imm_poster")
    cc, c, _ = register("imm_claimant")
    _fund(c["id"], 10)
    item = _offer(cp, price=1, quantity=2)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    row = db.query_one(
        "SELECT status, created_at, decided_at FROM catalog_claims WHERE id=%s",
        (claim["id"],),
    )
    assert row["status"] == "redeemed"
    assert row["decided_at"] == row["created_at"]


# ---- claims list ------------------------------------------------------------

def test_list_claims_by_role(register):
    cp, p, _ = register("rl_poster")
    cc, c, _ = register("rl_claimant")
    _fund(c["id"], 10)
    item = _offer(cp, price=1, quantity=5)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    # Claimant sees it as their request, with the item embedded.
    mine = cc.get("/api/claims").json()
    assert len(mine) == 1
    assert mine[0]["id"] == claim["id"]
    assert mine[0]["item"]["id"] == item["id"]
    assert mine[0]["item"]["poster"] == {"id": p["id"], "display_name": "rl_poster"}  # counterparty

    # Poster sees it as a request on their item, with the claimant.
    theirs = cp.get("/api/claims?role=poster").json()
    assert len(theirs) == 1
    assert theirs[0]["claimant"] == {"id": c["id"], "display_name": "rl_claimant"}    # counterparty

    # Poster has no claims as a claimant.
    assert cp.get("/api/claims").json() == []

    # Status filter. Only one state is written now; the others read back empty.
    assert len(cc.get("/api/claims?status=redeemed").json()) == 1
    assert cc.get("/api/claims?status=canceled").json() == []
