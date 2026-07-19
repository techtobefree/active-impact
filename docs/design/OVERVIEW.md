# Active Impact — Design Overview

> **Entry point for the implementing agent.** This design tree is decision-complete:
> every open question is resolved here, and the docs below specify the MVP down to
> DDL, endpoints, screens, and the deploy runbook. Read in this order:
>
> 1. **OVERVIEW.md** (this file) — mission, architecture, binding decisions, constants
> 2. [DOMAIN.md](./DOMAIN.md) — entities, full schema DDL, invariants, token accounting
> 3. [API.md](./API.md) — the complete HTTP contract
> 4. [FRONTEND.md](./FRONTEND.md) — PWA shell, router, screens, service worker
> 5. [DEPLOYMENT.md](./DEPLOYMENT.md) — containers, Caddy, and the given-a-URL runbook
> 6. [BUILD_PLAN.md](./BUILD_PLAN.md) — TDD milestones and the definition of done
>
> Source of intent: [`../intent.md`](../intent.md) (user's verbatim words — authoritative)
> and [`../interpreted-mvp.md`](../interpreted-mvp.md) (structured reading).
> Reference implementation mined for patterns: the deployed **home-keep** app
> (Express + vanilla PWA + Caddy). We copy its *shape*, not its code.

---

## Mission (one paragraph)

Active Impact is a non-profit platform that brings together **those who have a need
or a service project** (labor, goods, or services) and **those who want to give**
(time, expertise, goods). Volunteers find local **impact projects**, check in on
site by scanning a QR code and agreeing to the project's **waiver**, and earn
**impact tokens** (1 per hour) recorded in a Postgres ledger. A **catalog** lets
people post needs and offers priced in tokens, and anyone can **tip** tokens to
anyone. Essentialist MVP: simple, minimal, basic — improved over time as needs
arise.

## The three capabilities

| # | Capability | What it includes |
|---|-----------|------------------|
| 1 | **Social foundation** | Register/login (username+password), profiles, public stats |
| 2 | **Impact projects** | Browse/post projects (time, place, expected duration, images), QR check-in with waiver agreement, check-out, time recording, token minting |
| 3 | **Catalog** | Post needs/offers for goods & services, token prices (incl. coupon-style offers), claim/accept settlement, tipping |

## Architecture

```
                     ┌─────────────────────────── VM (single host) ───────────────────────────┐
                     │                                                                        │
  Browser (PWA)      │  ┌─────────┐        ┌──────────────────────────┐       ┌────────────┐  │
  vanilla JS, no ────┼─▶│  Caddy   │──────▶│  app: FastAPI + uvicorn  │──────▶│  Postgres  │  │
  build step         │  │ :80/:443 │ proxy │  /api/* JSON API         │ SQL   │  16-alpine │  │
  installable,       │  │ auto-TLS │       │  /*    static public/    │       │  (pgdata)  │  │
  QR deep links      │  └─────────┘        └──────────────────────────┘       └────────────┘  │
                     │   caddy_data             (no published ports)        (no published     │
                     │   volume (certs)                                      ports)           │
                     └────────────────────────────────────────────────────────────────────────┘
```

- **Three containers** (prod): `caddy` → `app` → `postgres`. Only Caddy publishes
  ports (80/443). Domain parameterized by a single env var `SITE_ADDRESS`;
  Caddy fetches/renews Let's Encrypt certs automatically.
- **One origin**: the FastAPI container serves both the JSON API under `/api` and
  the static PWA from `public/` — Caddy is a pure reverse proxy.
- **Frontend**: no-build vanilla-JS PWA (ES modules, hash router, service worker).
- **Data**: schema defined by **SQLAlchemy models** (`app/models.py`) and evolved
  with **Alembic** migrations (`alembic upgrade head` on boot); queries run through
  a psycopg connection pool.

## Binding decisions

These resolve all open questions from `interpreted-mvp.md` (Q1–Q6) and every other
fork in the road. Each is deliberately minimal and reversible later; **do not
re-litigate them during implementation.**

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Backend = FastAPI + uvicorn, sync handlers.** Schema via **SQLAlchemy models** (`app/models.py`); queries via psycopg 3. | Intent names FastAPI. SQLAlchemy models are the schema source of truth and give versioned migrations that update easily over time; runtime queries use psycopg for directness (the ORM is available for queries too). Pydantic gives free request validation. Sync is simpler than async at MVP scale. |
| D2 | **Schema = SQLAlchemy models + Alembic migrations.** `app/models.py` is the source of truth; `alembic upgrade head` (run by `python -m app.db --init`) applies pending migrations on **every container boot**. Evolve with `alembic revision --autogenerate -m "..."` → review → upgrade. | A real migration framework so the schema updates easily over time. Autogenerate reliably diffs columns/tables; hand-check partial/expression indexes + CHECK constraints (normal Alembic practice). |
| D3 | **Auth = username+password with bcrypt**; login mints an opaque token (`secrets.token_hex(32)`) stored in a `sessions` row with **30-day expiry**; client sends `Authorization: Bearer <token>`; token kept in `localStorage`. No cookies, no JWT, no CSRF surface. | home-keep's session-token mechanics (proven, instantly revocable) + real hashing home-keep lacked (its plaintext auth is a non-option for public users). |
| D4 (Q6) | **Frontend = no-build vanilla JS** ES modules + a ~30-line **hash router**; emoji icon system; CSS custom-property tokens incl. a `prefers-color-scheme: dark` override. No framework, no bundler, zero frontend deps. | Matches the deployed reference and the user's essentialism. The router is the one addition home-keep's pattern strictly needs (QR deep links). |
| D5 (Q1) | **QR = a plain URL.** Server renders SVG QR of `https://SITE/#/c/{checkin_code}`; the **leader displays it**, the **volunteer scans with the native camera app** — no in-app scanner, no getUserMedia. | Zero camera-permission code. A QR that is just a link works on every phone. |
| D6 (Q2) | **Check-out = explicit action**: volunteer self-checkout, or a leader checks out any participant from the roster, or the leader **closes the project** (checks out everyone still in). No cron, no auto-timeout. **Late checkouts cannot inflate the supply:** credited time is capped at 2× the project's expected duration (see D7) — a leader closing days late mints hours-scale, not days-scale, tokens. | Three cheap paths cover reality; forgetting is handled by the leader closing the project — and the cap makes "handled late" economically safe. Known simplification, documented. |
| D7 | **Tokens are integer points.** Mint at checkout with **half-up integer math — never Python's `round()` (banker's rounding)**: `minutes = (seconds+30)//60`, `credited = min(minutes, 2×expected_minutes)`, `tokens = (credited+30)//60`; same flat rate for everyone (volunteers and leaders). Ledger is **append-only** (`token_entries`), balance is a **guarded cache column** (`users.balance`, `CHECK >= 0`) updated in the same transaction. Kinds: `earn` / `tip` / `spend`. | Intent: 1 token per hour, flat, no extra complexity — and time is recorded *because it prices the token*, so the cap protects exactly what the intent protects. The tx-plus-ledger shape copies home-keep's `changes` pattern — the one place bugs are unacceptable. |
| D8 (Q3) | **Boundary:** anything with a **time and a place** is a *project*; standing **goods/services** are *catalog* items. "Register a need" = catalog item `kind='need'` (unpriced); helpers **tip the poster directly** from the need page. No separate "needs tokens" user flag; no in-app messaging (contact details go in the description if the poster wants). | One mechanism for needs instead of two. Messaging is real complexity deferred entirely. |
| D9 (Q5) | **Catalog settlement:** claimant requests → poster **accepts** (tokens transfer claimant→poster atomically; quantity decrements; item auto-closes at 0) or **declines**; claimant may cancel. Balance checked at accept, not reserved. All `offer`s are claimable (every offer is priced; 0 = free); `need`s are never claimable — they receive tips. | One extra state (`pending`) buys real UX; escrow/reservations deferred. |
| D10 (Q4) | **No first-class groups.** A group registers a user account. | Intent's essentialism clause. |
| D11 | **Images = BYTEA in Postgres.** Polymorphic `images` table for projects and catalog items; upload = base64-in-JSON (client canvas-resizes to ≤1600px JPEG q0.8, 10 MB decoded cap); served by an authenticated streaming endpoint; `<img>` via blob URLs. | home-keep's proven zero-extra-infrastructure pattern; one backup target. |
| D12 | **Everything requires auth except** register, login, `/api/health`, and the static shell. Browsing projects/catalog requires an account. | Public UGC with no moderation tooling is riskier than a login wall. Simplest safe default. **Recorded trade-off:** this sits in tension with the intent's "immediately available to anybody" / "inviting" — accepted for MVP safety, founder-reversible later via a public read-only project list without schema changes. |
| D13 | **Waivers are versioned and immutable.** Project create seeds v1 from a default template (placeholder text marked *not legal advice — replace before real events*); editing waiver text creates a new version; a participation row pins the `waiver_id` the volunteer agreed to — **the participation row is the signature.** | Legal answer with three columns. Copies home-keep's "the audit row is the source of truth". |
| D14 | **Pagination** everywhere lists can grow: `?limit=` (default 50, max 100) `&offset=`. Fixed `ORDER BY` per endpoint. | Decided up front (home-keep's gap). |
| D15 | **Errors:** FastAPI-native `{"detail": <code>}` with **machine-readable snake_case codes** (e.g. `invalid_credentials`, `insufficient_balance`, `already_checked_in`). 401 auth, 403 not-yours, 404 missing, 409 state conflicts, 413 image too large, 422 body validation (FastAPI default — accepted as-is). | One convention, documented in API.md. |
| D16 | **Config surface = two knobs**: `SITE_ADDRESS`, `POSTGRES_PASSWORD` (plus derived `DATABASE_URL` wired in compose). **No SECRET_KEY exists** — opaque DB tokens mean nothing is ever signed. | Two-knob `.env` is what makes "hand an agent a URL" work. |
| D17 | **Deploy = home-keep's topology**, adapted: dev/prod compose split (two files, not overrides), 5-line Caddyfile with `{$SITE_ADDRESS}`, tarball-over-SSH `deploy.sh` with `.env` guard. **Additions** home-keep lacked: app healthcheck, `json-file` log rotation on every service, a `scripts/backup.sh` pg_dump one-liner. | Proven live; the additions are cheap hygiene. |
| D18 | **No generic audit table.** `token_entries` is the only append-only ledger; other tables rely on `updated_at`/soft delete. | Essentialism: the audit `changes` pattern is noted as a future idea, not built. |
| D19 | App connects as the **postgres superuser** (MVP-accepted, documented). Session cleanup = opportunistic delete of expired rows on login. | Consciously accepted simplifications, listed so nobody "fixes" them mid-build. |
| D20 | **Time**: store `TIMESTAMPTZ` everywhere; minutes = `round(seconds/60)` at checkout; UI shows local time via JS. | One rule, no timezone columns. |

## Constants (single source of truth)

| Constant | Value | Where enforced |
|----------|-------|----------------|
| `TOKENS_PER_HOUR` | `1` | `app/tokens.py` — half-up capped math per DOMAIN.md § Checkout math |
| Mint cap | credited minutes ≤ `2 × expected_minutes` | `app/tokens.py` (D6/D7) |
| Session TTL | 30 days | `app/auth.py` |
| Username | `^[a-z0-9_-]{3,30}$` (stored lowercase, unique) | Pydantic + DB unique index |
| Password | 8–72 chars (bcrypt 72-byte truncation) | Pydantic |
| Display name | 1–60 chars | Pydantic |
| Title fields | 1–120 chars | Pydantic |
| Description/bio/waiver/note | ≤ 10 000 chars | Pydantic |
| Image | ≤ 10 MB decoded; `image/jpeg`, `image/png`, `image/webp`; client resize ≤1600px | API + DB CHECK (no separate body-size knob exists — this check is the bound; request-size hardening is deferred with rate limiting) |
| Pagination | limit default 50, max 100 | shared dependency |
| `checkin_code` | 8 chars, `secrets.token_urlsafe(6)`, regenerable | `app/projects.py` |
| Tip amount / price | integer ≥ 1 (price may be 0 for free offers) | Pydantic + DB CHECK |
| App port | `8000` (uvicorn; Caddy proxies to `app:8000`) | everywhere — one number |

## Repository layout (target)

```
active-impact/
├── app/                    # FastAPI backend (each file small and single-purpose)
│   ├── __init__.py
│   ├── main.py             # app assembly: routers, StaticFiles(public, html=True), health
│   ├── db.py               # psycopg pool, query()/tx() helpers, --init runs migrations
│   ├── models.py           # SQLAlchemy models — the schema source of truth
│   ├── auth.py             # register/login/logout, current_user dependency, bcrypt
│   ├── users.py            # /me, public profiles + stats
│   ├── projects.py         # projects, leaders, waivers, QR svg, roster
│   ├── checkin.py          # code resolve, agree(=check-in), checkout, close
│   ├── tokens.py           # ledger read, tip, mint/transfer primitives (the ONLY writers)
│   ├── catalog.py          # items, claims, accept/decline/cancel
│   └── images.py           # base64 upload, authed streaming, delete
├── alembic/  alembic.ini   # Alembic migrations (alembic upgrade head applies them on boot)
├── public/                 # the PWA — no build step, served by FastAPI StaticFiles
│   ├── index.html  style.css  sw.js  manifest.webmanifest
│   ├── icon.svg  icon-192.png  icon-512.png  apple-touch-icon.png
│   ├── app.js              # boot + hash router + chrome
│   ├── api.js  ui.js       # fetch helper / el(), esc(), widgets
│   └── views/              # auth.js projects.js checkin.js catalog.js wallet.js profile.js
├── tests/                  # pytest + httpx TestClient (see BUILD_PLAN.md)
├── scripts/                # smoke.py (stdlib-only), seed.py (dev-only), backup.sh
├── docs/                   # intent + this design tree
├── Dockerfile  docker-compose.yml  docker-compose.prod.yml  Caddyfile
├── deploy.sh  .env.example  .dockerignore  .gitignore  requirements.txt
└── README.md               # quickstart pointing here
```

`requirements.txt` (runtime): `fastapi`, `uvicorn[standard]`, `psycopg[binary]`,
`sqlalchemy`, `alembic`, `bcrypt`, `qrcode`. Dev: `pytest`, `httpx`.

## Deliberately deferred (do NOT build)

Blockchain/monetary value for tokens · differentiated token rates · groups/orgs ·
in-app messaging or comments · QR scanning in-app · moderation/admin tooling ·
email (no addresses collected) · password reset (re-register or future feature) ·
push notifications · offline data sync (shell-only offline) · maps/geocoding
(location is text) · rate limiting & request-size hardening · CI pipeline ·
avatars (UI renders initials) · generic audit table (D18) · account deletion /
soft-delete columns · a `canceled` project status (close covers it; one upgrade
line away) · optimistic concurrency (`version` columns) · animations/branding
polish ("skin it nicely later").

If implementation reveals a genuine gap, follow the framework: record an issue in
`docs/issues/`, choose the smallest resolution, keep moving.

## For the implementing agent

- **Workflow**: documentation → failing tests → code → green → manual verify →
  update docs → commit. Small commits per milestone (BUILD_PLAN.md).
- **The ledger is sacred**: every token movement goes through the two primitives in
  `app/tokens.py` (`mint`, `transfer`), each a single DB transaction; nothing else
  writes `token_entries` or `users.balance`. Test this hardest.
- **Escape all user content in the frontend** (`esc()` by convention or
  `textContent`) — this platform has public UGC, unlike the trusted-insider
  reference app.
- When you are given the domain, DEPLOYMENT.md § Runbook is the exact path to live.
