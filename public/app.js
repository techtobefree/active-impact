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
  [/^#\/$/, records.feedView],                      // HOME = the service feed
  [/^#\/log$/, records.logView],                    // log a service (photo + caption)
  [/^#\/r\/(\d+)$/, records.recordView],            // one record (share / deep link)
  [/^#\/login$/, auth.convertView, AUTH],           // "Save your log / Sign in" (convert)
  [/^#\/register$/, auth.convertView, AUTH],        // same convert screen
  [/^#\/projects$/, projects.listView],             // the service-projects list (was home)
  [/^#\/projects\/new$/, projects.newView],
  [/^#\/projects\/(\d+)$/, projects.detailView],
  // Lead/QR/roster/close/who's-coming are PER-EVENT (a project has many events).
  [/^#\/events\/(\d+)\/lead$/, projects.leadView],
  [/^#\/events\/(\d+)$/, projects.eventDetailView],
  // Deliberately loose: a mangled QR/URL code must reach the check-in view's
  // friendly "invalid code" card, not silently fall through to the home screen.
  [/^#\/c\/(.+)$/, checkin.checkinView],
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
export async function ensureSession() {
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
function updateChrome(hash) {
  const topbar = document.getElementById('topbar');
  const nav = document.getElementById('nav');
  const fab = document.getElementById('fab-log');
  topbar.classList.remove('hidden');
  nav.classList.remove('hidden');
  const active = (hash.startsWith('#/projects') || hash.startsWith('#/events')) ? 'projects'
    : hash.startsWith('#/catalog') ? 'catalog'
    : hash.startsWith('#/wallet') ? 'wallet'
    : (hash.startsWith('#/me') || hash.startsWith('#/u/')) ? 'me'
    : (hash === '#/login' || hash === '#/register' || hash.startsWith('#/c/')) ? ''
    : 'home'; // #/, #/log, #/r/…
  nav.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.tab === active));
  if (fab) fab.classList.toggle('hidden', !(hash === '#/' || hash.startsWith('#/r/')));
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

export async function render() {
  const hash = location.hash || '#/';
  const match = routes.find(([re]) => re.test(hash));
  if (!match) { location.hash = '#/'; return; }
  const [re, view, kind] = match;
  const me = currentUser();
  if (kind === AUTH) {
    // The convert screen: a real (non-guest) account is already saved -> home.
    // A guest stays put (do NOT redirect a guest away from convert).
    if (getToken() && me && me.is_guest === false) { location.hash = '#/'; return; }
  } else if (!getToken()) {
    // Truly token-less (a transient boot edge): stash + go to the convert screen.
    stashReturn(hash); location.hash = '#/login'; return;
  }
  window.scrollTo(0, 0);
  updateChrome(hash);
  const groups = hash.match(re).slice(1);
  try {
    await view(...groups);
  } catch (e) {
    if (e && e.sessionExpired) { location.hash = '#/login'; return; } // view load with a dead session
    mount(errorCard(e));
  }
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
