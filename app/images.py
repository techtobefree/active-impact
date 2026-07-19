"""Images: base64 upload, authenticated streaming, and hard delete.

Photos live in Postgres as BYTEA (OVERVIEW.md D11). Uploads are base64-in-JSON
and only the owning entity's leader/poster may attach one; the decoded payload is
capped at 10 MB. Reads require auth (D12) and stream the raw bytes with a private
cache header; the frontend fetches with Bearer and builds a blob URL. Delete is a
hard delete -- nothing references image rows.

See docs/design/API.md § Images and DOMAIN.md (images table).
"""
from __future__ import annotations

import base64
import binascii
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app import db
from app.auth import current_user
from app.deps import api_error

router = APIRouter()

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB decoded (OVERVIEW.md Constants)


# ---- request body -----------------------------------------------------------

class ImageUpload(BaseModel):
    entity: Literal["project", "catalog_item"]
    entity_id: int
    content_type: str
    data_base64: str


# ---- helpers ----------------------------------------------------------------

def _may_manage(entity: str, entity_id: int, user_id: int) -> bool:
    """True if user leads the project / posts the catalog item the image is on."""
    if entity == "project":
        return db.query_one(
            "SELECT 1 FROM project_leaders WHERE project_id = %s AND user_id = %s",
            (entity_id, user_id),
        ) is not None
    # catalog_item
    return db.query_one(
        "SELECT 1 FROM catalog_items WHERE id = %s AND poster_id = %s",
        (entity_id, user_id),
    ) is not None


def _get_image(image_id: int) -> dict | None:
    return db.query_one("SELECT * FROM images WHERE id = %s", (image_id,))


# ---- upload -----------------------------------------------------------------

@router.post("/images", status_code=201)
def upload_image(body: ImageUpload, user: dict = Depends(current_user)):
    # Only the owning entity's leader/poster may attach an image (covers a
    # missing entity too -- nobody manages a project/item that doesn't exist).
    if not _may_manage(body.entity, body.entity_id, user["id"]):
        code = "not_a_leader" if body.entity == "project" else "not_yours"
        raise api_error(403, code)

    if body.content_type not in ALLOWED_CONTENT_TYPES:
        raise api_error(422, "bad_content_type")

    try:
        data = base64.b64decode(body.data_base64)
    except (binascii.Error, ValueError):
        raise api_error(422, "bad_content_type")

    if len(data) > MAX_IMAGE_BYTES:
        raise api_error(413, "image_too_large")

    with db.tx() as c:
        row = c.execute(
            "INSERT INTO images(entity, entity_id, content_type, bytes, size, "
            "uploaded_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                body.entity,
                body.entity_id,
                body.content_type,
                data,  # psycopg3 adapts a bytes object to BYTEA
                len(data),
                user["id"],
            ),
        ).fetchone()
    return {"id": row["id"]}


# ---- stream (auth required) -------------------------------------------------

@router.get("/images/{image_id}")
def get_image(image_id: int, user: dict = Depends(current_user)):
    row = _get_image(image_id)
    if not row:
        raise api_error(404, "not_found")
    return Response(
        content=bytes(row["bytes"]),
        media_type=row["content_type"],
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ---- delete -----------------------------------------------------------------

@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: int, user: dict = Depends(current_user)):
    row = _get_image(image_id)
    if not row:
        raise api_error(404, "not_found")
    allowed = row["uploaded_by"] == user["id"] or _may_manage(
        row["entity"], row["entity_id"], user["id"]
    )
    if not allowed:
        raise api_error(403, "not_yours")
    with db.tx() as c:
        c.execute("DELETE FROM images WHERE id = %s", (image_id,))
    return Response(status_code=204)
