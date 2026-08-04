"""Catalog items (offers/needs) and the claim that redeems one.

An offer is a standing, priced good/service (0 = free); a need is unpriced and
receives tips, not claims. Only active, in-quantity offers can be claimed.

**A claim has no lifecycle** (T11). Claiming *is* the redemption: one transaction
snapshots the price, burns it, decrements quantity, auto-closes at 0, and writes
the claim already settled. There is nothing to accept, decline or cancel,
because a listing is binding until the poster withdraws it (T6) -- a poster who
could refuse an individual would hold a veto the domain says they do not have.

**The tokens are destroyed, not paid** (T12). The poster's return is the burn
itself: a public record that this much service was honoured here (T5).

See docs/design/API.md § Catalog and DOMAIN.md (invariants I7, I8, I10).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from app import db, serializers, tokens
from app.auth import current_user
from app.deps import Page, api_error, pagination

router = APIRouter()


# ---- request bodies ---------------------------------------------------------

class CatalogCreate(BaseModel):
    kind: Literal["offer", "need"]
    title: str
    description: str | None = None
    price_tokens: int | None = Field(default=None, ge=0)
    quantity: int | None = Field(default=None, gt=0)

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 120):
            raise ValueError("title must be 1-120 characters")
        return v

    @field_validator("description")
    @classmethod
    def _v_desc(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("description too long")
        return v


class CatalogUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    price_tokens: int | None = Field(default=None, ge=0)
    quantity: int | None = Field(default=None, gt=0)
    status: Literal["active", "closed"] | None = None

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (1 <= len(v) <= 120):
            raise ValueError("title must be 1-120 characters")
        return v

    @field_validator("description")
    @classmethod
    def _v_desc(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("description too long")
        return v


# ---- helpers ----------------------------------------------------------------

def _get_item(item_id: int) -> dict | None:
    return db.query_one("SELECT * FROM catalog_items WHERE id = %s", (item_id,))


def claim_brief(claim: dict) -> dict:
    """A claim on its own (claimant + the price that was burned for it)."""
    return {
        "id": claim["id"],
        "item_id": claim["item_id"],
        "claimant": serializers.user_brief(claim["claimant_id"]),
        "price_tokens": claim["price_tokens"],
        "status": claim["status"],
        "created_at": claim["created_at"],
        "decided_at": claim["decided_at"],
    }


def claim_full(claim: dict, item_row: dict) -> dict:
    """A claim with its item card -- covers both counterparties (poster + claimant)."""
    out = claim_brief(claim)
    out["item"] = serializers.item_card(item_row)
    return out


def _item_detail(row: dict, user: dict) -> dict:
    """item_card + description, image_ids, my_claim, and the poster's burn tally."""
    iid = row["id"]
    out = serializers.item_card(row)
    out["description"] = row["description"]
    out["image_ids"] = [
        r["id"]
        for r in db.query(
            "SELECT id FROM images WHERE entity = 'catalog_item' AND entity_id = %s "
            "ORDER BY id",
            (iid,),
        )
    ]
    # The viewer's most recent claim. Nothing to prefer by status any more:
    # every claim is settled, so the newest one is the live proof.
    mine = db.query_one(
        "SELECT * FROM catalog_claims WHERE item_id = %s AND claimant_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (iid, user["id"]),
    )
    out["my_claim"] = claim_brief(mine) if mine else None
    if row["poster_id"] == user["id"]:
        # What the poster gets instead of payment (T5). Two separate truths, on
        # purpose: the count comes from the claims, the tokens from the ledger --
        # pre-T12 redemptions were paid rather than burned, and this must not
        # quietly recount them as burns.
        out["redeemed_count"] = int(
            db.query_one(
                "SELECT COUNT(*) AS c FROM catalog_claims "
                "WHERE item_id = %s AND status = 'redeemed'",
                (iid,),
            )["c"]
        )
        out["burned_tokens"] = int(
            db.query_one(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM token_entries "
                "WHERE kind = 'burn' AND catalog_item_id = %s",
                (iid,),
            )["s"]
        )
    return out


# ---- catalog items ----------------------------------------------------------

@router.get("/catalog")
def list_catalog(
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None),
    mine: bool = Query(default=False),
    status: str = Query(default="active"),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """item_card[] for a filter, newest first, paginated."""
    where = ["status = %s"]
    params: list = [status]
    if kind:
        where.append("kind = %s")
        params.append(kind)
    if mine:
        where.append("poster_id = %s")
        params.append(user["id"])
    if q:
        where.append("(title ILIKE %s OR description ILIKE %s)")
        like = f"%{q}%"
        params += [like, like]
    sql = (
        "SELECT * FROM catalog_items WHERE "
        + " AND ".join(where)
        + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
    )
    params += [page.limit, page.offset]
    rows = db.query(sql, params)
    return [serializers.item_card(r) for r in rows]


