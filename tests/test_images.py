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


def _first_event_id(client, project):
    """The initial event created alongside a project (project detail lists events)."""
    detail = client.get(f"/api/projects/{project['id']}").json()
    return detail["events"][0]["id"]


def _upload(client, entity, entity_id, content_type="image/png", data=TINY_B64,
            is_primary=None):
    body = {
        "entity": entity,
        "entity_id": entity_id,
        "content_type": content_type,
        "data_base64": data,
    }
    if is_primary is not None:
        body["is_primary"] = is_primary
    return client.post("/api/images", json=body)


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
    r = ca.post(f"/api/projects/{proj['id']}/leaders", json={"email": "coleader_n@test.local"})
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


# ---- primary (cover) image --------------------------------------------------

def _is_primary(client, image_id):
    from app import db
    r = db.query_one("SELECT is_primary FROM images WHERE id=%s", (image_id,))
    return r["is_primary"]


def test_first_upload_becomes_primary(register):
    ca, a, _ = register("prim_a")
    proj = _project(ca)
    first = _upload(ca, "project", proj["id"]).json()["id"]
    assert _is_primary(ca, first) is True

    # A second upload does NOT steal primary automatically.
    second = _upload(ca, "project", proj["id"]).json()["id"]
    assert _is_primary(ca, second) is False
    assert _is_primary(ca, first) is True

    # Cover is the primary (the first), which also happens to be the lowest id.
    detail = ca.get(f"/api/projects/{proj['id']}").json()
    assert detail["cover_image_id"] == first
    assert detail["primary_image_id"] == first


def test_upload_with_is_primary_flag_unsets_others(register):
    ca, a, _ = register("prim_b")
    proj = _project(ca)
    first = _upload(ca, "project", proj["id"]).json()["id"]
    # Upload a new image explicitly flagged primary -> it takes over the cover.
    second = _upload(ca, "project", proj["id"], is_primary=True).json()["id"]

    assert _is_primary(ca, second) is True
    assert _is_primary(ca, first) is False
    detail = ca.get(f"/api/projects/{proj['id']}").json()
    assert detail["cover_image_id"] == second
    assert detail["primary_image_id"] == second


def test_set_primary_endpoint_unsets_others(register):
    ca, a, _ = register("prim_c")
    proj = _project(ca)
    first = _upload(ca, "project", proj["id"]).json()["id"]
    second = _upload(ca, "project", proj["id"]).json()["id"]
    assert _is_primary(ca, first) is True

    r = ca.post(f"/api/images/{second}/primary")
    assert r.status_code == 200, r.text
    assert r.json()["is_primary"] is True

    assert _is_primary(ca, second) is True
    assert _is_primary(ca, first) is False
    assert ca.get(f"/api/projects/{proj['id']}").json()["cover_image_id"] == second


def test_cover_prefers_primary_over_first_id(register):
    ca, a, _ = register("prim_d")
    proj = _project(ca)
    first = _upload(ca, "project", proj["id"]).json()["id"]
    second = _upload(ca, "project", proj["id"]).json()["id"]
    # Promote the higher-id image; cover must follow the primary, not the id order.
    assert ca.post(f"/api/images/{second}/primary").status_code == 200
    assert ca.get(f"/api/projects/{proj['id']}").json()["cover_image_id"] == second
    assert second > first


def test_delete_primary_falls_back_to_first(register):
    ca, a, _ = register("prim_e")
    proj = _project(ca)
    first = _upload(ca, "project", proj["id"]).json()["id"]
    second = _upload(ca, "project", proj["id"]).json()["id"]
    # first is primary; delete it -> cover falls back to the next by id (second).
    assert ca.delete(f"/api/images/{first}").status_code == 204
    detail = ca.get(f"/api/projects/{proj['id']}").json()
    assert detail["cover_image_id"] == second
    # No primary row remains, but the serializer still returns a cover.
    assert detail["primary_image_id"] == second


def test_set_primary_forbidden_for_stranger(register):
    ca, a, _ = register("prim_f")
    cb, b, _ = register("stranger_f2")
    proj = _project(ca)
    image_id = _upload(ca, "project", proj["id"]).json()["id"]
    r = cb.post(f"/api/images/{image_id}/primary")
    assert r.status_code == 403


