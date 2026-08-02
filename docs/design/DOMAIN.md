# Domain & Data Model

> The entity model, the complete schema (defined as **SQLAlchemy models** in
> `app/models.py` and built/evolved by **Alembic** — the DDL below is the logical
> reference, kept in sync with the models), token accounting rules, and the
> invariants the test suite must hold. House style follows home-keep's proven DDL
> conventions with CHECK constraints added (cheap DB-level safety it omitted) and
> its speculative columns removed (soft-delete/version/JSONB carried no MVP flow
> here — the D2 upgrade block is the sanctioned escape hatch if they're ever
> needed).

## Entities at a glance

```
users ──┬── sessions                    (opaque bearer tokens, 30-day expiry)
        ├── user_follows               (person -> PERSON; distinct from `follows` below)
        ├── blocks                     (one-way: "they may not see my activity"; keeps the follow)
        ├── activities                 (append-only PUBLIC projection: logged | rsvp |
        │                               checked_in | created_project | scheduled_event)
        ├── projects (owner) ──┬── project_leaders   (organizers; manage the project + all its events)
        │                      ├── waivers           (versioned, immutable text; project-scoped)
        │                      ├── follows           (interest / bookmark; drives follower_count)
        │                      └── events ──┬── location       (LOCATIONS.md: the remembered address,
        │                                    │                   upserted from location_text; learns lat/lon)
        │                                    ├── rsvps          (RSVP intent; is_leader = event-leader
        │                        (occurrence)│                  DESIGNATION, a flag with no powers yet)
        │                                    ├── participations (check-in/out; the WAIVER SIGNATURE;
        │                                    │                   source of minutes → tokens)
        │                                    └── attestations   (append-only SIGHTINGS: scanner reports
        │                                                        subject was here — CHECKIN_PROOF.md)
        ├── catalog_items (poster) ── catalog_claims (pending → accepted/declined/canceled)
        ├── token_entries              (append-only ledger: earn | tip | spend)
        ├── audit_log                  (append-only audit log: check_in | check_out; carries event + project)
        ├── images                     (BYTEA, polymorphic: project | catalog_item | event | service_record)
        └── service_records ──┬── cheers    (anonymous-first LOG: one photo + one caption)
          (author = guest or  └── reports   (cheers: one 🙌/user/record · reports: 3 distinct → auto-hide)
           real user)            └─ event_id → events  (FEED.md: which event it was logged AT;
                                                        NULL when nothing matched)
```

Conceptual rules:

- A **project** is the persistent *service project* — the durable umbrella (title,
  description, organizers, versioned waivers, images, follows). Each time it
  actually runs is an **event**: one occurrence with its own `starts_at`,
  `location_text`, `expected_minutes`, `checkin_code`, and `open|completed`
  status. A project has **many events**. A **catalog item** is a standing
  offer/need for goods or services.
- **rsvps and participations hang off an EVENT** (`event_id`), not the project.
  Waivers stay **project-scoped**; checking in to an event pins the event's
  project's current waiver version.
- A **participation** is created by agreeing to the waiver at check-in and closed
  at check-out. It is simultaneously: the attendance record, the signed waiver
  (via `waiver_id`), and the time sheet (minutes → tokens).
- The **ledger** (`token_entries`) is append-only; `users.balance` is a cached,
  guarded materialization of it. They must always agree.
- The **audit log** (`audit_log`, formerly the `events` table before that name was
  reassigned to the occurrence domain) is an append-only trail: one immutable
  `check_in` / `check_out` row per event, written in the **same transaction** as
  the state change it records (so a row never exists without its change, nor a
  change without its row). Each row carries both the `event_id` (occurrence) and
  the `project_id` (its umbrella) for event- and project-level reporting. Rows are
  never updated or deleted — the source of truth for later reporting.
- **All multi-statement writes are transactional** via `db.tx()` (one
  `conn.transaction()`): the state change and its ledger/audit rows commit or roll
  back together.
