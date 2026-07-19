"""Catalog items + claims: price rules, claim gating, settlement, invariants.

Owns catalog invariants I7 (claim transitions + decided_at), I8 (accepted-with-
price <-> exactly one 'spend' entry), I10 (only active in-quantity offers claim;
quantity 0 -> closed). Touches I1 (balance == ledger sum after accept).
"""
import pytest

from app import db, tokens


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
    # my_claim / pending count present for the poster's own detail view
    assert item["my_claim"] is None
    assert item["pending_claims_count"] == 0


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


def test_detail_pending_count_poster_only(register):
    cp, p, _ = register("shopkeep")
    cc, c, _ = register("buyer")
    _fund(c["id"], 10)
    item = _offer(cp, price=2)
    cc.post(f"/api/catalog/{item['id']}/claim")

    poster_view = cp.get(f"/api/catalog/{item['id']}").json()
    assert poster_view["pending_claims_count"] == 1
    assert poster_view["my_claim"] is None

    buyer_view = cc.get(f"/api/catalog/{item['id']}").json()
    assert "pending_claims_count" not in buyer_view       # poster-only field
    assert buyer_view["my_claim"]["status"] == "pending"


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

    # Poster raises the price AFTER the claim exists.
    cp.patch(f"/api/catalog/{item['id']}", json={"price_tokens": 10})
    # Snapshot on the claim is unchanged.
    still = cc.get(f"/api/catalog/{item['id']}").json()["my_claim"]
    assert still["price_tokens"] == 5

    # Accept settles on the snapshot (5), not the new price (10).
    cp.post(f"/api/claims/{claim['id']}/accept")
    assert _balance(c["id"]) == 15 and _balance(p["id"]) == 5


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


def test_claim_duplicate_pending(register):
    cp, p, _ = register("dupshop")
    cc, c, _ = register("dupbuyer")
    _fund(c["id"], 10)
    item = _offer(cp, price=1)
    assert cc.post(f"/api/catalog/{item['id']}/claim").status_code == 201
    r = cc.post(f"/api/catalog/{item['id']}/claim")
    assert r.status_code == 409 and r.json()["detail"] == "already_claimed"


def test_claim_missing_item_404(register):
    cc, c, _ = register("ghost")
    assert cc.post("/api/catalog/9999/claim").status_code == 404


