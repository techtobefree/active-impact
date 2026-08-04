// Minimal service worker: makes Active Impact installable and shell-cached so it
// opens offline. Shell assets are NETWORK-FIRST (fresh code always wins when
// online; cache only serves offline), so clients can never get stuck on a stale
// bundle. API calls bypass caching entirely.
const SHELL = 'impact-shell-v41';
const ASSETS = [
  '/', '/index.html', '/style.css', '/app.js', '/api.js', '/ui.js', '/scan.js',
  '/views/auth.js', '/views/records.js', '/views/projects.js', '/views/checkin.js', '/views/social.js',
  '/views/catalog.js', '/views/wallet.js', '/views/profile.js', '/push.js',
  '/manifest.webmanifest', '/icon.svg', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});
// ---- push (PUSH.md) --------------------------------------------------------
// This is what runs when the app is CLOSED: the push service wakes this worker,
// and showNotification puts it in the OS tray with sound and vibration.
self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { /* malformed: fall through */ }
  const title = data.title || 'Active Impact';
  const url = data.url || '#/notifications';
  e.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    // Same event collapses rather than stacking five times.
    tag: url,
    data: { url },
  }));
});

// Tapping it opens the thing it is about — focusing a tab we already have rather
// than piling up new ones.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = '/' + ((e.notification.data && e.notification.data.url) || '#/notifications');
  e.waitUntil((async () => {
    const windows = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of windows) {
      if (new URL(c.url).origin === self.location.origin) {
        await c.focus();
        if ('navigate' in c) { try { await c.navigate(url); } catch { /* focus is enough */ } }
        return;
      }
    }
    await clients.openWindow(url);
  })());
});

self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;
  if (new URL(request.url).pathname.startsWith('/api')) return; // API: always network
  // Network-first: serve fresh code whenever online, refresh the cache as we go,
  // and fall back to the cached shell only when the network fails (offline).
  e.respondWith(
    fetch(request)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(request)),
  );
});
