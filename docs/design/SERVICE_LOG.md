# Service Log — anonymous-first service records (design)

> A new **primary UI layer** on top of Active Impact: open the app and, with **no
> sign-up**, immediately log an act of service (a photo + a caption), see a live
> public feed of what others are logging, cheer them on, and only create a real
> account later — with all your history intact. Everything already built (projects,
> events, check-in, tokens, catalog, wallet) stays exactly as-is, one layer down.
>
> This doc is decision-complete and written to hand off to an implementing agent.
> Read alongside DOMAIN.md, API.md, FRONTEND.md, and `app/auth.py` / `app/images.py`
> (the integration points). Follow docs/framework/MIN.md: docs → TDD → verify → lean.

---

## 1. The loop

```
open app ──► have a token? ──yes──► HOME = service feed (others' + yours) + a big "＋ Log" button
   │                │
   │                └──no──► silently create a GUEST account (browser remembers the token)
   │                          first run goes STRAIGHT to the Log screen (camera + caption)
   │
returning real user taps "Sign in" ──► enters email+password ──► signs in; any guest logs on
                                                                   this device MERGE into that account
guest taps "Create account" ──► email+password ──► same guest account gains credentials, data intact
```

One record = **one photo + one caption**, authored by whoever you currently are
(guest or real). The feed is a photo-forward gallery; each record can be **cheered**
(one 🙌 per person). Nothing here touches tokens, participations, projects, or the
ledger — it is a **separate log** (see Non-goals).

---

## 2. Decisions (locked with the founder) + rationale

| # | Decision | Rationale |
|---|---|---|
| D1 | **Standalone social log.** A `service_record` is its own object with its own feed. It does **not** create participations, move tokens, or link to projects/events. | Lowest friction; matches "log and share"; keeps the existing app untouched. |
| D2 | **Server-side guest accounts.** First visit silently creates a real `users` row with **no email/password**; the browser stores its session token (the "local ID"). Records save to the server and appear in the shared feed immediately. | Sharing requires server storage + attribution; reuses the entire existing session/`current_user` machinery. |
| D3 | **Auto handle + avatar, zero input.** A guest is instantly given a friendly `display_name` (e.g. "Kind Otter") + the existing deterministic-color avatar. Renameable anytime. Carries over on conversion. | Maximum friction reduction — "go straight to logging." |
| D4 | **Capture = photo + caption.** Date auto-set. Photo is the star of the card; caption is the "what did you do?". No hours/location/category for MVP. | "Take a picture right there"; post in seconds. |
| D5 | **Feed = browse + one "cheer" reaction.** A single 🙌 per person per record, with a count. **No comments** (yet). | Social warmth with near-zero moderation/scope cost. |
| D6 | **Feed is HOME + always-present "Log" button.** The service feed is the landing screen; Projects / Catalog / Wallet demote to secondary nav. One cohesive app. New users are dropped straight into the Log screen on first run. | This layer is the primary UI for everyone. |
| D7 | **Convert = attach-or-merge.** New email → attach email+password to the guest (becomes real). Email already exists → **merge**: verify the existing account's password, re-point the guest's records to it, retire the guest. | "All that data gets associated with their email"; never lose logs; secure (proves ownership). |

### Smaller calls (flagged; adjustable — see §11)
- **C1 — Everything is public.** All records are visible in the global feed; no per-record privacy for MVP. (Private/journal mode is a future toggle.)
- **C2 — Photo required.** A record must have a photo (the feed is photo-forward). Caption required too (short, ≤280 chars). No text-only or photo-less records in MVP.
- **C3 — One photo per record.** Reuse the existing `images` infra with `entity='service_record'`. Multi-photo is future.
- **C4 — Global, newest-first feed.** No geo/following/groups yet. `?scope=mine` for your own log.
- **C5 — Auth is always "signed in" (guest or real).** "Sign in / Create account" is a single **convert** flow from the guest you already are; the standalone register screen is reframed as convert. Real-account login still works and merges the current guest.
- **C6 — Moderation is light.** A **Report** action + a `hidden` flag; author can delete. No admin UI yet (manual review by DB flag). Known limitation — see §9.
- **C7 — Photos stay authed.** Since everyone holds a guest token, the feed and image streaming keep the existing Bearer-auth model. Public (token-less) web sharing is future.

---

## 3. Non-goals (explicitly out of scope)

