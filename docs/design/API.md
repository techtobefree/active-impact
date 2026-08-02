# API Contract

> The complete HTTP surface — 52 endpoints. JSON only, same-origin, no CORS
> middleware. Conventions first, then every endpoint. Field constraints live in
> OVERVIEW.md § Constants; read shapes in DOMAIN.md § Standard read shapes.
> A **project** is the durable service project; an **event** is one occurrence of
> it (its own schedule/place/code/status). Per-occurrence actions are event-scoped.

## Conventions

- **Base path** `/api`. Success returns the bare object/array (no envelope);
  create returns **201** with the created resource; delete/logout return **204**.
- **Auth**: `Authorization: Bearer <token>` on every endpoint **except**
  `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/guest`,
  `GET /api/health`. Missing/invalid/expired token → **401**
  `{"detail": "auth_required" | "invalid_token"}`. Everyone (even a first-time
  visitor) holds a token: a **guest** is a `users` row with `email IS NULL`
  (SERVICE_LOG.md §4), minted by `POST /api/auth/guest`, reusing the whole session
  path. `POST /api/auth/guest` may optionally carry a token (returns it if valid).
- **Errors**: `{"detail": "<snake_case_code>"}`. Codes are machine-readable and
  stable; the frontend maps them to friendly text. FastAPI's native **422**
  validation shape is accepted as-is for malformed bodies.
- **Status codes**: 400 semantic bad request · 401 auth · 403 permission
  (`not_a_leader`, `not_yours`) · 404 `not_found` · 409 state conflict · 413
  `image_too_large` · 422 body validation.
- **Pagination**: `?limit=` (default 50, max 100) `&offset=` on every list marked 📄.
- **Timestamps**: ISO-8601 UTC in JSON; clients render local time.

## Auth — `app/auth.py`

