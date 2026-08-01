# Check-in proof — asserted vs attested presence (design)

> Today a check-in is a **claim**: I tap a button and the app believes me. That is
> useful (it records intent and starts the clock) but it proves nothing.
>
> This doc adds a **second layer**: presence backed by *another person who was
> there*. On arrival you scan somebody else's personal QR code. That single scan
> records **both** of you as present at that event — you, because you were holding
> the camera, and them, because their code was in front of it — as reported by the
> scanner. Everything already built stays exactly as-is; the button keeps working
> and is simply relabelled, in the data, as what it always was: an assertion.
>
> Read alongside DOMAIN.md, API.md, FRONTEND.md, and `app/checkin.py` /
> `app/events.py` (the integration points). Follow docs/framework/MIN.md:
> docs → TDD → verify → lean.

---

## 1. The two layers

```
ASSERTED  "I say I was here."
          the Check in button  ·  POST /events/{id}/checkin
          also: the event-code QR landing (#/c/{code} → agree)
          → participation.attested = false

ATTESTED  "Somebody else's code says we were both here."
          I arrive, I open the app, I scan a person's QR  ·  #/s/{qr_token}/{event_id}
          → attestation row (event, scanner, subject)
          → participation.attested = true for BOTH, where each has standing
```

The unit of proof is a **sighting**: *scanner B reports that subject A's code was
physically in front of B, at event E.* One sighting is evidence about two people,
and that is exactly what gets stored.

```
     A (already at the event)              B (just arrived)
     shows their QR for event E   ──scan──►  confirms
                                              │
                                              ▼
                              attestations(event E, scanner B, subject A)
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                           ▼
        B: participation (B signs the waiver          A: existing open participation
           on their own device) attested = true          flipped to attested = true
```

---

## 2. Decisions (locked with the founder) + rationale

| # | Decision | Rationale |
|---|---|---|
| P1 | **The button stays.** It is not removed, gated, or degraded — it just records `attested = false`. | Scanning fails for real reasons (no camera, no permission, bad light, a dead phone). Presence must always be recordable. |
| P2 | **The QR identifies a person + an event**, not a project. `#/s/{qr_token}/{event_id}` | The project is derivable from the event — storing it twice would be a second source of truth. |
| P3 | **The QR is a URL**, like the existing event QR (D5). | It works with the in-app scanner *and* with any phone's native camera, with zero extra code. A payload-only JSON blob would work with neither. |
| P4 | **Identity in the QR is an opaque per-user token** (`users.qr_token`), never the raw user id. | A raw id is guessable: anyone could claim any user was anywhere. A token means you must have actually seen the code. One column, generated once — no added moving parts. |
| P5 | **The QR is static and printable.** No nonce, no rotation, no expiry, no signature. | The founder's use case is explicit: print it and pin it to the wall. Any freshness scheme breaks that, and breaks it *silently*, at an event, with no way to recover. |
| P6 | **Both parties are recorded, attributed to the scanner.** `attestations(event_id, scanner_user_id, subject_user_id)` | This is the founder's definition of a check-in. Keeping the direction (who reported whom) costs one column and preserves who-said-what for any later dispute or reputation work. |
| P7 | **A scan never forges a waiver signature.** The scanner signs, on their own device, as part of confirming. The subject's participation is only *upgraded* if it already exists. | `participations.waiver_id` **is** the signature (I6). Creating one for someone who never tapped agree would fabricate a legal record. See §5.3. |
| P8 | **A sighting for someone not yet checked in is still stored**, and upgrades their participation when they do check in. | The evidence is real the moment it happens; it should not be thrown away because of ordering. |
| P9 | **`attested` changes no token math.** Hours and minting are untouched. | This release adds a *proof* dimension. What the platform eventually does with it (trust tiers, disputed hours) is a later, separate decision. |
| P10 | **Self-scanning is rejected** (409 `self_scan`). | A sighting of yourself is not evidence. |

---

## 3. Identity — what the QR actually points at

A person's permanent handle here is their **`users.id`**, wrapped in an opaque
`users.qr_token` for display. Both survive everything the identity layer does —
with one exception, which matters and is called out.