- ❌ Linking a service record to a project/event (the "evolve into option 3" idea) — **deliberately not built toward**; no coupling, no foreign keys to projects/events. It stays a separate log.
- ❌ Tokens / hours / impact math for service records. No ledger entries. (The token economy belongs to the events layer only.)
- ❌ Comments, following, geo-feeds, per-record privacy, multi-photo — all future.
- ❌ Changing the existing projects/events/catalog/wallet behaviour. This is purely additive.

---

## 4. Identity model (the heart of the integration)

A **guest is just a `users` row with `email IS NULL` and `password_hash IS NULL`.**
That single fact drives everything: `email IS NULL` ⇔ guest. This reuses `sessions`,
`current_user`, `me_shape`, `avatarEl`, and the whole auth path unchanged.

**Schema change:** make `users.email` and `users.password_hash` **NULLABLE** (migration).
The existing unique index on `lower(email)` is unaffected — Postgres treats NULLs as
distinct, so many guests coexist. Real accounts still require both (enforced in the
handler, not the column).

**Bootstrap (first visit).** Frontend has no `ai_token` → calls `POST /api/auth/guest`.
Server inserts a guest user with an auto handle (§4.1) and returns `{token, user}`
exactly like login. From then on the guest is "logged in" and can post/cheer.

**Rename.** Guests (and real users) rename via the existing `PATCH /api/me {display_name}`.

**Convert (D7).** `POST /api/auth/convert` (authed as the guest) `{email, password, display_name?}`:
- `email` free → set `email`, `password_hash`, optional `display_name` on the current
  guest row → it is now a real account. Same id, same records. Return a fresh session.
- `email` taken → **merge**: `bcrypt.checkpw(password, existing.password_hash)`; if it
  matches, in one tx re-point the guest's `service_records.user_id` and `cheers.user_id`
  to the existing account, delete the guest user + its sessions, and return a session for
  the **existing** account. If it doesn't match → `401 invalid_credentials` (do not reveal
  which case it was beyond "wrong password for that email").
- Never allow converting a guest that would collide on `display_name` — handles are
  non-unique already, so no constraint issue.

**Login while holding a guest.** The Sign-in screen posts to `convert` (not `login`) when
a guest token is present, so a returning real user's device-local guest logs merge into
their real account automatically. `POST /api/auth/login` remains for completeness but the
guest-first UI routes through `convert`.

**`me_shape`** gains `is_guest: (email is None)` so the UI can show the "Create account to
save your history" nudge. Guests have `balance` 0 and no email; never expose a null email
as a string.

### 4.1 Handle + avatar generation (server-side)
- `display_name = f"{Adjective} {Animal}"` from two short word lists (e.g. Kind/Brave/Sunny
  × Otter/Fox/Heron), picked with `secrets.choice`. On the rare exact-collision it's fine
  (non-unique); optionally append a 2-digit number for readability.
- Avatar reuses the existing initials-on-deterministic-color component (`avatarEl`, colour
  seeded by user id) — no new asset work. (Optional polish: render the animal emoji.)

---

## 5. Domain model

New tables (migrations start at **0008**, after 0007). Match the ORM/naming-convention
style in `app/models.py`; keep migrations guarded like 0002–0007 (no-op on a fresh
`create_all` DB, real work on an existing one).

```
users                      -- MODIFIED
  email          TEXT NULL          -- was NOT NULL; NULL ⇒ guest
  password_hash  TEXT NULL          -- was NOT NULL; NULL ⇒ guest
  (display_name, bio, balance, created_at unchanged)

service_records            -- NEW  (the log entry; standalone)
  id          BIGSERIAL PK
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE   -- author (guest or real)
  caption     TEXT NOT NULL                                          -- ≤280 chars (validate)
  hidden      BOOLEAN NOT NULL DEFAULT false                         -- moderation (§9)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  index (created_at DESC), index (user_id)

cheers                     -- NEW  (one 🙌 per user per record)
  id          BIGSERIAL PK
  record_id   BIGINT NOT NULL REFERENCES service_records(id) ON DELETE CASCADE
  user_id     INT    NOT NULL REFERENCES users(id) ON DELETE CASCADE
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  UNIQUE (record_id, user_id)                                        -- toggle = insert/delete

reports                    -- NEW  (moderation-light; §9)
  id          BIGSERIAL PK
  record_id   BIGINT NOT NULL REFERENCES service_records(id) ON DELETE CASCADE
  user_id     INT    NOT NULL REFERENCES users(id) ON DELETE CASCADE -- reporter
  reason      TEXT NULL
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  UNIQUE (record_id, user_id)

images                     -- REUSED
  entity CHECK now allows 'service_record'  (migration: widen ck_images_entity_valid,
         exactly like 0007 did for 'event'). One image per record (entity='service_record',
         entity_id = record id). cover = serializers.cover_image_id('service_record', id).
```

