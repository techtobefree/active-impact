"""Which event was this service logged at? (FEED.md §4)

The whole merge of the two feeds rests on this guess. A guest can log a photo
without ever having checked in, so the answer comes from whatever we honestly
know: an open check-in, a past one, an RSVP, or — failing all of those — the
phone's coordinates against the events happening around them right now.

One function, three callers: attach on create (app/records.py), preview the
target on the log screen, and rank the picker (GET /events/candidates).

Nothing here writes; ``resolve_event`` is a pure read that returns
``(event_id | None, reason | None)``.
"""
from __future__ import annotations

from app import db

# --- constants (FEED.md §4; the single source of truth) ----------------------

WINDOW_BEFORE = "2 hours"   # people arrive and start early
WINDOW_AFTER = "6 hours"    # …and post the photo on the couch that evening
MAX_MATCH_KM = 5.0          # a geo match is never made beyond this
FEED_RECORDS_PER_EVENT = 2  # how many photos a project card carries

# Priority, strongest first. The flag is the SQL column; the reason is stored on
# the record as match_reason so the guess can be audited later.
_SIGNALS = (("checked_in", "checked_in"), ("participated", "participated"), ("rsvped", "rsvp"))

# An event's LIVE WINDOW: open for collecting photos.
_IN_WINDOW = (
    f"now() >= e.starts_at - interval '{WINDOW_BEFORE}' AND "
    f"now() <= e.starts_at + make_interval(mins => e.expected_minutes) "
    f"+ interval '{WINDOW_AFTER}'"
)

# Great-circle km between the device and the event (haversine in SQL -- no
# PostGIS). The cosine is computed once in a LATERAL so a missing coordinate on
# EITHER side stays NULL all the way to distance_km.
#
# The clamp is a CASE, deliberately NOT least(1, greatest(-1, v)): Postgres's
# GREATEST/LEAST *ignore* NULLs, so greatest(-1, NULL) is -1 -- which turned an
# unlocated event into a confident "20015 km away" (acos(-1) = pi). A CASE
# propagates the NULL, which is the truth: we do not know how far away it is.
_COSINE = (
    "sin(radians(%s::double precision)) * sin(radians(e.lat)) + "
    "cos(radians(%s::double precision)) * cos(radians(e.lat)) * "
    "cos(radians(e.lon) - radians(%s::double precision))"
)
_KM = "6371 * acos(CASE WHEN d.v > 1 THEN 1 WHEN d.v < -1 THEN -1 ELSE d.v END)"

# Every event in its live window, with the caller's signals and their distance.
# Ordered so an event the caller has a signal at can never fall off the LIMIT,
# then in-progress first, then closest start time.
_CANDIDATES = f"""
SELECT * FROM (
    SELECT e.id AS event_id, e.project_id, p.title AS project_title,
           e.starts_at, e.location_text,
           {_KM} AS distance_km,
           (now() BETWEEN e.starts_at
                      AND e.starts_at + make_interval(mins => e.expected_minutes)) AS in_progress,
           abs(extract(epoch FROM (now() - e.starts_at))) AS time_delta,
           EXISTS (SELECT 1 FROM participations pa WHERE pa.event_id = e.id
                   AND pa.user_id = %s AND pa.checked_out_at IS NULL) AS checked_in,
           EXISTS (SELECT 1 FROM participations pa WHERE pa.event_id = e.id
                   AND pa.user_id = %s) AS participated,
           EXISTS (SELECT 1 FROM rsvps r WHERE r.event_id = e.id
                   AND r.user_id = %s) AS rsvped
    FROM events e
    JOIN projects p ON p.id = e.project_id
    LEFT JOIN LATERAL (SELECT {_COSINE} AS v) d ON true
    WHERE {_IN_WINDOW}
) c
ORDER BY (c.checked_in OR c.participated OR c.rsvped) DESC,
         c.in_progress DESC, c.time_delta ASC
LIMIT 100
"""


def candidate_rows(user_id: int, lat: float | None, lon: float | None) -> list[dict]:
    """Every event in its live window, ranked most-likely-first for this caller."""
    # Positional params bind in TEXT order: the three EXISTS clauses sit in the
    # SELECT list, the cosine in the LATERAL below it.
    return db.query(_CANDIDATES, (user_id, user_id, user_id, lat, lat, lon))


def reason_for(row: dict) -> str | None:
    """The strongest signal this candidate row carries, or None."""
    for flag, reason in _SIGNALS:
        if row[flag]:
            return reason
    if row["distance_km"] is not None and row["distance_km"] <= MAX_MATCH_KM:
        return "nearby"
    return None


def may_attach(user_id: int, event_id: int) -> bool:
    """May this author put a record on this event?

    True while the event is collecting (its live window), or whenever they have
    ever been there — a deliberate choice about an event you attended is trusted
    long after the clock has moved on (FEED.md F-I2).
    """
    row = db.query_one(
        f"SELECT ({_IN_WINDOW}) AS in_window, "
        "EXISTS (SELECT 1 FROM participations pa WHERE pa.event_id = e.id "
        "        AND pa.user_id = %s) AS mine "
        "FROM events e WHERE e.id = %s",
        (user_id, event_id),
    )
    if row is None:
        raise LookupError("event_not_found")
    return bool(row["in_window"] or row["mine"])


def resolve_event(
    user_id: int,
    lat: float | None = None,
    lon: float | None = None,
    explicit_event_id: int | None = None,
) -> tuple[int | None, str | None]:
    """Pick the event this service was logged at → ``(event_id, match_reason)``.

    Priority (first hit wins): ``explicit`` > ``checked_in`` > ``participated`` >
    ``rsvp`` > ``nearby``. ``(None, None)`` means nothing matched — the record is
    saved unattached rather than guessed at (FEED.md F7).

    Raises ``LookupError`` if ``explicit_event_id`` names an event that does not
    exist. An explicit id that exists but is no longer attachable (a stale client
    holding a dead event) is not an error: it falls through to the honest guess.
    """
    if explicit_event_id is not None and may_attach(user_id, explicit_event_id):
        return explicit_event_id, "explicit"

    rows = candidate_rows(user_id, lat, lon)
    for flag, reason in _SIGNALS:
        for r in rows:
            if r[flag]:
                return r["event_id"], reason

    near = [r for r in rows if r["distance_km"] is not None and r["distance_km"] <= MAX_MATCH_KM]
    if near:
        return min(near, key=lambda r: r["distance_km"])["event_id"], "nearby"
    return None, None


def bootstrap_coords(c, event_id: int, lat: float | None, lon: float | None) -> None:
    """Let a matched record geolocate its event, once (FEED.md F5/F-I7).

    Leaders will not reliably fill in a map field, so the first person who checks
    in and posts a photo pins the event for everyone after them. Guarded on NULL:
    a leader's own coordinates are never overwritten.

    The event's LOCATION learns it too (LOCATIONS.md L4), so the knowledge outlives
    this occurrence -- next month's event at the same address is located from the
    moment it is created.
    """
    if lat is None or lon is None:
        return
    from app import locations

    row = c.execute(
        "UPDATE events SET lat = %s, lon = %s "
        "WHERE id = %s AND lat IS NULL AND lon IS NULL RETURNING location_id",
        (lat, lon, event_id),
    ).fetchone()
    if row:
        locations.teach(c, row["location_id"], lat, lon)
