"""Peer check-in — the ATTESTED layer (docs/design/CHECKIN_PROOF.md).

Everything in app/checkin.py and app/events.py records what somebody *says*: they
tap a button, or they hold a phone up to a sign. This module records what somebody
*else's code* corroborates.

A person's QR is a plain URL — ``{scheme}://{host}/#/s/{qr_token}/{event_id}`` — a
person and an event, nothing more (P2/P3). It is deliberately **static**: printable,
pin-it-to-the-wall, no nonce and no expiry (P5). Scanning it drives:

- ``GET  /api/events/{id}/my-qr.svg``            the code I show other people
- ``GET  /api/scan/{qr_token}/{event_id}``          resolve a scanned code
- ``POST /api/scan/{qr_token}/{event_id}/confirm``  the peer check-in

One confirmed scan writes ONE sighting — ``attestations(event, scanner, subject)``
— and that single row is evidence about **both** people, attributed to the
scanner. What it does *not* do is sign the subject's waiver: the scanner agrees on
their own device and gets a participation, while the subject's is only *upgraded*
if they already made one themselves (I14). A sighting that arrives first is kept
and upgrades them the moment they do check in (§5.4).
"""
from __future__ import annotations

import io

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Request, Response

from app import activity, audit, db, serializers
from app.auth import current_user
from app.deps import api_error
from app.projects import current_waiver

router = APIRouter()

_IS_OVER = (
    "(e.status = 'completed' OR "
    "now() > e.starts_at + make_interval(mins => e.expected_minutes))"
)


# ---- the QR payload ---------------------------------------------------------

def personal_qr_url(scheme: str, host: str, qr_token: str, event_id: int) -> str:
    """A person + an event. The project is derivable from the event, so it is not
    carried twice (P2), and nothing identifying is in the code itself (§4)."""
    return f"{scheme}://{host}/#/s/{qr_token}/{event_id}"


# ---- shared with the asserted check-in paths --------------------------------

def has_attestation(event_id: int, user_id: int) -> bool:
    """Was this person *seen* at this event, in either direction?

    Scanning somebody is as much evidence that you were there as being scanned,
    so both roles count. app/events.py and app/checkin.py call this at insert
    time, which is what lets a sighting recorded BEFORE someone checks in still
    land on their participation (P8 / §5.4).
    """
    return db.query_one(
        "SELECT 1 FROM attestations WHERE event_id = %s "
        "AND (subject_user_id = %s OR scanner_user_id = %s) LIMIT 1",
        (event_id, user_id, user_id),
    ) is not None


def _mark_open_attested(c, event_id: int, user_id: int) -> None:
    """Flip a still-OPEN participation to attested. A closed one is left alone —
    today's sighting must not vouch for a shift that ended last week (I15)."""
    c.execute(
        "UPDATE participations SET attested = true "
        "WHERE event_id = %s AND user_id = %s AND checked_out_at IS NULL",
        (event_id, user_id),
    )


# ---- helpers ----------------------------------------------------------------

def _open_event(event_id: int) -> dict | None:
    """The event a scanned code resolves to — only while it is still open (I11)."""
    return db.query_one(
        f"SELECT *, {_IS_OVER} AS is_over FROM events e "
        "WHERE e.id = %s AND e.status = 'open'",
        (event_id,),
    )


def _user_by_qr(qr_token: str) -> dict | None:
    return db.query_one(
        "SELECT id, display_name FROM users WHERE qr_token = %s", (qr_token,)
    )


def _resolve(qr_token: str, event_id: int) -> tuple[dict, dict]:
    """(person, event) for a scanned code, or 404. Both halves must be valid: an
    unknown token and a closed event are the same story to the volunteer — this
    code doesn't work — so they share one error."""
    person = _user_by_qr(qr_token)
    event = _open_event(event_id) if person else None
    if not person or not event:
        raise api_error(404, "invalid_qr")
    return person, event


def _attested_between(event_id: int, scanner_id: int, subject_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM attestations WHERE event_id = %s "
        "AND scanner_user_id = %s AND subject_user_id = %s",
        (event_id, scanner_id, subject_id),
    ) is not None


def _is_attending(event_id: int, user_id: int) -> bool:
    """RSVP'd or checked in — who may show a code for this event (§5.2)."""
    return db.query_one(
        "SELECT 1 FROM rsvps WHERE event_id = %s AND user_id = %s "
        "UNION ALL "
        "SELECT 1 FROM participations WHERE event_id = %s AND user_id = %s "
        "LIMIT 1",
        (event_id, user_id, event_id, user_id),
    ) is not None


# ---- the code I show other people -------------------------------------------

