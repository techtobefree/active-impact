"""SOCIAL.md — following people, their activity, blocking, notifications.

The three actions that become public activity, the one visibility rule
("someone I blocked never sees my activity") applied to every read surface, and
notifications derived from a watermark. Invariants S-I1…S-I8 live here.
"""
import base64

from app import db

from tests.test_events import make_project, _future
from tests.test_feed_matching import _now_event

TINY_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00").decode()


def _log(client, caption="Sorted boxes", **extra):
    body = {"caption": caption, "content_type": "image/png", "data_base64": TINY_B64}
    body.update(extra)
    return client.post("/api/service_records", json=body)


def _kinds(rows):
    return [r["kind"] for r in rows]


# ---- following (S-I1, S-I2) -------------------------------------------------

def test_follow_and_unfollow_are_idempotent(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    for _ in range(2):
        r = ana.post(f"/api/users/{ben_u['id']}/follow")
        assert r.status_code == 200, r.text
        assert r.json() == {"is_following": True, "follower_count": 1}
    for _ in range(2):
        r = ana.delete(f"/api/users/{ben_u['id']}/follow")
        assert r.json() == {"is_following": False, "follower_count": 0}


def test_nobody_follows_themselves(register):
    ana, ana_u, _ = register("ana")
    r = ana.post(f"/api/users/{ana_u['id']}/follow")
    assert r.status_code == 409
    assert r.json()["detail"] == "cannot_follow_self"


def test_follower_and_following_lists(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")

    followers = ben.get(f"/api/users/{ben_u['id']}/followers").json()
    assert [f["display_name"] for f in followers] == ["ana"]
    assert followers[0]["is_blocked"] is False

    following = ana.get(f"/api/users/{ana_u['id']}/following").json()
    assert [f["id"] for f in following] == [ben_u["id"]]


def test_lists_never_expose_an_email(register):
    """S-I8 — the public-shape rule holds here too."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    body = str(ben.get(f"/api/users/{ben_u['id']}/followers").json())
    assert "@" not in body and "email" not in body


def test_profile_carries_the_social_state(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = ana.get(f"/api/users/{ben_u['id']}").json()
    assert p["is_following"] is True
    assert p["follower_count"] == 1
    assert p["following_count"] == 0
    assert p["is_blocked"] is False


def test_a_guest_is_a_person_like_anyone_else(api, register):
    """S11: guests follow and are followed — most first-timers ARE guests."""
    from fastapi.testclient import TestClient
    from app.main import app
    data = api.post("/api/auth/guest").json()
    guest = TestClient(app)
    guest.headers.update({"Authorization": "Bearer " + data["token"]})
    ana, ana_u, _ = register("ana")
    assert guest.post(f"/api/users/{ana_u['id']}/follow").status_code == 200
    assert ana.get(f"/api/users/{ana_u['id']}/followers").json()[0]["id"] == data["user"]["id"]


# ---- the three public actions (S2, S-I5) ------------------------------------

def test_logging_a_service_is_activity(register):
    ana, ana_u, _ = register("ana")
    p, ev = _now_event(ana)
    _log(ana, event_id=ev["id"])
    rows = ana.get(f"/api/users/{ana_u['id']}/activity").json()
    assert _kinds(rows) == ["logged"]
    assert rows[0]["event"]["project_title"] == p["title"]
    assert rows[0]["record"]["caption"] == "Sorted boxes"   # the photo itself rides along
    assert rows[0]["actor"]["id"] == ana_u["id"]


def test_rsvp_is_activity_once(register):
    ana, ana_u, _ = register("ana")
    p = make_project(ana)
    ev = p["events"][0]["id"]
    ana.post(f"/api/events/{ev}/rsvp")
    ana.post(f"/api/events/{ev}/rsvp")   # idempotent — must not re-announce
    rows = ana.get(f"/api/users/{ana_u['id']}/activity").json()
    assert _kinds(rows) == ["rsvp"]
    assert rows[0]["event"]["id"] == ev


def test_checking_in_is_activity(register):
    ana, ana_u, _ = register("ana")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    rows = ana.get(f"/api/users/{ana_u['id']}/activity").json()
    # The rsvp the check-in ensures is silent: the check-in is the news.
    assert _kinds(rows) == ["checked_in"]


def test_checking_in_by_qr_code_is_activity(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    p, ev = _now_event(ana)
    code = ana.get(f"/api/events/{ev['id']}").json()["checkin_code"]
    ben.post(f"/api/checkin/{code}/agree")
    assert _kinds(ben.get(f"/api/users/{ben_u['id']}/activity").json()) == ["checked_in"]


def test_deleting_a_record_removes_its_activity(register):
    """S-I5: a feed must never point at something that is gone."""
    ana, ana_u, _ = register("ana")
    p, ev = _now_event(ana)
    rec = _log(ana, event_id=ev["id"]).json()
    ana.delete(f"/api/service_records/{rec['id']}")
    assert ana.get(f"/api/users/{ana_u['id']}/activity").json() == []


def test_checkout_is_not_activity(register):
    """Only the three actions the founder named are public."""
    ana, ana_u, _ = register("ana")
    p, ev = _now_event(ana)
    r = ana.post(f"/api/events/{ev['id']}/checkin")
    ana.post(f"/api/participations/{r.json()['my_open_participation']['id']}/checkout")
    assert _kinds(ana.get(f"/api/users/{ana_u['id']}/activity").json()) == ["checked_in"]


# ---- the following feed (S-I6) ----------------------------------------------

def test_the_following_feed_is_what_the_people_i_follow_did(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    cara, cara_u, _ = register("cara")
    p, ev = _now_event(ben)
    ben.post(f"/api/events/{ev['id']}/checkin")
    cara.post(f"/api/events/{ev['id']}/rsvp")

    ana.post(f"/api/users/{ben_u['id']}/follow")
    rows = ana.get("/api/feed/following").json()
    assert [r["actor"]["id"] for r in rows] == [ben_u["id"]]   # not cara: not followed

    ana.post(f"/api/users/{cara_u['id']}/follow")
    assert {r["actor"]["id"] for r in ana.get("/api/feed/following").json()} == {ben_u["id"], cara_u["id"]}


def test_my_own_activity_is_not_in_my_following_feed(register):
    ana, ana_u, _ = register("ana")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    assert ana.get("/api/feed/following").json() == []


def test_the_following_feed_is_newest_first(register):
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    p, ev = _now_event(ben)
    ben.post(f"/api/events/{ev['id']}/rsvp")
    ben.post(f"/api/events/{ev['id']}/checkin")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    assert _kinds(ana.get("/api/feed/following").json()) == ["checked_in", "rsvp"]


# ---- blocking (S-I3, S-I4, S-I7) --------------------------------------------

def test_blocking_keeps_the_follower(register):
    """S-I3, the founder's exact ask: they remain our followers."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")

    r = ana.post(f"/api/users/{ben_u['id']}/block")
    assert r.status_code == 200
    assert r.json() == {"is_blocked": True}

    followers = ana.get(f"/api/users/{ana_u['id']}/followers").json()
    assert [f["id"] for f in followers] == [ben_u["id"]]        # still there
    assert followers[0]["is_blocked"] is True                   # and marked
    assert ana.get(f"/api/users/{ana_u['id']}").json()["follower_count"] == 1


def test_a_blocked_follower_sees_none_of_my_activity(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/users/{ana_u['id']}/follow")

    assert len(ben.get("/api/feed/following").json()) == 1
    assert len(ben.get(f"/api/users/{ana_u['id']}/activity").json()) == 1

    ana.post(f"/api/users/{ben_u['id']}/block")
    assert ben.get("/api/feed/following").json() == []           # gone from the feed
    assert ben.get(f"/api/users/{ana_u['id']}/activity").json() == []  # and the profile


def test_unblocking_restores_everything(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    ana.post(f"/api/users/{ben_u['id']}/block")
    r = ana.delete(f"/api/users/{ben_u['id']}/block")
    assert r.json() == {"is_blocked": False}
    assert len(ben.get("/api/feed/following").json()) == 1


def test_a_block_does_not_hide_me_from_everyone_else(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    cara, cara_u, _ = register("cara")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    cara.post(f"/api/users/{ana_u['id']}/follow")
    ana.post(f"/api/users/{ben_u['id']}/block")
    assert ben.get("/api/feed/following").json() == []
    assert len(cara.get("/api/feed/following").json()) == 1


def test_i_always_see_my_own_activity(register):
    ana, ana_u, _ = register("ana")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    assert len(ana.get(f"/api/users/{ana_u['id']}/activity").json()) == 1


def test_nobody_blocks_themselves(register):
    ana, ana_u, _ = register("ana")
    r = ana.post(f"/api/users/{ana_u['id']}/block")
    assert r.status_code == 409
    assert r.json()["detail"] == "cannot_block_self"


def test_blocking_is_idempotent(register):
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    for _ in range(2):
        assert ana.post(f"/api/users/{ben_u['id']}/block").json() == {"is_blocked": True}
    for _ in range(2):
        assert ana.delete(f"/api/users/{ben_u['id']}/block").json() == {"is_blocked": False}


# ---- notifications (S-I7) ---------------------------------------------------

def test_unread_counts_rsvps_and_checkins_from_people_i_follow(register):
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p, ev = _now_event(ben)
    ben.post(f"/api/events/{ev['id']}/rsvp")
    ben.post(f"/api/events/{ev['id']}/checkin")

    data = ana.get("/api/notifications").json()
    assert data["unread"] == 2
    assert _kinds(data["items"]) == ["checked_in", "rsvp"]


def test_a_logged_photo_does_not_ping(register):
    """S7: photos are ambient; someone turning up is the thing worth a nudge."""
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p, ev = _now_event(ben)
    _log(ben, event_id=ev["id"])
    data = ana.get("/api/notifications").json()
    assert data["unread"] == 0 and data["items"] == []
    # …but it IS in the feed.
    assert _kinds(ana.get("/api/feed/following").json()) == ["logged"]


def test_seen_clears_the_badge_and_new_activity_raises_it_again(register):
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p, ev = _now_event(ben)
    ben.post(f"/api/events/{ev['id']}/rsvp")
    assert ana.get("/api/notifications").json()["unread"] == 1

    assert ana.post("/api/notifications/seen").json() == {"unread": 0}
    assert ana.get("/api/notifications").json()["unread"] == 0
    # The items stay readable — seen is not deleted.
    assert len(ana.get("/api/notifications").json()["items"]) == 1

    ben.post(f"/api/events/{ev['id']}/checkin")
    assert ana.get("/api/notifications").json()["unread"] == 1


def test_turning_notifications_off_silences_the_badge(register):
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p, ev = _now_event(ben)
    ben.post(f"/api/events/{ev['id']}/rsvp")
    assert ana.patch("/api/me", json={"notify_activity": False}).json()["notify_activity"] is False
    assert ana.get("/api/notifications").json()["unread"] == 0
    assert ana.patch("/api/me", json={"notify_activity": True}).json()["notify_activity"] is True
    assert ana.get("/api/notifications").json()["unread"] == 1


def test_a_blocked_follower_is_never_notified(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    ana.post(f"/api/users/{ben_u['id']}/block")
    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")
    assert ben.get("/api/notifications").json()["unread"] == 0


def test_me_carries_the_notification_preference(register):
    ana, _, _ = register("ana")
    me = ana.get("/api/me").json()
    assert me["notify_activity"] is True
