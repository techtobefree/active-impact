"""Catalog items (offers/needs) and their claim lifecycle.

An offer is a standing, priced good/service (0 = free); a need is unpriced and
receives tips, not claims. Only active, in-quantity offers can be claimed. A
claim snapshots the item price at claim time, so later price edits never touch
existing claims. Tokens move exactly once, on accept: claimant -> poster
('spend'), in the same tx that decrements quantity and auto-closes at 0.

See docs/design/API.md § Catalog and DOMAIN.md (invariants I7, I8, I10).
"""
from __future__ import annotations

from typing import Literal

import psycopg
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


def _get_claim(claim_id: int) -> dict | None:
    return db.query_one("SELECT * FROM catalog_claims WHERE id = %s", (claim_id,))


def claim_brief(claim: dict) -> dict:
    """A claim on its own (claimant + snapshot price + lifecycle)."""
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
    """item_card + description, image_ids, my_claim, pending_claims_count (poster)."""
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
    # The viewer's own claim: the pending one if any, else their most recent.
    mine = db.query_one(
        "SELECT * FROM catalog_claims WHERE item_id = %s AND claimant_id = %s "
        "ORDER BY (status = 'pending') DESC, created_at DESC, id DESC LIMIT 1",
        (iid, user["id"]),
    )
    out["my_claim"] = claim_brief(mine) if mine else None
    if row["poster_id"] == user["id"]:
        out["pending_claims_count"] = int(
            db.query_one(
                "SELECT COUNT(*) AS c FROM catalog_claims "
                "WHERE item_id = %s AND status = 'pending'",
                (iid,),
            )["c"]
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
    item = _get_item(item_id)
    if not item:
        raise api_error(404, "not_found")
    if item["kind"] != "offer":
        raise api_error(409, "not_claimable")
    if item["poster_id"] == user["id"]:
        raise api_error(409, "own_item")
    if item["status"] != "active":
        raise api_error(409, "item_closed")
    try:
        with db.tx() as c:
            claim = c.execute(
                "INSERT INTO catalog_claims(item_id, claimant_id, price_tokens) "
                "VALUES (%s, %s, %s) RETURNING *",
                (item_id, user["id"], item["price_tokens"]),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Partial unique idx_claims_pending: one live claim per user per item.
        raise api_error(409, "already_claimed")
    return claim_full(claim, item)


@router.get("/claims")
def list_claims(
    role: str = Query(default="claimant"),
    status: str | None = Query(default=None),
    page: Page = Depends(pagination),
    user: dict = Depends(current_user),
):
    """My claims (claimant, default) or claims on my items (poster), newest first."""
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


@router.post("/claims/{claim_id}/accept")
def accept_claim(claim_id: int, user: dict = Depends(current_user)):
    claim = _get_claim(claim_id)
    if not claim:
        raise api_error(404, "not_found")
    item = _get_item(claim["item_id"])
    if item["poster_id"] != user["id"]:
        raise api_error(403, "not_yours")

    with db.tx() as c:
        cl = c.execute(
            "SELECT * FROM catalog_claims WHERE id = %s FOR UPDATE", (claim_id,)
        ).fetchone()
        if cl["status"] != "pending":
            raise api_error(409, "claim_not_pending")
        it = c.execute(
            "SELECT * FROM catalog_items WHERE id = %s FOR UPDATE", (item["id"],)
        ).fetchone()
        # A closed item has no stock to give (auto-closed at 0, or closed by the poster).
        if it["status"] != "active":
            raise api_error(409, "quantity_exhausted")

        # Move tokens on the SNAPSHOT price (0 = free -> no ledger entry). An
        # InsufficientBalance rolls back the whole tx, leaving the claim pending.
        if cl["price_tokens"] > 0:
            tokens.transfer(
                c, cl["claimant_id"], it["poster_id"], cl["price_tokens"], "spend",
                claim_id=cl["id"], catalog_item_id=it["id"],
            )

        if it["quantity"] is not None:
            new_q = it["quantity"] - 1
            # Auto-close when the last unit is settled (I10); 0 is stored truthfully.
            c.execute(
                "UPDATE catalog_items SET quantity = %s, status = %s, updated_at = now() "
                "WHERE id = %s",
                (new_q, "closed" if new_q == 0 else it["status"], it["id"]),
            )

        updated = c.execute(
            "UPDATE catalog_claims SET status = 'accepted', decided_at = now() "
            "WHERE id = %s RETURNING *",
            (claim_id,),
        ).fetchone()

    return claim_full(updated, _get_item(claim["item_id"]))


@router.post("/claims/{claim_id}/decline")
def decline_claim(claim_id: int, user: dict = Depends(current_user)):
    claim = _get_claim(claim_id)
    if not claim:
        raise api_error(404, "not_found")
    item = _get_item(claim["item_id"])
    if item["poster_id"] != user["id"]:
        raise api_error(403, "not_yours")
    with db.tx() as c:
        cl = c.execute(
            "SELECT status FROM catalog_claims WHERE id = %s FOR UPDATE", (claim_id,)
        ).fetchone()
        if cl["status"] != "pending":
            raise api_error(409, "claim_not_pending")
        updated = c.execute(
            "UPDATE catalog_claims SET status = 'declined', decided_at = now() "
            "WHERE id = %s RETURNING *",
            (claim_id,),
        ).fetchone()
    return claim_full(updated, item)


@router.post("/claims/{claim_id}/cancel")
def cancel_claim(claim_id: int, user: dict = Depends(current_user)):
    claim = _get_claim(claim_id)
    if not claim:
        raise api_error(404, "not_found")
    if claim["claimant_id"] != user["id"]:
        raise api_error(403, "not_yours")
    with db.tx() as c:
        cl = c.execute(
            "SELECT status FROM catalog_claims WHERE id = %s FOR UPDATE", (claim_id,)
        ).fetchone()
        if cl["status"] != "pending":
            raise api_error(409, "claim_not_pending")
        updated = c.execute(
            "UPDATE catalog_claims SET status = 'canceled', decided_at = now() "
            "WHERE id = %s RETURNING *",
            (claim_id,),
        ).fetchone()
    return claim_full(updated, _get_item(claim["item_id"]))
