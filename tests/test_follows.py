"""Following a project: idempotent follow, unfollow, and the detail fields.

Covers app/projects.py's follow surface: POST /follow is idempotent (a double
follow still leaves exactly one row, count 1); DELETE /follow removes it and is
idempotent; project detail carries is_following (per requester) and
follower_count; 404 on a missing project for both verbs.
"""
from datetime import datetime, timedelta, timezone

from app import db


def _future(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def make_project(client, **extra):
    body = {
        "title": "Beach Cleanup",
        "location_text": "The Beach",
        "starts_at": _future(),
        "expected_minutes": 120,
    }
    body.update(extra)
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _follow_rows(project_id):
    return db.query("SELECT * FROM follows WHERE project_id = %s", (project_id,))


def test_follow_is_idempotent(register):
    owner, _o, _ = register("f_owner")
    vol, _v, _ = register("f_vol")
    pid = make_project(owner)["id"]

    r = vol.post(f"/api/projects/{pid}/follow")
    assert r.status_code == 200
    assert r.json() == {"is_following": True, "follower_count": 1}
    assert len(_follow_rows(pid)) == 1

    # Second follow: still one row, count unchanged.
    r = vol.post(f"/api/projects/{pid}/follow")
    assert r.status_code == 200
    assert r.json() == {"is_following": True, "follower_count": 1}
    assert len(_follow_rows(pid)) == 1


def test_unfollow_removes_the_row(register):
    owner, _o, _ = register("f_owner2")
    vol, _v, _ = register("f_vol2")
    pid = make_project(owner)["id"]

    vol.post(f"/api/projects/{pid}/follow")
    assert len(_follow_rows(pid)) == 1

    r = vol.delete(f"/api/projects/{pid}/follow")
    assert r.status_code == 200
    assert r.json() == {"is_following": False, "follower_count": 0}
    assert _follow_rows(pid) == []

    # Unfollowing again is a harmless no-op.
    r = vol.delete(f"/api/projects/{pid}/follow")
    assert r.status_code == 200
    assert r.json() == {"is_following": False, "follower_count": 0}


def test_detail_carries_is_following_and_count_per_user(register):
    owner, _o, _ = register("f_owner3")
    vol_a, _a, _ = register("f_vol3a")
    vol_b, _b, _ = register("f_vol3b")
    pid = make_project(owner)["id"]

    # No followers yet.
    detail = vol_a.get(f"/api/projects/{pid}").json()
    assert detail["is_following"] is False
    assert detail["follower_count"] == 0

    vol_a.post(f"/api/projects/{pid}/follow")

    # The follower sees is_following True and the count.
    detail = vol_a.get(f"/api/projects/{pid}").json()
    assert detail["is_following"] is True
    assert detail["follower_count"] == 1

    # A different user sees the same count but is_following False.
    detail = vol_b.get(f"/api/projects/{pid}").json()
    assert detail["is_following"] is False
    assert detail["follower_count"] == 1


def test_follow_missing_project_404(register):
    vol, _v, _ = register("f_vol4")
    assert vol.post("/api/projects/999999/follow").status_code == 404
    assert vol.delete("/api/projects/999999/follow").status_code == 404
