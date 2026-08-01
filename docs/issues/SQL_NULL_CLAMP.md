# `GREATEST`/`LEAST` swallow NULL — a missing coordinate became "20 015 km away"

**Status:** fixed (2026-08-01, same day) · **Severity:** low (wrong number shown; matching was never affected)
**Found:** by deploying the API and reading the real response, not by the tests

## What happened

The haversine distance in `app/matching.py` clamped the cosine the textbook way:

```sql
6371 * acos(least(1, greatest(-1, <cosine> )))
```

For an event with no coordinates the cosine is NULL — and **Postgres's `GREATEST`
and `LEAST` ignore NULL arguments** rather than propagating them:

```sql
SELECT greatest(-1, NULL);   -- -1, not NULL
```

So the clamp produced `-1`, `acos(-1)` is π, and every unlocated event reported
itself as exactly `20015.09` km away — half the Earth's circumference, stated with
total confidence. `GET /api/events/candidates` would have rendered "20015 km away"
next to a project down the street.

## Why the tests missed it

Every matching test asserted the *decision* (`resolve_event` → no match), and the
decision was right: 20 015 km is well past `MAX_MATCH_KM`, so an unlocated event
still never matched. The bug lived entirely in the *reported* value, which only
the candidates endpoint exposes — and that endpoint was only ever asserted with
events that had coordinates.

The lesson is the general one: a test that asserts a threshold decision does not
constrain the quantity behind it. Where a number reaches the UI, assert the number.

## Fix

Compute the cosine once in a `LEFT JOIN LATERAL` and clamp with a `CASE`, which
propagates NULL correctly:

```sql
LEFT JOIN LATERAL (SELECT <cosine> AS v) d ON true
...
6371 * acos(CASE WHEN d.v > 1 THEN 1 WHEN d.v < -1 THEN -1 ELSE d.v END)
```

`NULL > 1` and `NULL < -1` are both NULL (not true), so the `ELSE` returns NULL,
`acos(NULL)` is NULL, and `distance_km` is honestly unknown.

Regression tests: `tests/test_feed_matching.py::test_an_event_without_coordinates_reports_no_distance`
and `::test_no_device_gps_reports_no_distance`.

## Watch for it elsewhere

`GREATEST`/`LEAST` are used nowhere else in the codebase today. If they reappear,
remember they are NULL-ignoring in Postgres (unlike almost every other function)
— which is convenient exactly when you want a default, and a silent lie when you
want NULL to mean "unknown".
