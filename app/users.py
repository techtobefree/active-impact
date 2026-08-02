"""Users: /me and public profiles + stats.

GET /api/me returns the private self view (includes email + balance); PATCH
/api/me edits display_name/bio and bumps updated_at; GET /api/users/{user_id}
returns the public profile with volunteer stats (no email, no balance).
See API.md § Users.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from app import db, social
from app.auth import current_user
from app.deps import api_error
from app.serializers import me_shape, user_public

router = APIRouter()


class MeUpdate(BaseModel):
    display_name: str | None = None
    bio: str | None = None
    notify_activity: bool | None = None  # the bell's on/off switch (SOCIAL.md S7)

    @field_validator("display_name")
    @classmethod
    def _v_display(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (1 <= len(v) <= 60):
            raise ValueError("display name must be 1-60 characters")
        return v

    @field_validator("bio")
    @classmethod
    def _v_bio(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) > 10000:
            raise ValueError("bio must be at most 10000 characters")
        return v


@router.get("/me")
def get_me(user: dict = Depends(current_user)):
    """Private self view -- includes balance."""
    return me_shape(user)


@router.patch("/me")
def update_me(body: MeUpdate, user: dict = Depends(current_user)):
    """Update display_name and/or bio, bumping updated_at. Returns me_shape."""
    # Only apply fields the client actually sent (and that aren't null) -- both
    # columns are NOT NULL, so an omitted or explicit-null field is a no-op.
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        return me_shape(user)
    # Keys come from MeUpdate's fixed field set, never raw user input -> safe to
    # interpolate into the SET clause; values stay parameterized.
    sets = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [user["id"]]
    with db.tx() as c:
        row = c.execute(
            f"UPDATE users SET {sets}, updated_at = now() WHERE id = %s RETURNING *",
            params,
        ).fetchone()
    return me_shape(row)


@router.get("/users/{user_id}")
def get_user(user_id: int, user: dict = Depends(current_user)):
    """Public profile + stats + MY relationship to them (SOCIAL.md §4).

    The social state is assembled here rather than inside ``user_public`` so the
    serializers stay a leaf module -- they are imported by app/activity.py, which
    app/social.py imports in turn.
    """
    row = db.query_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if not row:
        raise api_error(404, "not_found")
    shape = user_public(row)
    shape["is_following"] = social.is_following(user["id"], user_id)
    shape["is_blocked"] = social.is_blocked(user["id"], user_id)
    shape["follower_count"] = social.follower_count(user_id)
    shape["following_count"] = social.following_count(user_id)
    return shape
