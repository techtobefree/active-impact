"""Impact-token ledger -- the sacred core.

This module is the ONLY code permitted to write token_entries or users.balance
(invariant I2). Every movement goes through mint() or transfer(), each called
inside a db.tx() so the ledger row and the balance update commit together.

See docs/design/DOMAIN.md § Token accounting.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app import db, serializers
from app.auth import current_user
from app.deps import Page, api_error, pagination

router = APIRouter()

TOKENS_PER_HOUR = 1


class InsufficientBalance(Exception):
    """Raised by transfer() when the sender cannot cover the amount (-> 409)."""


# ---- checkout math (half-up integers; NEVER Python round(), which is banker's) ----

def elapsed_minutes(seconds: float) -> int:
    """Whole minutes elapsed, rounded half-up."""
    return (int(seconds) + 30) // 60


def tokens_for(minutes: int, expected_minutes: int) -> int:
    """Tokens for a participation: nearest hour half-up, capped at 2x expected."""
    credited = min(minutes, 2 * expected_minutes)
    return ((credited + 30) // 60) * TOKENS_PER_HOUR


# ---- ledger primitives (the only writers of token_entries / users.balance) ----

def mint(c, to_user_id: int, amount: int, participation_id: int | None = None,
         note: str | None = None) -> dict:
    """System -> user (kind 'earn'). Caller ensures amount > 0."""
    c.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, to_user_id))
    return c.execute(
        "INSERT INTO token_entries(from_user_id, to_user_id, amount, kind, participation_id, note) "
        "VALUES (NULL, %s, %s, 'earn', %s, %s) RETURNING *",
        (to_user_id, amount, participation_id, note),
    ).fetchone()


def transfer(c, from_user_id: int, to_user_id: int, amount: int, kind: str,
             claim_id: int | None = None, catalog_item_id: int | None = None,
             note: str | None = None) -> dict:
    """user -> user (kind 'tip' | 'spend'). Atomic overdraft guard on the debit."""
    debited = c.execute(
        "UPDATE users SET balance = balance - %s WHERE id = %s AND balance >= %s RETURNING id",
        (amount, from_user_id, amount),
    ).fetchone()
    if not debited:
        raise InsufficientBalance()
    c.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, to_user_id))
    return c.execute(
        "INSERT INTO token_entries(from_user_id, to_user_id, amount, kind, claim_id, catalog_item_id, note) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (from_user_id, to_user_id, amount, kind, claim_id, catalog_item_id, note),
    ).fetchone()


def do_checkout(c, participation: dict) -> dict:
    """Close an OPEN participation and mint its tokens, in the caller's tx.

    ``participation`` must carry: id, user_id, checked_in_at, expected_minutes.
    Used by the checkout endpoint and by project close.
    """
    now = c.execute("SELECT now() AS now").fetchone()["now"]
    seconds = (now - participation["checked_in_at"]).total_seconds()
    minutes = elapsed_minutes(seconds)
    tokens = tokens_for(minutes, participation["expected_minutes"])
    row = c.execute(
        "UPDATE participations SET checked_out_at = %s, minutes = %s, tokens_awarded = %s "
        "WHERE id = %s RETURNING *",
        (now, minutes, tokens, participation["id"]),
    ).fetchone()
    if tokens > 0:
        mint(c, participation["user_id"], tokens, participation_id=participation["id"])
    return row


# ---- API ----

def entry_out(entry: dict, me_id: int) -> dict:
    """Ledger row from a viewer's perspective (direction + resolved counterparty)."""
    direction = "in" if entry["to_user_id"] == me_id else "out"
    other = entry["from_user_id"] if direction == "in" else entry["to_user_id"]
    return {
        "id": entry["id"],
        "amount": entry["amount"],
        "kind": entry["kind"],
        "direction": direction,
        "note": entry["note"],
        "created_at": entry["created_at"],
        "counterparty": serializers.user_brief(other),
        "participation_id": entry["participation_id"],
        "catalog_item_id": entry["catalog_item_id"],
        "claim_id": entry["claim_id"],
    }


class TipIn(BaseModel):
    to_username: str
    amount: int = Field(ge=1)
    note: str | None = None
    catalog_item_id: int | None = None

    @field_validator("to_username")
    @classmethod
    def _norm(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("note")
    @classmethod
    def _note_len(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("note too long")
        return v


@router.get("/tokens/ledger")
def ledger(page: Page = Depends(pagination), user: dict = Depends(current_user)):
    rows = db.query(
        "SELECT * FROM token_entries WHERE from_user_id = %s OR to_user_id = %s "
        "ORDER BY id DESC LIMIT %s OFFSET %s",
        (user["id"], user["id"], page.limit, page.offset),
    )
    return [entry_out(e, user["id"]) for e in rows]


@router.post("/tokens/tip", status_code=201)
def tip(body: TipIn, user: dict = Depends(current_user)):
    to = db.query_one("SELECT id FROM users WHERE lower(username) = %s", (body.to_username,))
    if not to:
        raise api_error(404, "user_not_found")
    if to["id"] == user["id"]:
        raise api_error(409, "cannot_tip_self")
    with db.tx() as c:
        entry = transfer(
            c, user["id"], to["id"], body.amount, "tip",
            catalog_item_id=body.catalog_item_id, note=body.note,
        )
    return entry_out(entry, user["id"])
