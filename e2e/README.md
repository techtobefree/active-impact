# Active Impact — E2E (Playwright) layer

Browser tests that drive the app **as a user would**: they screenshot every step
and assert each screen against expectations. The guiding rule is that a screen
must show the *right* thing, not merely "not crash" — e.g. a validation error
must state **why** (never the generic "Something went wrong"). That expectation
is what would have caught the register-error bug.

This layer is **not** part of the default `pytest` run. It's here to grow and run
on demand.

## Run

1. Start the app + Postgres (from the repo root):
   ```bash
   docker compose up -d --build          # → http://localhost:8000
   # (or the fast local loop: docker compose up -d postgres; uvicorn app.main:app)
   ```
   Some token-flow tests use seeded accounts:
   ```bash
   python scripts/seed.py                # users ana / ben / mia (password123)
   ```
2. Install once:
   ```bash
   cd e2e && npm install
   # This config uses the system Google Chrome (channel: 'chrome'). To use
   # Playwright's own browser instead, remove `channel` in playwright.config.js
   # and run:  npx playwright install chromium
   ```
3. Run:
   ```bash
   BASE_URL=http://localhost:8000 npx playwright test      # point at your instance
   npx playwright test tests/auth.spec.js                  # one file
   npx playwright show-report                              # HTML report + screenshots
   ```

Per-step screenshots land in `e2e/screenshots/<test>/NN-step.png` for review.

## What's covered

- **auth** — registration validation surfaces *specific* messages; wrong-password
  login says so; sign in/out.
- **projects** — create a project, open the lead screen (QR renders), self
  check-in via the waiver, check out.
- **catalog** — post an offer; a second user finds and claims it.
- **wallet & profile** — insufficient-balance tip is explained; a funded (seeded)
  user tips another; profile edit persists.

Each spec calls `expectNoGenericError(page)` after success paths — the guard that
fails if the UI ever swallows a real error into the generic message.