| Path | What happens to the row | QR token |
|---|---|---|
| Guest created (`POST /auth/guest`) | new `users` row, `email IS NULL` | minted with the row |
| Guest → real, **ATTACH** (email free) | **same row** gains credentials — same `id` | **survives** |
| Guest → real, **MERGE** (email already exists) | guest row is re-pointed and **deleted**; the surviving row is the older account | **dies with the guest row** |
| Real account | stable forever | stable forever |

So: *a guest identity is permanent right up until that guest merges into an
account that already existed.* This is the honest answer to "does the guest
identity ever go away." In practice the merge path retires a throwaway identity in
favour of a real one the person already owned, which is the desired outcome — but
**a QR printed under the retired token stops resolving** (404 → "show a fresh
code"). No silent wrong-person attribution: the token is gone, not reassigned.

> **Known gap, pre-existing:** the MERGE path re-points `service_records`,
> `cheers`, `reports`, and `images` but *not* `participations`, `rsvps`,
> `token_entries`, or `follows` — all of which have un-cascaded FKs to
> `users.id`. A guest who has checked in anywhere therefore cannot merge (the
> `DELETE FROM users` raises a FK violation). `attestations` adds one more FK of
> the same shape. Tracked in `docs/issues/GUEST_MERGE_FK.md`; out of scope here.

---

## 4. The QR payload

```
{scheme}://{host}/#/s/{qr_token}/{event_id}
                     └── person ──┘ └─ event ─┘
```

Nothing else. No name, no project, no timestamp — a QR is scanned by strangers
and photographed by cameras; the less it carries the better. The scanning client
resolves the token to a display name over the authenticated API, so the person's
identity is shown only to someone already signed in.

`qr_token` is `secrets.token_urlsafe(8)` (~11 chars, ~64 bits) — the same
generator and shape as `events.checkin_code`. Unique, never reissued.

### What this does and does not prove

| Claim | Backed? |
|---|---|
| Two accounts were in the same place, one holding the other's code | **Yes** — modulo the honesty of the scanner. |
| The subject consented to being recorded | **Yes, implicitly** — they published a code for this specific event. |
| The subject signed the waiver | **Only if they checked in themselves** (P7). |
| The scanner was physically present | **Weakly** — a code, once seen, can be re-shown. See below. |

The residual attack is **relay**: the code is static, so anyone who has ever seen
it can present it again — a photo of it, forwarded to a friend across town. This
is an accepted, deliberate trade (P5): the founder's requirement to print and pin
a code *is* the requirement that it be replayable. The defence is social, not
cryptographic — the sighting names its reporter, so a pattern of false reports is
attributable to an account. Anything stronger (rotating codes, signed nonces,
proximity checks) is a real design with real failure modes and belongs in its own
document, not smuggled in here.

---

## 5. Domain model

### 5.1 Schema additions

```sql
-- the person's public, permanent, opaque handle
ALTER TABLE users ADD COLUMN qr_token TEXT NOT NULL UNIQUE;   -- secrets.token_urlsafe(8)

-- did anything corroborate this participation?
ALTER TABLE participations ADD COLUMN attested BOOLEAN NOT NULL DEFAULT false;

-- the atomic sighting: "scanner reports subject was here"
CREATE TABLE attestations (
  id               BIGSERIAL PRIMARY KEY,
  event_id         BIGINT  NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  scanner_user_id  INTEGER NOT NULL REFERENCES users(id),   -- who scanned (the reporter)
  subject_user_id  INTEGER NOT NULL REFERENCES users(id),   -- whose code was scanned
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_attestations_not_self CHECK (scanner_user_id <> subject_user_id),
  CONSTRAINT uq_attestations_event_scanner_subject
    UNIQUE (event_id, scanner_user_id, subject_user_id)
);
CREATE INDEX idx_attestations_event    ON attestations(event_id);
CREATE INDEX idx_attestations_subject  ON attestations(subject_user_id);
```

`attestations` is **append-only**, like `audit_log`: a sighting happened or it
did not. The UNIQUE makes a re-scan idempotent rather than an error — the same two
people meeting twice at one event is one fact, and a volunteer who taps twice
should get a shrug, not a 409.

`participations.attested` is a deliberate denormalisation of "∃ an attestation for
(this event, this user)". It is pinned to the *participation*, not recomputed, so
that a sighting during today's shift does not retroactively vouch for a
participation the same person closed last week at the same event.

### 5.2 Who may display a QR for an event

Anyone with an `rsvps` row or a `participations` row for that event. Checking in
creates an RSVP, so this is "RSVP'd or checked in" — exactly the founder's rule:
*"anybody can say I am already checked into this event, or if they're even just
RSVP'd."* Everyone else gets 403 `not_attending`.

### 5.3 What one confirmed scan writes (single transaction)

Given scanner **B** scanning subject **A**'s code for event **E**:

1. `attestations` ← `(E, B, A)`, `ON CONFLICT DO NOTHING`.
2. **B** (the scanner — is here, and is agreeing right now on their own device):
   - ensure an `rsvps` row;
   - if B has an open participation → `attested = true`;
   - else insert one, pinned to the project's current waiver, `attested = true`
     — the confirm screen shows the waiver, so this **is** B's signature;
   - `audit_log` ← `check_in` when a participation was created.
3. **A** (the subject — present, but has not agreed to anything on this device):
   - ensure an `rsvps` row;
   - if A has an open participation → `attested = true`;
   - **else nothing more** — no participation is created (P7). The attestation
     stands and will upgrade A's participation the moment A checks in.
4. `audit_log` ← `attest` (actor = B, subject = A) — always, so the append-only
   trail carries the sighting itself, not just its side effects.

### 5.4 What every other check-in path now does

`POST /events/{id}/checkin` and `POST /checkin/{code}/agree` insert their
participation with

```sql
attested = EXISTS (SELECT 1 FROM attestations
                    WHERE event_id = %s AND (subject_user_id = %s OR scanner_user_id = %s))
```

which is what makes P8 work: A gets scanned at 09:00, taps Check in at 09:05, and
lands already attested.

### 5.5 New invariants (DOMAIN.md § Invariants)

| # | Invariant |
|---|---|
| I13 | An `attestations` row always names two **different** users (CHECK) and is unique per (event, scanner, subject) — a repeat scan is a no-op, never an error |
| I14 | A scan never creates a participation for the **subject**; the scanner's participation always carries a `waiver_id` from the event's project (I6 holds for every row, however created) |
| I15 | `participations.attested` is true ⟺ an attestation for that (event, user) existed at or before the participation was written — set at insert, and flipped by a later scan only on a participation that is still open |

---

## 6. API

| Endpoint | Notes | Errors |
|---|---|---|
| `GET /api/events/{id}/my-qr.svg` | **My personal QR for this event** — `image/svg+xml` of `{scheme}://{host}/#/s/{my qr_token}/{id}`. Any attendee (RSVP or participation), not just leaders — this is the code *I* show other people | 403 `not_attending`; 404 |
| `GET /api/scan/{qr_token}/{event_id}` | Resolve a scanned personal QR → `{person: {id, display_name}, is_self, event: event_card, project: project_card, waiver: {id,version,text}, my_open_participation \| null, already_attested}`. `already_attested` = *this* pair is already recorded here; `is_self` = I scanned my own code — resolve still 200s so the UI can explain it kindly, only confirm refuses | 404 `invalid_qr` (unknown token, or event not `open`) |
| `POST /api/scan/{qr_token}/{event_id}/confirm` | **The peer check-in.** One tx, exactly §5.3 → **201** `{participation, person, attested: true}` | 404 `invalid_qr`; 409 `self_scan`; 409 `event_over` |

Changed shapes:

- `GET /api/me` gains **`qr_token`** (my own token — private view only, so a code
  is only ever handed out by its owner).
- The participation shapes in `GET /api/events/{id}/roster` and every
  `my_open_participation` gain **`attested`**.
- `GET /api/events/{id}/rsvps` rows gain **`is_attested`**, so an organizer can
  see at a glance who is corroborated.

Unchanged: `POST /api/events/{id}/checkin` keeps its path, body, and response.
It is the *asserted* check-in and always was; only its stored `attested` value
(false, unless §5.4 finds a prior sighting) is new.

---

## 7. Frontend

### 7.1 The Check in button becomes scanner-first

```
tap "Check in"
   │
   ├─ scanner available? (BarcodeDetector + getUserMedia + a camera)
   │     │
   │     ├─ yes → full-screen scanner overlay
   │     │          ├─ scans a valid #/s/… URL → route there (the ATTESTED path)
   │     │          ├─ scans something else    → "That isn't an Active Impact code"
   │     │          └─ user taps Cancel        → do nothing, stay put
   │     │
   │     └─ no / permission denied / no camera
   │            → toast: "Camera scanning isn't available here — checking you in
   │                      as self-reported."
   │            → POST /events/{id}/checkin   (the ASSERTED path, unchanged)
```

Cancel is deliberately *not* the same as unavailable: backing out of the scanner
is a decision, and silently checking someone in after they cancelled would be a
surprise. Unavailable falls through, because that is the founder's stated
requirement — *"if that doesn't work then it will say I can't do that and then it
will just check them in."*

`BarcodeDetector` is Chromium-only today. On Safari/Firefox the fallback fires,
**and** the native camera still works on the printed code because the QR is a URL
(P3) — an iPhone user points the built-in camera at the sheet on the wall and
lands on `#/s/…` in the PWA. No library, no build step, no new dependency (D4).

### 7.1b Scan from the app bar — check in without finding the event first

**(2026-08, founder addition.)** A code already carries its event: a personal QR is
`#/s/{qr_token}/{event_id}`, an event's is `#/c/{code}`. So a scanner never needs
to navigate to the right project first — *"you can just click check in at the top
of the app, because the code you scan has the information on it."*

A **scan button lives in the app bar**, on every screen, and is in practice how
people will check in. It opens the same `scanQR()` overlay as §7.1 and routes to
whatever it reads. The per-event **Check in** button stays exactly as it is (§7.1)
— it is still the right control once you are already looking at the event, and it
is the one that can fall back to an asserted check-in, because it knows which
event that would be.

That difference is the whole design of the fallback here: **the app-bar scanner has
no event context, so it has nothing to fall back to.** Where §7.1 quietly checks
you in as self-reported, this one has to explain itself instead:

| Outcome | What happens |
|---|---|
| a code we recognize | route to `#/s/…` or `#/c/…` — the normal landing |
| something else scanned | "That isn't an Active Impact code" + scan again |
| user cancels | back where they came from — a decision, honoured |
| **no scanner here** (iOS today) | a card explaining the native-camera path: *point your phone's camera at the code and it opens right here* — not a toast, because on Safari this is the permanent answer and it must be readable, not a flash |

It is a real route, `#/scan`, not a modal: deep-linkable, back-button-friendly, and
the unavailable state is a screen a person can actually read.

### 7.2 New screens

| Route | Screen |
|---|---|
| `#/s/:qr_token/:event_id` | **Peer check-in landing.** "You're checking in with **Ana**" + the event summary + the full waiver + one big confirm. Post-confirm: a ✅ verified state with Check out, mirroring `#/c/{code}` |
| `#/scan` | **Scan a code** (§7.1b). Opens the camera immediately; explains itself when there is no scanner |
| `#/events/:id` | Gains **Show my code** for any attendee — a card with the QR (`/my-qr.svg`), the person's name under it, and a "print or hold this up" hint |

`#/c/{code}` (the event-code landing) is untouched.

**Generating** a QR from the app bar is deliberately *not* offered: every code we
mint belongs to an event (a person's code is per-event too), so there is nothing
useful to generate without one. Codes are shown where the event is — the lead hub's
event QR and the event page's **Show my code**.

### 7.3 Showing the difference

A checked-in state is now one of two pills, everywhere it appears (event detail,
roster row, lead hub):

- `✅ Verified` — `attested = true`, with "confirmed by someone at the event"
- `● Self-reported` — `attested = false`

Neutral, non-punitive wording: self-reported is a legitimate outcome, not a
failure. The organizer's roster is where the distinction earns its keep.

---

## 8. Non-goals

Rotating / signed / expiring codes · proximity or geofence checks · scoring or
ranking people by attestation count · any effect on token minting (P9) · a public
attestation graph · offline scan queueing · retroactively attesting a *closed*
participation · scanning to check somebody **out**.