**Photo storage** reuses `images` (BYTEA, authed streaming, `_may_manage`). No new blob path.

---

## 6. API surface (new/changed — all under `/api`)

Conventions from API.md (Bearer auth, `{"detail": code}` errors, 201 on create).

| Endpoint | Body → Response | Notes |
|---|---|---|
| `POST /auth/guest` | — → **201** `{token, user}` | Create an anonymous account + session + auto handle. Idempotent-ish: if a valid token is already sent, just return it. |
| `POST /auth/convert` | `{email, password, display_name?}` → `{token, user}` | Attach-or-merge (D7/§4). 401 `invalid_credentials` on a taken email with a wrong password; 422 on bad email/password shape (reuse RegisterIn validators). Authed as the guest. |
| `PATCH /me` | `{display_name?, bio?}` | **exists** — used to rename the handle. |
| `POST /service_records` | `{caption, content_type, data_base64}` → **201** record | One shot: in a tx, insert the record + its image (entity='service_record'). Validates caption length + image (reuse the 10 MB / content-type checks). |
| `GET /service_records` 📄 | `?scope=all\|mine` → `record_card[]` | Global feed newest-first (`all`, default) or the caller's own (`mine`). Excludes `hidden`. |
| `GET /service_records/{id}` | → `record_card` | Single record (share target / detail). 404 if hidden/absent. |
| `DELETE /service_records/{id}` | → **204** | Author only (403 `not_yours`). Cascades cheers/reports/image. |
| `POST /service_records/{id}/cheer` | → `{cheered:true, cheer_count}` | Idempotent (`ON CONFLICT DO NOTHING`). |
| `DELETE /service_records/{id}/cheer` | → `{cheered:false, cheer_count}` | Remove your cheer. |
| `POST /service_records/{id}/report` | `{reason?}` → **204** | Idempotent per user. Auto-set `hidden=true` after **N=3** distinct reports (tunable). |
| `POST /images` | entity union gains `'service_record'`; `_may_manage` → the record's author. | Usually not called directly (the one-shot create handles it), but kept consistent. |

**`record_card` shape** (what the feed renders):
```json
{ "id", "author": { "id", "display_name", "is_guest" },
  "caption", "photo_image_id", "created_at",
  "cheer_count", "i_cheered" }
```
Author never exposes email. `photo_image_id` streams via the existing `GET /images/{id}`.
Feed enrichment (cheer_count / i_cheered) must be **batched by record id** (no N+1), like
`GET /projects` already does.

---

## 7. Frontend (no-build PWA — see FRONTEND.md)

### Routing / boot
- **Boot change (`app.js`):** if no `ai_token`, call `POST /auth/guest`, store the returned
  token/user, THEN render — so the app is always "signed in." Guests are treated as authed
  by the router (the existing auth-gate stops redirecting to `#/login`). The QR return-to
  logic stays.
- **New routes:** `#/` → **feed** (was projects list); `#/log` → the create screen (or a modal);
  `#/r/(\d+)` → record detail (share target). Existing `#/projects...` etc. keep their paths.
- **First run:** brand-new guest lands on `#/log` (camera up) once, then on the feed thereafter.

### Views
| Route | Content |
|---|---|
| `#/` **Feed (home)** | `GET /service_records`. Photo-forward cards: big photo, caption, author avatar+handle, relative time, a 🙌 cheer toggle + count, a "⋯" (report / delete-if-mine). Infinite scroll / "load more". A persistent, prominent **＋ Log** button (FAB or center-nav). Empty state invites the first log. |
| `#/log` **Log a service** | Camera/file picker first (reuse `imagesStrip`/`resizeImage` pipeline → JPEG), a caption box, one **Post** button → `POST /service_records` → land on the feed with the new record on top. Cancel returns to feed. |
| `#/r/:id` **Record** | Single record (deep-link/share), cheer, report, delete-if-mine, link to author profile. |
| `#/me` **Me** | Guests: the auto-handle (rename), an avatar, and a prominent **"Create an account to save your service"** card → the **convert** form (email + password). Real accounts: today's profile/edit/logout. Both: link to Projects/Catalog/Wallet. |
| Auth screens | The register screen is reframed as **convert** (from the current guest). `#/login` posts to `convert` when a guest token is present (so login merges the guest). Copy: "Sign in / Create account — your logs come with you." |

