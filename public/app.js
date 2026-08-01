// Active Impact PWA — boot, hash router, chrome. No build step.
import * as auth from './views/auth.js';
import * as records from './views/records.js';
import * as projects from './views/projects.js';
import * as checkin from './views/checkin.js';
import * as catalog from './views/catalog.js';
import * as wallet from './views/wallet.js';
import * as profile from './views/profile.js';
import { api, getToken, currentUser, setSession, stashReturn } from './api.js';
import { el, mount, errMessage } from './ui.js';

// Route kinds: AUTH = the convert/"save account" screen (a real user is bounced
// home; a guest stays). Everything else is protected — a GUEST token counts as
// authed (SERVICE_LOG.md §7: the app is always "signed in"), so only a truly
// token-less visitor is sent to the convert screen.
const AUTH = 'auth';

// [regex, viewFn, kind]. Captures pass to the view as args.
const routes = [
  // HOME is THE feed (FEED.md F2): projects, each carrying its event's photos.
  [/^#\/$/, projects.listView],
  [/^#\/log$/, records.logView],                    // log a service (photo + caption)
  [/^#\/log\/(\d+)$/, records.logView],             // …straight to a named event
  [/^#\/r\/(\d+)$/, records.recordView],            // one record (share / deep link)
  [/^#\/login$/, auth.convertView, AUTH],           // "Save your log / Sign in" (convert)
  [/^#\/register$/, auth.convertView, AUTH],        // same convert screen
  [/^#\/projects$/, projects.listView],             // the same screen as #/ (legacy path)
  [/^#\/projects\/new$/, projects.newView],
  [/^#\/projects\/(\d+)$/, projects.detailView],
  // Lead/QR/roster/close/who's-coming are PER-EVENT (a project has many events).
  [/^#\/events\/(\d+)\/lead$/, projects.leadView],
  [/^#\/events\/(\d+)$/, projects.eventDetailView],
  // Deliberately loose: a mangled QR/URL code must reach the check-in view's
  // friendly "invalid code" card, not silently fall through to the home screen.
  [/^#\/c\/(.+)$/, checkin.checkinView],
  // PEER check-in: a person's code + the event it was shown for. Strict on the
  // event id (it is ours to generate), loose on the token (same reasoning as above).
  [/^#\/s\/([^/]+)\/(\d+)$/, checkin.scanView],
  // The app bar's scanner — check in from anywhere, because the code carries
  // its own event (CHECKIN_PROOF.md §7.1b).
  [/^#\/scan$/, checkin.scanEntryView],
  [/^#\/catalog$/, catalog.listView],
  [/^#\/catalog\/new$/, catalog.newView],
  [/^#\/catalog\/(\d+)$/, catalog.detailView],
  [/^#\/wallet$/, wallet.walletView],
  [/^#\/me$/, profile.meView],
  [/^#\/u\/(\d+)$/, profile.userView],
];

export function updateBalance(me) {
  const b = document.getElementById('balance');
  if (b && me && me.balance != null) b.textContent = '🪙 ' + me.balance;
}

// Refresh the cached self (and topbar balance). Call after any token movement.
export async function refreshMe() {
  if (!getToken()) return null;
  try {
    const me = await api('/me');
    setSession(getToken(), me);
    updateBalance(me);
    return me;
  } catch {
    return null;
  }
}

// Ensure we hold a valid session — validating an existing token, else silently
// creating a GUEST (SERVICE_LOG.md §4). Returns true when a FRESH guest was just
// created (a first run / after an expiry), false for an already-valid session.
//
// SINGLE-FLIGHT: boot calls this, and so does the router when a route arrives
// before boot has finished (a QR deep link on a first run). Sharing the one
// in-flight promise means that never mints two guests.
let sessionInFlight = null;
export function ensureSession() {
  if (!sessionInFlight) {
    sessionInFlight = _ensureSession().finally(() => { sessionInFlight = null; });
  }
  return sessionInFlight;
}

async function _ensureSession() {
  if (getToken()) {
    const me = await refreshMe(); // validates; a dead token (401) is cleared here
    if (me) return false;
    // Token still present ⇒ /me failed transiently (offline), NOT a dead session:
    // keep it rather than clobbering a valid account with a throwaway guest.
    if (getToken()) return false;
  }
  try {
    const data = await api('/auth/guest', { method: 'POST' });
    setSession(data.token, data.user);
    updateBalance(data.user);
    return true;
  } catch {
    return false; // offline — the view will surface the error
  }
}

// Everyone is signed in (guest or real), so the chrome is always shown. Just
// pick the active tab and toggle the ＋ Log FAB (feed + record detail only).
// Where the app-bar scanner should put you back when you cancel it. Tracked from
// the routes you actually visit, so it works for a tap from anywhere AND for a
// cold deep link to #/scan (which just falls back home).
let lastNonScanHash = '#/';
export function scanReturnHash() { return lastNonScanHash; }

function updateChrome(hash) {
  const topbar = document.getElementById('topbar');
  const nav = document.getElementById('nav');
  const fab = document.getElementById('fab-log');
  topbar.classList.remove('hidden');
  nav.classList.remove('hidden');
  if (hash !== '#/scan') lastNonScanHash = hash;
  const active = hash.startsWith('#/catalog') ? 'catalog'
    : hash.startsWith('#/wallet') ? 'wallet'
    : (hash.startsWith('#/me') || hash.startsWith('#/u/')) ? 'me'
    : (hash === '#/login' || hash === '#/register' || hash === '#/scan'
       || hash.startsWith('#/c/') || hash.startsWith('#/s/')) ? ''
    : 'home'; // #/, #/projects…, #/events…, #/log, #/r/… — all one feed now
  nav.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.tab === active));
  // ＋ Log rides along wherever a photo makes sense: the feed, a project, an
  // event, a record. Not on forms, auth, check-in or the lead hub.
  const canLog = hash === '#/' || hash === '#/projects'
    || /^#\/projects\/\d+$/.test(hash) || /^#\/events\/\d+$/.test(hash)
    || hash.startsWith('#/r/');
  if (fab) fab.classList.toggle('hidden', !canLog);
  updateBalance(currentUser());
}

function errorCard(e) {
  const card = el('<div class="card stack center"></div>');
  card.append(el(`<p>${errMessage(e)}</p>`));
  const back = el('<button class="act">Back home</button>');
  back.onclick = () => { location.hash = '#/'; };
  card.append(back);
  return card;
}

// Views are SERIALIZED. Every view fetches first and mounts second, so two
// overlapping renders race to paint: tap a project card and then a nav tab, and
// the card's slower page can land on top of the tab you actually chose. Queueing
// the view calls means the newest render always mounts last. (Correcting it
// afterwards instead — re-rendering when the hash moved — is worse: it wipes a
// form the user has already typed into.) Nothing ever awaits render(), so the
// queue cannot deadlock; a rejected render still passes the baton on.
let queue = Promise.resolve();

async function runView(view, groups) {
  try {
    await view(...groups);
  } catch (e) {
    if (e && e.sessionExpired) { location.hash = '#/login'; return; } // dead session mid-load
    mount(errorCard(e));
  }
}

export function render() {
  const hash = location.hash || '#/';
  const match = routes.find(([re]) => re.test(hash));
  if (!match) { location.hash = '#/'; return queue; }
  const [re, view, kind] = match;
  const me = currentUser();
  if (kind === AUTH) {
    // The convert screen: a real (non-guest) account is already saved -> home.
    // A guest stays put (do NOT redirect a guest away from convert).
    if (getToken() && me && me.is_guest === false) { location.hash = '#/'; return queue; }
  } else if (!getToken()) {
    // No token YET. On a first run the guest mint is usually still in flight —
    // a scanned QR opens the app and the deep link can beat it — so WAIT for the
    // session instead of bouncing to the convert screen. Being asked to sign up
    // because the network was slow is the exact friction guests exist to remove.
    window.scrollTo(0, 0);
    updateChrome(hash);
    const groups = hash.match(re).slice(1);
    const gated = async () => {
      await ensureSession();
      if (getToken()) return runView(view, groups);
      stashReturn(hash); location.hash = '#/login';  // genuinely offline
    };
    queue = queue.then(gated, gated);
    return queue;
  }
  // The chrome is synchronous, so it moves with the tap rather than waiting in
  // the queue behind a slow fetch.
  window.scrollTo(0, 0);
  updateChrome(hash);
  const groups = hash.match(re).slice(1);
  queue = queue.then(() => runView(view, groups), () => runView(view, groups));
  return queue;
}

// re-run the current route (in-place refresh after a mutation)
export function refresh() { return render(); }

// Auto-reload an open tab when a new build is deployed, so it never runs stale
// code. /api/version changes on every server (re)start. Two safety rules:
// 1. NEVER reload while the user has typed into a form (a reload would wipe it);
//    retry on the next check instead.
// 2. Purge SW caches before reloading so the reload fetches the NEW bundle,
//    never the old cached shell (belt-and-braces with the network-first SW).
let BUILD = null;
function formIsDirty() {
  return [...document.querySelectorAll('#view input, #view textarea')]
    .some((i) => i.value !== '' && i.value !== i.defaultValue);
}
async function checkVersion() {
  try {
    const r = await fetch('/api/version', { cache: 'no-store' });
    if (!r.ok) return;
    const { version } = await r.json();
    if (BUILD === null) { BUILD = version; return; }
    if (version !== BUILD && !formIsDirty()) {
      if ('caches' in window) {
        try { const ks = await caches.keys(); await Promise.all(ks.map((k) => caches.delete(k))); } catch { /* ignore */ }
      }
      location.reload();
    }
  } catch { /* offline — ignore */ }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkVersion(); });
setInterval(checkVersion, 20_000);

window.addEventListener('hashchange', render);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

(async () => {
  await checkVersion(); // record the build we loaded with
  // Guarantee a session BEFORE the first render so the app is always signed in.
  const freshGuest = await ensureSession();
  const atRoot = !location.hash || location.hash === '#/';
  if (freshGuest && atRoot) {
    // FIRST RUN (a brand-new guest, landing at the root) -> straight to the Log
    // screen (camera up). A returning token renders the feed normally.
    if (location.hash === '#/log') render(); else location.hash = '#/log';
  } else if (!location.hash) {
    location.hash = '#/';
  } else {
    render();
  }
})();