def test_cancel_frees_the_pending_slot(register):
    cp, p, _ = register("reshop")
    cc, c, _ = register("rebuyer")
    _fund(c["id"], 10)
    item = _offer(cp, price=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    cc.post(f"/api/claims/{claim['id']}/cancel")
    # The partial unique index only covers pending rows, so re-claiming works.
    assert cc.post(f"/api/catalog/{item['id']}/claim").status_code == 201


# ---- accept settlement (I8, I10, I1) ----------------------------------------

def test_accept_moves_one_spend_entry_and_closes_at_zero(register):
    cp, p, _ = register("seller")
    cc, c, _ = register("payer")
    _fund(c["id"], 10)
    item = _offer(cp, price=3, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    r = cp.post(f"/api/claims/{claim['id']}/accept")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted" and body["decided_at"] is not None

    # Exactly one spend entry, tagged with the claim (I8).
    spends = db.query(
        "SELECT * FROM token_entries WHERE kind='spend' AND claim_id=%s", (claim["id"],)
    )
    assert len(spends) == 1
    assert spends[0]["amount"] == 3
    assert spends[0]["from_user_id"] == c["id"] and spends[0]["to_user_id"] == p["id"]
    assert spends[0]["catalog_item_id"] == item["id"]

    # Last unit consumed -> quantity 0 and item auto-closed (I10).
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 0 and it["status"] == "closed"

    # Balances and I1.
    assert _balance(c["id"]) == 7 and _balance(p["id"]) == 3
    assert _ledger_balance(c["id"]) == 7 and _ledger_balance(p["id"]) == 3


def test_accept_price_zero_no_ledger_entry(register):
    cp, p, _ = register("freebie")
    cc, c, _ = register("taker")
    item = _offer(cp, price=0, quantity=2)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    r = cp.post(f"/api/claims/{claim['id']}/accept")
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    # No token entry at all for a free offer.
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries")["c"] == 0
    # Quantity decremented but not exhausted -> still active.
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 1 and it["status"] == "active"


def test_accept_unlimited_quantity_stays_open(register):
    cp, p, _ = register("unlim")
    cc, c, _ = register("unlimbuyer")
    item = _offer(cp, price=0)   # quantity None = unlimited
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    cp.post(f"/api/claims/{claim['id']}/accept")
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] is None and it["status"] == "active"


def test_accept_insufficient_balance_leaves_claim_pending(register):
    cp, p, _ = register("bigseller")
    cc, c, _ = register("brokebuyer")
    _fund(c["id"], 5)
    item = _offer(cp, price=100, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    r = cp.post(f"/api/claims/{claim['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"] == "insufficient_balance"

    # Nothing moved; claim untouched; item not decremented (I9 spirit).
    row = db.query_one("SELECT status, decided_at FROM catalog_claims WHERE id=%s", (claim["id"],))
    assert row["status"] == "pending" and row["decided_at"] is None
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 1 and it["status"] == "active"
    assert _balance(c["id"]) == 5 and _balance(p["id"]) == 0
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries WHERE kind='spend'")["c"] == 0


def test_accept_quantity_exhausted_on_second(register):
    cp, p, _ = register("oneseller")
    c1, u1, _ = register("firstbuyer")
    c2, u2, _ = register("secondbuyer")
    _fund(u1["id"], 10)
    _fund(u2["id"], 10)
    item = _offer(cp, price=1, quantity=1)
    claim1 = c1.post(f"/api/catalog/{item['id']}/claim").json()
    claim2 = c2.post(f"/api/catalog/{item['id']}/claim").json()

    assert cp.post(f"/api/claims/{claim1['id']}/accept").status_code == 200
    r = cp.post(f"/api/claims/{claim2['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"] == "quantity_exhausted"


def test_accept_permissions_and_state(register):
    cp, p, _ = register("acc_poster")
    cc, c, _ = register("acc_claimant")
    cx, x, _ = register("acc_stranger")
    _fund(c["id"], 10)
    item = _offer(cp, price=2, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    # Only the poster may accept.
    assert cx.post(f"/api/claims/{claim['id']}/accept").status_code == 403
    assert cc.post(f"/api/claims/{claim['id']}/accept").status_code == 403
    # Missing claim.
    assert cp.post("/api/claims/9999/accept").status_code == 404
    # Accept once, then re-accept -> claim_not_pending.
    assert cp.post(f"/api/claims/{claim['id']}/accept").status_code == 200
    r = cp.post(f"/api/claims/{claim['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"] == "claim_not_pending"


# ---- decline / cancel (I7) --------------------------------------------------

def test_decline_stamps_decided_at_no_tokens(register):
    cp, p, _ = register("decliner")
    cc, c, _ = register("declined")
    _fund(c["id"], 10)
    item = _offer(cp, price=4, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    assert claim["decided_at"] is None

    r = cp.post(f"/api/claims/{claim['id']}/decline")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "declined" and body["decided_at"] is not None

    # No tokens moved; item untouched (I7 + no ledger entry).
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries")["c"] == 1  # only the mint fund
    it = db.query_one("SELECT quantity, status FROM catalog_items WHERE id=%s", (item["id"],))
    assert it["quantity"] == 1 and it["status"] == "active"
    assert _balance(c["id"]) == 10 and _balance(p["id"]) == 0


def test_cancel_stamps_decided_at_no_tokens(register):
    cp, p, _ = register("cxl_poster")
    cc, c, _ = register("canceller")
    _fund(c["id"], 10)
    item = _offer(cp, price=4, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()

    r = cc.post(f"/api/claims/{claim['id']}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "canceled" and body["decided_at"] is not None
    assert db.query_one("SELECT COUNT(*) AS c FROM token_entries WHERE kind='spend'")["c"] == 0
    assert _balance(c["id"]) == 10 and _balance(p["id"]) == 0


def test_decline_permissions_and_state(register):
    cp, p, _ = register("dec_poster")
    cc, c, _ = register("dec_claimant")
    item = _offer(cp, price=0, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    # Claimant cannot decline (poster action).
    assert cc.post(f"/api/claims/{claim['id']}/decline").status_code == 403
    assert cp.post(f"/api/claims/{claim['id']}/decline").status_code == 200
    # Re-decline -> claim_not_pending.
    assert cp.post(f"/api/claims/{claim['id']}/decline").status_code == 409


def test_cancel_permissions_and_state(register):
    cp, p, _ = register("can_poster")
    cc, c, _ = register("can_claimant")
    item = _offer(cp, price=0, quantity=1)
    claim = cc.post(f"/api/catalog/{item['id']}/claim").json()
    # Poster cannot cancel (claimant action).
    assert cp.post(f"/api/claims/{claim['id']}/cancel").status_code == 403
    assert cc.post(f"/api/claims/{claim['id']}/cancel").status_code == 200
    assert cc.post(f"/api/claims/{claim['id']}/cancel").status_code == 409


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

    # Status filter.
    cp.post(f"/api/claims/{claim['id']}/accept")
    assert cc.get("/api/claims?status=pending").json() == []
    assert len(cc.get("/api/claims?status=accepted").json()) == 1