def test_set_primary_missing_404(register):
    ca, a, _ = register("prim_g")
    r = ca.post("/api/images/999999/primary")
    assert r.status_code == 404
    assert r.json()["detail"] == "not_found"


def test_catalog_first_upload_primary_and_set(register):
    ca, a, _ = register("prim_h")
    item = _offer(ca)
    first = _upload(ca, "catalog_item", item["id"]).json()["id"]
    second = _upload(ca, "catalog_item", item["id"]).json()["id"]
    assert _is_primary(ca, first) is True
    assert ca.get(f"/api/catalog/{item['id']}").json()["cover_image_id"] == first

    assert ca.post(f"/api/images/{second}/primary").status_code == 200
    assert ca.get(f"/api/catalog/{item['id']}").json()["cover_image_id"] == second


# ---- event images -----------------------------------------------------------

def test_event_leader_uploads_and_surfaces(register):
    """A project leader may attach an image to one of the project's events; it
    surfaces on GET /events/{id} (cover_image_id + image_ids) and in project detail."""
    ca, a, _ = register("ev_leader_a")
    proj = _project(ca)
    eid = _first_event_id(ca, proj)

    r = _upload(ca, "event", eid)
    assert r.status_code == 201, r.text
    img = r.json()["id"]

    ev = ca.get(f"/api/events/{eid}").json()
    assert ev["cover_image_id"] == img
    assert ev["image_ids"] == [img]

    # Also surfaces on the event embedded in project detail.
    detail = ca.get(f"/api/projects/{proj['id']}").json()
    embedded = next(e for e in detail["events"] if e["id"] == eid)
    assert embedded["cover_image_id"] == img
    assert embedded["image_ids"] == [img]


def test_event_non_leader_upload_forbidden(register):
    ca, a, _ = register("ev_owner_b")
    cb, b, _ = register("ev_stranger_b")
    proj = _project(ca)
    eid = _first_event_id(ca, proj)

    r = _upload(cb, "event", eid)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_a_leader"


def test_event_upload_missing_event_forbidden(register):
    ca, a, _ = register("ev_nobody_c")
    r = _upload(ca, "event", 999999)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not_a_leader"


def test_event_set_primary_and_cover_follows(register):
    ca, a, _ = register("ev_prim_d")
    proj = _project(ca)
    eid = _first_event_id(ca, proj)
    first = _upload(ca, "event", eid).json()["id"]
    second = _upload(ca, "event", eid).json()["id"]

    assert _is_primary(ca, first) is True
    assert ca.get(f"/api/events/{eid}").json()["cover_image_id"] == first
    assert ca.get(f"/api/events/{eid}").json()["image_ids"] == [first, second]

    assert ca.post(f"/api/images/{second}/primary").status_code == 200
    assert ca.get(f"/api/events/{eid}").json()["cover_image_id"] == second


def test_event_set_primary_forbidden_for_stranger(register):
    ca, a, _ = register("ev_prim_e")
    cb, b, _ = register("ev_stranger_e")
    proj = _project(ca)
    eid = _first_event_id(ca, proj)
    img = _upload(ca, "event", eid).json()["id"]

    r = cb.post(f"/api/images/{img}/primary")
    assert r.status_code == 403
    assert r.json()["detail"] == "not_a_leader"


def test_event_delete_primary_falls_back(register):
    ca, a, _ = register("ev_prim_f")
    proj = _project(ca)
    eid = _first_event_id(ca, proj)
    first = _upload(ca, "event", eid).json()["id"]
    second = _upload(ca, "event", eid).json()["id"]

    # first is primary; delete it -> cover falls back to the next by id.
    assert ca.delete(f"/api/images/{first}").status_code == 204
    ev = ca.get(f"/api/events/{eid}").json()
    assert ev["cover_image_id"] == second
    assert ev["image_ids"] == [second]


def test_event_delete_by_stranger_forbidden(register):
    ca, a, _ = register("ev_del_g")
    cb, b, _ = register("ev_stranger_g")
    proj = _project(ca)
    eid = _first_event_id(ca, proj)
    img = _upload(ca, "event", eid).json()["id"]

    r = cb.delete(f"/api/images/{img}")
    assert r.status_code == 403, r.text
    assert ca.get(f"/api/images/{img}").status_code == 200
