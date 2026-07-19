// Minimal service worker: makes Active Impact installable and shell-cached so it
// opens offline. API calls are network-first (fall back to nothing offline).
// Bump SHELL on ANY public/ change. The full ASSETS list lands in the frontend phase.
const SHELL = 'impact-shell-v1';
const ASSETS = ['/', '/index.html', '/style.css', '/manifest.webmanifest', '/icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;
  if (new URL(request.url).pathname.startsWith('/api')) return;
  e.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
});
