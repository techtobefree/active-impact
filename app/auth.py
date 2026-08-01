"""Auth: register / login / logout and the current_user dependency.

Real email+password with bcrypt; login mints an opaque 30-day bearer token
stored in the sessions table (instant revocation, nothing signed). See
docs/design/API.md § Auth and OVERVIEW.md D3.
"""
from __future__ import annotations

import re
import secrets

import bcrypt
import psycopg
from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, field_validator

from app import db
from app.deps import api_error
from app.serializers import me_shape

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254
SESSION_TTL = "30 days"


def normalize_email(v: str) -> str:
    """Lowercase + trim, then validate shape and length. Raises ValueError."""
    v = v.strip().lower()
    if len(v) > EMAIL_MAX:
        raise ValueError(f"email must be at most {EMAIL_MAX} characters")
    if not EMAIL_RE.match(v):
        raise ValueError("invalid email address")
    return v


def _hash_password(password: str) -> str:
    # bcrypt caps at 72 bytes; truncate defensively so multibyte input can't error.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def _check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())


def _new_session(c, user_id: int) -> str:
    token = secrets.token_hex(32)
    c.execute(
        "INSERT INTO sessions(token, user_id, expires_at) "
        "VALUES (%s, %s, now() + %s::interval)",
        (token, user_id, SESSION_TTL),
    )
    return token


def _bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip() or None


# Auto handles for guest accounts: "Adjective Animal" (SERVICE_LOG.md §4.1),
# picked with secrets.choice. Non-unique by design -- a rare collision is fine.
_HANDLE_ADJECTIVES = (
    "Kind", "Brave", "Sunny", "Gentle", "Bright", "Bold",
    "Calm", "Merry", "Swift", "Wise", "Warm", "Noble",
)
_HANDLE_ANIMALS = (
    "Otter", "Fox", "Heron", "Panda", "Robin", "Koala",
    "Wren", "Lynx", "Finch", "Hare", "Deer", "Crane",
)


def _new_handle() -> str:
    return f"{secrets.choice(_HANDLE_ADJECTIVES)} {secrets.choice(_HANDLE_ANIMALS)}"


def new_qr_token() -> str:
    """The permanent opaque handle a personal QR carries (CHECKIN_PROOF.md §3/P4).

    Same generator and shape as ``events.checkin_code``. Minted with the row --
    guests included, since a guest can be scanned like anyone else -- and never
    reissued: a printed code has to keep working.
    """
    return secrets.token_urlsafe(8)


class RegisterIn(BaseModel):
    email: str
    password: str
    display_name: str

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("password")
    @classmethod
    def _check_password_len(cls, v: str) -> str:
        if not (1 <= len(v) <= 72):
            raise ValueError("password must be 1-72 characters")
        return v

    @field_validator("display_name")
    @classmethod
    def _norm_display(cls, v: str) -> str:
        v = v.strip()
        if not (1 <= len(v) <= 60):
            raise ValueError("display name must be 1-60 characters")
        return v


class LoginIn(BaseModel):
    email: str
    password: str


class ConvertIn(BaseModel):
    """Attach-or-merge a guest into a real account. Reuses RegisterIn's rules:
    email regex/lowercase/length and password 1-72; display_name is optional."""
    email: str
    password: str
    display_name: str | None = None

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("password")
    @classmethod
    def _check_password_len(cls, v: str) -> str:
        if not (1 <= len(v) <= 72):
            raise ValueError("password must be 1-72 characters")
        return v

    @field_validator("display_name")
    @classmethod
    def _norm_display(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (1 <= len(v) <= 60):
            raise ValueError("display name must be 1-60 characters")
        return v


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """Resolve a bearer token -> unexpired session -> user row. Injected everywhere."""
    token = _bearer(authorization)
    if not token:
        raise api_error(401, "auth_required")
    row = db.query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = %s AND s.expires_at > now()",
        (token,),
    )
    if not row:
        raise api_error(401, "invalid_token")
    return row


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn):
    try:
        with db.tx() as c:
            user = c.execute(
                "INSERT INTO users(email, password_hash, display_name, qr_token) "
                "VALUES (%s, %s, %s, %s) RETURNING *",
                (body.email, _hash_password(body.password), body.display_name,
                 new_qr_token()),
            ).fetchone()
            token = _new_session(c, user["id"])
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "email_taken")
    return {"token": token, "user": me_shape(user)}