### Navigation
Bottom nav becomes: **🏠 Home (feed) · 🌱 Projects · 🎁 Catalog · 🪙 Wallet · 👤 Me**
(5 items — acceptable; or group Catalog/Wallet if it's tight on 390px). The **＋ Log** action
is a distinct always-visible control (FAB over the feed or a center nav button), not buried
in a tab. Projects/Catalog/Wallet are unchanged screens, just no longer the front door.

### Reuse
- Photo capture/resize: existing `resizeImage` + `imagesStrip` add-photo pipeline.
- Avatars: existing `avatarEl`.
- Auth/session/`api()` helper, toasts, forms (`addForm`) — all reused.
- **SW bump** on any `public/` change (currently v18 → v19).

---

## 8. Photos

Reuse `images` with `entity='service_record'`. The one-shot `POST /service_records` creates
the record and the image in a single `db.tx()` (record first for the id, then the image
pinned to it — mirror the check-in "insert then pin" pattern). `_may_manage('service_record', id)`
→ `record.user_id == user_id`. Deleting a record cascades its image. Widen
`ck_images_entity_valid` via a migration (copy 0007 verbatim, adding `'service_record'`).

---

## 9. Moderation & abuse (MVP-light — a known gap)

Public UGC from anonymous accounts needs a floor of safety:
- **Report** action → `reports` row; at **N=3** distinct reports a record auto-sets
  `hidden=true` (dropped from all feeds). Author-`DELETE` also removes it.
- **No admin UI yet** (the app has no admin role). Review/unhide is a manual DB flag for now
  — call this out as a limitation and a fast follow (an admin role + a moderation queue).
- **Abuse controls to include:** the existing 10 MB image cap + content-type allowlist; a
  simple **rate limit** (e.g. ≤ N records / guest / hour, ≤ N guest-creates / IP / hour) to
  blunt spam; caption length cap; `esc()` on every user string (non-negotiable — public UGC).
- Consider (future): image-safety scanning, block/mute, per-account report thresholds.

---

## 10. Testing & rollout (per the framework: TDD first)

- **pytest:** guest bootstrap; convert-attach (new email) keeps the same id + records;
  convert-merge (taken email, right password) re-points records and retires the guest;
  convert-merge wrong password → 401; create record (record + image in one tx); feed
  excludes `hidden` and another user's `mine`; cheer toggle idempotent + count; report →
  auto-hide at threshold; author-only delete; `me_shape.is_guest`; caption/image validation.
- **e2e (Playwright):** first-run → land on Log → post a photo+caption → it appears at the top
  of the feed; cheer flips + count; a second (guest) user sees it and cheers; convert a guest
  to a real account and confirm the record still shows as mine after "logging in"; delete.
- **Data safety:** the `users` email/password → nullable migration must be guarded and
  reversible; verify on a copy of the (already-migrated) prod DB before "update the web".
- **Deploy:** the standard `./deploy.sh` runs `alembic upgrade head` on the droplet
  (currently at 0007) — the new migrations 0008+ apply there. Then flip nothing else.

---

## 11. Detail decisions — CONFIRMED (founder accepted the defaults)

The founder reviewed and accepted the proposed defaults as-written; these are now
locked, not open. Recorded here so the implementing pass treats them as decisions:
1. **Photo required** on every record (C2). No text-only logs in MVP.
2. **All records public** (C1). No per-record private toggle in MVP (future).
3. **Caption cap 280 chars.**
4. **Report → auto-hide at N=3** distinct reports (tunable constant).
5. **Handle scheme** = "Adjective Animal" (e.g. "Kind Otter"), server-picked word lists.
6. **Nav** = 5-item bottom bar (Home / Projects / Catalog / Wallet / Me) + an always-visible
   **＋ Log** FAB. (Revisit consolidation only if 390px feels tight in build.)
7. **Guests may cheer and report** (they always have an identity).
8. **Rate limits** (implementer to pick sane values): e.g. ≤ ~20 records / guest / hour and
   a modest guest-create-per-IP cap; tune in review.

Everything in §2–§10 was locked during the design Q&A. **This doc is ready to hand to an
implementing (ultracode) pass.**
