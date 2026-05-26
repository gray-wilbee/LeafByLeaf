// Bump version string whenever CSS or JS changes to invalidate the cache
const CACHE = 'journal-v39';

const PRECACHE = [
  '/static/style.css',
  '/static/chat.js',
  '/static/entities.js',
  '/static/tasks.js',
  '/static/topics.js',
  '/static/search.js',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;

  // Static assets: cache-first (fast, versioned by CACHE string above)
  if (e.request.url.includes('/static/')) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).then(res => {
        caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      }))
    );
    return;
  }

  // Everything else: network-first, silent fail (no offline page needed for a personal app)
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
