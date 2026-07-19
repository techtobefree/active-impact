"""Auth: register / login / logout and the current_user dependency.

Real username+password with bcrypt; login mints an opaque 30-day bearer token
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

USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,30}$")
SESSION_TTL = "30 days"


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


class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str | None = None

    @field_validator("username")
    @classmethod
    def _norm_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.match(v):
            raise ValueError("username must be 3-30 chars of a-z, 0-9, _ or -")
        return v

    @field_validator("password")
    @classmethod
    def _check_password_len(cls, v: str) -> str:
        if not (8 <= len(v) <= 72):
            raise ValueError("password must be 8-72 characters")
        return v

    @field_validator("display_name")
    @classmethod
    def _norm_display(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) > 60:
            raise ValueError("display name too long")
        return v or None


class LoginIn(BaseModel):
    username: str
    password: str


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
    display = body.display_name or body.username
    try:
        with db.tx() as c:
            user = c.execute(
                "INSERT INTO users(username, password_hash, display_name) "
                "VALUES (%s, %s, %s) RETURNING *",
                (body.username, _hash_password(body.password), display),
            ).fetchone()
            token = _new_session(c, user["id"])
    except psycopg.errors.UniqueViolation:
        raise api_error(409, "username_taken")
    return {"token": token, "user": me_shape(user)}


@router.post("/auth/login")
def login(body: LoginIn):
    username = body.username.strip().lower()
    user = db.query_one("SELECT * FROM users WHERE lower(username) = %s", (username,))
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


@router.post("/auth/logout", status_code=204)
def logout(authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token:
        db.query("DELETE FROM sessions WHERE token = %s", (token,))
    return Response(status_code=204)
