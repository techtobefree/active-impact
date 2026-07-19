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
├── views/auth.js           # login + register screens
├── views/projects.js       # list, detail, create/edit, lead screen (QR + roster)
├── views/checkin.js        # the #/c/{code} landing: waiver → I agree → checked-in state
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
  [/^#\/$/,                    views.projectList],
  [/^#\/login$/,               views.login],       // public
  [/^#\/register$/,            views.register],    // public
  [/^#\/projects\/new$/,       views.projectNew],
  [/^#\/projects\/(\d+)$/,     views.projectDetail],
  [/^#\/projects\/(\d+)\/lead$/, views.projectLead],
  [/^#\/c\/([\w-]+)$/,         views.checkin],     // QR landing
  [/^#\/catalog$/,             views.catalog],
  [/^#\/catalog\/new$/,        views.itemNew],
  [/^#\/catalog\/(\d+)$/,      views.itemDetail],
  [/^#\/wallet$/,              views.wallet],
  [/^#\/me$/,                  views.myProfile],
  [/^#\/u\/([\w-]+)$/,         views.profile],
];
// render(): match hash (default '#/'), auth-gate, call view(...groups)
window.addEventListener('hashchange', render);
```

**Auth gating & return-to (critical for the QR flow):** if a protected route is
hit without a token, stash it — `sessionStorage.setItem('ai_return',
location.hash)` — and go to `#/login`. After successful login/register, navigate
to the stashed hash (else `#/`). So: *scan QR → register → land back on the
waiver screen.* Test this path explicitly.

## Screens

| Route | Content & API calls |
|---|---|
| `#/login`, `#/register` | **Email + password** (register also requires Display name — the public identity). `autocomplete`/`inputmode=email` attrs; live field-attributed validation. On success: store `ai_token`/`ai_user` in localStorage → return-to. Link between the two |
| `#/` **Projects** | Upcoming project cards (`GET /projects`): title, 📍 location, 🗓 local time, ⏱ expected duration, cover image, checked-in count. Client + `q` search. "＋ New project". Tabs: Upcoming · Past · Mine |
| `#/projects/new` | `addForm`: title, description, location, starts_at (`<input type="datetime-local">` → ISO), expected duration (hours picker → minutes), waiver textarea **left blank → server seeds the default template** (placeholder text says so; template lives server-side only — no client copy to drift) + banner: *"Blank uses our standard template — not legal advice. Edit to fit your project."* |
| `#/projects/:id` | Detail: images strip, description, leaders (→ profiles), waiver (collapsed `<details>`), my state — checked-in banner with live elapsed time + **Check out** button when open participation exists. `am_leader` → **Lead screen** link + edit. `POST /participations/:id/checkout` on checkout, then show "🎉 +N tokens" |
| `#/projects/:id/lead` | Leader hub: **big QR** (`<img src=blob>` of `/qr.svg` — authed fetch), the `checkin_code` as text fallback (from project detail, `am_leader` only), regenerate button (confirm), **"Check in yourself"** link → `#/c/{code}` (leaders earn too — intent), roster with per-row **Check out** (posts the row's participation `id`), live count, **Close project** (confirm: "checks out everyone & completes"), add leader by email / remove by ✕ (owner irremovable; responses show display names only), image upload |
| `#/c/:code` **Check-in landing** | The heart. `GET /checkin/:code` → project summary + **full waiver text** + `[ I agree — check me in ]`. Agree → `POST /agree` → success state: "✅ You're checked in — HH:MM. Find the leader if you need anything." Already checked in → banner + Check out. Invalid → friendly error + link home |
| `#/catalog` | Tabs **Offers · Needs** (`?kind=`), cards: title, poster, 🪙 price (offers) / "need" badge, image. "＋ Post" |
| `#/catalog/new` | Kind toggle first — *offer*: price 🪙 (0 = free) + optional quantity; *need*: no price, helper text "people can send you tokens from your post". Description placeholder mentions pickup/contact/coupon terms |
| `#/catalog/:id` | Detail + role-aware actions. Viewer on offer: **Claim (N 🪙)** / claim status chip (pending→Cancel; accepted→"show this screen as proof"). Viewer on need: **Send tokens** (tip form, `catalog_item_id` attached). Poster: edit/close, **image upload via `imagesStrip` (poster only** — the food example needs a photo**)**, pending claims list with **Accept / Decline** (accept errors surface `insufficient_balance` as "claimant doesn't have enough tokens yet") |
| `#/wallet` | Balance hero (🪙 big number), **Send tokens** (recipient **email**, amount, note), ledger list (`direction` arrows, counterparty display name, note, kind chip, local time), claims section: *mine* + *on my items* with pending-action rows |
| `#/u/:id` | Public profile: initials avatar (deterministic bg), display name, bio, joined; stats row: ⏱ hours · 🪙 earned · 📋 projects. **Send tokens** button (tips by `to_user_id`) |
| `#/me` | Own profile + edit (display_name, bio) + logout. Install-app button lives here too |

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
- Bottom **tab nav** (fixed, 4 tabs): 🌱 Projects · 🎁 Catalog · 🪙 Wallet ·
  👤 Me. Emoji are the entire icon system for MVP.
- Class vocabulary: `.card .row .grow .muted .pill .tag .act .primary .ghost .del`.
  Status pills: open=green, completed=muted, pending=amber, declined/closed=red.
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
LEADER (lead screen)                    VOLUNTEER
GET /qr.svg → blob → big <img>          native camera scans → opens
        │                               https://SITE/#/c/{code} in browser
        │                                  │ no token? stash #/c/{code} → login/register → back
        │                                  ▼
        │                               GET /api/checkin/{code} → waiver screen
        │                               [ I agree — check me in ]
        │                                  ▼ POST /agree (201)
roster refreshes on next render ◀──     "✅ checked in"
… later: self checkout, leader checkout, or Close project → "🎉 +N tokens"
```

No in-app scanner exists (D5). The lead screen shows the code as text under the
QR for camera-less fallback ("type it at `SITE/#/c/<code>`").