- **Liveness is `status`**, everywhere: **events** are `open|completed`, items are
  `active|closed`, claims have their lifecycle. No soft-delete columns exist
  except none at all — images are hard-deleted (nothing references them).
- **An event is over** when `status='completed'` OR `now() > starts_at +
  expected_minutes` (a per-EVENT property). The feed splits projects on exactly
  this: **`upcoming` = has a not-over event** (card shows the soonest such event),
  **`past` = has no not-over event** (card shows the most-recent event, or null) —
  complementary, so a project whose only event ended shows under `past` and never
  under `upcoming`.
- **A follow** (`follows`) is a lightweight bookmark / interest signal, one row
  per (user, project), distinct from an RSVP (attendance intent on an event). It
  carries no powers; it only drives `is_following` and `follower_count`.
- **A guest is a `users` row with `email IS NULL` and `password_hash IS NULL`**
  (SERVICE_LOG.md §4). `email IS NULL ⇔ guest` drives everything — it reuses
  `sessions`, `current_user`, and `me_shape` unchanged. Both columns are NULLABLE;
  real accounts require them at the handler level, not the column. The unique
  `lower(email)` index is unaffected (Postgres treats NULLs as distinct, so many
  guests coexist). `me_shape.is_guest` = `email IS NULL`.
- **A `service_record` is one logged act of service** (SERVICE_LOG.md) — one photo
  (reusing `images` with `entity='service_record'`) + one caption, authored by
  whoever you currently are. It creates **no** participations and moves **no**
  tokens, but it **does belong to an event** (`event_id`, nullable — FEED.md): the
  server resolves which one from the author's check-in / RSVP / GPS + time at log
  time. `event_id IS NULL` means nothing matched — the record is the author's own
  log entry until they attach it. **cheers** are one 🙌
  per user per record (toggle = insert/delete on the UNIQUE(record_id,user_id));
  **reports** auto-hide a record (`hidden=true`) once **3 distinct** reporters file
  one. On **convert** (guest→real), a guest's records/cheers/reports re-point to the
  target account in one tx and the guest row is retired.

## DDL house style

- `SERIAL` PKs (`BIGSERIAL` for high-volume append tables: `token_entries`, `images`)
- `TIMESTAMPTZ` with `DEFAULT now()`; plural snake_case table names; `<parent>_id` FKs
- `ON DELETE CASCADE` for owned children; `SET NULL` for optional references
- Status/kind columns are `TEXT` **with CHECK constraints** listing allowed values
- `updated_at` on user-editable tables, bumped by every PATCH
- Schema changes are **Alembic migrations** (autogenerated from `app/models.py`,
  reviewed, then `alembic upgrade head`) — no hand-rolled idempotent DDL

**Schema source & migrations:** the schema is defined as **SQLAlchemy models** in
`app/models.py` (the source of truth) and built/evolved by **Alembic**. On boot
`python -m app.db --init` runs `alembic upgrade head`. To change the schema: edit
the models → `alembic revision --autogenerate -m "..."` → review the generated
migration → `alembic upgrade head`. SQLAlchemy resolves table-creation order, so
the cross-table FKs need no special handling.

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
  email         TEXT,                          -- login credential; PRIVATE; lowercased. NULL ⇒ GUEST (SERVICE_LOG.md §4)
  password_hash TEXT,                          -- bcrypt. NULL ⇒ GUEST. Real accounts require both, enforced in the handler
  display_name  TEXT NOT NULL,
  bio           TEXT NOT NULL DEFAULT '',
  balance       INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),  -- cached ledger sum
  qr_token      TEXT NOT NULL UNIQUE,          -- secrets.token_urlsafe(8); my permanent opaque handle, the thing my personal QR carries (CHECKIN_PROOF.md §3)
  notifications_seen_at TIMESTAMPTZ,           -- the watermark notifications are DERIVED from (SOCIAL.md S6)
  notify_activity BOOLEAN NOT NULL DEFAULT true,  -- "tell me when people I follow RSVP or check in"
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));
-- (Historically this column was `username`; Alembic migration 0002 renamed it.)