| Endpoint | Body → Response | Errors |
|---|---|---|
| `POST /api/auth/register` | `{email, password, display_name}` → **201** `{token, user}` (auto-login; email lowercased/trimmed; `display_name` **required** — the public identity) | 409 `email_taken`; 422 pattern/length |
| `POST /api/auth/login` | `{email, password}` → `{token, user}` (also deletes this user's expired sessions — D19) | 401 `invalid_credentials` (same code whether the account exists or not) |
| `POST /api/auth/guest` | — → **201** `{token, user}`. Silently create an anonymous account (`email`/`password_hash` NULL) with an auto "Adjective Animal" handle + session. **No auth required.** Idempotent-ish: if a still-valid token is presented, that same `{token, user}` is returned (no spare guest) | — |
| `POST /api/auth/convert` | `{email, password, display_name?}` → `{token, user}`. **Authed as the guest** (D7 / §4). `email` **free** → attach email/password(+display_name) to this same guest row (same id, records intact); `email` **taken** → verify the existing account's password, MERGE the guest's records/cheers/reports into it, retire the guest, return a session for the **existing** account. Reuses RegisterIn validators | 401 `invalid_credentials` (taken email, wrong password); 409 `not_a_guest` (a real account may not convert); 409 `email_taken` (lost a create race); 422 email/password shape |
| `POST /api/auth/logout` | — → **204** (deletes the presented session row) | — |

`user` here = `/api/me` shape below (it now also carries `is_guest`). A guest's
`email` is `null`; `balance` is `0`. bcrypt via the `bcrypt` package;
`bcrypt.checkpw` on login. Token = `secrets.token_hex(32)`, expiry now+30 days.
The `current_user` FastAPI dependency resolves token → session (unexpired) → user
and injects it into every protected handler.

## Users — `app/users.py`

| Endpoint | → Response | Errors |
|---|---|---|
| `GET /api/me` | `{id, email, display_name, bio, balance, created_at, is_guest, qr_token}` (email appears **only** here; `is_guest` = `email IS NULL`; a guest's email is JSON `null`; `qr_token` is my permanent opaque handle — private view only, so a code is only ever handed out by its owner — CHECKIN_PROOF.md §3) | — |
| `PATCH /api/me` | body `{display_name?, bio?}` → updated me (bumps `updated_at`) | 422 |
| `GET /api/users/{user_id}` | **user_public** (includes stats; NO balance, NO email) | 404 |

## Projects — `app/projects.py`

A project is the durable umbrella; each occurrence is an **event** (see below).
Project endpoints are project-scoped; per-occurrence actions are event-scoped.

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/projects` 📄 | **This is the app's feed** (FEED.md F2). `?scope=upcoming` (default: projects with **≥1 not-over event**; card embeds the **soonest** not-over event; ordered by that event ASC) · `past` (projects with **no** not-over event; card embeds the **most-recent** event, or `null`; ordered by that event DESC) · `mine` (**leaderships ∪ rsvp/participation on any event**; DESC). `&q=` ILIKE on project title/description or any event's location. Returns **project_card[]** (`id, title, cover_image_id, follower_count, event`), the embedded **event_card** carrying the event's own `cover_image_id`, `record_count`, `records` (the **≤2 newest non-hidden record_cards of that event** — the photos the card shows) + per-requesting-user state (`is_over, checked_in_count, my_rsvp, my_open_participation, my_hours_here`) — all batched by event id, no N+1 | — |
| `POST /api/projects` | `{title, description?, waiver_text?, location_text, starts_at, expected_minutes, lat?, lon?}` → **201** project detail. In one tx: insert project, owner into `project_leaders`, waiver v1 (`waiver_text` or `DEFAULT_WAIVER` — placeholder marked *not legal advice*), and the **first event** (fresh `checkin_code`, status `open`, optional coordinates) | 422 |
| `GET /api/projects/{id}` | Detail: `id, title, description, owner {id,display_name}, leaders[] {id,display_name}, image_ids[], cover_image_id, primary_image_id` (cover: primary else first by id, or null)`, waiver {id,version,text}` (current)`, am_leader, is_following, follower_count, events[]` — each an **event_detail** (`id, starts_at, location_text, expected_minutes, status, is_over, cover_image_id, image_ids[], checked_in_count, my_rsvp, my_open_participation, my_hours_here` + `checkin_code` **only when `am_leader`**), ordered not-over ASC then over DESC | 404 |
| `PATCH /api/projects/{id}` | Leader only. `{title?, description?, waiver_text?}` — a **changed** `waiver_text` INSERTs waiver v(n+1) (I5). Event fields are edited per event, not here | 403 `not_a_leader` |
| `POST /api/projects/{id}/events` | Leader. `{location_text, starts_at, expected_minutes, lat?, lon?}` → **201** **event_detail** for the new occurrence (fresh `checkin_code`, status `open`) | 403 `not_a_leader`; 404; 422 |
| `POST /api/projects/{id}/leaders` | Leader. `{email}` → **201** leaders list (display names only, never the email) | 403; 404 `user_not_found`; 409 `already_leader` |
| `DELETE /api/projects/{id}/leaders/{user_id}` | Leader. Owner cannot be removed | 403; 409 `cannot_remove_owner`; 404 |
| `POST /api/projects/{id}/follow` | Follow the project. Idempotent (`ON CONFLICT (user_id,project_id) DO NOTHING`). → `{is_following: true, follower_count}` | 404 |
| `DELETE /api/projects/{id}/follow` | Unfollow. Idempotent. **200** (not 204 — the frontend needs the fresh count) → `{is_following: false, follower_count}` | 404 |

## Events — `app/events.py`

The per-occurrence surface. **Leader** = a `project_leaders` organizer of the
event's project (resolved event → project_id).

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/events/candidates` | **Which event am I at?** (FEED.md §4). `?lat=&lon=` (both optional) → `{match: event_candidate\|null, candidates: event_candidate[]}` — `match` is what a record posted *right now* would attach to; `candidates` is every event in its live window ranked the same way (in-progress first, then closest start; geo candidates carry `distance_km`). Powers the log screen's "Posting to…" line and its picker | — |
| `GET /api/events/{id}` | **event_detail** (incl. `lat`/`lon`) + `project {id, title, cover_image_id}` summary + `waiver {id,version,text}` (the project's current) + `am_leader` | 404 |
| `PATCH /api/events/{id}` | Leader. `{starts_at?, location_text?, expected_minutes?, lat?, lon?}` → event detail. The only way to correct an occurrence's time/place, and how a leader pins its coordinates for geo matching (`lat`/`lon` must be sent together; both `null` clears them) | 403 `not_a_leader`; 404; 422 |
| `POST /api/events/{id}/rsvp` | RSVP any time the event is **not over**. Idempotent (`ON CONFLICT (event_id,user_id) DO NOTHING`). → event detail | 404; 409 `event_over` |
| `POST /api/events/{id}/checkin` | **Self-service, ASSERTED check-in** (no QR, no waiver screen) — "I say I was here", `attested = false` unless a sighting already exists (CHECKIN_PROOF.md §5.4). Ensures an RSVP row, then inserts a participation pinned to the event's project's **current** waiver (I6). Re-check-in after checkout is fine while not over. → event detail | 404; 409 `event_over`; 409 `already_checked_in` |
| `GET /api/events/{id}/rsvps` | Leader only. Everyone who RSVP'd, oldest-first: `[{user: {id, display_name}, is_leader, is_checked_in` (open participation exists)`, has_participated` (any participation)`, is_attested` (a sighting exists for them at this event)`, created_at}]` | 403 `not_a_leader`; 404 |
| `POST /api/events/{id}/rsvps/{user_id}/leader` | Leader only. `{is_leader: bool}` sets the event-leader **designation** (a flag with no powers yet — NOT `project_leaders`). → updated rsvps list | 403 `not_a_leader`; 404 `not_found` (that user never RSVP'd) |
| `POST /api/events/{id}/close` | Leader. `open → completed`; checks out ALL open participations, minting (capped math) in the same tx. Also how an event that never happened is ended — zero-minute participations mint 0. → event detail | 403; 404; 409 `event_not_open` |
| `POST /api/events/{id}/code/regenerate` | Leader. New `checkin_code` (old QR instantly dead) → `{checkin_code}` | 403; 404 |
| `GET /api/events/{id}/qr.svg` | Leader. `image/svg+xml` QR of `{scheme}://{host}/#/c/{checkin_code}` — **origin = `request.url.scheme` + Host**. Behind Caddy the scheme is https (uvicorn `--proxy-headers`); on the dev LAN it is honestly `http://<ip>:8000` | 403; 404 |
| `GET /api/events/{id}/my-qr.svg` | **My personal QR for this event** — `image/svg+xml` of `{scheme}://{host}/#/s/{my qr_token}/{id}` (same origin rule). Any **attendee** (an RSVP or a participation), not just leaders: this is the code *I* show other people so they can check in off me. Static and printable (CHECKIN_PROOF.md P5) | 403 `not_attending`; 404 |
| `GET /api/events/{id}/roster` 📄 | Leader. Participations newest-first with `{id` (**participation id — the per-row Check-out button posts it**)`, user: {id, display_name}, checked_in_at, checked_out_at, minutes, tokens_awarded, attested}` + `checked_in_count` | 403; 404 |

## Check-in — `app/checkin.py`

The QR encodes a URL, so the volunteer's **native camera** opens the PWA at
`#/c/{code}`; a check-in code belongs to an **event**. The frontend then drives:

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/checkin/{code}` | Resolve a scanned code → `{event: event_card, project: project_card (embedding that event), waiver: {id, version, text}, my_open_participation \| null}` | 404 `invalid_code` (unknown code or non-`open` event) |
| `POST /api/checkin/{code}/agree` | **The signature.** → **201** participation (carries `event_id`). One tx: re-validate code, ensure an RSVP row (idempotent — so QR check-ins appear in the organizer's RSVP list), insert participation pinned to the event's project's **current** waiver version. Leaders check in through this same endpoint | 404 `invalid_code`; 409 `already_checked_in` |
| `POST /api/participations/{id}/checkout` | Self **or** leader of the event's project. Joins participation → event for `expected_minutes`; runs the checkout math from DOMAIN.md (half-up minutes, capped tokens, mint) in one tx → updated participation incl. `minutes`, `tokens_awarded` | 403 `not_allowed`; 409 `already_checked_out`; 404 |

Every check-in and check-out is recorded to the internal append-only **audit log**
(`audit_log` table) in the same tx as the change, carrying both `event_id` and
`project_id` — no public endpoint yet.

## Peer check-in (scan) — `app/scan.py`

The **attested** layer (CHECKIN_PROOF.md). Everything above records what someone
*says*; this records what someone *else's code* corroborates. A person's QR is a
plain URL — `{scheme}://{host}/#/s/{qr_token}/{event_id}` — so it resolves through
the in-app scanner **and** any phone's native camera, and it is static enough to
print and pin to a wall.

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/scan/{qr_token}/{event_id}` | Resolve a scanned personal QR → `{person: {id, display_name}, is_self, event: event_card, project: project_card, waiver: {id,version,text}, my_open_participation \| null, already_attested}` (`already_attested` = *this* pair has already been recorded at this event; `is_self` = I scanned my own code — resolve still 200s so the UI can say something kind, only `confirm` refuses) | 404 `invalid_qr` (unknown token **or** the event is not `open`) |
| `POST /api/scan/{qr_token}/{event_id}/confirm` | **The peer check-in**, one tx: append `attestations(event, scanner=me, subject=person)` (`ON CONFLICT DO NOTHING` — a re-scan is a no-op); ensure RSVPs for both; check the **scanner** in against the project's current waiver (the confirm screen shows it, so this is their signature) or flip their open participation to `attested`; flip the **subject's** open participation to `attested` if they have one — never create one for them (I14); `audit_log` ← `check_in` (if created) + `attest`. → **201** `{participation, person, attested: true}` | 404 `invalid_qr`; 409 `self_scan`; 409 `event_over` |

A scan of someone who has not checked in yet is still stored: the sighting is real
when it happens, and it upgrades their participation to `attested` the moment they
do check in (CHECKIN_PROOF.md P8 / §5.4).

## Tokens — `app/tokens.py`

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/tokens/ledger` 📄 | Entries where I'm `from` or `to`, newest-first, with counterparty `{id, display_name}` resolved, `direction: in\|out` | — |
| `POST /api/tokens/tip` | `{to_user_id \| to_email (exactly one), amount, note?, catalog_item_id?}` → **201** entry. UI buttons (profile/need pages) use `to_user_id`; the wallet's free-form send uses `to_email`. Responses never echo an email. `transfer(kind='tip')` — covers tipping AND donating to a need | 404 `user_not_found`; 409 `insufficient_balance`; 409 `cannot_tip_self`; 422 amount < 1 or not-exactly-one recipient |

## Catalog — `app/catalog.py`

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/catalog` 📄 | `?kind=offer\|need` `&q=` `&mine=1` `&status=active` (default) — **item_card[]**, newest-first | — |
| `POST /api/catalog` | `{kind, title, description?, price_tokens?, quantity?}` → **201** detail. `price_tokens` required (≥0) for offers, forbidden for needs (422 `price_on_need` / `price_required`) | 422 |
| `GET /api/catalog/{id}` | Detail: card + `description`, `image_ids[]`, `my_claim \| null`, `pending_claims_count` (poster only) | 404 |
| `PATCH /api/catalog/{id}` | Poster. `{title?, description?, price_tokens?, quantity?, status?}` (status `closed` to end it; price changes don't touch existing claims — snapshot rules) | 403 `not_yours` |
| `POST /api/catalog/{id}/claim` | → **201** claim (`pending`, price snapshotted). Active, in-quantity **offers only** (every offer is priced; 0 = free) — needs 409 `not_claimable`; own item 409 `own_item` | 409 `already_claimed`, `item_closed` |
| `GET /api/claims` 📄 | `?role=claimant` (default: my requests) `\|poster` (requests on my items) `&status=` — with item + counterparty summaries | — |
| `POST /api/claims/{id}/accept` | Poster. One tx: re-check status/quantity → `transfer(claimant→poster, price, 'spend', claim_id)` (price 0 = no entry, still accepted) → decrement quantity, close item at 0 → `accepted`, `decided_at` | 403; 409 `claim_not_pending`, `insufficient_balance` (claimant's), `quantity_exhausted` |
| `POST /api/claims/{id}/decline` | Poster → `declined`, `decided_at` | 403; 409 `claim_not_pending` |
| `POST /api/claims/{id}/cancel` | Claimant → `canceled`, `decided_at` | 403; 409 `claim_not_pending` |

Coupons need no special mechanics: an offer titled "50% off X" priced at N tokens
— the accepted-claim screen is the proof the claimant shows the business
(description carries redemption terms). Fulfillment is off-platform trust (D8/D9).

Service offers (the intent's dentist example) earn the same way: the poster
prices the offer in tokens and is **paid by claimants** — the catalog never
system-mints. Minting is exclusive to project checkout (D7); a time-and-place
charity session can instead be posted as a *project* to earn via check-in.

## Service records — `app/records.py`

One record = one photo + one caption, authored by whoever you currently are (guest
or real — SERVICE_LOG.md §4). It creates no participations and moves no tokens,
but it **belongs to an event** (FEED.md): the server resolves which one from the
author's check-in / RSVP / GPS + time, and the event's project card carries its
latest two. All endpoints require a token (a guest's counts). Returns
**record_card** (`id, author {id, display_name, is_guest}, caption,
photo_image_id, created_at, cheer_count, i_cheered, event`) — the author **never**
exposes an email, and the record's `lat`/`lon`/`match_reason` are never served.

| Endpoint | Notes | Errors |
|---|---|---|
| `POST /api/service_records` | `{caption, content_type, data_base64, event_id?, lat?, lon?}` → **201** record_card. One tx: resolve the event (FEED.md §4 — `explicit` > `checked_in` > `participated` > `rsvp` > `nearby` > none), insert the record with `event_id`/`match_reason`, then its photo (`entity='service_record'`, `is_primary`), then bootstrap the event's coordinates if it had none. Caption 1–280 (stripped); photo reuses the 10 MB / content-type gate. Rate-limited to ≤ 20 records / author / hour | 404 `event_not_found` (explicit id); 422 `bad_content_type`; 413 `image_too_large`; 429 `rate_limited` |
| `GET /api/service_records` 📄 | `?scope=all` (default, every visible record) `\|mine` (the caller's own, attached or not) `\|unattached` (mine, `event_id IS NULL` — the loose log entries F7 leaves on the author's own page). `&event_id=` narrows any scope to **one event's feed** — what the event page renders. Newest-first, **excludes `hidden`**. Author, cheers, photo and event are batched by record id (no N+1) | — |
| `GET /api/service_records/{id}` | → record_card (share target / detail) | 404 `not_found` (absent **or** hidden) |
| `PATCH /api/service_records/{id}` | **Author only.** `{event_id}` — attach, re-attach, or (with `null`) detach; `match_reason` becomes `explicit`. The remedy when the auto-match guessed wrong or found nothing → record_card. Targets are bounded like an explicit create: an event still collecting photos, or one the author has been to | 403 `not_yours`; 404 `not_found`, `event_not_found`; 409 `event_not_attachable` |
| `DELETE /api/service_records/{id}` | **Author only** → **204**. Cascades cheers/reports (FK); the polymorphic image is removed in the same tx | 403 `not_yours`; 404 |
| `POST /api/service_records/{id}/cheer` | Add my 🙌. Idempotent (`ON CONFLICT DO NOTHING`) → `{cheered: true, cheer_count}` | 404 |
| `DELETE /api/service_records/{id}/cheer` | Remove my 🙌. Idempotent → `{cheered: false, cheer_count}` | 404 |
| `POST /api/service_records/{id}/report` | `{reason?}` → **204**. Idempotent per user (`UNIQUE(record_id,user_id)`). At **3 distinct** reporters the record auto-sets `hidden=true` (dropped from all feeds). Guests may report | 404 |

**Moderation is MVP-light (a known gap, §9):** report + `hidden` flag + author
delete; no admin UI yet (unhide is a manual DB flag). The 10 MB image cap, the
content-type allowlist, the caption cap, and the per-hour rate limit are the spam
floor.

## Social — `app/social.py`

Person -> person (SOCIAL.md). Following decides whose activity reaches my feed
and my bell; **blocking is a one-way visibility mute that keeps the follow**.
Every activity read composes the one visibility clause in `app/activity.py`:
*someone I have blocked never sees my activity*.

| Endpoint | Notes | Errors |
|---|---|---|
| `POST /api/users/{id}/follow` | Idempotent → `{is_following: true, follower_count}` | 404; 409 `cannot_follow_self` |
| `DELETE /api/users/{id}/follow` | Idempotent → `{is_following: false, follower_count}` | 404 |
| `GET /api/users/{id}/followers` 📄 | **person_card[]**. `?sort=recent` (default) `\|name`; an unknown value falls back to `recent` rather than erroring, so a stale client still gets a sane list | 404 |
| `GET /api/users/{id}/following` 📄 | **person_card[]**, same `?sort=` | 404 |
| `POST /api/users/{id}/block` | Mine only, idempotent → `{is_blocked: true}`. **Never touches `user_follows`** — they stay a follower (S4) | 404; 409 `cannot_block_self` |
| `DELETE /api/users/{id}/block` | Idempotent → `{is_blocked: false}`. Restores everything, because blocking only ever filtered reads | 404 |
| `GET /api/users/{id}/activity` 📄 | **activity_card[]**, newest first. A viewer they blocked gets an empty stream — no error, no banner | 404 |
| `GET /api/users/{id}/upcoming` | Their **current status**: not-over events they have an RSVP or participation for, soonest first, each `{event_id, project_id, project_title, starts_at, location_text, is_here_now}`. Sits above their history on their page. Same block filter — a blocked viewer gets `[]` | 404 |
| `GET /api/feed/following` 📄 | **activity_card[]** from everyone I follow, never my own. Powers home's Following tab | — |
| `GET /api/notifications` 📄 | `{unread, items: activity_card[]}` — items are the notifiable kinds (`rsvp`, `checked_in`) from my followees; `unread` counts those after my watermark, and is 0 when `notify_activity` is off | — |
| `POST /api/notifications/seen` | Moves the watermark to now → `{unread: 0}`. The items stay readable — seen is not a delete | — |

`GET /api/users/{id}` additionally carries `is_following`, `is_blocked`,
`follower_count`, `following_count`; `PATCH /api/me` accepts `notify_activity`.

## Locations — `app/locations.py`

The address book the app builds itself (LOCATIONS.md). Every `location_text` sent
to an event writer upserts a location, matched on a normalized key, and links the
event to it; an event with no coordinates **inherits the venue's**, which is what
makes FEED.md's `nearby` matching work for a brand-new event at a known address.
There is no create/update/delete surface — the list is a side effect.

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/locations` | `?q=` → **location_suggestion[]** (`id, label, event_count`), max 10. Prefix matches first, then substring; most-used first within each, then most recently used. No `q` → the venues most in use, so focusing an empty address field already helps. Matching is case- and spacing-insensitive; **coordinates are never served** (L5) | — |

## Images — `app/images.py`

| Endpoint | Notes | Errors |
|---|---|---|
| `POST /api/images` | `{entity: 'project'\|'catalog_item'\|'event'\|'service_record', entity_id, content_type, data_base64, is_primary?}` → **201** `{id}`. Only that entity's manager may upload — a project/event's **leader** (403 `not_a_leader`), a catalog item's **poster** or a service record's **author** (403 `not_yours`). Decoded size ≤ 10 MB. **Cover:** if the entity has no primary yet the new image becomes primary automatically; `is_primary:true` force-sets it (unsetting the others in one tx). Usually not called directly for a record — `POST /api/service_records` attaches the photo in one shot | 403; 413 `image_too_large`; 422 `bad_content_type` |
| `GET /api/images/{id}` | Raw bytes, correct `Content-Type`, `Cache-Control: private, max-age=86400`. Auth required (D12) — frontend fetches with Bearer + blob URL | 404 |
| `POST /api/images/{id}/primary` | Entity leader/poster only. Sets this image as the entity's cover and unsets the others in one tx → `{id, entity, entity_id, is_primary}` | 403; 404 |
| `DELETE /api/images/{id}` | Uploader or entity leader/poster → **204** (hard delete — nothing references image rows). Deleting the primary is fine — the cover falls back to the first remaining by id | 403; 404 |

## Health — `app/main.py`

| Endpoint | Notes |
|---|---|
| `GET /api/health` | No auth. `{ok: true, db: true}` (runs `SELECT 1`; db failure → 503 `{ok: false, db: false}`). Compose healthcheck + smoke probe target |

## Permission model (summary)

| Actor on resource | May |
|---|---|
| Any authed user (incl. **guest**) | Browse everything; create projects (with a first event); check in via a valid code; RSVP/self check-in to an event; check **self** out; tip; claim offers; edit own profile; **log service records, cheer, and report** |
| Project **leader** (incl. owner) | All project edits, add events, and per-event QR/code/roster/close, check out anyone there, event-leader designation; add/remove leaders; project images |
| Project **owner** | Leader powers; irremovable as leader |
| Item **poster** | Edit/close item, accept/decline claims, item images |
| Record **author** | Delete own service record, manage its image |
| Claimant | Cancel own pending claim |
| **Nobody** | Mutate ledger entries, waiver rows, others' profiles (no admin exists in MVP — D-deferred) |
