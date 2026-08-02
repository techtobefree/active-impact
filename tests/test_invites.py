"""SOCIAL.md §5b — inviting people from your follow graph to a project.

The founder's ask: the Invite button offers "other people we follow, or people
that follow us". These cover that both directions count, that the server enforces
it rather than trusting the picker, and that an invite lands where it should —
the invitee's notifications — and nowhere else. S-I11…S-I13.
"""
from app import db

from tests.test_events import make_project


def _invitable(client, project_id):
    r = client.get(f"/api/projects/{project_id}/invitable")
    assert r.status_code == 200, r.text
    return r.json()


def _invite(client, project_id, ids):
    return client.post(f"/api/projects/{project_id}/invite", json={"user_ids": ids})


# ---- who is invitable (S12) -------------------------------------------------

def test_people_i_follow_are_invitable(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana)
    assert [c["display_name"] for c in _invitable(ana, p["id"])] == ["ben"]


def test_people_who_follow_me_are_invitable_too(register):
    """Both directions count — the founder said "we follow, or follow us"."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    p = make_project(ana)
    assert [c["display_name"] for c in _invitable(ana, p["id"])] == ["ben"]


def test_a_stranger_is_not_invitable(register):
    ana, _, _ = register("ana")
    register("ben")
    p = make_project(ana)
    assert _invitable(ana, p["id"]) == []


def test_the_graph_is_deduplicated(register):
    """Following each other is one person, not two rows."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    p = make_project(ana)
    assert len(_invitable(ana, p["id"])) == 1


def test_somebody_who_blocked_me_is_not_invitable(register):
    """S15: a block means "stop reaching me", and an invite is reaching them."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    ben.post(f"/api/users/{ana_u['id']}/block")
    p = make_project(ana)
    assert _invitable(ana, p["id"]) == []


def test_invitable_marks_who_i_already_invited(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana)
    assert _invitable(ana, p["id"])[0]["invited"] is False
    _invite(ana, p["id"], [ben_u["id"]])
    assert _invitable(ana, p["id"])[0]["invited"] is True


# ---- inviting (S-I12, S-I13) ------------------------------------------------

def test_inviting_someone_in_my_graph_works(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana)
    assert _invite(ana, p["id"], [ben_u["id"]]).json() == {"invited": 1}


def test_the_server_enforces_the_graph_not_just_the_picker(register):
    """S-I12: an endpoint that took arbitrary user ids would be a blast weapon."""
    ana, _, _ = register("ana")
    ben, ben_u, _ = register("ben")          # no relationship at all
    p = make_project(ana)
    assert _invite(ana, p["id"], [ben_u["id"]]).json() == {"invited": 0}
    assert db.query_one("SELECT count(*) AS c FROM invites")["c"] == 0


def test_someone_who_blocked_me_cannot_be_invited(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ben.post(f"/api/users/{ana_u['id']}/follow")
    ben.post(f"/api/users/{ana_u['id']}/block")
    p = make_project(ana)
    assert _invite(ana, p["id"], [ben_u["id"]]).json() == {"invited": 0}


def test_inviting_twice_notifies_once(register):
    """S-I13: re-tapping a stale picker must change nothing."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana)
    assert _invite(ana, p["id"], [ben_u["id"]]).json() == {"invited": 1}
    assert _invite(ana, p["id"], [ben_u["id"]]).json() == {"invited": 0}
    assert db.query_one("SELECT count(*) AS c FROM invites")["c"] == 1


def test_two_different_people_inviting_me_is_two_invitations(register):
    """…but a second person's invitation is genuinely new information (S16)."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    cara, cara_u, _ = register("cara")
    ana.post(f"/api/users/{cara_u['id']}/follow")
    ben.post(f"/api/users/{cara_u['id']}/follow")
    p = make_project(ana)
    _invite(ana, p["id"], [cara_u["id"]])
    # ben can invite to somebody else's project — invites are not a leader power
    _invite(ben, p["id"], [cara_u["id"]])
    assert cara.get("/api/notifications").json()["unread"] == 2


def test_inviting_several_at_once(register):
    ana, ana_u, _ = register("ana")
    ids = []
    for name in ("ben", "cara", "dee"):
        _, u, _ = register(name)
        ana.post(f"/api/users/{u['id']}/follow")
        ids.append(u["id"])
    p = make_project(ana)
    assert _invite(ana, p["id"], ids).json() == {"invited": 3}


def test_a_mixed_list_invites_only_the_allowed_ones(register):
    """A stale picker must never make the button fail at somebody."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    stranger, stranger_u, _ = register("stranger")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana)
    assert _invite(ana, p["id"], [ben_u["id"], stranger_u["id"], 999999]).json() == {"invited": 1}