@router.get("/events/{event_id}/my-qr.svg")
def my_event_qr(event_id: int, request: Request, user: dict = Depends(current_user)):
    """My personal QR for this event. Any attendee — this is not a leader tool.

    Origin follows the same rule as the event QR: ``request.url.scheme`` + Host,
    so it is https behind Caddy and honestly http://<ip>:8000 on the dev LAN.
    """
    if not db.query_one("SELECT 1 FROM events WHERE id = %s", (event_id,)):
        raise api_error(404, "not_found")
    if not _is_attending(event_id, user["id"]):
        raise api_error(403, "not_attending")
    url = personal_qr_url(
        request.url.scheme, request.headers.get("host", ""), user["qr_token"], event_id
    )
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# ---- resolve a scanned personal QR ------------------------------------------

@router.get("/scan/{qr_token}/{event_id}")
def resolve(qr_token: str, event_id: int, user: dict = Depends(current_user)):
    """Resolve a scanned code -> who it is, which event, and the waiver to sign.

    Scanning your own code resolves fine and is flagged ``is_self`` — the UI can
    then say something kind instead of showing a dead end. Only *confirming* it
    is refused (409 ``self_scan``).
    """
    person, event = _resolve(qr_token, event_id)
    pid = event["project_id"]
    project = db.query_one("SELECT * FROM projects WHERE id = %s", (pid,))
    state = serializers.event_state(event["id"], user["id"])
    event_card = serializers.event_card(event, state)
    return {
        "person": person,
        "is_self": person["id"] == user["id"],
        "event": event_card,
        "project": serializers.project_card(project, event_card),
        "waiver": current_waiver(pid),
        "my_open_participation": state["my_open_participation"],
        "already_attested": _attested_between(event["id"], user["id"], person["id"]),
    }


# ---- confirm = the peer check-in --------------------------------------------

@router.post("/scan/{qr_token}/{event_id}/confirm", status_code=201)
def confirm(qr_token: str, event_id: int, user: dict = Depends(current_user)):
    """Record the sighting and check the scanner in — one transaction (§5.3).

    Idempotent by construction: the attestation UNIQUE absorbs a re-scan and the
    participation INSERT targets the partial unique index, so tapping twice is a
    shrug rather than a 409. Two people meeting twice at one event is one fact.
    """
    person, event = _resolve(qr_token, event_id)
    if person["id"] == user["id"]:
        raise api_error(409, "self_scan")
    if event["is_over"]:
        raise api_error(409, "event_over")

    pid = event["project_id"]
    waiver = current_waiver(pid)
    me, subject = user["id"], person["id"]

    with db.tx() as c:
        # 1. The sighting itself — append-only, direction preserved (P6).
        c.execute(
            "INSERT INTO attestations(event_id, scanner_user_id, subject_user_id) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (event_id, me, subject),
        )
        # 2. Both parties belong on the organizer's list now.
        c.execute(
            "INSERT INTO rsvps(event_id, user_id) VALUES (%s, %s), (%s, %s) "
            "ON CONFLICT (event_id, user_id) DO NOTHING",
            (event_id, me, event_id, subject),
        )
        # 3. The SCANNER is agreeing right now, on their own device: the confirm
        #    screen shows the waiver, so this insert IS their signature (I6).
        #    Targeting the partial unique index makes "already checked in" a
        #    no-op instead of a race.
        row = c.execute(
            "INSERT INTO participations(event_id, user_id, waiver_id, attested) "
            "VALUES (%s, %s, %s, true) "
            "ON CONFLICT (event_id, user_id) WHERE checked_out_at IS NULL DO NOTHING "
            "RETURNING *",
            (event_id, me, waiver["id"]),
        ).fetchone()
        if row is None:
            # They were already on site (button or code) — upgrade, don't duplicate.
            _mark_open_attested(c, event_id, me)
            row = c.execute(
                "SELECT * FROM participations WHERE event_id = %s AND user_id = %s "
                "AND checked_out_at IS NULL",
                (event_id, me),
            ).fetchone()
        else:
            audit.log(
                c, "check_in", actor_user_id=me, subject_user_id=me,
                project_id=pid, event_id=event_id, participation_id=row["id"],
                meta={"via": "scan", "attested_by": subject},
            )
            # Only the SCANNER checked in here. The subject agreed to nothing on
            # their own device (I14), so announcing them would be putting words in
            # their mouth — their participation is only ever upgraded, never made.
            activity.record(c, "checked_in", me, event_id=event_id, project_id=pid)
        # 4. The SUBJECT is present, but has agreed to nothing here — only ever
        #    upgrade an existing participation, never create one (I14).
        _mark_open_attested(c, event_id, subject)
        # 5. The sighting goes in the append-only trail on its own account, so the
        #    record survives even when neither side's participation changed.
        audit.log(
            c, "attest", actor_user_id=me, subject_user_id=subject,
            project_id=pid, event_id=event_id,
        )

    return {"participation": row, "person": person, "attested": True}