@router.post("/auth/login")
def login(body: LoginIn):
    email = body.email.strip().lower()
    user = db.query_one("SELECT * FROM users WHERE lower(email) = %s", (email,))
    if not user or not _check_password(body.password, user["password_hash"]):
        raise api_error(401, "invalid_credentials")
    with db.tx() as c:
        # Opportunistic cleanup of this user's expired sessions (D19).
        c.execute(
            "DELETE FROM sessions WHERE user_id = %s AND expires_at <= now()",
            (user["id"],),
        )
        token = _new_session(c, user["id"])
    return {"token": token, "user": me_shape(user)}


@router.post("/auth/guest", status_code=201)
def guest(authorization: str | None = Header(default=None)):
    """Silently create an anonymous account + session (SERVICE_LOG.md §4).

    Idempotent-ish: if a still-valid bearer token is already presented, return
    that same session's {token, user} instead of minting another guest -- so a
    reload/second call never spawns a spare guest.
    """
    token = _bearer(authorization)
    if token:
        row = db.query_one(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = %s AND s.expires_at > now()",
            (token,),
        )
        if row:
            return {"token": token, "user": me_shape(row)}
    with db.tx() as c:
        user = c.execute(
            "INSERT INTO users(email, password_hash, display_name, qr_token) "
            "VALUES (NULL, NULL, %s, %s) RETURNING *",
            (_new_handle(), new_qr_token()),
        ).fetchone()
        new_token = _new_session(c, user["id"])
    return {"token": new_token, "user": me_shape(user)}


@router.post("/auth/convert")
def convert(body: ConvertIn, user: dict = Depends(current_user)):
    """Attach-or-merge the current GUEST into a real account (D7 / §4).

    - Only a guest (email IS NULL) may convert; a real account -> 409 not_a_guest.
    - email FREE  -> set email/password_hash(+display_name) on this same row; it
      becomes a real account (same id, same records). Returns a fresh session.
    - email TAKEN -> verify the existing account's password; on match, MERGE the
      guest's records/cheers/reports into it (one tx), retire the guest, and
      return a NEW session for the EXISTING account. Mismatch -> 401.
    """
    if user["email"] is not None:
        raise api_error(409, "not_a_guest")
    guest_id = user["id"]
    existing = db.query_one(
        "SELECT * FROM users WHERE lower(email) = %s", (body.email,)
    )

    if existing is None:
        # ATTACH: this guest row gains credentials and becomes real (same id).
        try:
            with db.tx() as c:
                if body.display_name:
                    row = c.execute(
                        "UPDATE users SET email = %s, password_hash = %s, "
                        "display_name = %s, updated_at = now() WHERE id = %s RETURNING *",
                        (body.email, _hash_password(body.password), body.display_name, guest_id),
                    ).fetchone()
                else:
                    row = c.execute(
                        "UPDATE users SET email = %s, password_hash = %s, "
                        "updated_at = now() WHERE id = %s RETURNING *",
                        (body.email, _hash_password(body.password), guest_id),
                    ).fetchone()
                token = _new_session(c, guest_id)
        except psycopg.errors.UniqueViolation:
            # Lost a race for an email that was free at SELECT time.
            raise api_error(409, "email_taken")
        return {"token": token, "user": me_shape(row)}

    # MERGE: prove ownership of the existing account, then re-point + retire guest.
    if not _check_password(body.password, existing["password_hash"]):
        raise api_error(401, "invalid_credentials")
    existing_id = existing["id"]
    with db.tx() as c:
        # Records carry no per-user uniqueness -- straight re-point.
        c.execute(
            "UPDATE service_records SET user_id = %s WHERE user_id = %s",
            (existing_id, guest_id),
        )
        # cheers/reports have UNIQUE(record_id, user_id): delete the guest's rows
        # that would collide with the existing account's, then re-point the rest.
        for table in ("cheers", "reports"):
            c.execute(
                f"DELETE FROM {table} WHERE user_id = %s AND record_id IN "
                f"(SELECT record_id FROM {table} WHERE user_id = %s)",
                (guest_id, existing_id),
            )
            c.execute(
                f"UPDATE {table} SET user_id = %s WHERE user_id = %s",
                (existing_id, guest_id),
            )
        # Re-point the guest's image uploads: images.uploaded_by has no cascade,
        # so it must not dangle when the guest row is deleted.
        c.execute(
            "UPDATE images SET uploaded_by = %s WHERE uploaded_by = %s",
            (existing_id, guest_id),
        )
        # Retire the guest: its sessions first, then the row itself.
        c.execute("DELETE FROM sessions WHERE user_id = %s", (guest_id,))
        c.execute("DELETE FROM users WHERE id = %s", (guest_id,))
        token = _new_session(c, existing_id)
    return {"token": token, "user": me_shape(existing)}


@router.post("/auth/logout", status_code=204)
def logout(authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token:
        db.query("DELETE FROM sessions WHERE token = %s", (token,))
    return Response(status_code=204)