def test_inviting_nobody_is_not_an_error(register):
    ana, _, _ = register("ana")
    p = make_project(ana)
    assert _invite(ana, p["id"], []).json() == {"invited": 0}


def test_inviting_on_a_missing_project_is_404(register):
    ana, _, _ = register("ana")
    assert _invite(ana, 999999, [1]).status_code == 404


# ---- where an invite lands (S-I11) ------------------------------------------

def test_the_invitee_is_notified(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana, title="Riverside Cleanup")
    _invite(ana, p["id"], [ben_u["id"]])

    data = ben.get("/api/notifications").json()
    assert data["unread"] == 1
    item = data["items"][0]
    assert item["kind"] == "invited"
    assert item["actor"]["display_name"] == "ana"
    assert item["project"] == {"id": p["id"], "title": "Riverside Cleanup"}


def test_an_invite_is_not_public_activity(register):
    """S-I11: activity answers "what did this person do"; an invite is a message
    to one person and belongs in nobody's feed."""
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    cara, cara_u, _ = register("cara")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    cara.post(f"/api/users/{ana_u['id']}/follow")     # cara watches ana
    p = make_project(ana)
    _invite(ana, p["id"], [ben_u["id"]])

    kinds = [a["kind"] for a in cara.get("/api/feed/following").json()]
    assert "invited" not in kinds                     # not in a watcher's feed
    mine = [a["kind"] for a in ana.get(f"/api/users/{ana_u['id']}/activity").json()]
    assert "invited" not in mine                      # nor on the inviter's page
    assert cara.get("/api/notifications").json()["unread"] == 0


def test_seen_clears_invites_from_the_badge_too(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana)
    _invite(ana, p["id"], [ben_u["id"]])
    assert ben.get("/api/notifications").json()["unread"] == 1
    ben.post("/api/notifications/seen")
    assert ben.get("/api/notifications").json()["unread"] == 0
    # …and the invitation is still readable: seen is a watermark, not a delete.
    assert ben.get("/api/notifications").json()["items"][0]["kind"] == "invited"


def test_notifications_off_silences_invites_too(register):
    ana, ana_u, _ = register("ana")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    ben.patch("/api/me", json={"notify_activity": False})
    p = make_project(ana)
    _invite(ana, p["id"], [ben_u["id"]])
    assert ben.get("/api/notifications").json()["unread"] == 0


def test_an_invite_buzzes_the_invitees_phone(monkeypatch, register):
    from app import push

    sent = []
    monkeypatch.setattr(push, "_deliver",
                        lambda sub, payload, pem, subject: sent.append(payload))
    ana, ana_u, _ = register("ana", display_name="Ana Fields")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    ben.post("/api/push/subscribe", json={
        "endpoint": "https://push.example/ben", "p256dh": "k", "auth": "a"})

    p = make_project(ana, title="Riverside Cleanup")
    _invite(ana, p["id"], [ben_u["id"]])
    push._pool.shutdown(wait=True)

    assert sent == [{
        "title": "Ana Fields",
        "body": "invited you to Riverside Cleanup",
        "url": f"#/projects/{p['id']}",
    }]


def _restore_pool():
    from concurrent.futures import ThreadPoolExecutor
    from app import push
    if push._pool._shutdown:
        push._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="push")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_pool():
    yield
    _restore_pool()


# ---- event-scoped invitations (S17) -----------------------------------------

def _event_invitable(client, event_id):
    r = client.get(f"/api/events/{event_id}/invitable")
    assert r.status_code == 200, r.text
    return r.json()


def _event_invite(client, event_id, ids):
    return client.post(f"/api/events/{event_id}/invite", json={"user_ids": ids})


def _pair(register):
    """ana, who ben follows, plus a project with one event."""
    ana, ana_u, _ = register("ana", display_name="Ana Fields")
    ben, ben_u, _ = register("ben")
    ana.post(f"/api/users/{ben_u['id']}/follow")
    p = make_project(ana, title="Riverside Cleanup")
    return ana, ana_u, ben, ben_u, p


