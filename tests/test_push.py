"""PUSH.md — Web Push: subscriptions, keys, and who a buzz reaches.

The transport itself (ECDH + AES-GCM to a real push service) is pywebpush's job
and is not re-tested here. What IS ours, and what these cover: the keys are
stable, a device registers exactly once, a dead subscription is dropped, and
**the people who get pushed are exactly the people the bell counts** — the one
rule that must not drift between the two transports.
"""
import pytest

from app import db, push

from tests.test_events import make_project
from tests.test_feed_matching import _now_event


def _sub(client, endpoint="https://push.example/abc", p256dh="key-abc", auth="auth-abc"):
    return client.post("/api/push/subscribe", json={
        "endpoint": endpoint, "p256dh": p256dh, "auth": auth,
    })


# ---- keys (P-I1) ------------------------------------------------------------

def test_the_vapid_key_is_minted_once_and_stays_put(register):
    """Every subscription a browser holds is bound to this key: if it changed,
    every device would go silent without a word."""
    client, _, _ = register("ana")
    first = client.get("/api/push/key").json()["public_key"]
    second = client.get("/api/push/key").json()["public_key"]
    assert first and first == second
    # …and it survives a fresh read of the module's state, because it is in the DB.
    assert push.keys()[1] == first
    assert db.query_one("SELECT count(*) AS c FROM app_keys")["c"] == 2


def test_the_public_key_is_a_usable_application_server_key(register):
    """base64url of an uncompressed P-256 point: 65 bytes -> 87 chars, no padding."""
    client, _, _ = register("ana")
    key = client.get("/api/push/key").json()["public_key"]
    assert len(key) == 87
    assert "=" not in key and "+" not in key and "/" not in key


# ---- subscribing (P-I4) -----------------------------------------------------

def test_subscribe_then_status_then_unsubscribe(register):
    client, _, _ = register("ana")
    assert client.get("/api/push/status?endpoint=https://push.example/abc").json() == {
        "subscribed": False}
    assert _sub(client).status_code == 201
    assert client.get("/api/push/status?endpoint=https://push.example/abc").json() == {
        "subscribed": True}
    r = client.post("/api/push/unsubscribe", json={"endpoint": "https://push.example/abc"})
    assert r.json() == {"subscribed": False}
    assert client.get("/api/push/status?endpoint=https://push.example/abc").json() == {
        "subscribed": False}


def test_subscribing_twice_is_one_device(register):
    client, _, _ = register("ana")
    _sub(client)
    _sub(client, p256dh="rotated", auth="rotated")
    rows = db.query("SELECT * FROM push_subscriptions")
    assert len(rows) == 1
    assert rows[0]["p256dh"] == "rotated"     # the newest keys win


def test_a_shared_phone_moves_to_whoever_signs_in(register):
    """P-I4: the endpoint IS the device. It must never keep notifying its
    previous owner after somebody else signs in on it."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    _sub(ana, endpoint="https://push.example/phone")
    _sub(ben, endpoint="https://push.example/phone")
    rows = db.query("SELECT user_id FROM push_subscriptions")
    assert [r["user_id"] for r in rows] == [ben_u["id"]]


def test_one_person_can_have_several_devices(register):
    client, user, _ = register("ana")
    _sub(client, endpoint="https://push.example/phone")
    _sub(client, endpoint="https://push.example/laptop")
    assert db.query_one("SELECT count(*) AS c FROM push_subscriptions")["c"] == 2


def test_unsubscribing_someone_elses_device_does_nothing(register):
    ana, _, _ = register("ana")
    ben, _, _ = register("ben")
    _sub(ana, endpoint="https://push.example/ana-phone")
    ben.post("/api/push/unsubscribe", json={"endpoint": "https://push.example/ana-phone"})
    assert db.query_one("SELECT count(*) AS c FROM push_subscriptions")["c"] == 1


# ---- who gets buzzed (P-I2) -------------------------------------------------

def test_followers_devices_are_the_recipients(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    _sub(ben, endpoint="https://push.example/ben")
    assert [s["endpoint"] for s in push.recipients(ana_u["id"])] == ["https://push.example/ben"]


def test_a_stranger_is_not_a_recipient(register):
    ana, ana_u, _ = register("ana")
    ben, _, _ = register("ben")
    _sub(ben, endpoint="https://push.example/ben")     # subscribed, but follows nobody
    assert push.recipients(ana_u["id"]) == []


def test_i_am_never_buzzed_by_my_own_actions(register):
    ana, ana_u, _ = register("ana")
    _sub(ana, endpoint="https://push.example/ana")
    assert push.recipients(ana_u["id"]) == []


def test_notifications_off_means_no_buzz(register):
    """One switch governs both transports (P7) — the bell and the phone."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    _sub(ben, endpoint="https://push.example/ben")
    ben.patch("/api/me", json={"notify_activity": False})
    assert push.recipients(ana_u["id"]) == []


