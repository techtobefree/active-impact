# Domain & Data Model

> The entity model, the complete schema DDL (this **is** the spec for
> `db/schema.sql` — copy it, don't reinvent it), token accounting rules, and the
> invariants the test suite must hold. House style follows home-keep's proven DDL
> conventions with CHECK constraints added (cheap DB-level safety it omitted) and
> its speculative columns removed (soft-delete/version/JSONB carried no MVP flow
> here — the D2 upgrade block is the sanctioned escape hatch if they're ever
> needed).

## Entities at a glance

```
users ──┬── sessions                    (opaque bearer tokens, 30-day expiry)
        ├── projects (owner) ──┬── project_leaders   (owner auto-added; leaders manage)
        │                      ├── waivers           (versioned, immutable text)
        │                      └── participations    (check-in/out; the WAIVER SIGNATURE;
        │                                             source of minutes → tokens)
        ├── catalog_items (poster) ── catalog_claims (pending → accepted/declined/canceled)
        ├── token_entries              (append-only ledger: earn | tip | spend)
        └── images                     (BYTEA, polymorphic: project | catalog_item)
```

Conceptual rules:

- A **project** is anything with a time and a place (a need for labor *is* a
  project). A **catalog item** is a standing offer/need for goods or services.
- A **participation** is created by agreeing to the waiver at check-in and closed
  at check-out. It is simultaneously: the attendance record, the signed waiver
  (via `waiver_id`), and the time sheet (minutes → tokens).
- The **ledger** (`token_entries`) is append-only; `users.balance` is a cached,
  guarded materialization of it. They must always agree.
- **Liveness is `status`**, everywhere: projects are `open|completed`, items are
  `active|closed`, claims have their lifecycle. No soft-delete columns exist
  except none at all — images are hard-deleted (nothing references them).

## DDL house style

- `SERIAL` PKs (`BIGSERIAL` for high-volume append tables: `token_entries`, `images`)
- `TIMESTAMPTZ` with `DEFAULT now()`; plural snake_case table names; `<parent>_id` FKs
- `ON DELETE CASCADE` for owned children; `SET NULL` for optional references
- Status/kind columns are `TEXT` **with CHECK constraints** listing allowed values
- `updated_at` on user-editable tables, bumped by every PATCH
- Idempotent: `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, plus a
  trailing "idempotent upgrades" block of `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
  for post-launch columns (empty at launch)

⚠ **psycopg3 init trap** (from the reference study): applying a multi-statement
`schema.sql` requires client-side execution — server-side binding rejects it. In
`app/db.py --init`:

```python
conn = psycopg.connect(DATABASE_URL, autocommit=True, cursor_factory=psycopg.ClientCursor)
conn.execute(open("db/schema.sql").read())   # ClientCursor → PQexec → multi-statement OK
```

The two `DO $$ … $$` blocks near the end are dollar-quoted and pass through
PQexec untouched — no client-side statement splitting occurs, so do not "fix"
them away (they add the two cross-table FKs `ADD CONSTRAINT` can't declare
idempotently inline).

`app/db.py` defaults `DATABASE_URL` to
`postgres://postgres:postgres@localhost:5433/impact` when the env var is unset
(the dev-compose socket), mirroring the reference app's local-default pattern.

## Schema (complete)

```sql
-- Active Impact schema. Idempotent: applied on every container boot.
-- Postgres 16. See docs/design/DOMAIN.md for the reasoning behind every table.

-- ---- identity ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
  id            SERIAL PRIMARY KEY,
  username      TEXT NOT NULL,                 -- ^[a-z0-9_-]{3,30}$, stored lowercase
  password_hash TEXT NOT NULL,                 -- bcrypt
  display_name  TEXT NOT NULL,
  bio           TEXT NOT NULL DEFAULT '',
  balance       INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),  -- cached ledger sum
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(lower(username));

-- Bearer-token sessions. Expired rows are deleted opportunistically on login.
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,                 -- secrets.token_hex(32)
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,             -- now() + 30 days at mint
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- ---- impact projects --------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
  id               SERIAL PRIMARY KEY,
  owner_id         INTEGER NOT NULL REFERENCES users(id),
  title            TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  location_text    TEXT NOT NULL,              -- free text; maps/geocoding deferred
  starts_at        TIMESTAMPTZ NOT NULL,
  expected_minutes INTEGER NOT NULL CHECK (expected_minutes > 0),
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed')),
  checkin_code     TEXT NOT NULL UNIQUE,       -- secrets.token_urlsafe(6); regenerable
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_projects_starts ON projects(starts_at);
CREATE INDEX IF NOT EXISTS idx_projects_owner  ON projects(owner_id);

-- Leaders may edit the project, show the QR, manage the roster, close it.
-- The owner is inserted here at project creation and cannot be removed.
CREATE TABLE IF NOT EXISTS project_leaders (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_leaders_user ON project_leaders(user_id);

-- Waiver text is immutable once created; edits INSERT a new version.
-- Project creation seeds version 1 (default template if none supplied).
CREATE TABLE IF NOT EXISTS waivers (
  id         SERIAL PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version    INTEGER NOT NULL,
  text       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, version)
);

-- A participation is the check-in record, the signed waiver, and the time sheet.
-- Created by POST /api/checkin/{code}/agree; closed by checkout (self, leader,
-- or project close). tokens_awarded is set exactly once, at checkout.
CREATE TABLE IF NOT EXISTS participations (
  id              SERIAL PRIMARY KEY,
  project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  waiver_id       INTEGER NOT NULL REFERENCES waivers(id), -- the version agreed to
  checked_in_at   TIMESTAMPTZ NOT NULL DEFAULT now(),      -- agreement timestamp = signature
  checked_out_at  TIMESTAMPTZ,
  minutes         INTEGER CHECK (minutes >= 0),            -- actual elapsed, half-up
  tokens_awarded  INTEGER CHECK (tokens_awarded >= 0),     -- from CAPPED minutes; may be 0
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One OPEN participation per user per project (re-check-in after checkout is fine).
CREATE UNIQUE INDEX IF NOT EXISTS idx_participations_open
  ON participations(project_id, user_id) WHERE checked_out_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_participations_user    ON participations(user_id);
CREATE INDEX IF NOT EXISTS idx_participations_project ON participations(project_id);

-- ---- impact tokens ----------------------------------------------------------

-- APPEND-ONLY ledger. Never UPDATE, never DELETE (no endpoint may exist).
-- from_user NULL = system mint (kind 'earn'). All amounts positive; direction
-- is the from/to pair. users.balance is updated in the SAME transaction.
CREATE TABLE IF NOT EXISTS token_entries (
  id               BIGSERIAL PRIMARY KEY,
  from_user_id     INTEGER REFERENCES users(id),          -- NULL = minted by system
  to_user_id       INTEGER NOT NULL REFERENCES users(id),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  kind             TEXT NOT NULL CHECK (kind IN ('earn', 'tip', 'spend')),
  participation_id INTEGER REFERENCES participations(id) ON DELETE SET NULL, -- kind=earn
  claim_id         INTEGER,                               -- kind=spend (FK added below)
  catalog_item_id  INTEGER,                               -- optional context for tips to a need
  note             TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entries_to   ON token_entries(to_user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_entries_from ON token_entries(from_user_id, id DESC);

-- ---- catalog ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_items (
  id           SERIAL PRIMARY KEY,
  poster_id    INTEGER NOT NULL REFERENCES users(id),
  kind         TEXT NOT NULL CHECK (kind IN ('offer', 'need')),
  title        TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',      -- coupon terms, contact info, etc. live here
  price_tokens INTEGER CHECK (price_tokens >= 0),  -- offers: required (0 = free); needs: NULL
  quantity     INTEGER CHECK (quantity > 0),  -- NULL = unlimited; auto-close at 0
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- every offer is priced (0 allowed); needs are never priced
  CHECK ((kind = 'need' AND price_tokens IS NULL) OR (kind = 'offer' AND price_tokens IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_catalog_kind   ON catalog_items(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_poster ON catalog_items(poster_id);

-- Claim lifecycle: pending → accepted | declined | canceled. Tokens move ONLY on
-- accept (claimant → poster, kind 'spend'), in the same transaction that
-- decrements quantity and stamps decided_at.
CREATE TABLE IF NOT EXISTS catalog_claims (
  id           SERIAL PRIMARY KEY,
  item_id      INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
  claimant_id  INTEGER NOT NULL REFERENCES users(id),
  price_tokens INTEGER NOT NULL CHECK (price_tokens >= 0),  -- snapshot at claim time
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'accepted', 'declined', 'canceled')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at   TIMESTAMPTZ
);
-- One live claim per user per item.
CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_pending
  ON catalog_claims(item_id, claimant_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_claims_claimant ON catalog_claims(claimant_id);
CREATE INDEX IF NOT EXISTS idx_claims_item     ON catalog_claims(item_id);

-- ---- images -----------------------------------------------------------------

-- Photos live in Postgres (BYTEA). Client resizes to <=1600px JPEG before upload.
-- DELETE /api/images/{id} hard-deletes the row (nothing references images).
CREATE TABLE IF NOT EXISTS images (
  id           BIGSERIAL PRIMARY KEY,
  entity       TEXT NOT NULL CHECK (entity IN ('project', 'catalog_item')),
  entity_id    INTEGER NOT NULL,
  content_type TEXT NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
  bytes        BYTEA NOT NULL,
  size         INTEGER NOT NULL CHECK (size > 0 AND size <= 10485760),  -- 10 MB
  uploaded_by  INTEGER REFERENCES users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_images_entity ON images(entity, entity_id);

-- ---- cross-table FKs (added late so tables exist) ---------------------------
-- Dollar-quoted DO blocks: needed because ADD CONSTRAINT has no IF NOT EXISTS.
-- They re-run safely and pass through PQexec whole — do not remove or split.

DO $$ BEGIN
  ALTER TABLE token_entries ADD CONSTRAINT fk_entries_claim
    FOREIGN KEY (claim_id) REFERENCES catalog_claims(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE token_entries ADD CONSTRAINT fk_entries_item
    FOREIGN KEY (catalog_item_id) REFERENCES catalog_items(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---- idempotent upgrades (no-ops on a fresh database) -----------------------
-- Post-launch column additions go here, e.g.:
-- ALTER TABLE projects ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ;
```

## Token accounting

**Two primitives in `app/tokens.py` — the only code that writes `token_entries`
or `users.balance`.** Both run inside a single `tx()`:

```
mint(c, to_user, amount, participation_id, note=None)
  → INSERT token_entries(from NULL, to, amount, 'earn', participation_id)
  → UPDATE users SET balance = balance + amount WHERE id = to_user

transfer(c, from_user, to_user, amount, kind, claim_id=None, catalog_item_id=None, note=None)
  # kind ∈ {'tip', 'spend'}
  → UPDATE users SET balance = balance - amount
      WHERE id = from_user AND balance >= amount     -- atomic overdraft guard
    (rowcount 0 → raise InsufficientBalance → HTTP 409 'insufficient_balance')
  → UPDATE users SET balance = balance + amount WHERE id = to_user
  → INSERT token_entries(from, to, amount, kind, …)
```

### Checkout math (exact — half-up, capped)

⚠ **Python's built-in `round()` is banker's rounding (`round(0.5) == 0`) and must
NOT be used anywhere in this math.** Integer half-up expressions only:

```
checkout(participation):                      # runs inside one tx, self/leader/close alike
  elapsed_seconds = (now - checked_in_at).total_seconds()
  minutes  = (int(elapsed_seconds) + 30) // 60          # actual elapsed, half-up — stored truthfully
  credited = min(minutes, 2 * project.expected_minutes) # MINT CAP: forgotten checkouts
                                                        # cannot inflate the token supply (D6/D7)
  tokens   = (credited + 30) // 60                      # nearest hour, half-up; may be 0
  UPDATE participations SET checked_out_at = now, minutes = minutes, tokens_awarded = tokens
  if tokens > 0: mint(...)
```

Worked boundaries (these examples are the authority; I12 tests them):
29 min → 0 · 30 min → 1 · 89 min → 1 · 90 min → 2 · 150 min → 3.
Cap example: `expected_minutes=120`, checked out 600 min later → `minutes=600`
stored, `credited=240`, **tokens = 4** (not 10). Leaders earn identically to
volunteers (flat rate is intent).

## Invariants (the test suite must assert these)

| # | Invariant |
|---|-----------|
| I1 | `users.balance` = Σ entries in − Σ entries out for every user, always ≥ 0 |
| I2 | `token_entries` is append-only: no UPDATE/DELETE code path exists — asserted by a static source check that only `app/tokens.py` writes the table (BUILD_PLAN M4) |
| I3 | At most one open participation per (project, user) — enforced by partial unique index |
| I4 | `minutes` and `tokens_awarded` are both set iff `checked_out_at` is set |
| I5 | Waiver rows are never mutated; a text edit inserts version n+1 |
| I6 | Every participation's `waiver_id` belongs to its `project_id` |
| I7 | Claims only transition `pending → accepted/declined` (by poster) or `pending → canceled` (by claimant); `decided_at` stamped exactly then |
| I8 | An accepted claim with price > 0 ↔ exactly one `spend` entry with that `claim_id`; declined/canceled claims have none |
| I9 | Transfer with insufficient balance changes **nothing** (no entry, no balance drift) |
| I10 | Only active, in-quantity `offer`s can be claimed (every offer is priced; 0 = free); quantity hits 0 → item `closed` |
| I11 | Check-in requires the presented `checkin_code` to match an `open` project |
| I12 | Checkout math: the 29/30/89/90/150-minute boundaries **and** the mint cap (600 elapsed @ 120 expected → 4 tokens) above |

## Standard read shapes (used by API.md)

- **user_public**: `id, username, display_name, bio, created_at` + stats
  (`hours_volunteered` = Σ minutes/60 rounded to 1 decimal, `tokens_earned` =
  Σ `earn` entries, `projects_joined` = count distinct closed participations'
  projects). Balance is **private** (only in `/api/me`).
- **project_card**: `id, title, location_text, starts_at, expected_minutes,
  status, cover_image_id (first image or null), checked_in_count, owner {id,
  username, display_name}`.
- **item_card**: `id, kind, title, price_tokens, quantity, status,
  cover_image_id, poster {id, username, display_name}, created_at`.
