const UNBLOCKED_PWA_VERSION = 4;
const CACHE = 'unblocked-pwa-v' + UNBLOCKED_PWA_VERSION;
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      cache.addAll([
        new URL('manifest.json', self.location).href,
        new URL('car-music-icon.png', self.location).href,
        new URL('unblocked-sw.js', self.location).href,
      ])
    )
  );
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names.map((n) => {
          if (n !== CACHE) {
            return caches.delete(n);
          }
          return Promise.resolve();
        })
      )
    )
  );
  return self.clients.claim();
});
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }
  const u = new URL(event.request.url);
  if (u.origin !== self.location.origin) {
    return;
  }
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
    );
    return;
  }
  if (u.pathname.includes('/api/')) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res && res.status === 200) {
          const c = res.clone();
          caches.open(CACHE).then((cache) => {
            try {
              cache.put(event.request, c);
            } catch (e) {}
          });
        }
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});