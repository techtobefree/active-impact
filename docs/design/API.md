# API Contract

> The complete HTTP surface — 34 endpoints. JSON only, same-origin, no CORS
> middleware. Conventions first, then every endpoint. Field constraints live in
> OVERVIEW.md § Constants; read shapes in DOMAIN.md § Standard read shapes.

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

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/projects` 📄 | `?scope=upcoming` (default: `status='open' AND starts_at >= now()-'12 hours'`, ASC) · `past` (the rest, DESC) · `mine` (**participations ∪ leaderships** — a poster who never checked in still finds their project here). `&q=` ILIKE on title/description/location. Returns **project_card[]** | — |
| `POST /api/projects` | `{title, description?, location_text, starts_at, expected_minutes, waiver_text?}` → **201** project detail. In one tx: insert project (fresh `checkin_code`), owner into `project_leaders`, waiver v1 (`waiver_text` or `DEFAULT_WAIVER` template — placeholder marked *not legal advice*) | 422 |
| `GET /api/projects/{id}` | Detail: card fields + `description`, `image_ids[]`, `leaders[] {id, display_name}`, `waiver {id, version, text}` (current), `am_leader`, `checkin_code` (**present only when `am_leader`** — feeds the lead screen's text fallback and smoke.py), `my_open_participation {id, checked_in_at} \| null`, `my_hours_here` | 404 |
| `PATCH /api/projects/{id}` | Leader only. `{title?, description?, location_text?, starts_at?, expected_minutes?, waiver_text?}` — a **changed** `waiver_text` INSERTs waiver v(n+1) (I5) | 403 `not_a_leader`; 409 `project_not_open` |
| `POST /api/projects/{id}/close` | Leader. `open → completed`; checks out ALL open participations, minting (capped math) in the same tx. Also how a project that never happened is ended — zero-minute participations mint 0 | 403; 409 `project_not_open` |
| `POST /api/projects/{id}/leaders` | Leader. `{email}` → **201** leaders list (the response shows display names, never the email) | 403; 404 `user_not_found`; 409 `already_leader` |
| `DELETE /api/projects/{id}/leaders/{user_id}` | Leader. Owner cannot be removed | 403; 409 `cannot_remove_owner`; 404 |
| `POST /api/projects/{id}/code/regenerate` | Leader. New `checkin_code` (old QR instantly dead) → `{checkin_code}` | 403 |
| `GET /api/projects/{id}/qr.svg` | Leader. `image/svg+xml` QR of `{scheme}://{host}/#/c/{checkin_code}` — **origin = `request.url.scheme` + Host**. Behind Caddy the scheme is https because the Dockerfile CMD runs uvicorn with `--proxy-headers --forwarded-allow-ips='*'` (trusting X-Forwarded-Proto; safe — only Caddy can reach the app). On the dev LAN it is honestly `http://<ip>:8000`, so M2's phone-scan verify works | 403 |
| `GET /api/projects/{id}/roster` 📄 | Leader. Participations newest-first with `{id` (**participation id — the per-row Check-out button posts it**)`, user: {id, display_name}, checked_in_at, checked_out_at, minutes, tokens_awarded}` + `checked_in_count` | 403 |

## Check-in — `app/checkin.py`

The QR encodes a URL, so the volunteer's **native camera** opens the PWA at
`#/c/{code}`; the frontend then drives these:

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/checkin/{code}` | Resolve a scanned code → `{project: project_card, waiver: {id, version, text}, my_open_participation \| null}` | 404 `invalid_code` (unknown code or non-`open` project) |
| `POST /api/checkin/{code}/agree` | **The signature.** → **201** participation. One tx: re-validate code, insert participation pinned to the **current** waiver version. Leaders check in through this same endpoint (their lead screen has the code) | 404 `invalid_code`; 409 `already_checked_in` |
| `POST /api/participations/{id}/checkout` | Self **or** leader of that project. Runs the checkout math from DOMAIN.md (half-up minutes, capped tokens, mint) in one tx → updated participation incl. `minutes`, `tokens_awarded` | 403 `not_allowed`; 409 `already_checked_out`; 404 |

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
| `POST /api/images` | `{entity: 'project'\|'catalog_item', entity_id, content_type, data_base64}` → **201** `{id}`. Only that entity's leader/poster may upload. Decoded size ≤ 10 MB | 403; 413 `image_too_large`; 422 `bad_content_type` |
| `GET /api/images/{id}` | Raw bytes, correct `Content-Type`, `Cache-Control: private, max-age=86400`. Auth required (D12) — frontend fetches with Bearer + blob URL | 404 |
| `DELETE /api/images/{id}` | Uploader or entity leader/poster → **204** (hard delete — nothing references image rows) | 403; 404 |

## Health — `app/main.py`

| Endpoint | Notes |
|---|---|
| `GET /api/health` | No auth. `{ok: true, db: true}` (runs `SELECT 1`; db failure → 503 `{ok: false, db: false}`). Compose healthcheck + smoke probe target |

## Permission model (summary)

| Actor on resource | May |
|---|---|
| Any authed user | Browse everything; create projects/items; check in via a valid code; check **self** out; tip; claim offers; edit own profile |
| Project **leader** (incl. owner) | All project edits, QR/code, roster, check out anyone there, close, add/remove leaders, project images |
| Project **owner** | Leader powers; irremovable as leader |
| Item **poster** | Edit/close item, accept/decline claims, item images |
| Claimant | Cancel own pending claim |
| **Nobody** | Mutate ledger entries, waiver rows, others' profiles (no admin exists in MVP — D-deferred) |