-- Bearer-token sessions. Expired rows are deleted opportunistically on login.
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,                 -- secrets.token_hex(32)
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,             -- now() + 30 days at mint
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- ---- impact projects --------------------------------------------------------

-- The DURABLE service project. Event-specific fields (schedule/place/code/status)
-- live on `events`; a project has many events.
CREATE TABLE IF NOT EXISTS projects (
  id               SERIAL PRIMARY KEY,
  owner_id         INTEGER NOT NULL REFERENCES users(id),
  title            TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_projects_owner  ON projects(owner_id);

-- An EVENT is one occurrence of a project: a specific dated/located session with
-- its own check-in code and open/completed lifecycle. participations and rsvps
-- hang off an event. is_over := status='completed' OR now() > starts_at +
-- expected_minutes (per event).
CREATE TABLE IF NOT EXISTS events (
  id               BIGSERIAL PRIMARY KEY,
  project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  starts_at        TIMESTAMPTZ NOT NULL,
  expected_minutes INTEGER NOT NULL CHECK (expected_minutes > 0),
  location_text    TEXT NOT NULL,              -- free text; maps/geocoding deferred
  lat              DOUBLE PRECISION,           -- where it actually is (FEED.md F5): set by a
  lon              DOUBLE PRECISION,           -- leader, bootstrapped from the first matched record's
                                               -- GPS, or inherited from its LOCATION. NULL = unknown
  location_id      INTEGER REFERENCES locations(id) ON DELETE SET NULL,  -- the shared place (LOCATIONS.md)
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed')),
  checkin_code     TEXT NOT NULL UNIQUE,       -- secrets.token_urlsafe(6); regenerable
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);
CREATE INDEX IF NOT EXISTS idx_events_starts  ON events(starts_at);

-- Leaders may edit the project, add events, show a QR, manage the roster, close
-- an event.
-- The owner is inserted here at project creation and cannot be removed.
CREATE TABLE IF NOT EXISTS project_leaders (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_leaders_user ON project_leaders(user_id);

-- RSVP / check-in intent on an EVENT. One row per (event, user), created by
-- POST /api/events/{id}/rsvp, self check-in, or a QR agree (all idempotent).
-- is_leader is an event-leader DESIGNATION (a pure flag with no powers yet),
-- toggled by the organizer -- DISTINCT from project_leaders (organizers).
CREATE TABLE IF NOT EXISTS rsvps (
  id         SERIAL PRIMARY KEY,
  event_id   BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  is_leader  BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (event_id, user_id)                   -- constraint name: event_user
);
CREATE INDEX IF NOT EXISTS idx_rsvps_user ON rsvps(user_id);

-- Follows: a lightweight interest/bookmark, one row per (user, project).
-- Distinct from an RSVP (attendance intent); carries no powers. Created by
-- POST /api/projects/{id}/follow (idempotent ON CONFLICT), removed by DELETE.
-- Drives is_following (per requester) and follower_count in project detail.
CREATE TABLE IF NOT EXISTS follows (
  id         SERIAL PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, project_id)                 -- constraint name: uq_follow
);
CREATE INDEX IF NOT EXISTS idx_follows_project ON follows(project_id);

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

-- A participation is the check-in record, the signed waiver, and the time sheet,
-- on an EVENT. Created by POST /api/checkin/{code}/agree; closed by checkout
-- (self, leader, or event close). tokens_awarded is set exactly once, at checkout.
CREATE TABLE IF NOT EXISTS participations (
  id              SERIAL PRIMARY KEY,
  event_id        BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  waiver_id       INTEGER NOT NULL REFERENCES waivers(id), -- the version agreed to (project-scoped)
  checked_in_at   TIMESTAMPTZ NOT NULL DEFAULT now(),      -- agreement timestamp = signature
  checked_out_at  TIMESTAMPTZ,
  minutes         INTEGER CHECK (minutes >= 0),            -- actual elapsed, half-up
  tokens_awarded  INTEGER CHECK (tokens_awarded >= 0),     -- from CAPPED minutes; may be 0
  attested        BOOLEAN NOT NULL DEFAULT false,          -- someone else's QR corroborates this (CHECKIN_PROOF.md)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One OPEN participation per user per event (re-check-in after checkout is fine).
CREATE UNIQUE INDEX IF NOT EXISTS idx_participations_open
  ON participations(event_id, user_id) WHERE checked_out_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_participations_user  ON participations(user_id);
CREATE INDEX IF NOT EXISTS idx_participations_event ON participations(event_id);

-- ---- presence proof (CHECKIN_PROOF.md) ---------------------------------------

-- APPEND-ONLY sightings. One row = "scanner reports subject's personal QR was in
-- front of them, at this event". A single row is evidence about BOTH people; it
-- keeps its direction so who-reported-whom survives. The UNIQUE makes a re-scan a
-- no-op rather than an error. A scan never creates a participation for the
-- SUBJECT — that would forge their waiver signature (I14).
CREATE TABLE IF NOT EXISTS attestations (
  id              BIGSERIAL PRIMARY KEY,
  event_id        BIGINT  NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  scanner_user_id INTEGER NOT NULL REFERENCES users(id),   -- who scanned (the reporter)
  subject_user_id INTEGER NOT NULL REFERENCES users(id),   -- whose code was scanned
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_attestations_not_self CHECK (scanner_user_id <> subject_user_id),
  CONSTRAINT uq_attestations_event_scanner_subject UNIQUE (event_id, scanner_user_id, subject_user_id)
);
CREATE INDEX IF NOT EXISTS idx_attestations_event   ON attestations(event_id);
CREATE INDEX IF NOT EXISTS idx_attestations_subject ON attestations(subject_user_id);

-- ---- social (SOCIAL.md) -----------------------------------------------------
-- Person -> person. `follows` (below) is person -> PROJECT: same word, different
-- object, deliberately different tables.
CREATE TABLE IF NOT EXISTS user_follows (
  id          SERIAL PRIMARY KEY,
  follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_user_follows_not_self CHECK (follower_id <> followee_id),
  CONSTRAINT uq_user_follows UNIQUE (follower_id, followee_id)
);
CREATE INDEX IF NOT EXISTS idx_user_follows_followee ON user_follows(followee_id);

-- "This person may not see my activity" (S4). Never touches user_follows: a
-- blocked person REMAINS a follower and can be unblocked.
CREATE TABLE IF NOT EXISTS blocks (
  id         SERIAL PRIMARY KEY,
  blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_blocks_not_self CHECK (blocker_id <> blocked_id),
  CONSTRAINT uq_blocks UNIQUE (blocker_id, blocked_id)
);
CREATE INDEX IF NOT EXISTS idx_blocks_blocked ON blocks(blocked_id);

-- APPEND-ONLY public projection of what someone did, written in the SAME tx as
-- the action (S2). Kept separate from audit_log on purpose (S3): an audit row is
-- a reporting record, an activity row is public and CASCADEs with its subject so
-- a feed never points at something that is gone.
CREATE TABLE IF NOT EXISTS activities (
  id         BIGSERIAL PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('logged', 'rsvp', 'checked_in',
                                           'created_project', 'scheduled_event')),
  event_id   BIGINT  REFERENCES events(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  record_id  BIGINT  REFERENCES service_records(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activities_user    ON activities(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at DESC);

-- ---- locations (LOCATIONS.md) -----------------------------------------------
-- The address book the app builds itself: every address typed on an event upserts
-- a row here, matched on `norm`. There is no "create a location" flow. lat/lon are
-- LEARNED (from an event, or from a photo's GPS) and never overwritten, so the
-- second event at a venue is located from the moment it is created.
CREATE TABLE IF NOT EXISTS locations (
  id         SERIAL PRIMARY KEY,
  label      TEXT NOT NULL,                    -- as first typed; what suggestions offer back
  norm       TEXT NOT NULL UNIQUE,             -- lower(collapsed ws, edge punctuation stripped)
  lat        DOUBLE PRECISION,
  lon        DOUBLE PRECISION,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_location ON events(location_id);

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
  quantity     INTEGER CHECK (quantity >= 0),  -- NULL = unlimited; reaches 0 -> auto-closed
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
-- is_primary marks the entity's COVER. On upload, if the entity has no primary
-- yet, the new image becomes primary automatically; POST /api/images/{id}/primary
-- (or upload with is_primary=true) sets one and unsets the others in one tx.
-- COVER RULE: cover_image_id = the primary image, else the first by id
--   (ORDER BY is_primary DESC, id ASC LIMIT 1) -- so deleting the primary falls
--   back to the first remaining image.
-- Polymorphic: an image attaches to a project, a catalog_item, an event, OR a
-- service_record. An event's images are managed by the leaders of the event's
-- project; a service_record's image is managed by its author.
CREATE TABLE IF NOT EXISTS images (
  id           BIGSERIAL PRIMARY KEY,
  entity       TEXT NOT NULL CHECK (entity IN ('project', 'catalog_item', 'event', 'service_record')),
  entity_id    INTEGER NOT NULL,
  content_type TEXT NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp')),
  bytes        BYTEA NOT NULL,
  size         INTEGER NOT NULL CHECK (size > 0 AND size <= 10485760),  -- 10 MB
  uploaded_by  INTEGER REFERENCES users(id),
  is_primary   BOOLEAN NOT NULL DEFAULT false,          -- the entity's cover
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_images_entity ON images(entity, entity_id);

-- audit_log: append-only audit log (renamed from the old `events` table, which
-- name now belongs to the occurrence domain above). One immutable row per
-- check-in / check-out, written in the SAME tx as the state change (see
-- app/audit.log). Carries the occurrence (event_id) AND its project (project_id).
-- Never updated or deleted -- the source of truth for later reporting.
CREATE TABLE IF NOT EXISTS audit_log (
  id               BIGSERIAL PRIMARY KEY,
  type             TEXT NOT NULL,                          -- 'check_in' | 'check_out'
  actor_user_id    INTEGER REFERENCES users(id),           -- who performed it (self / leader / closer)
  subject_user_id  INTEGER REFERENCES users(id),           -- the volunteer it is about
  project_id       INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  event_id         BIGINT  REFERENCES events(id)   ON DELETE SET NULL,
  participation_id INTEGER REFERENCES participations(id) ON DELETE SET NULL,
  minutes          INTEGER,
  tokens           INTEGER,
  meta             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_type ON audit_log(type, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_project ON audit_log(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_subject ON audit_log(subject_user_id);

-- ---- service log (anonymous-first) ------------------------------------------
-- A standalone social log (SERVICE_LOG.md): one photo + one caption, authored by
-- whoever you currently are (guest or real). Touches no tokens/projects/ledger.
-- The photo reuses images (entity='service_record'); cheers/reports drive light
-- moderation. Author FKs are ON DELETE CASCADE so a retired guest's leftovers
-- clean up; convert re-points them to the target account first (nothing is lost).
CREATE TABLE IF NOT EXISTS service_records (
  id           BIGSERIAL PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- author
  caption      TEXT NOT NULL,                         -- 1-280 chars (validated in the handler)
  hidden       BOOLEAN NOT NULL DEFAULT false,        -- moderation: 3 distinct reports auto-set this
  -- Which event this was logged AT (FEED.md F1). SET NULL, never CASCADE: deleting
  -- an event must not delete the photos people took there.
  event_id     BIGINT REFERENCES events(id) ON DELETE SET NULL,
  -- The author's position at log time — a MATCHING INPUT ONLY, never served in any
  -- read shape (FEED.md F6). A caption is public; coordinates are not.
  lat          DOUBLE PRECISION,
  lon          DOUBLE PRECISION,
  match_reason TEXT,                                  -- explicit|checked_in|participated|rsvp|nearby|NULL
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_service_records_created ON service_records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_service_records_user    ON service_records(user_id);
CREATE INDEX IF NOT EXISTS idx_service_records_event   ON service_records(event_id, created_at DESC);

-- One 🙌 per user per record. Toggle = insert / delete; the UNIQUE also serves the
-- feed's cheer_count + the auto-hide DISTINCT count.
CREATE TABLE IF NOT EXISTS cheers (
  id          BIGSERIAL PRIMARY KEY,
  record_id   BIGINT  NOT NULL REFERENCES service_records(id) ON DELETE CASCADE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (record_id, user_id)
);

-- Moderation-light: 3 distinct reporters (UNIQUE(record_id,user_id) keeps it one
-- per user) auto-hide the record. No admin UI yet — unhide is a manual DB flag.
CREATE TABLE IF NOT EXISTS reports (
  id          BIGSERIAL PRIMARY KEY,
  record_id   BIGINT  NOT NULL REFERENCES service_records(id) ON DELETE CASCADE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- reporter
  reason      TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (record_id, user_id)
);

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
  credited = min(minutes, 2 * event.expected_minutes)   # MINT CAP: forgotten checkouts
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
| I3 | At most one open participation per (event, user) — enforced by partial unique index |
| I4 | `minutes`/`tokens_awarded` are set exactly once, **atomically** with `checked_out_at` — the checkout UPDATE is guarded `… WHERE checked_out_at IS NULL`, so concurrent checkouts (double-tap / self-vs-leader / checkout-vs-close) mint once, never twice |
| I5 | Waiver rows are never mutated; a text edit inserts version n+1 |
| I6 | Every participation's `waiver_id` belongs to its event's `project_id` (waivers are project-scoped; check-in pins the event's project's current waiver) |
| I7 | Claims only transition `pending → accepted/declined` (by poster) or `pending → canceled` (by claimant); `decided_at` stamped exactly then |
| I8 | An accepted claim with price > 0 ↔ exactly one `spend` entry with that `claim_id`; declined/canceled claims have none |
| I9 | Transfer with insufficient balance changes **nothing** (no entry, no balance drift) |
| I10 | Only active, in-quantity `offer`s can be claimed (every offer is priced; 0 = free); quantity hits 0 → item `closed` |
| I11 | Check-in requires the presented `checkin_code` to match an `open` event |
| I12 | Checkout math: the 29/30/89/90/150-minute boundaries **and** the mint cap (600 elapsed @ 120 expected → 4 tokens) above |
| I13 | An `attestations` row always names two **different** users (CHECK) and is unique per (event, scanner, subject) — a repeat scan is a no-op, never an error |
| I14 | A scan never creates a participation for the **subject**; the scanner's participation always carries a `waiver_id` from the event's project (I6 holds for every row, however created) |
| I15 | `participations.attested` is true ⟺ an attestation for that (event, user) existed at or before the participation was written — set at insert, and flipped by a later scan only on a participation that is still open |
| S-I1…S-I8 | The social invariants — nobody follows or blocks themselves, blocking keeps the follower, a blocked viewer sees no activity anywhere, and unread is exactly the notifiable activity after my watermark. Stated and tested in [SOCIAL.md § 6](./SOCIAL.md#6-invariants) |
| L-I1…L-I6 | The location invariants — one row per normalized address, coordinates that flow both ways and are never overwritten, prefix-first suggestions that never expose a position. Stated and tested in [LOCATIONS.md § 6](./LOCATIONS.md#6-invariants) |
| F-I1…F-I9 | The one-feed invariants — event matching, the ≤2 records per card, and "coordinates are never served". Stated and tested in [FEED.md § 9](./FEED.md#9-invariants-the-test-suite-asserts-these) |

## Standard read shapes (used by API.md)

- **user_public**: `id, display_name, bio, created_at` + stats (never the email)
  (`hours_volunteered` = Σ minutes/60 rounded to 1 decimal, `tokens_earned` =
  Σ `earn` entries, `projects_joined` = count distinct **projects** across the
  events of my closed participations). Balance is **private** (only in `/api/me`).
- **project_card**: `id, title, cover_image_id` (primary image, else first by id,
  or null)`, follower_count, event` — where `event` is ONE embedded **event_card**
  (the soonest not-over event for `upcoming`, the most-recent for `past`) or
  `null` (a project with no relevant event). The card's photos come from
  `event.records` — records belong to an occurrence, so the event owns them.
- **event_card**: `id, starts_at, location_text, expected_minutes, status,
  is_over` (per-event), `cover_image_id` (the event's own cover — primary image
  else first by id, or null), `checked_in_count`, `record_count` (non-hidden),
  `records` (the **≤2 most recent non-hidden record_cards** of this event,
  newest-first, `[]` when none — FEED.md F3) + per-requesting-user state
  `my_rsvp {is_leader}|null`, `my_open_participation {id, checked_in_at}|null`,
  `my_hours_here` (all batched by event id — no N+1).
- **event_detail**: event_card + `image_ids[]` (the event's images, entity
  `'event'`, ordered by id) + `lat`/`lon` (nullable — the ONLY shape serving
  coordinates, so a leader can see and correct them) + `checkin_code` (present
  **only** when the requester leads the event's project). Events may carry their
  own images (entity `'event'`); the event's project leaders manage them. Used
  inside project detail, returned by `POST /api/projects/{id}/events`, and by the
  event-scoped endpoints.
- **location_suggestion** (LOCATIONS.md §3): `id, label, event_count` — never
  `lat`/`lon`.
- **event_candidate** (FEED.md §4, the "which event am I at?" picker):
  `event_id, project_id, project_title, starts_at, location_text,
  distance_km|null, reason`.
- **item_card**: `id, kind, title, price_tokens, quantity, status,
  cover_image_id, poster {id, display_name}, created_at`.
- **record_card** (service log): `id, author {id, display_name, is_guest},
  caption, photo_image_id` (the record's cover image, streamed via
  `GET /api/images/{id}`)`, created_at, cheer_count, i_cheered, event` — where
  `event` is `{id, project_id, project_title, starts_at}` or `null` (unattached).
  The author is identity-only — it **never** exposes an email; the record's
  `lat`/`lon`/`match_reason` are **never** exposed at all (FEED.md F6).
  `cheer_count` + `i_cheered` are **batched by record id** in the feed (no N+1),
  like `event_card`.
- **activity_card** (SOCIAL.md): `id, kind` (`logged|rsvp|checked_in`)`, actor
  {id, display_name, is_guest}, created_at, event | null, record: record_card |
  null` — the `event` shape is the one record_card already carries.
- **person_card** (SOCIAL.md): `id, display_name, is_guest, is_following,
  is_blocked` — my relationship to them. Never an email.
- **me** (`GET /api/me`, the private self view): `id, email, display_name, bio,
  balance, created_at, is_guest, notify_activity, follower_count, following_count`
  — the counts label the profile card's two tabs and decide whether a "See all N"
  belongs under the first 100. The **only** shape carrying the email (and
  balance). `is_guest` = `email IS NULL`; a guest's `email` serializes as JSON
  `null`, never the string `"None"`.