def test_a_blocked_follower_is_never_buzzed(register):
    """P-I2: the buzz obeys the same block the badge does."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    _sub(ben, endpoint="https://push.example/ben")
    ana.post(f"/api/users/{ben_u['id']}/block")
    assert push.recipients(ana_u["id"]) == []


def test_push_recipients_match_the_bell_exactly(register):
    """The invariant that matters most: a badge and a buzz that disagree would be
    a bug nobody could explain."""
    ana, ana_u, _ = register("ana")
    watchers = []
    for name in ("ben", "cara", "dee"):
        c, u, _ = register(name)
        c.post(f"/api/users/{ana_u['id']}/follow")
        _sub(c, endpoint=f"https://push.example/{name}")
        watchers.append((name, c, u))
    # dee turns notifications off; cara gets blocked.
    watchers[2][1].patch("/api/me", json={"notify_activity": False})
    ana.post(f"/api/users/{watchers[1][2]['id']}/block")

    p, ev = _now_event(ana)
    ana.post(f"/api/events/{ev['id']}/checkin")

    buzzed = {s["endpoint"].rsplit("/", 1)[-1] for s in push.recipients(ana_u["id"])}
    belled = {name for name, c, _ in watchers
              if c.get("/api/notifications").json()["unread"] > 0}
    assert buzzed == belled == {"ben"}


# ---- delivery (P-I3, P-I5) --------------------------------------------------

def test_a_dead_subscription_is_deleted(monkeypatch, register):
    """404/410 means the browser threw it away. Drop it rather than failing on it
    forever -- self-healing, with no cron."""
    from pywebpush import WebPushException

    client, _, _ = register("ana")
    _sub(client, endpoint="https://push.example/dead")

    class Gone:
        status_code = 410

    def boom(**kwargs):
        raise WebPushException("gone", response=Gone())

    monkeypatch.setattr(push, "webpush", boom)
    push._deliver(
        {"endpoint": "https://push.example/dead", "p256dh": "x", "auth": "y"},
        {"title": "t"}, "pem", "https://example.test",
    )
    assert db.query_one("SELECT count(*) AS c FROM push_subscriptions")["c"] == 0


def test_a_transient_failure_keeps_the_subscription(monkeypatch, register):
    from pywebpush import WebPushException

    client, _, _ = register("ana")
    _sub(client, endpoint="https://push.example/flaky")

    class Down:
        status_code = 503

    def boom(**kwargs):
        raise WebPushException("service down", response=Down())

    monkeypatch.setattr(push, "webpush", boom)
    push._deliver(
        {"endpoint": "https://push.example/flaky", "p256dh": "x", "auth": "y"},
        {"title": "t"}, "pem", "https://example.test",
    )
    assert db.query_one("SELECT count(*) AS c FROM push_subscriptions")["c"] == 1


def test_checking_in_still_works_when_push_is_broken(monkeypatch, register):
    """P-I5: a push must never fail the action that caused it."""
    ana, ana_u, _ = register("ana")
    ben, _, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    _sub(ben, endpoint="https://push.example/ben")

    def explode(*a, **kw):
        raise RuntimeError("push service on fire")

    monkeypatch.setattr(push, "recipients", explode)
    p, ev = _now_event(ana)
    r = ana.post(f"/api/events/{ev['id']}/checkin")
    assert r.status_code == 200
    assert r.json()["my_open_participation"] is not None


def test_a_notification_says_who_did_what_and_where(monkeypatch, register):
    sent = []
    monkeypatch.setattr(push, "_deliver",
                        lambda sub, payload, pem, subject: sent.append(payload))
    ana, ana_u, _ = register("ana", display_name="Ana Fields")
    ben, _, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    _sub(ben, endpoint="https://push.example/ben")

    p = make_project(ana, title="Riverside Cleanup")
    ev = p["events"][0]["id"]
    ana.post(f"/api/events/{ev}/rsvp")
    push._pool.shutdown(wait=True)          # the fan-out runs off the request path

    assert sent == [{
        "title": "Ana Fields",
        "body": "is going to Riverside Cleanup",
        "url": f"#/events/{ev}",
    }]


@pytest.fixture(autouse=True)
def _fresh_pool():
    """Each test that shuts the pool down gets a working one back."""
    yield
    from concurrent.futures import ThreadPoolExecutor
    if push._pool._shutdown:
        push._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="push")
