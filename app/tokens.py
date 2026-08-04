"""Impact-token ledger -- the sacred core.

This module is the ONLY code permitted to write token_entries or users.balance
(invariant I2). Every movement goes through mint(), transfer() or burn(), each
called inside a db.tx() so the ledger row and the balance update commit together.

The token has exactly two doors: it is minted for service and burned on
redemption (TOKEN_ONCHAIN.md T1). transfer() is not a door -- a tip hands one
between people without changing how many exist.

See docs/design/DOMAIN.md § Token accounting.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

from app import audit, db, serializers
from app.auth import current_user
from app.deps import Page, api_error, pagination

router = APIRouter()

TOKENS_PER_HOUR = 1


class InsufficientBalance(Exception):
    """Raised by transfer()/burn() when the payer cannot cover the amount (-> 409)."""


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


def _debit(c, from_user_id: int, amount: int) -> None:
    """Take tokens off a balance, or refuse. The guard is the WHERE clause: only
    the statement that matches a row has taken anything, so two concurrent
    spenders of the same last token cannot both win."""
    debited = c.execute(
        "UPDATE users SET balance = balance - %s WHERE id = %s AND balance >= %s RETURNING id",
        (amount, from_user_id, amount),
    ).fetchone()
    if not debited:
        raise InsufficientBalance()


def transfer(c, from_user_id: int, to_user_id: int, amount: int, kind: str,
             claim_id: int | None = None, catalog_item_id: int | None = None,
             note: str | None = None) -> dict:
    """user -> user (kind 'tip'). Supply is unchanged; it just changes hands.

    ('spend' remains readable in old rows: before T12, redeeming an offer paid
    the poster. It burns now, so nothing writes that kind any more.)
    """
    _debit(c, from_user_id, amount)
    c.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, to_user_id))
    return c.execute(
        "INSERT INTO token_entries(from_user_id, to_user_id, amount, kind, claim_id, catalog_item_id, note) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (from_user_id, to_user_id, amount, kind, claim_id, catalog_item_id, note),
    ).fetchone()


def burn(c, from_user_id: int, amount: int, claim_id: int | None = None,
         catalog_item_id: int | None = None, note: str | None = None) -> dict:
    """user -> nobody (kind 'burn'). The tokens stop existing (T12).

    Deliberately NOT transfer(to_user_id=poster): the poster is credited with the
    deed, never the tokens (T4). Paying them would recycle the token and let one
    hour of service be honoured twice -- and then outstanding supply would no
    longer mean "service done, not yet honoured", which is the only thing it is
    for. There is no credit half of this function, and there must never be one.
    """
    _debit(c, from_user_id, amount)
    return c.execute(
        "INSERT INTO token_entries(from_user_id, to_user_id, amount, kind, claim_id, catalog_item_id, note) "
        "VALUES (%s, NULL, %s, 'burn', %s, %s, %s) RETURNING *",
        (from_user_id, amount, claim_id, catalog_item_id, note),
    ).fetchone()


def do_checkout(c, participation: dict, actor_user_id: int | None = None) -> dict | None:
    """Close an OPEN participation and mint its tokens, in the caller's tx.

    ``participation`` must carry: id, user_id, checked_in_at, expected_minutes,
    event_id, project_id (expected_minutes/event_id come from the participation's
    event; project_id is the event's project -- callers join to supply them).
    ``actor_user_id`` is who performed the checkout (self, or a leader/closer).
    Returns the updated row, or None if it was already checked out by a racing
    request (the guarded UPDATE matched no row). Used by the checkout endpoint
    and by event close.

    On the REAL transition (and only then) appends a 'check_out' row to the audit
    log in the same tx -- never on the already-checked-out (None) path.
    """
    now = c.execute("SELECT now() AS now").fetchone()["now"]
    seconds = (now - participation["checked_in_at"]).total_seconds()
    minutes = elapsed_minutes(seconds)
    tokens = tokens_for(minutes, participation["expected_minutes"])
    # Atomic transition: only the request that flips checked_out_at from NULL
    # mints. Without the `AND checked_out_at IS NULL` guard, a double-tap,
    # self-vs-leader, or checkout-vs-close race would each mint again for one
    # participation (free money; violates I4 / "tokens set once at checkout").
    row = c.execute(
        "UPDATE participations SET checked_out_at = %s, minutes = %s, tokens_awarded = %s "
        "WHERE id = %s AND checked_out_at IS NULL RETURNING *",
        (now, minutes, tokens, participation["id"]),
    ).fetchone()
    if row is None:
        return None  # already checked out by a concurrent request
    if tokens > 0:
        mint(c, participation["user_id"], tokens, participation_id=participation["id"])
    # Audit the real transition ONLY (never on the None path). A 0-minute /
    # 0-token checkout still logs -- the row records that it happened. Carries the
    # occurrence (event_id) and its project (project_id) for later reporting.
    audit.log(
        c, "check_out", actor_user_id=actor_user_id, subject_user_id=row["user_id"],
        project_id=participation["project_id"], event_id=participation["event_id"],
        participation_id=row["id"], minutes=minutes, tokens=tokens,
    )
    return row


# ---- API ----

def entry_out(entry: dict, me_id: int) -> dict:
    """Ledger row from a viewer's perspective (direction + resolved counterparty).

    A burn falls out correctly without a special case: no recipient means it is
    never "in", and the counterparty resolves to null because there genuinely
    isn't one. The client names the item instead.
    """
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
    """Recipient is EITHER a user id (profile/need-page buttons) OR an email
    (the wallet free-form field) -- exactly one. Responses never echo the email."""
    to_user_id: int | None = None
    to_email: str | None = None
    amount: int = Field(ge=1)
    note: str | None = None
    catalog_item_id: int | None = None

    @field_validator("to_email")
    @classmethod
    def _norm(cls, v: str | None) -> str | None:
        return v.strip().lower() if v is not None else None

    @field_validator("note")
    @classmethod
    def _note_len(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 10000:
            raise ValueError("note too long")
        return v

    @model_validator(mode="after")
    def _exactly_one_recipient(self):
        if (self.to_user_id is None) == (self.to_email is None):
            raise ValueError("exactly one of to_user_id or to_email is required")
        return self


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
    if body.to_user_id is not None:
        to = db.query_one("SELECT id FROM users WHERE id = %s", (body.to_user_id,))
    else:
        to = db.query_one("SELECT id FROM users WHERE lower(email) = %s", (body.to_email,))
    if not to:
        raise api_error(404, "user_not_found")
    if to["id"] == user["id"]:
        raise api_error(409, "cannot_tip_self")
    if body.catalog_item_id is not None and not db.query_one(
        "SELECT 1 FROM catalog_items WHERE id = %s", (body.catalog_item_id,)
    ):
        raise api_error(404, "not_found")
    with db.tx() as c:
        entry = transfer(
            c, user["id"], to["id"], body.amount, "tip",
            catalog_item_id=body.catalog_item_id, note=body.note,
        )
    return entry_out(entry, user["id"])