@router.post("/catalog", status_code=201)
def create_catalog(body: CatalogCreate, user: dict = Depends(current_user)):
    # Every offer is priced (0 = free); a need is never priced.
    if body.kind == "offer" and body.price_tokens is None:
        raise api_error(422, "price_required")
    if body.kind == "need" and body.price_tokens is not None:
        raise api_error(422, "price_on_need")
    with db.tx() as c:
        row = c.execute(
            "INSERT INTO catalog_items"
            "(poster_id, kind, title, description, price_tokens, quantity) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (
                user["id"],
                body.kind,
                body.title,
                body.description or "",
                body.price_tokens,
                body.quantity,
            ),
        ).fetchone()
    return _item_detail(row, user)


@router.get("/catalog/{item_id}")
def get_catalog(item_id: int, user: dict = Depends(current_user)):
    row = _get_item(item_id)
    if not row:
        raise api_error(404, "not_found")
    return _item_detail(row, user)


@router.patch("/catalog/{item_id}")
def update_catalog(
    item_id: int, body: CatalogUpdate, user: dict = Depends(current_user)
):
    row = _get_item(item_id)
    if not row:
        raise api_error(404, "not_found")
    if row["poster_id"] != user["id"]:
        raise api_error(403, "not_yours")

    data = body.model_dump(exclude_unset=True)
    # Drop explicit nulls for NOT NULL columns so {"title": null} is a no-op, not a 500.
    data = {k: v for k, v in data.items() if not (k in ("title", "description", "status") and v is None)}
    # Guard the item's kind/price invariant so a bad edit can't hit the DB CHECK.
    if "price_tokens" in data:
        if row["kind"] == "need" and data["price_tokens"] is not None:
            raise api_error(422, "price_on_need")
        if row["kind"] == "offer" and data["price_tokens"] is None:
            raise api_error(422, "price_required")

    if not data:
        return _item_detail(row, user)

    sets = ", ".join(f"{k} = %s" for k in data)
    params = list(data.values()) + [item_id]
    with db.tx() as c:
        c.execute(
            f"UPDATE catalog_items SET {sets}, updated_at = now() WHERE id = %s",
            params,
        )
    return _item_detail(_get_item(item_id), user)


# ---- claims -----------------------------------------------------------------

@router.post("/catalog/{item_id}/claim", status_code=201)
def create_claim(item_id: int, user: dict = Depends(current_user)):
    """Claim = redeem. One transaction, settled when it returns.

    Every check that guards the settlement is re-run INSIDE the row lock: the
    cheap ones above it only save a round trip. Two people going for the last
    unit at once both pass the first read; only one gets past `FOR UPDATE`.
    """
    item = _get_item(item_id)
    if not item:
        raise api_error(404, "not_found")
    if item["kind"] != "offer":
        raise api_error(409, "not_claimable")
    if item["poster_id"] == user["id"]:
        raise api_error(409, "own_item")
    if item["status"] != "active":
        raise api_error(409, "item_closed")

    with db.tx() as c:
        it = c.execute(
            "SELECT * FROM catalog_items WHERE id = %s FOR UPDATE", (item_id,)
        ).fetchone()
        # Withdrawn, or the last unit went to somebody else while we read.
        if it["status"] != "active":
            raise api_error(409, "item_closed")

        # decided_at is stamped at insert: there is no later moment to stamp it.
        claim = c.execute(
            "INSERT INTO catalog_claims"
            "(item_id, claimant_id, price_tokens, status, decided_at) "
            "VALUES (%s, %s, %s, 'redeemed', now()) RETURNING *",
            (item_id, user["id"], it["price_tokens"]),
        ).fetchone()

        # The price as it stands right now -- a later edit never reprices this.
        # Too poor to redeem rolls the whole thing back: no claim, no decrement.
        if it["price_tokens"] > 0:
            tokens.burn(
                c, user["id"], it["price_tokens"],
                claim_id=claim["id"], catalog_item_id=it["id"],
            )

        if it["quantity"] is not None:
            new_q = it["quantity"] - 1
            # Auto-close when the last unit goes (I10); 0 is stored truthfully.
            c.execute(
                "UPDATE catalog_items SET quantity = %s, status = %s, updated_at = now() "
                "WHERE id = %s",
                (new_q, "closed" if new_q == 0 else it["status"], it["id"]),
            )

    return claim_full(claim, _get_item(item_id))


@router.get("/claims")
def list_claims(
    role: str = Query(default="claimant"),
    status: str | None = Query(default=None),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """What I redeemed (claimant, default) or what was redeemed from me (poster)."""
    params: list = [user["id"]]
    if role == "poster":
        sql = (
            "SELECT cc.* FROM catalog_claims cc "
            "JOIN catalog_items ci ON ci.id = cc.item_id "
            "WHERE ci.poster_id = %s"
        )
    else:  # claimant (default)
        sql = "SELECT cc.* FROM catalog_claims cc WHERE cc.claimant_id = %s"
    if status:
        sql += " AND cc.status = %s"
        params.append(status)
    sql += " ORDER BY cc.created_at DESC, cc.id DESC LIMIT %s OFFSET %s"
    params += [page.limit, page.offset]
    rows = db.query(sql, params)
    return [claim_full(r, _get_item(r["item_id"])) for r in rows]

# There is no accept, decline or cancel. Claiming already settled it (T11), and
# an accepted claim's tokens are gone (T12) -- there is no undo to offer either
# side. The poster's control is PATCH status='closed', which is forward-only.
