// Minimal service worker: makes Active Impact installable and shell-cached so it
// opens offline. Shell assets are NETWORK-FIRST (fresh code always wins when
// online; cache only serves offline), so clients can never get stuck on a stale
// bundle. API calls bypass caching entirely.
const SHELL = 'impact-shell-v8';
const ASSETS = [
  '/', '/index.html', '/style.css', '/app.js', '/api.js', '/ui.js',
  '/views/auth.js', '/views/projects.js', '/views/checkin.js',
  '/views/catalog.js', '/views/wallet.js', '/views/profile.js',
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
