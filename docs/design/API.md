# API Contract

> The complete HTTP surface — 43 endpoints. JSON only, same-origin, no CORS
> middleware. Conventions first, then every endpoint. Field constraints live in
> OVERVIEW.md § Constants; read shapes in DOMAIN.md § Standard read shapes.
> A **project** is the durable service project; an **event** is one occurrence of
> it (its own schedule/place/code/status). Per-occurrence actions are event-scoped.

## Conventions

- **Base path** `/api`. Success returns the bare object/array (no envelope);
  create returns **201** with the created resource; delete/logout return **204**.
- **Auth**: `Authorization: Bearer <token>` on every endpoint **except**
  `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/health`.
  Missing/invalid/expired token → **401** `{"detail": "auth_required" | "invalid_token"}`.
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
| `POST /api/auth/logout` | — → **204** (deletes the presented session row) | — |

`user` here = `/api/me` shape below. bcrypt via the `bcrypt` package;
`bcrypt.checkpw` on login. Token = `secrets.token_hex(32)`, expiry now+30 days.
The `current_user` FastAPI dependency resolves token → session (unexpired) → user
and injects it into every protected handler.

## Users — `app/users.py`

| Endpoint | → Response | Errors |
|---|---|---|
| `GET /api/me` | `{id, email, display_name, bio, balance, created_at}` (email appears **only** here) | — |
| `PATCH /api/me` | body `{display_name?, bio?}` → updated me (bumps `updated_at`) | 422 |
| `GET /api/users/{user_id}` | **user_public** (includes stats; NO balance, NO email) | 404 |

## Projects — `app/projects.py`

