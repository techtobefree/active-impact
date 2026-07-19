"""Images: base64 upload authorization, size/content-type gating, authed
streaming, and hard delete.

Covers every branch app/images.py owns: leader/poster-only upload (403),
bad_content_type (422), image_too_large (413), authed GET streaming the exact
bytes with the private cache header, GET auth wall (401), delete by uploader and
by entity manager (204), delete by a stranger (403), and 404s for missing ids.
"""
import base64

import pytest

# A few bytes is a valid upload -- the endpoint validates size + declared
# content_type only, never the image internals.
TINY_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00"
TINY_B64 = base64.b64encode(TINY_PNG).decode()

MAX_IMAGE_BYTES = 10 * 1024 * 1024


# ---- setup helpers ----------------------------------------------------------

def _project(client, title="Beach Cleanup"):
    r = client.post(
        "/api/projects",
        json={
            "title": title,
            "location_text": "Pier 7",
            "starts_at": "2026-08-01T10:00:00Z",
            "expected_minutes": 120,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _offer(client, title="Free Bike"):
    r = client.post(
        "/api/catalog", json={"kind": "offer", "title": title, "price_tokens": 0}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, entity, entity_id, content_type="image/png", data=TINY_B64):
    return client.post(
        "/api/images",
        json={
            "entity": entity,
            "entity_id": entity_id,
            "content_type": content_type,
            "data_base64": data,
        },
    )


# ---- upload: project leader authorization -----------------------------------

def test_leader_uploads_project_image(register):
    ca, a, _ = register("leader_a")
    proj = _project(ca)
    r = _upload(ca, "project", proj["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["id"], int)

    # The image now surfaces on the project detail (image_ids + cover_image_id).
    detail = ca.get(f"/api/projects/{proj['id']}").json()
    assert detail["image_ids"] == [body["id"]]
    assert detail["cover_image_id"] == body["id"]


def test_non_leader_upload_forbidden(register):
    ca, a, _ = register("owner_b")
    cb, b, _ = register("stranger_b")
    proj = _project(ca)
    r = _upload(cb, "project", proj["id"])
    assert r.status_code == 403, r.text


def test_upload_to_missing_project_forbidden(register):
    ca, a, _ = register("nobody_c")
    r = _upload(ca, "project", 999999)
    assert r.status_code == 403, r.text


# ---- upload: catalog poster authorization -----------------------------------

def test_poster_uploads_catalog_image(register):
    ca, a, _ = register("poster_d")
    item = _offer(ca)
    r = _upload(ca, "catalog_item", item["id"])
    assert r.status_code == 201, r.text

    detail = ca.get(f"/api/catalog/{item['id']}").json()
    assert detail["image_ids"] == [r.json()["id"]]
    assert detail["cover_image_id"] == r.json()["id"]


def test_non_poster_upload_forbidden(register):
    ca, a, _ = register("poster_e")
    cb, b, _ = register("stranger_e")
    item = _offer(ca)
    r = _upload(cb, "catalog_item", item["id"])
    assert r.status_code == 403, r.text


# ---- upload: content-type + size gating -------------------------------------

def test_bad_content_type_422(register):
    ca, a, _ = register("leader_f")
    proj = _project(ca)
    r = _upload(ca, "project", proj["id"], content_type="image/gif")
    assert r.status_code == 422
    assert r.json()["detail"] == "bad_content_type"


@pytest.mark.parametrize("ct", ["image/jpeg", "image/png", "image/webp"])
def test_all_allowed_content_types(register, ct):
    ca, a, _ = register("leader_g")
    proj = _project(ca)
    r = _upload(ca, "project", proj["id"], content_type=ct)
    assert r.status_code == 201, r.text


def test_oversized_image_413(register):
    ca, a, _ = register("leader_h")
    proj = _project(ca)
    big = base64.b64encode(b"\x00" * (MAX_IMAGE_BYTES + 1)).decode()
    r = _upload(ca, "project", proj["id"], data=big)
    assert r.status_code == 413
    assert r.json()["detail"] == "image_too_large"


def test_exactly_max_size_allowed(register):
    ca, a, _ = register("leader_i")
    proj = _project(ca)
    big = base64.b64encode(b"\x00" * MAX_IMAGE_BYTES).decode()
    r = _upload(ca, "project", proj["id"], data=big)
    assert r.status_code == 201, r.text


# ---- GET: streaming + auth wall ---------------------------------------------

def test_get_streams_bytes_with_headers(register):
    ca, a, _ = register("leader_j")
    proj = _project(ca)
    image_id = _upload(ca, "project", proj["id"], content_type="image/webp").json()["id"]

    r = ca.get(f"/api/images/{image_id}")
    assert r.status_code == 200
    assert r.content == TINY_PNG
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "private, max-age=86400"


def test_get_requires_auth(api, register):
    ca, a, _ = register("leader_k")
    proj = _project(ca)
    image_id = _upload(ca, "project", proj["id"]).json()["id"]

    # No Authorization header -> 401 (D12: reads are behind the login wall).
    r = api.get(f"/api/images/{image_id}")
    assert r.status_code == 401


def test_get_missing_404(register):
    ca, a, _ = register("leader_l")
    r = ca.get("/api/images/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "not_found"


# ---- DELETE -----------------------------------------------------------------

def test_delete_by_uploader_then_gone(register):
    ca, a, _ = register("leader_m")
    proj = _project(ca)
    image_id = _upload(ca, "project", proj["id"]).json()["id"]

    r = ca.delete(f"/api/images/{image_id}")
    assert r.status_code == 204
    assert r.content == b""

    # Hard delete -- the row is gone.
    assert ca.get(f"/api/images/{image_id}").status_code == 404


def test_delete_by_entity_leader_not_uploader(register):
    """A co-leader who did not upload may still delete a project image."""
    ca, a, _ = register("owner_n")
    cb, b, _ = register("coleader_n")
    proj = _project(ca)
    # Owner uploads; then promotes b to co-leader.
    image_id = _upload(ca, "project", proj["id"]).json()["id"]
    r = ca.post(f"/api/projects/{proj['id']}/leaders", json={"username": "coleader_n"})
    assert r.status_code == 201, r.text

    r = cb.delete(f"/api/images/{image_id}")
    assert r.status_code == 204, r.text
    assert ca.get(f"/api/images/{image_id}").status_code == 404


def test_delete_by_stranger_forbidden(register):
    ca, a, _ = register("owner_o")
    cb, b, _ = register("stranger_o")
    proj = _project(ca)
    image_id = _upload(ca, "project", proj["id"]).json()["id"]

    r = cb.delete(f"/api/images/{image_id}")
    assert r.status_code == 403, r.text
    # Still there after a forbidden delete.
    assert ca.get(f"/api/images/{image_id}").status_code == 200


def test_delete_missing_404(register):
    ca, a, _ = register("leader_p")
    r = ca.delete("/api/images/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "not_found"
