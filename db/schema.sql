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

-- Claim lifecycle: pending -> accepted | declined | canceled. Tokens move ONLY on
-- accept (claimant -> poster, kind 'spend'), in the same transaction that
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
-- They re-run safely and pass through PQexec whole -- do not remove or split.

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
