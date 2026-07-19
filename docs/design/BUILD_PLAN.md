# Build Plan — TDD Milestones

> Seven milestones, each shippable and committed. Per milestone: **red** (failing
> tests from this design) → **code** → **green** (full suite) → **manual verify**
> (run it, click it — curl/Playwright/browser) → **docs touch-up** → **commit**.
> Surprises become `docs/issues/` entries, not silent detours. Scope is fixed:
> the Deferred list in OVERVIEW.md is off-limits.

## Test harness (set up in M0)

- `pytest` + FastAPI `TestClient` (httpx). Real Postgres, no mocks of the DB.
- `tests/conftest.py`: connect `postgres://postgres:postgres@localhost:5433/postgres`
  (dev-compose socket) → `CREATE DATABASE impact_test` if missing → run migrations
  (`alembic upgrade head`) → app under test runs with
  `DATABASE_URL=…/impact_test`. Autouse fixture truncates all tables
  (`TRUNCATE … RESTART IDENTITY CASCADE`) between tests. Helper fixtures:
  `register(username) → (client_with_token, user)`.
- **Time trick** (no clock mocking): to test minting math, `UPDATE
  participations SET checked_in_at = now() - interval 'X minutes'` directly, then
  call checkout. No test backdoor exists in the API itself.

## M0 — Skeleton that already deploys

Scaffold per OVERVIEW § Repository layout: `app/db.py` (pool, `query`, `tx`,
`--init` runs Alembic), `app/models.py` + `alembic/` migrations, `app/main.py`
(health + StaticFiles(`public/`, html=True) mounted after routers), placeholder
`public/index.html`, requirements, Dockerfile, both compose files, Caddyfile,
`.env.example`, deploy.sh, backup.sh, .dockerignore.

- Tests: health 200 `{ok,db}`; schema applies **twice** without error (idempotency).
- Verify: `docker compose up -d --build` → browser shows shell, `/api/health` ok;
  prod compose boots with `SITE_ADDRESS=:80` and serves on :80.
- ✅ *Deployable to a VM from this commit onward.*

## M1 — Auth & profiles

`app/auth.py` (register/login/logout, bcrypt, token mint, `current_user` dep,
expired-session sweep on login), `app/users.py` (me, patch, public profile with
zeroed stats). Frontend: shell chrome, router + return-to, `api.js`, `ui.js`
(`el`, `esc`, error map), auth views, Me view, bottom nav.

- Tests: register→me roundtrip; duplicate username 409; case-insensitivity;
  bad login 401 `invalid_credentials`; expired session 401; logout kills token;
  username/password validation bounds; **`esc()` unit sanity** (script-y
  display_name comes back inert in a rendered string).
- Verify: register/login/logout in browser; XSS display-name renders inert.

## M2 — Projects, leaders, waivers, images, QR

`app/projects.py` + `app/images.py`. Frontend: project list/detail/new, lead
screen (QR via authed blob, code text, regenerate, add leader), image strips.

- Tests: create seeds waiver v1 + owner-leader + code; waiver edit → v2, v1
  untouched (I5); leader permission matrix (non-leader PATCH → 403);
  add/remove leader, owner irremovable (409); regenerate kills old code;
  qr.svg 200 leader-only; image upload caps (413 oversize, 422 bad type),
  authed streaming, non-leader upload 403; list scopes + pagination + `q`.
- Verify: phone-scan a QR from the lead screen on the dev LAN → lands on `#/c/…`.

## M3 — Check-in / check-out / minting (the heart)

`app/checkin.py` + checkout math + `mint` primitive in `app/tokens.py`.
Frontend: checkin view (waiver → agree → success), detail-page check-out,
roster check-out, close project.

- Tests: resolve valid/invalid/closed-project code; agree pins **current** waiver
  version (I6); duplicate agree 409 (I3); re-check-in after checkout OK;
  checkout self / by leader / by stranger 403; **minting boundaries
  29/30/89/90/150 min via the backdate trick — half-up integer math, NOT
  Python's `round()` — plus the mint cap: 600 elapsed @ 120 expected → 4 tokens
  (I12)**; zero-minute → 0 tokens, no ledger row; close checks out all + mints
  capped (I4); balance/ledger agree after every case (I1).
- Verify: full two-browser (leader + volunteer) walk on dev; tokens appear.

## M4 — Wallet & tipping

Ledger read + `transfer` primitive + tip endpoint. Frontend: wallet view
(balance, ledger, send-tokens form), profile Send-tokens.

- Tests: tip happy path (both balances, two ledger directions); insufficient
  409 **and nothing changed** (I9); self-tip 409; amount <1 422; unknown user
  404; ledger pagination + direction field; concurrency: two simultaneous tips
  exhausting a balance — exactly one succeeds (run two threads; the atomic
  guard must hold); **I2 static check**: a test that greps `app/` and asserts
  `token_entries` appears in INSERT statements only inside `app/tokens.py` and
  in no UPDATE/DELETE statement anywhere.
- Verify: tip between two browser sessions; ledger reads right both sides.

## M5 — Catalog & claims

`app/catalog.py`. Frontend: catalog tabs/detail/new, claim buttons, poster
claim management, wallet claims section, tip-to-need wiring
(`catalog_item_id`).

- Tests: offer/need creation rules (price required on offers, forbidden on
  needs); claim rules — needs/own/closed 409 per API.md, duplicate pending 409
  (one live claim); accept → spend entry + quantity decrement + auto-close at 0
  (I8, I10); accept with claimant-insufficient 409 leaves claim pending;
  decline/cancel stamp `decided_at`, no tokens (I7); price-0 accept → no entry;
  price edit doesn't touch existing claims' snapshot.
- Verify: full offer→claim→accept loop and need→tip loop in browser.

## M6 — PWA polish & the live gate

sw.js + manifest + icons + install flows; offline message; empty states pass;
`scripts/smoke.py`; `scripts/seed.py`; README quickstart; Lighthouse-installable
check; docs sweep (design ↔ code drift).

- Tests: full suite green; smoke.py passes against local prod-mode compose
  (`SITE_ADDRESS=:80` → `http://localhost`).
- Verify: install to a phone homescreen; airplane-mode shows shell + offline
  message; register-through-QR return-to path once more.
- ✅ Exit = **Definition of done** below.

## M7 — Go live (when the user hands over the URL)

Follow DEPLOYMENT § Runbook exactly: DNS sanity → deploy → .env → deploy →
health → `smoke.py https://<domain>` → phone install → hand the user the URL and
the first-registered account note. Set up the backup cron. Record any surprise
in `docs/issues/`.

## Definition of done (MVP is live when every box ticks)

- [ ] `https://<domain>` serves the PWA with a valid cert; installable on a phone
- [ ] `smoke.py` passes against the live domain
- [ ] Register → post project → second account scans QR → waiver → agree →
      checkout → tokens appear in wallet — **on real phones**
- [ ] Tip between the two accounts; ledger correct both sides
- [ ] Post offer → claim → accept → tokens moved, quantity/closure correct
- [ ] Post need → tip from its page with item context in the ledger
- [ ] Full pytest suite green; every DOMAIN.md invariant has at least one test
- [ ] Backup cron installed; `backups/` receives a dump
- [ ] README quickstart + this design tree match what shipped

## Idea backlog (write to `docs/ideas/`, do not build)

Generic audit table (D18) · auto-checkout timers · notifications · messaging ·
admin/moderation · avatars · maps · token decimals · blockchain export ·
CI pipeline · password reset.
