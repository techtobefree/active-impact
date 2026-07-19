// Minimal service worker: makes Active Impact installable and shell-cached so it
// opens offline. API calls are network-first (fall back to nothing offline).
// !! Bump SHELL on ANY change to a file in public/ (else clients keep the old shell).
const SHELL = 'impact-shell-v5';
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
  e.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
});
