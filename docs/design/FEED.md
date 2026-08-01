# One Feed — service logged *on events* (design)

> **Supersedes SERVICE_LOG.md §3's first non-goal** ("never link a record to a
> project/event"). The founder reversed that call: the standalone service-log feed
> and the projects feed **collapse into one feed**. A logged service now belongs to
> an **event**, and an event's photos surface on the **project card** that already
> scrolls past on the home screen.
>
> Read alongside [DOMAIN.md](./DOMAIN.md) (schema + shapes), [API.md](./API.md)
> (the contract), [FRONTEND.md](./FRONTEND.md) (screens), and
> [SERVICE_LOG.md](./SERVICE_LOG.md) (the anonymous-first identity layer this
> builds on — guests, cheers, reports and moderation are unchanged).
>
> Source of intent: `../intent.md` § "One feed".

---

## 1. The intent, in one paragraph

Anyone can use the app as a guest — no sign-up — and that guest can log an act of
service. Today those logs live in their own feed, parallel to the projects feed.
That is two feeds for one idea. From now on **a logged service attaches to the
event it happened at**, and the home screen is a single stream of **project
cards**: the project's details, then the one or two most recent photos people
logged at its current event. Scroll: project, a photo or two, next project. As
people log service the cards change — the feed is alive.

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| **F1** | **A service record belongs to an event** — `service_records.event_id`, **nullable**. | The merge, in one column. Nullable because a photo must never be *rejected* for being unmatchable (§5). |
| **F2** | **Home (`#/`) is the projects feed.** The standalone record feed view is deleted; `GET /projects` (scopes `upcoming`/`past`/`mine`, unchanged) is *the* feed. | "Collapse it down into one." One endpoint, one ordering, one mental model — and no new feed machinery to maintain. |
| **F3** | **One card, not two.** A project card renders the project *and* up to **2** most recent records for its embedded event, inline — same card, no nested card chrome. | The founder's "I don't want a double card." |
| **F4** | **The server guesses the event** at log time — check-in, then RSVP, then GPS + time (§4). The client may also state it outright (logging from an event page). | "We may not always know which event… hopefully we can look at their GPS and the time of day." |
| **F5** | **Events carry optional coordinates** (`events.lat/lon`), set by a leader ("use my location") **or bootstrapped**: when a record matches an event by a non-geo signal and carries GPS, an event with no coordinates adopts them. | Leaders will not reliably fill in a map field. The first checked-in volunteer's phone geolocates the event for everyone after them, for free. |
| **F6** | **Coordinates are matching inputs, never published.** A record's `lat/lon` appear in **no** read shape. An event's appear only in `event_detail` (so its leader can see/edit them). | A photo caption is public; a person's coordinates are not. Deliberate asymmetry. |
| **F7** | **An unmatched record is still saved** — `event_id NULL`. It appears on its author's own log (Me / their profile) and on its own detail page, where the author can **attach** it to an event later. It does **not** appear in the global feed. | Never lose someone's photo; never invent an association we do not believe. The global feed is the projects feed (F2) — a record with no project has no place in it. |
| **F8** | **The log screen shows where the post is going, before posting** — "Posting to *Riverside Cleanup*" with a **change** link, or a picker when nothing matched. | Turns F4's guess into something the person can correct in one tap, and turns F7's fallback from a dead end into a choice. |
| **F9** | **The event page is the record's home.** `#/events/{id}` shows the event's details (and the leader's QR) with the **full feed of what people logged there** underneath. After posting, the author lands there — their photo at the top. | The founder: "if you click on the event… we can put the feed of everything people have posted about that event underneath that data." |
| **F10** | **Comments stay out of scope**, as does per-record privacy, multi-photo, and geo-fenced browsing. Cheers/reports/moderation are unchanged from SERVICE_LOG.md §9. | "Maybe some comments — probably we don't worry about comments right now." |

## 3. Schema delta

```sql
-- The merge (F1) + the matching inputs (F4/F6).
ALTER TABLE service_records
  ADD COLUMN event_id     BIGINT REFERENCES events(id) ON DELETE SET NULL,
  ADD COLUMN lat          DOUBLE PRECISION,   -- author's position at log time; NEVER served
  ADD COLUMN lon          DOUBLE PRECISION,
  ADD COLUMN match_reason TEXT;               -- how event_id was chosen (§4) — audit/tuning
CREATE INDEX idx_service_records_event ON service_records(event_id, created_at DESC);

-- Where an event actually is (F5). NULL until a leader sets it or a record
-- bootstraps it.
ALTER TABLE events
  ADD COLUMN lat DOUBLE PRECISION,
  ADD COLUMN lon DOUBLE PRECISION;
```

`ON DELETE SET NULL` (not CASCADE): deleting an event must never delete the
photos people took there — they fall back to unattached (F7), exactly like a
record that never matched.

`match_reason` ∈ `explicit | checked_in | participated | rsvp | nearby | NULL`.
It is not a public field; it exists so the guess can be audited and tuned, and so
the UI can say *why* it picked an event.

## 4. Matching — which event was this?

One function, `app/matching.py::resolve_event(user_id, lat, lon, explicit_event_id)`,
used in **three** places: to attach on create, to preview the target on the log
screen, and to rank the candidate list in the picker.

**Candidates** are events inside their *live window*:

```
starts_at − 2h  ≤  now()  ≤  starts_at + expected_minutes + 6h
```

Two hours before (people arrive and start early), six hours after (people post the
photo on the couch that evening). `status` is **not** filtered: a leader who
closed the event ten minutes ago must still collect its photos.

Candidates are ordered by **in-progress first, then closest start time**:

```sql
ORDER BY (now() BETWEEN e.starts_at AND e.starts_at + make_interval(mins => e.expected_minutes)) DESC,
         abs(extract(epoch FROM (now() - e.starts_at))) ASC
```

**Priority — first hit wins:**

| # | Reason | Rule |
|---|---|---|
| 1 | `explicit` | The client named an event (logged from the event page, or picked in §F8). Must exist; must be a candidate **or** an event the author has ever participated in — a deliberate choice is trusted over the clock. |
| 2 | `checked_in` | The author has an **open participation** at a candidate. They are standing there right now — the strongest signal there is. |
| 3 | `participated` | The author has **any** participation at a candidate (checked out an hour ago). |
| 4 | `rsvp` | The author **RSVP'd** to a candidate. |
| 5 | `nearby` | GPS: the **nearest** candidate with coordinates within **5 km**, by haversine in SQL (no PostGIS). |
| 6 | *(null)* | Nothing matched → unattached (F7). |

Signals 2–4 need no GPS at all, which matters: they carry the common case
(a volunteer who checked in) without asking anyone for a location permission.

**Bootstrap (F5):** when the winner came from 1–4, the record carries GPS, and the
event has no coordinates, the event adopts the record's — one `UPDATE … WHERE lat
IS NULL`, in the same transaction.

**Constants** (`app/matching.py`, single source of truth):

| Constant | Value |
|---|---|
| `WINDOW_BEFORE` | 2 hours |
| `WINDOW_AFTER` | 6 hours past the expected end |
| `MAX_MATCH_KM` | 5.0 |
| `FEED_RECORDS_PER_EVENT` | 2 |

## 5. Read shapes (delta to DOMAIN.md)

- **record_card** gains `event: {id, project_id, project_title, starts_at} | null`.
  Never `lat`/`lon`, never `match_reason`.
- **event_card** gains `records: record_card[]` — the **≤2 most recent
  non-hidden** records of that event, newest-first — and `record_count`
  (non-hidden), both batched by event id across a whole page (no N+1).
  **Records hang off the event, not the project**: a photo belongs to an
  occurrence, and one owner beats mirroring the same list onto two shapes. A
  project card therefore shows its photos as `card.event.records`, and every
  other surface that embeds an event (project detail's event rows, the check-in
  screen) gets them for free.
- **event_detail** gains `lat`, `lon` (nullable) — the only place coordinates are
  served, so a leader can see and correct them.
- **event_candidate** (new, for the picker): `{event_id, project_id,
  project_title, starts_at, location_text, distance_km | null, reason}`.

## 6. API (delta to API.md)

| Endpoint | Change |
|---|---|
| `POST /api/service_records` | Body gains `event_id?`, `lat?`, `lon?`. Runs §4, stores `event_id` + `match_reason`, may bootstrap the event's coordinates. Response carries `event`. |
| `GET /api/service_records` | Gains `?event_id=` (one event's feed) and `scope=unattached` (mine, no event). `scope=all` keeps meaning *all* — an API that lies about its own word is worse than an unused scope. F7 is enforced where it belongs: **no screen renders a global log feed**. |
| `PATCH /api/service_records/{id}` | **New.** Author only. `{event_id \| null}` — attach, re-attach, or detach. `match_reason` becomes `explicit`. Targets are bounded exactly like an explicit create (`matching.may_attach`): an event still collecting, or one the author has been to — so a record cannot be parked on an arbitrary stranger's project. 409 `event_not_attachable` otherwise. |
| `GET /api/events/candidates` | **New.** `?lat=&lon=` → `event_candidate[]` ranked by §4, plus `match` = the auto-choice (what a post right now would attach to). Powers both the "Posting to…" preview and the picker. |
| `PATCH /api/events/{id}` | **New.** Leader only. `{starts_at?, location_text?, expected_minutes?, lat?, lon?}`. Closes a real gap: an event's time and place were previously uneditable. |
| `POST /api/projects/{id}/events` | Body gains `lat?`, `lon?`. |
| `POST /api/projects` | Body gains `lat?`, `lon?` for the first event. |
| `GET /api/projects` | Each card's embedded `event` now carries `records[]` + `record_count` (§5). |

## 7. Screens (delta to FRONTEND.md)

| Route | Change |
|---|---|
| `#/` **Home** | Was the record feed → **is the projects feed** (`projects.listView`, tabs Upcoming/Past/Mine). Each card: cover, title, event meta, action button, then ≤2 record photos with caption + author + 🙌. |
| `#/projects` | Redirects to `#/` — the same screen; the nav's duplicate **Projects** tab is removed (4 tabs + the ＋ Log FAB). |
| `#/log` | Captures GPS (best-effort, never blocking), shows **"Posting to …"** with a change link or a picker (F8). |
| `#/log/:eventId` | **New.** Log straight to a named event ("＋ Log to this event" from the event page). |
| `#/projects/:id` | Events: **upcoming ascending at the top with the next one highlighted**, past descending below; each row shows ≤2 of its records. |
| `#/events/:id` | Details + leader QR, then **the event's full record feed** underneath, and a **＋ Log to this event** button. |
| `#/r/:id` | Record detail gains the event line ("at *Riverside Cleanup* · Sat 10:00") and, for the author of an unattached record, **Attach to an event**. |
| `#/me` | Gains **My log** — my records, including unattached ones (F7). |

## 8. Migration of existing data

The 2026-07 records in production have no event. They stay `event_id NULL` —
i.e. they become their authors' personal log entries. **No back-fill is
attempted**: guessing an association for a photo taken weeks ago from a location
we never recorded would be inventing data. Authors can attach them by hand
(`PATCH`), which is exactly the affordance F7 already needs.

## 9. Invariants (the test suite asserts these)

| # | Invariant |
|---|---|
| **F-I1** | A record's `event_id` is either NULL or an existing event; deleting an event sets its records to NULL and deletes none of them. |
| **F-I2** | `resolve_event` never returns an event outside the live window **unless** it was named explicitly and the author has participated in it. |
| **F-I3** | Priority order holds: an open participation beats an RSVP, which beats a geo match; `explicit` beats everything. |
| **F-I4** | A geo match is never returned beyond `MAX_MATCH_KM`, and never for an event with NULL coordinates. |
| **F-I5** | No response body anywhere contains a record's `lat`, `lon`, or `match_reason` (F6). |
| **F-I6** | An event card carries at most `FEED_RECORDS_PER_EVENT` records, newest-first, all belonging to that event, none hidden. |
| **F-I7** | Bootstrap only ever fills coordinates that were NULL — it never overwrites a leader's. |
| **F-I8** | `PATCH /service_records/{id}` is author-only and cannot point a record at a nonexistent event. |
| **F-I9** | Hidden records (3 reports) vanish from project cards and event feeds, exactly as they already vanish from the record feed. |
