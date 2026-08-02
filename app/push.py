"""Web Push: buzz the phone with the app closed (PUSH.md).

No APK, no third party, no cost. We mint a VAPID key pair on first use, keep it
in ``app_keys`` (it must stay stable -- every subscription a browser holds is
bound to it), and send encrypted payloads straight to whatever push endpoint each
browser hands us. Their service worker wakes up and posts to the OS tray.

**Who gets pushed is exactly who the bell counts** (P4): a follower of the actor,
with notifications on, not blocked, never the actor themselves. That question is
answered by the same SQL the badge uses, because a badge and a buzz that disagree
would be a bug nobody could explain.

Sending happens OFF the request path (P5): a check-in must not wait on three
round trips to Google, and a failed push must never roll back a check-in.
"""
from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Depends, Query
from py_vapid import Vapid01
from pydantic import BaseModel
from pywebpush import WebPushException, webpush

from app import db
from app.auth import current_user

router = APIRouter()
log = logging.getLogger("push")

# Small and bounded: sends are short HTTPS calls and a burst is one per follower.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="push")

_PRIVATE = "vapid_private_pem"
_PUBLIC = "vapid_public"


# ---- keys (P1) --------------------------------------------------------------

def _generate() -> tuple[str, str]:
    """A fresh VAPID pair: (private PEM, public key as base64url raw point)."""
    v = Vapid01()
    v.generate_keys()
    pem = v.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    raw = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return pem, base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def keys() -> tuple[str, str]:
    """The app's VAPID pair, minting it the first time. Stable forever after.

    Race-safe: two workers starting together both INSERT, one wins on the PK, and
    both then read the winner's pair. Rotating (deleting these rows) would
    invalidate every existing subscription -- see P-I1.
    """
    rows = {
        r["name"]: r["value"]
        for r in db.query("SELECT name, value FROM app_keys WHERE name = ANY(%s)",
                          ([_PRIVATE, _PUBLIC],))
    }
    if _PRIVATE in rows and _PUBLIC in rows:
        return rows[_PRIVATE], rows[_PUBLIC]

    pem, pub = _generate()
    with db.tx() as c:
        c.execute(
            "INSERT INTO app_keys(name, value) VALUES (%s, %s), (%s, %s) "
            "ON CONFLICT (name) DO NOTHING",
            (_PRIVATE, pem, _PUBLIC, pub),
        )
    rows = {
        r["name"]: r["value"]
        for r in db.query("SELECT name, value FROM app_keys WHERE name = ANY(%s)",
                          ([_PRIVATE, _PUBLIC],))
    }
    return rows[_PRIVATE], rows[_PUBLIC]


# ---- request bodies ---------------------------------------------------------

class SubscribeIn(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class UnsubscribeIn(BaseModel):
    endpoint: str


# ---- endpoints --------------------------------------------------------------

@router.get("/push/key")
def push_key(_user: dict = Depends(current_user)):
    """The application server key a browser needs in order to subscribe."""
    return {"public_key": keys()[1]}


@router.post("/push/subscribe", status_code=201)
def subscribe(body: SubscribeIn, user: dict = Depends(current_user)):
    """Register THIS device. Idempotent on the endpoint, which is the device's
    identity -- and re-subscribing an endpoint MOVES it to the current user (P-I4),
    so a shared phone never keeps notifying whoever had it last."""
    with db.tx() as c:
        c.execute(
            "INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id, "
            "  p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth",
            (user["id"], body.endpoint, body.p256dh, body.auth),
        )
    return {"subscribed": True}


@router.post("/push/unsubscribe")
def unsubscribe(body: UnsubscribeIn, user: dict = Depends(current_user)):
    """Stop this device buzzing. Idempotent."""
    with db.tx() as c:
        c.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = %s AND user_id = %s",
            (body.endpoint, user["id"]),
        )
    return {"subscribed": False}


@router.get("/push/status")
def status(endpoint: str = Query(...), user: dict = Depends(current_user)):
    """Is THIS device registered? The browser knows it has a subscription; only we
    know whether the server still has it."""
    row = db.query_one(
        "SELECT 1 FROM push_subscriptions WHERE endpoint = %s AND user_id = %s",
        (endpoint, user["id"]),
    )
    return {"subscribed": row is not None}


# ---- sending ----------------------------------------------------------------