def test_inviting_to_one_event(register):
    ana, _, ben, ben_u, p = _pair(register)
    ev = p["events"][0]["id"]
    assert _event_invite(ana, ev, [ben_u["id"]]).json() == {"invited": 1}
    item = ben.get("/api/notifications").json()["items"][0]
    assert item["kind"] == "invited"
    # It points at the OCCURRENCE that was meant, not just the project.
    assert item["event"]["id"] == ev
    assert item["event"]["project_title"] == "Riverside Cleanup"
    assert item["project"] is None


def test_a_project_invite_still_points_at_the_project(register):
    ana, _, ben, ben_u, p = _pair(register)
    _invite(ana, p["id"], [ben_u["id"]])
    item = ben.get("/api/notifications").json()["items"][0]
    assert item["event"] is None
    assert item["project"]["id"] == p["id"]


def test_event_and_project_invitations_are_different_messages(register):
    """"Come to this project" and "come on Saturday" are not the same thing."""
    ana, _, ben, ben_u, p = _pair(register)
    ev = p["events"][0]["id"]
    assert _invite(ana, p["id"], [ben_u["id"]]).json() == {"invited": 1}
    assert _event_invite(ana, ev, [ben_u["id"]]).json() == {"invited": 1}
    assert ben.get("/api/notifications").json()["unread"] == 2


def test_inviting_to_the_same_event_twice_notifies_once(register):
    ana, _, ben, ben_u, p = _pair(register)
    ev = p["events"][0]["id"]
    assert _event_invite(ana, ev, [ben_u["id"]]).json() == {"invited": 1}
    assert _event_invite(ana, ev, [ben_u["id"]]).json() == {"invited": 0}


def test_two_events_of_one_project_are_separate_invitations(register):
    ana, _, ben, ben_u, p = _pair(register)
    from tests.test_events import _future
    second = ana.post(f"/api/projects/{p['id']}/events", json={
        "location_text": "North gate", "starts_at": _future(days=5), "expected_minutes": 60,
    }).json()["id"]
    _event_invite(ana, p["events"][0]["id"], [ben_u["id"]])
    _event_invite(ana, second, [ben_u["id"]])
    assert ben.get("/api/notifications").json()["unread"] == 2


def test_the_event_picker_tracks_its_own_invitations(register):
    """Being invited to the project must not mark the event as already invited."""
    ana, _, ben, ben_u, p = _pair(register)
    ev = p["events"][0]["id"]
    _invite(ana, p["id"], [ben_u["id"]])
    assert _event_invitable(ana, ev)[0]["invited"] is False
    _event_invite(ana, ev, [ben_u["id"]])
    assert _event_invitable(ana, ev)[0]["invited"] is True
    assert _invitable(ana, p["id"])[0]["invited"] is True     # project one stands


def test_the_event_graph_is_enforced_too(register):
    ana, _, _, _, p = _pair(register)
    stranger, stranger_u, _ = register("stranger")
    assert _event_invite(ana, p["events"][0]["id"], [stranger_u["id"]]).json() == {"invited": 0}


def test_inviting_to_a_missing_event_is_404(register):
    ana, _, _, _, _p = _pair(register)
    assert _event_invite(ana, 999999, [1]).status_code == 404
    assert ana.get("/api/events/999999/invitable").status_code == 404


def test_an_event_invite_buzzes_with_the_event_link(monkeypatch, register):
    from app import push

    sent = []
    monkeypatch.setattr(push, "_deliver",
                        lambda sub, payload, pem, subject: sent.append(payload))
    ana, _, ben, ben_u, p = _pair(register)
    ben.post("/api/push/subscribe", json={
        "endpoint": "https://push.example/ben", "p256dh": "k", "auth": "a"})
    ev = p["events"][0]["id"]
    _event_invite(ana, ev, [ben_u["id"]])
    push._pool.shutdown(wait=True)

    assert sent == [{
        "title": "Ana Fields",
        "body": "invited you to Riverside Cleanup",
        "url": f"#/events/{ev}",
    }]


def test_an_event_invite_is_still_not_public_activity(register):
    ana, ana_u, ben, ben_u, p = _pair(register)
    cara, cara_u, _ = register("cara")
    cara.post(f"/api/users/{ana_u['id']}/follow")
    _event_invite(ana, p["events"][0]["id"], [ben_u["id"]])
    assert "invited" not in [a["kind"] for a in cara.get("/api/feed/following").json()]
    assert cara.get("/api/notifications").json()["unread"] == 0
