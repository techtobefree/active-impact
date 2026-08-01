# Frontend — PWA Spec

> A no-build vanilla-JS PWA (D4): plain ES modules served straight from
> `public/`, a ~30-line hash router, `el()` template-literal rendering, a
> ≤25-line service worker. The shape is copied from the deployed home-keep app;
> the deltas are the router (QR deep links), module file-split, mandatory output
> escaping (public UGC!), and a dark-mode token override.

## Files (all must appear in the SW `ASSETS` list)

```
public/
├── index.html              # shell: PWA meta, header, nav, <main id="view">, one module script
├── style.css               # :root tokens + component vocabulary (see § Look)
├── app.js                  # boot, hash router, chrome/nav state, SW registration
├── api.js                  # api() fetch helper — Bearer header, 401 redirect, 204→null
├── ui.js                   # el(), esc(), fmt helpers, addForm(), imagesStrip(), install flow
├── views/auth.js           # the single CONVERT ("save your log / sign in") screen
├── views/records.js        # service log: the record CARD (reused by the feed + event pages), log (photo + caption + event), record detail
├── views/projects.js       # projects list (#/projects), project detail (+ its events), create, event detail, per-event lead hub (QR + roster)
├── views/checkin.js        # the #/c/{code} landing (event code) AND the #/s/{token}/{event} landing (peer scan)
├── scan.js                 # in-app QR scanner: BarcodeDetector + getUserMedia overlay, graceful "unavailable"
├── views/catalog.js        # list (offers|needs tabs), detail, create/edit, claims
├── views/wallet.js         # balance, ledger, my claims (both roles), tip form
├── views/profile.js        # public profile, my profile edit
├── sw.js  manifest.webmanifest
└── icon.svg  icon-192.png  icon-512.png  apple-touch-icon.png
```

Target ≈ 2 000–2 500 lines total. If a file crowds 500, split a view — never add
a build step.

## Router (the one structural addition)

Hash-based so no server fallback is needed and QR URLs deep-link on any phone:

```js
// app.js — route table maps location.hash to view functions
const routes = [
  [/^#\/$/,                    views.projectList], // HOME = THE feed: projects + their photos
  [/^#\/log$/,                 views.log],         // log a service (photo + caption)
  [/^#\/log\/(\d+)$/,          views.log],         // …straight to a named event
  [/^#\/r\/(\d+)$/,            views.record],      // one record (share / deep link)
  [/^#\/login$/,               views.convert, AUTH],   // "save your log / sign in"
  [/^#\/register$/,            views.convert, AUTH],   // same convert screen
  [/^#\/projects$/,            views.projectList], // the same screen as #/ (legacy path)
  [/^#\/projects\/new$/,       views.projectNew],
  [/^#\/projects\/(\d+)$/,     views.projectDetail],  // durable project + its events
  [/^#\/events\/(\d+)$/,       views.eventDetail],    // one occurrence (optional deep link)
  [/^#\/events\/(\d+)\/lead$/, views.eventLead],      // per-event lead hub (QR/roster/close)
  [/^#\/c\/([\w-]+)$/,         views.checkin],     // QR landing (resolves an event)
  [/^#\/s\/([\w-]+)\/(\d+)$/,  views.scan],        // PEER QR landing (a person + an event)
  [/^#\/catalog$/,             views.catalog],
  [/^#\/catalog\/new$/,        views.itemNew],
  [/^#\/catalog\/(\d+)$/,      views.itemDetail],
  [/^#\/wallet$/,              views.wallet],
  [/^#\/me$/,                  views.myProfile],
  [/^#\/u\/([\w-]+)$/,         views.profile],
];
// render(): match hash (default '#/'), auth-gate, chrome — all synchronous — then
// QUEUE the view call. Views fetch-then-mount, so overlapping renders race to
// paint and a slow one lands on top of the screen you actually chose; serializing
// the async half means the newest render always mounts last, without ever
// repainting (and wiping) a form the user has started filling in.
// See ../issues/STALE_VIEW_RACE.md. Rule: never `await refresh()` inside a view.
window.addEventListener('hashchange', render);
```

**Always signed in (SERVICE_LOG.md §4/§7).** On boot, if there is no `ai_token`,
`POST /auth/guest` mints a silent GUEST session BEFORE the first render — so every
route is authed. A brand-new guest (a first run) lands on `#/log`; a returning
token renders the feed. Sign-out drops back to a fresh guest.