def recipients(actor_id: int) -> list[dict]:
    """Every device that should buzz for something ``actor_id`` just did (P4).

    The rule is the bell's rule, asked of the same tables: a follower of the
    actor, with notify_activity on, who the actor has not blocked. The actor's
    own devices are excluded by construction -- nobody follows themselves.
    """
    return db.query(
        "SELECT ps.endpoint, ps.p256dh, ps.auth "
        "FROM push_subscriptions ps "
        "JOIN users u ON u.id = ps.user_id "
        "JOIN user_follows f ON f.follower_id = ps.user_id AND f.followee_id = %s "
        "WHERE u.notify_activity = true "
        "  AND NOT EXISTS (SELECT 1 FROM blocks b "
        "                  WHERE b.blocker_id = %s AND b.blocked_id = ps.user_id)",
        (actor_id, actor_id),
    )


def _deliver(sub: dict, payload: dict, private_pem: str, subject: str) -> None:
    """One send. A dead subscription is deleted (P3); anything else is logged and
    dropped -- "your friend arrived somewhere" does not deserve a retry queue."""
    try:
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=json.dumps(payload),
            vapid_private_key=private_pem,
            vapid_claims={"sub": subject},
            timeout=10,
        )
    except WebPushException as e:
        gone = getattr(e.response, "status_code", None) in (404, 410)
        if gone:
            # The browser threw this subscription away: uninstalled, cleared, or
            # expired. Delete it rather than failing here forever.
            with db.tx() as c:
                c.execute("DELETE FROM push_subscriptions WHERE endpoint = %s",
                          (sub["endpoint"],))
        else:
            log.warning("push failed (%s): %s", getattr(e.response, "status_code", "?"), e)
    except Exception as e:  # noqa: BLE001 - a push must never break its caller
        log.warning("push error: %s", e)


# What each notifiable kind reads as on a lock screen.
_VERB = {"rsvp": "is going to", "checked_in": "checked in at"}


def announce_activity(actor: dict, kind: str, event_id: int) -> None:
    """Buzz the actor's followers about something they just did (P5).

    Call AFTER the transaction that recorded the activity has committed: a push
    can then neither slow down nor roll back the thing it is announcing. The
    project lookup happens on the pool too, so the request path pays nothing.
    """
    if kind not in _VERB:
        return

    def work() -> None:
        try:
            subs = recipients(actor["id"])
            if not subs:
                return                      # nobody is listening; do no work at all
            row = db.query_one(
                "SELECT p.title FROM events e JOIN projects p ON p.id = e.project_id "
                "WHERE e.id = %s",
                (event_id,),
            )
            where = row["title"] if row else "a service project"
            payload = {
                "title": actor["display_name"],
                "body": f"{_VERB[kind]} {where}",
                "url": f"#/events/{event_id}",
            }
            private_pem, _ = keys()
            subject = _subject()
            for sub in subs:
                _deliver(sub, payload, private_pem, subject)
        except Exception as e:  # noqa: BLE001 - a push must never break its caller
            log.warning("push fan-out failed: %s", e)

    _pool.submit(work)


def send_invite(inviter: dict, invitee_id: int, target: dict) -> None:
    """Buzz ONE person: somebody invited them to a project or an event.

    ``target`` is ``{title, url}`` -- the caller knows whether the invitation was
    to the project or to one occurrence, and the notification should open
    whichever was meant. Directed rather than fanned out, so it goes to that
    person's devices only, still off the request path, still honouring their
    notify switch.
    """
    def work() -> None:
        try:
            subs = db.query(
                "SELECT ps.endpoint, ps.p256dh, ps.auth FROM push_subscriptions ps "
                "JOIN users u ON u.id = ps.user_id "
                "WHERE ps.user_id = %s AND u.notify_activity = true",
                (invitee_id,),
            )
            if not subs:
                return
            payload = {
                "title": inviter["display_name"],
                "body": f"invited you to {target['title']}",
                "url": target["url"],
            }
            private_pem, _ = keys()
            subject = _subject()
            for sub in subs:
                _deliver(sub, payload, private_pem, subject)
        except Exception as e:  # noqa: BLE001 - a push must never break its caller
            log.warning("invite push failed: %s", e)

    _pool.submit(work)


def _subject() -> str:
    """The VAPID `sub` claim: who to contact about this sender. Derived from the
    deployment's own address so it is not another config knob."""
    import os

    site = (os.environ.get("SITE_ADDRESS") or "").split(",")[0].strip()
    host = site.lstrip(":") or "localhost"
    return f"https://{host}" if not host.startswith("http") else host
