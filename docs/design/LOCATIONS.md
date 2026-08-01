# Locations — the address book the app builds itself (design)

> Events happen *somewhere*, and the same somewhere over and over: the same park,
> the same community centre, the same side entrance. Today each event carries a
> loose string and the next organizer retypes it (differently). From now on **every
> address typed becomes a location**, and the next person typing something similar
> is offered it.
>
> Read alongside [DOMAIN.md](./DOMAIN.md) and [FEED.md](./FEED.md) — the payoff is
> mostly in FEED.md's matching: a remembered location remembers its **coordinates**,
> so an event created at a known address can match photos by distance from the
> moment it is created.
>
> Source of intent: `../intent.md` § "Scan button and locations".

---

## 1. Decisions

| # | Decision | Rationale |
|---|---|---|
| **L1** | **Every address typed on an event upserts a `locations` row**, matched on a normalized form. No separate "create a location" flow exists. | The founder's ask, and the only version anyone will actually use: the address book fills itself as a side effect of scheduling events. |
| **L2** | **The event keeps `location_text`** and *also* gains `location_id`. The text is the display snapshot; the link is the shared identity. | Renaming a location must never silently rewrite the wording of a past event's page. Cheap, and it keeps every existing read path working. |
| **L3** | **Matching is by a normalized key**, not fuzzy search: lowercased, whitespace collapsed, surrounding punctuation stripped. "Riverside Park, Boathouse " and "riverside park, boathouse" are one location; "Riverside Park north gate" is a different one. | Fuzzy *merging* would silently fuse two real places. Fuzzy *suggesting* (§3) is safe because a human confirms it. |
| **L4** | **Coordinates flow both ways.** A location adopts an event's coordinates when it has none; an event adopts its location's coordinates when it has none. FEED.md's record→event bootstrap writes through to the location too. | This is the point. Geo knowledge accumulates in the place that is reused, so the *second* event at a venue matches photos by distance without anyone touching a map. |
| **L5** | **Suggestions are text-only** — `{id, label, event_count}`. Coordinates are never served here; the server applies them when it recognizes the address. | The client never needs them, and a location list is a public-ish surface. Keeps FEED.md F6's "coordinates are matching inputs" intact. |
| **L6** | **No geocoding, no map, no admin UI, no merge/rename tooling.** A location is a remembered string with optional coordinates. | OVERVIEW.md defers maps/geocoding. This is the smallest thing that removes the retyping. Merging duplicates is a future chore, listed in §6. |

## 2. Schema delta

```sql
CREATE TABLE IF NOT EXISTS locations (
  id         SERIAL PRIMARY KEY,
  label      TEXT NOT NULL,               -- as first typed; what suggestions show
  norm       TEXT NOT NULL UNIQUE,        -- the matching key (L3)
  lat        DOUBLE PRECISION,            -- learned (L4); NULL until something teaches it
  lon        DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE events ADD COLUMN location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_events_location ON events(location_id);
```

**No `uses` counter.** Popularity is `COUNT(events)` computed when suggestions are
read — a denormalized counter would need maintaining on every event edit and could
drift; the count is a single grouped query over a small table.

`ON DELETE SET NULL` matches the house rule for optional references: deleting a
location (no endpoint does yet) must not cascade into events.

## 3. Suggesting

`GET /api/locations?q=` returns up to 10 **location_suggestion** rows
(`{id, label, event_count}`), ranked:

1. **prefix matches first** (`norm LIKE 'q%'`) — what you are typing is probably a start
2. then substring matches (`norm LIKE '%q%'`)
3. within each, **most-used first**, then most-recently-used

With no `q`, it returns the most-used locations — so focusing an empty address
field already offers the venues this community actually uses.

Matching is on the *normalized* key against a normalized `q`, so case and spacing
never hide a suggestion.

## 4. Where it hooks in

| Path | Behaviour |
|---|---|
| `POST /api/projects` (first event) | `location_text` → upsert location → `location_id`; coordinates flow (L4) |
| `POST /api/projects/{id}/events` | same |
| `PATCH /api/events/{id}` | a changed `location_text` re-links (and can teach a new location its coordinates) |
| FEED.md geo bootstrap | when a record's GPS teaches an event its coordinates, the event's location learns them too |

All of it lives in `app/locations.py` — one `resolve()` used by every writer, so
there is exactly one place that knows the rules.

## 5. Screens

The location input on **New project**, **Add event** and **Edit event** gains a
native `<datalist>` fed by `GET /locations?q=` (debounced, same 250ms as the
project search). No new screen, no picker, no framework: type, see the venues you
already use, pick one — and the coordinates come along invisibly.

## 6. Invariants

| # | Invariant |
|---|---|
| **L-I1** | Two events whose addresses normalize the same share one `location_id`; a differently-worded address creates a different location. |
| **L-I2** | `locations.norm` is unique, and `resolve()` is idempotent — the same address never creates a second row, under concurrency either. |
| **L-I3** | An event created at a location that already knows its coordinates comes out with those coordinates, so FEED.md's `nearby` matching works for it immediately. |
| **L-I4** | A location learns coordinates only when it has none — nothing ever overwrites a known position. |
| **L-I5** | Suggestions never expose `lat`/`lon` (L5), and rank prefix matches above substring matches. |
| **L-I6** | Editing an event's address re-links it and leaves the old location's other events untouched. |

## 7. Deliberately deferred

Geocoding to real coordinates · a map picker · merging or renaming duplicates ·
per-community scoping (all locations are global today) · address validation ·
deleting unused locations. Each is a real future chore; none is needed to stop
people retyping the same address.