**Auth gating & return-to (critical for the QR flow):** a GUEST token counts as
authed, so protected routes never bounce to the convert screen. The `AUTH` routes
(`#/login`, `#/register`) render the convert form for a guest and redirect a REAL
(non-guest) user home. If a truly token-less edge hits a protected route, stash it
— `sessionStorage.setItem('ai_return', location.hash)` — and go to `#/login`; on a
successful convert, navigate to the stashed hash (else `#/`). So: *scan QR →
save account → land back on the waiver screen.* Test this path explicitly.

## Screens

| Route | Content & API calls |
|---|---|
| `#/login`, `#/register` **Convert** | ONE "save your log / sign in" form — **email + password + optional display name** (`POST /auth/convert`, authed as the current guest). `autocomplete`/`inputmode=email` attrs; live field-attributed validation. A NEW email attaches (guest → real, same id + records); an EXISTING email + right password **merges** the guest into it; wrong password → 401 shown as "Wrong email or password" (the guest session survives — retry in place). On success: `setSession` → `refreshMe` → return-to. A real (non-guest) user is redirected to `#/`. Reused by the Me "create account" card |
| `#/` **Feed (home)** | **The** feed (FEED.md F2) — the projects list below, rendered at `#/`. Each project card also carries its event's **latest ≤2 service photos** inline (`p.records`): photo (taps through to `#/r/:id`), one-line caption, author handle, and the **🙌 cheer** toggle — inside the *same* card, no nested card chrome (F3). `#/projects` renders the identical screen (legacy path) |
| `#/log` · `#/log/:eventId` **Log a service** | Photo picker first (hidden `<input type=file accept="image/*">` — **no `capture` attr** — → `resizeImage` → JPEG base64 + preview), a required caption `<textarea maxlength=280>` (remaining counter). Above the button, the **target line** (F8): "Posting to **{project}** · {event time}" with a **Change** link, from `GET /events/candidates?lat=&lon=` — GPS is requested best-effort (`navigator.geolocation`, never blocking, silent on denial). Change / no match → an inline picker listing the ranked candidates (+ "Not at an event"). `#/log/:eventId` pre-selects and locks the event. **Post** (enabled only once a photo AND caption exist — C2) → `POST /service_records {caption, content_type:'image/jpeg', data_base64, event_id?, lat?, lon?}` → land on `#/events/:id` (the record on top of that event's feed) or, unattached, on `#/r/:id`. Cancel → `#/` |
| `#/r/:id` **Record** | Single record (`GET /service_records/:id`) — the same card, larger: cheer, report, delete-if-mine, author link, the **event line** ("at *Riverside Cleanup* · Sat 10:00" → `#/events/:id`) and, for the author of an **unattached** record, **Attach to an event** (the same picker → `PATCH /service_records/:id`). 404 → friendly "not here anymore" + a link home |
| `#/projects` **Projects list** | Project cards (`GET /projects?scope=`), each **embedding one event** (`p.event`, the soonest not-over / most-recent occurrence): cover on top (**prefers the event's own cover** `p.event.cover_image_id` — an event with photos shows its own cover — else the durable project cover), then a **details-left / action-right** row — project title (`<h3>`) on the left, and if `p.event` the embedded event's 📍 location, 🗓 local time, ⏱ expected duration, checked-in count under it; the **right column** is a right-aligned stack: the event **status pill on top**, then the **shared RSVP / check-in / check-out action** (`actionEl(p.event)`, per-user state on the event) below it. The button acts **in place** (stops the card's `<a>` navigation, refreshes just that card via `GET /events/:id` so the current tab + scroll survive). `p.event == null` (a past project with no listable event) → muted "No upcoming events", no action. Upcoming excludes ended events (they fall to Past). Client + `q` search. "＋ New service project". Tabs: Upcoming · Past · Mine |
| `#/projects/new` | `addForm` → `POST /projects` (creates the project **and its first event**): title, description, location, starts_at (`<input type="datetime-local">` → ISO), expected minutes, waiver textarea **left blank → server seeds the default template** (placeholder text says so; template lives server-side only — no client copy to drift) + banner: *"Blank uses our standard template — not legal advice. Edit to fit your project."* Location/starts_at hints note they seed the first event |
| `#/projects/:id` | **Service project** detail (`GET /projects/:id`): primary-image cover at top, images strip (★ marks the cover; leaders get "Make primary"), title (`<h1>`) + description, a **Share · Follow · Invite** row (every signed-in viewer, three equal `.act` buttons) + a muted follower count — Share uses `navigator.share` (else copies the link + toast); Follow toggles `POST`/`DELETE /projects/:id/follow` (`{is_following, follower_count}`) and repaints the button (✓ Following + `.primary`) and count in place; Invite is a "coming soon" toast. `am_leader` → **Edit project** (title/description/waiver via `PATCH /projects/:id`). **Organizers** section (→ profiles); `am_leader` adds by email (`POST /projects/:id/leaders`) / removes by ✕ (`DELETE …/leaders/:uid`, owner irremovable — project_leaders are project-wide). Waiver (collapsed `<details>`). Then an **Events** section listing `p.events` (upcoming ASC, then past DESC — the **next one up is highlighted** with a "Next up" marker and an accent border, and it is not always the first row): each event is a card with (if it has its own cover) a small leading `.thumb`, its 📍🗓⏱👥 meta + "You've logged Nh here" on the left and the **status pill + shared action** (`actionEl(event)`, refreshes that row via `GET /events/:id`) on the right, then its **latest ≤2 service photos** inline, plus (`am_leader`) a **Manage** link → `#/events/:id/lead`. `am_leader` also gets a **＋ Add event** control (a small form: starts_at, location, expected_minutes, 📍 use-my-location → `POST /projects/:id/events` → refresh) |
| `#/events/:id` | Optional per-event deep link (`GET /events/:id`): cover at top (**event's own cover** `ev.cover_image_id` else project cover) + title, that event's meta, status pill + shared `actionEl(event)`, waiver (collapsed), **Show my code** for any attendee (RSVP'd or checked in) — a card with my personal QR (`<img src=blob>` of `/events/:id/my-qr.svg`), my display name under it, and a "hold this up, or print it and pin it" hint so others can check in off me, (`am_leader`) an **images strip** for the event's photos (★ marks the event cover) + a **Manage event** link → `#/events/:id/lead`, and a link back to the service project. Underneath all of that: **the event's feed** — every service record logged here (`GET /service_records?event_id=:id` 📄, newest-first, "Load more"), with a **＋ Log to this event** button above it (→ `#/log/:id`). This is where a new post lands (FEED.md F9) |
| `#/events/:id/lead` | **Per-event** leader hub (`GET /events/:id` + `GET /events/:id/roster`): back-link to the parent project, project title + this event's meta, **big QR** (`<img src=blob>` of `/events/:id/qr.svg` — authed fetch), the `checkin_code` as text fallback (`am_leader` only), regenerate button (confirm, `POST /events/:id/code/regenerate`), **"Check in yourself"** link → `#/c/{code}` (leaders earn too — intent), a **Photos** section (`imagesStrip('event', …)` — leaders add/set-cover/delete the event's photos, ★ marks the event cover), roster with per-row **Check out** (`POST /participations/:id/checkout`) + live count, **Who's coming (N)** (`GET /events/:id/rsvps`): each RSVP with avatar, "checked in" pill, and an event-**leader** toggle (`POST /events/:id/rsvps/:user_id/leader`, a per-event designation — distinct from project Organizers), and **Close event** (confirm, `POST /events/:id/close`: "checks out everyone & completes"). Organizer management stays on the project detail (project-scoped) |
| `#/c/:code` **Check-in landing** | The heart. `GET /checkin/:code` resolves an **event** → `{event, project card, current waiver, my_open_participation}`; renders the project title + **that event's** 📍🗓⏱ + **full waiver text** + `[ I agree — check me in ]`. Agree → `POST /checkin/:code/agree` → success state: "✅ You're checked in — HH:MM. Find the leader if you need anything." Already checked in → banner + Check out (`POST /participations/:id/checkout`). Invalid → friendly error + link home |
| `#/s/:qr_token/:event_id` **Peer check-in landing** | The **attested** path (CHECKIN_PROOF.md). `GET /scan/:token/:event_id` → `{person, event, project, waiver, my_open_participation, already_attested}`; renders "**You're checking in with {person}**" + that event's 📍🗓⏱ + **full waiver text** + `[ Confirm — we're both here ]`. Confirm → `POST /scan/:token/:event_id/confirm` → "✅ Verified — {person} is your confirmation", then Check out. Scanning your own code → 409 `self_scan` → "That's your own code — scan somebody else's." Unknown token / closed event → the same friendly invalid card as `#/c/` |
| `#/catalog` | Tabs **Offers · Needs** (`?kind=`), cards — **the whole card is the link** (`<a class="card" href="#/catalog/:id">`, like a project card): cover, plain title (`<h3>`, no nested `<a>`), status pill, 🪙 price (offers) / "need" badge + quantity, poster as **plain muted text** ("by Name" — a nested link would break the card link; the poster stays a real profile link only on the item detail). "＋ Post" |
| `#/catalog/new` | Kind toggle first — *offer*: price 🪙 (0 = free) + optional quantity; *need*: no price, helper text "people can send you tokens from your post". Description placeholder mentions pickup/contact/coupon terms |
| `#/catalog/:id` | Detail + role-aware actions. Viewer on offer: **Claim (N 🪙)** / claim status chip (pending→Cancel; accepted→"show this screen as proof"). Viewer on need: **Tip** (tip form, `catalog_item_id` attached). Poster: edit/close, **image upload via `imagesStrip` (poster only** — the food example needs a photo**)**, pending claims list with **Accept / Decline** (accept errors surface `insufficient_balance` as "claimant doesn't have enough tokens yet") |
| `#/wallet` | Balance hero (🪙 big number), **Tip tokens** (recipient **email**, amount, note), ledger list (`direction` arrows, counterparty display name, note, kind chip, local time), claims section: *mine* + *on my items* with pending-action rows |
| `#/u/:id` | Public profile: initials avatar (deterministic bg), display name, bio, joined; stats row: ⏱ hours · 🪙 earned · 📋 projects. **Tip** button (tips by `to_user_id`) |
| `#/me` | **Guest** (`me.is_guest`): the auto-handle + avatar, a **Rename** form (`PATCH /me {display_name}`), and a prominent **"Create an account to save your service"** card → the convert form (email + password + optional display name → `POST /auth/convert`; on success toast + re-render as a real profile). **Real**: profile summary (email — "only you can see this" — + balance) + edit (display_name, bio) + **Sign out** (clears the session, drops back to a fresh guest). Both keep the **Dark mode** toggle + Install-app button. Both also get **My log** — my own records (`GET /service_records?scope=mine` 📄), which is where an **unattached** record lives until its author attaches it (FEED.md F7) |

Empty states are one-line muted guidance (home-keep pattern): *"No projects yet.
Post the first one."* · *"Nothing in your ledger yet — volunteer an hour to earn
your first token."*

## `api.js` (contract with the backend)

home-keep's helper, renamed keys (`ai_token`, `ai_user`) with the return-to hook:

- Prefix `/api`, JSON headers, `Authorization: Bearer` from localStorage
- `401` → clear token, stash `ai_return`, route `#/login`, throw `unauthorized`
- `204 → null`; non-2xx → throw `{status, detail}` — views catch and render the
  code via `ERRORS[detail] ?? generic` map in `ui.js`
- No retries, no spinners beyond the view-level "Loading…" placeholder

## Escaping rule (non-negotiable)

All user-originated strings pass through `esc()` (HTML-entity escaper in `ui.js`)
inside template literals, or are assigned via `textContent`. The reference app
skipped this (trusted insiders); Active Impact is public — a display name must never
execute. Add one regression test-page check to manual verification: register as
`<img src=x onerror=alert(1)>`-style display name, confirm it renders inert.

## Look & feel ("slick but not over-engineered")

- **Tokens** in `:root` — palette: warm gray bg `#f6f7f5`, white cards, ink
  `#20241f`, accent **impact green** `#2e7d5b`, amber `#b8860b`, red `#b4452f`,
  hairline `#e3e6e1`; radius 10px; system-ui font stack. Plus
  `@media (prefers-color-scheme: dark)` overriding the same custom properties
  (dark bg `#141613`, card `#1e211d`, ink `#e8eae6`).
- Mobile-first single column, `main { max-width: 640px; margin: 0 auto }`,
  sticky top bar, `viewport-fit=cover` + safe-area padding.
- Bottom **tab nav** (fixed, **4 tabs**): 🏠 Home (the one feed — projects and
  their photos) · 🎁 Catalog · 🪙 Wallet · 👤 Me. The separate **Projects** tab is
  gone: home *is* projects now (FEED.md F2), and a tab that reopens the current
  screen is a bug in disguise. Emoji are the entire icon system for MVP. A distinct
  always-visible **＋ Log** floating action button sits over the feed, a project,
  an event, and a record → `#/log`; it is not one of the tabs.
- Class vocabulary: `.card .row .grow .muted .pill .tag .act .primary .ghost .del`.
  Status pills: open=green, completed=muted, pending=amber, declined/closed=red.
- **Vertical rhythm**: a view that mounts several siblings wraps them in a single
  `<div class="stack">` root (`.stack > * + *` spaces them). Don't rely on
  `.card + .card` alone — it fails between a card and a non-card (form, label,
  button), so a `.stack` root is the pattern for every multi-child view.
- Branding/animations deferred by intent — clean spacing + one accent does the
  "inviting" work.

## PWA mechanics

- **manifest.webmanifest**: name "Active Impact", short_name "Impact",
  `display: standalone`, `start_url: /`, theme `#2e7d5b`, background `#f6f7f5`,
  icons: `icon.svg` (`any maskable`) + 192/512 PNGs. Icon = hand-written tiny SVG:
  rounded square, green fill, white spark/leaf glyph — placeholder until branding.
- **sw.js** — copy home-keep's 22-line worker: cache name `impact-shell-v1`,
  precache `ASSETS` (every file in § Files), cache-first for GET static,
  `/api` always network, non-GET untouched, `skipWaiting` + `clients.claim`,
  old-cache cleanup. **Rule (documented in README): any `public/` change bumps
  the version string** — the reference app is at v33; forgetting is the #1
  staleness bug.
- Registration: one line, bottom of `app.js`.
- **Install**: header/Me button when authed & not standalone —
  `beforeinstallprompt` on Chromium, alert() walkthrough on iOS (Share → Add to
  Home Screen), generic fallback otherwise. Verbatim home-keep pattern.
- Offline = shell opens, API calls fail; add ONE catch-level "You're offline"
  message in `api.js` (`TypeError` on fetch) instead of silent "Loading…".

## Images

- Upload: `<input type="file" accept="image/*" capture="environment" multiple hidden>`
  → canvas resize ≤1600px JPEG q0.8 → base64 → `POST /api/images`.
- Display: authed `fetch` → `URL.createObjectURL` blob (Bearer headers don't
  attach to `<img>`); revoke on view teardown. Both helpers live in `ui.js`
  (`imagesStrip(entity, id, canEdit)`).

## QR flow (end-to-end, both phones)

```
LEADER (event lead hub, #/events/:id/lead)  VOLUNTEER
GET /events/:id/qr.svg → blob → big <img>    native camera scans → opens
        │                                    https://SITE/#/c/{code} in browser
        │                                       │ no token? stash #/c/{code} → login/register → back
        │                                       ▼
        │                                    GET /api/checkin/{code} → resolves the EVENT → waiver screen
        │                                    [ I agree — check me in ]
        │                                       ▼ POST /checkin/{code}/agree (201)
roster refreshes on next render ◀──          "✅ checked in"
… later: self checkout, leader checkout, or Close event → "🎉 +N tokens"
```

The event lead hub shows the code as text under the QR for camera-less fallback
("type it at `SITE/#/c/<code>`"). A check-in code belongs to one event; closing
the event invalidates it.

## Peer check-in flow (the attested layer — CHECKIN_PROOF.md)

The event QR above proves you saw *a sign*. This one is backed by a *person*.

```
ANA (already at the event)                   BEN (just arriving)
#/events/:id → "Show my code"                taps [ Check in ]
GET /events/:id/my-qr.svg → blob → big <img>       │
        │                                          ├─ scanner available? ──yes──► overlay opens
        │                                          │      BarcodeDetector reads Ana's code
        │  ◄──────── camera ────────────────────── │      → route to #/s/{ana}/{event}
        │  (or Ben's NATIVE camera on a printed     │            │
        │   sheet — the QR is just a URL)           │            ▼
        │                                          │      GET /api/scan/{ana}/{event} → waiver screen
        │                                          │      [ Confirm — we're both here ]
        │                                          │            ▼ POST …/confirm (201)
Ana's participation flips to attested ◀────────────┘      "✅ Verified — Ana is your confirmation"
                                                   │
                                                   └─ no scanner / denied / no camera
                                                          toast "Camera scanning isn't available
                                                          here — checking you in as self-reported"
                                                          → POST /events/:id/checkin  (asserted)
```

**Cancelling the scanner is not the same as it being unavailable.** Backing out
does nothing (it was a decision); *unavailable* falls through to the asserted
check-in, which is the founder's stated requirement.

`BarcodeDetector` is Chromium-only today, so the fallback is the common path on
iOS — and there the **native camera still works** on a printed code, because the
peer QR is a plain URL. No scanning library, no build step, no new dependency (D4).

A checked-in state renders as one of two pills wherever it appears (event detail,
roster, lead hub): **`✅ Verified`** (`attested`) or **`● Self-reported`**. The
wording is deliberately neutral — self-reported is a legitimate outcome.
