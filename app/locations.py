"""Locations: the address book the app builds itself (LOCATIONS.md).

Events happen at the same places over and over, so every address typed on an
event upserts a ``locations`` row, matched on a normalized key. Nobody ever
"creates a location" — the list fills itself as a side effect of scheduling.

The payoff is FEED.md's matching: coordinates flow BOTH ways (L4), so the second
event at a venue starts out knowing where it is and can match photos by distance
from the moment it is created.

``resolve``/``apply_to_event`` run inside the caller's transaction; they are the
only code that writes ``locations``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import db
from app.auth import current_user

router = APIRouter()

SUGGEST_LIMIT = 10


def normalize(text: str) -> str:
    """The matching key (L3): lowercased, whitespace collapsed, edge punctuation
    stripped. "Riverside Park, Boathouse " and "riverside   park, boathouse" are
    one place; "Riverside Park north gate" is another.

    Deliberately NOT fuzzy: fuzzily *merging* two real venues would be a silent
    data error, while fuzzily *suggesting* (below) is safe because a human picks.
    """
    return " ".join(str(text or "").lower().split()).strip(" .,;:-")


def resolve(c, label: str, lat: float | None = None, lon: float | None = None) -> dict | None:
    """Upsert the location for this address; return its row (None for a blank).

    Idempotent under concurrency (L-I2): the UNIQUE on ``norm`` plus an
    ON CONFLICT that returns the existing row means two simultaneous creates
    produce one location, never a duplicate or an error.
    """
    norm = normalize(label)
    if not norm:
        return None
    row = c.execute("SELECT * FROM locations WHERE norm = %s", (norm,)).fetchone()
    if row is None:
        return c.execute(
            "INSERT INTO locations(label, norm, lat, lon) VALUES (%s, %s, %s, %s) "
            # A no-op update so a lost race still RETURNS the winner's row.
            "ON CONFLICT (norm) DO UPDATE SET label = locations.label RETURNING *",
            (str(label).strip(), norm, lat, lon),
        ).fetchone()
    # Teach it where this is -- but only if it does not already know (L-I4).
    if row["lat"] is None and lat is not None:
        updated = c.execute(
            "UPDATE locations SET lat = %s, lon = %s, updated_at = now() "
            "WHERE id = %s AND lat IS NULL RETURNING *",
            (lat, lon, row["id"]),
        ).fetchone()
        if updated:
            return updated
    return row


def apply_to_event(
    c, label: str, lat: float | None = None, lon: float | None = None
) -> tuple[int | None, float | None, float | None]:
    """What an event write needs: ``(location_id, lat, lon)`` for its row.

    An event with no coordinates INHERITS its location's (L-I3) -- which is the
    whole point: type an address you have used before and photos logged there
    attach themselves, with nobody touching a map.
    """
    loc = resolve(c, label, lat, lon)
    if loc is None:
        return None, lat, lon
    if lat is None and loc["lat"] is not None:
        return loc["id"], loc["lat"], loc["lon"]
    return loc["id"], lat, lon


def teach(c, location_id: int | None, lat: float | None, lon: float | None) -> None:
    """Give a location coordinates it does not have yet (L-I4). Used by the
    FEED.md record bootstrap, so a photo teaches the VENUE, not just this event."""
    if location_id is None or lat is None or lon is None:
        return
    c.execute(
        "UPDATE locations SET lat = %s, lon = %s, updated_at = now() "
        "WHERE id = %s AND lat IS NULL",
        (lat, lon, location_id),
    )


# ---- suggestions ------------------------------------------------------------

def _like(q: str) -> str:
    """Escape LIKE wildcards so a typed % or _ matches itself."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/locations")
def list_locations(
    q: str | None = Query(default=None),
    user: dict = Depends(current_user),
):
    """Address suggestions for the event forms (LOCATIONS.md §3).

    Prefix matches first, then substring; most-used first within each, then most
    recently used. No ``q`` returns the venues this community actually uses, so
    focusing an empty address field already helps. Never serves coordinates (L5).
    """
    norm = normalize(q or "")
    if norm:
        where = "WHERE l.norm LIKE %s"
        params: list = [f"%{_like(norm)}%", f"{_like(norm)}%"]
        order = "ORDER BY (l.norm LIKE %s) DESC, COUNT(e.id) DESC, l.updated_at DESC"
    else:
        where = ""
        params = []
        order = "ORDER BY COUNT(e.id) DESC, l.updated_at DESC"
    rows = db.query(
        "SELECT l.id, l.label, COUNT(e.id) AS event_count "
        "FROM locations l LEFT JOIN events e ON e.location_id = l.id "
        f"{where} GROUP BY l.id, l.label, l.updated_at {order} LIMIT %s",
        params + [SUGGEST_LIMIT],
    )
    return [
        {"id": r["id"], "label": r["label"], "event_count": int(r["event_count"])}
        for r in rows
    ]