A project is the durable umbrella; each occurrence is an **event** (see below).
Project endpoints are project-scoped; per-occurrence actions are event-scoped.

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/projects` 📄 | `?scope=upcoming` (default: projects with **≥1 not-over event**; card embeds the **soonest** not-over event; ordered by that event ASC) · `past` (projects with **no** not-over event; card embeds the **most-recent** event, or `null`; ordered by that event DESC) · `mine` (**leaderships ∪ rsvp/participation on any event**; DESC). `&q=` ILIKE on project title/description or any event's location. Returns **project_card[]** (`id, title, cover_image_id, follower_count, event`), the embedded **event_card** carrying the event's own `cover_image_id` + per-requesting-user state (`is_over, checked_in_count, my_rsvp, my_open_participation, my_hours_here`) batched by event id — no N+1 | — |
| `POST /api/projects` | `{title, description?, waiver_text?, location_text, starts_at, expected_minutes}` → **201** project detail. In one tx: insert project, owner into `project_leaders`, waiver v1 (`waiver_text` or `DEFAULT_WAIVER` — placeholder marked *not legal advice*), and the **first event** (fresh `checkin_code`, status `open`) | 422 |
| `GET /api/projects/{id}` | Detail: `id, title, description, owner {id,display_name}, leaders[] {id,display_name}, image_ids[], cover_image_id, primary_image_id` (cover: primary else first by id, or null)`, waiver {id,version,text}` (current)`, am_leader, is_following, follower_count, events[]` — each an **event_detail** (`id, starts_at, location_text, expected_minutes, status, is_over, cover_image_id, image_ids[], checked_in_count, my_rsvp, my_open_participation, my_hours_here` + `checkin_code` **only when `am_leader`**), ordered not-over ASC then over DESC | 404 |
| `PATCH /api/projects/{id}` | Leader only. `{title?, description?, waiver_text?}` — a **changed** `waiver_text` INSERTs waiver v(n+1) (I5). Event fields are edited per event, not here | 403 `not_a_leader` |
| `POST /api/projects/{id}/events` | Leader. `{location_text, starts_at, expected_minutes}` → **201** **event_detail** for the new occurrence (fresh `checkin_code`, status `open`) | 403 `not_a_leader`; 404; 422 |
| `POST /api/projects/{id}/leaders` | Leader. `{email}` → **201** leaders list (display names only, never the email) | 403; 404 `user_not_found`; 409 `already_leader` |
| `DELETE /api/projects/{id}/leaders/{user_id}` | Leader. Owner cannot be removed | 403; 409 `cannot_remove_owner`; 404 |
| `POST /api/projects/{id}/follow` | Follow the project. Idempotent (`ON CONFLICT (user_id,project_id) DO NOTHING`). → `{is_following: true, follower_count}` | 404 |
| `DELETE /api/projects/{id}/follow` | Unfollow. Idempotent. **200** (not 204 — the frontend needs the fresh count) → `{is_following: false, follower_count}` | 404 |

## Events — `app/events.py`

The per-occurrence surface. **Leader** = a `project_leaders` organizer of the
event's project (resolved event → project_id).

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/events/{id}` | **event_detail** + `project {id, title, cover_image_id}` summary + `waiver {id,version,text}` (the project's current) + `am_leader` | 404 |
| `POST /api/events/{id}/rsvp` | RSVP any time the event is **not over**. Idempotent (`ON CONFLICT (event_id,user_id) DO NOTHING`). → event detail | 404; 409 `event_over` |
| `POST /api/events/{id}/checkin` | **Self-service check-in** (no QR, no waiver screen). Ensures an RSVP row, then inserts a participation pinned to the event's project's **current** waiver (I6). Re-check-in after checkout is fine while not over. → event detail | 404; 409 `event_over`; 409 `already_checked_in` |
| `GET /api/events/{id}/rsvps` | Leader only. Everyone who RSVP'd, oldest-first: `[{user: {id, display_name}, is_leader, is_checked_in` (open participation exists)`, has_participated` (any participation)`, created_at}]` | 403 `not_a_leader`; 404 |
| `POST /api/events/{id}/rsvps/{user_id}/leader` | Leader only. `{is_leader: bool}` sets the event-leader **designation** (a flag with no powers yet — NOT `project_leaders`). → updated rsvps list | 403 `not_a_leader`; 404 `not_found` (that user never RSVP'd) |
| `POST /api/events/{id}/close` | Leader. `open → completed`; checks out ALL open participations, minting (capped math) in the same tx. Also how an event that never happened is ended — zero-minute participations mint 0. → event detail | 403; 404; 409 `event_not_open` |
| `POST /api/events/{id}/code/regenerate` | Leader. New `checkin_code` (old QR instantly dead) → `{checkin_code}` | 403; 404 |
| `GET /api/events/{id}/qr.svg` | Leader. `image/svg+xml` QR of `{scheme}://{host}/#/c/{checkin_code}` — **origin = `request.url.scheme` + Host**. Behind Caddy the scheme is https (uvicorn `--proxy-headers`); on the dev LAN it is honestly `http://<ip>:8000` | 403; 404 |
| `GET /api/events/{id}/roster` 📄 | Leader. Participations newest-first with `{id` (**participation id — the per-row Check-out button posts it**)`, user: {id, display_name}, checked_in_at, checked_out_at, minutes, tokens_awarded}` + `checked_in_count` | 403; 404 |

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

## Images — `app/images.py`

| Endpoint | Notes | Errors |
|---|---|---|
| `POST /api/images` | `{entity: 'project'\|'catalog_item'\|'event', entity_id, content_type, data_base64, is_primary?}` → **201** `{id}`. Only that entity's leader/poster may upload — for an `event`, the leaders of the event's project (403 `not_a_leader`). Decoded size ≤ 10 MB. **Cover:** if the entity has no primary yet the new image becomes primary automatically; `is_primary:true` force-sets it (unsetting the others in one tx). An event's cover = its primary else first by id | 403; 413 `image_too_large`; 422 `bad_content_type` |
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
| Any authed user | Browse everything; create projects (with a first event); check in via a valid code; RSVP/self check-in to an event; check **self** out; tip; claim offers; edit own profile |
| Project **leader** (incl. owner) | All project edits, add events, and per-event QR/code/roster/close, check out anyone there, event-leader designation; add/remove leaders; project images |
| Project **owner** | Leader powers; irremovable as leader |
| Item **poster** | Edit/close item, accept/decline claims, item images |
| Claimant | Cancel own pending claim |
| **Nobody** | Mutate ledger entries, waiver rows, others' profiles (no admin exists in MVP — D-deferred) |
