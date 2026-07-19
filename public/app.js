// Active Impact PWA — boot, hash router, chrome. No build step.
import * as auth from './views/auth.js';
import * as projects from './views/projects.js';
import * as checkin from './views/checkin.js';
import * as catalog from './views/catalog.js';
import * as wallet from './views/wallet.js';
import * as profile from './views/profile.js';
import { api, getToken, currentUser, setSession, stashReturn } from './api.js';
import { el, mount, errMessage } from './ui.js';

// [regex, viewFn, isPublic]. Captures pass to the view as args.
const routes = [
  [/^#\/login$/, auth.loginView, true],
  [/^#\/register$/, auth.registerView, true],
  [/^#\/$/, projects.listView],
  [/^#\/projects\/new$/, projects.newView],
  [/^#\/projects\/(\d+)$/, projects.detailView],
  [/^#\/projects\/(\d+)\/lead$/, projects.leadView],
  [/^#\/c\/([\w-]+)$/, checkin.checkinView],
  [/^#\/catalog$/, catalog.listView],
  [/^#\/catalog\/new$/, catalog.newView],
  [/^#\/catalog\/(\d+)$/, catalog.detailView],
  [/^#\/wallet$/, wallet.walletView],
  [/^#\/me$/, profile.meView],
  [/^#\/u\/([\w-]+)$/, profile.userView],
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

function updateChrome(hash, isPublic) {
  const topbar = document.getElementById('topbar');
  const nav = document.getElementById('nav');
  topbar.classList.toggle('hidden', isPublic);
  nav.classList.toggle('hidden', isPublic);
  const active = hash.startsWith('#/catalog') ? 'catalog'
    : hash.startsWith('#/wallet') ? 'wallet'
    : (hash.startsWith('#/me') || hash.startsWith('#/u/')) ? 'me'
    : hash.startsWith('#/c/') ? '' : 'projects';
  nav.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.tab === active));
  updateBalance(currentUser());
}

function errorCard(e) {
  const card = el('<div class="card stack center"></div>');
  card.append(el(`<p>${errMessage(e)}</p>`));
  const back = el('<button class="act">Back to projects</button>');
  back.onclick = () => { location.hash = '#/'; };
  card.append(back);
  return card;
}

export async function render() {
  const hash = location.hash || '#/';
  const match = routes.find(([re]) => re.test(hash));
  if (!match) { location.hash = '#/'; return; }
  const [re, view, isPublic] = match;
  // Auth gate + return-to: an unauthenticated visitor to a protected route
  // (e.g. a scanned QR /#/c/CODE) is sent to login and bounced back after.
  if (!isPublic && !getToken()) { stashReturn(hash); location.hash = '#/login'; return; }
  if (isPublic && getToken()) { location.hash = '#/'; return; }
  window.scrollTo(0, 0);
  updateChrome(hash, isPublic);
  const groups = hash.match(re).slice(1);
  try {
    await view(...groups);
  } catch (e) {
    if (e && (e.detail === 'unauthorized')) return; // api() already redirected
    mount(errorCard(e));
  }
}

// re-run the current route (in-place refresh after a mutation)
export function refresh() { return render(); }

window.addEventListener('hashchange', render);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

(async () => {
  if (getToken()) await refreshMe();
  if (!location.hash) location.hash = '#/'; else render();
})();
