/* AutoMan service worker — deliberately minimal.
   Purpose: satisfy PWA installability (a fetch handler must exist) and
   cache only static assets. Everything else — HTML pages and especially
   /api/* — goes straight to the network: the backend sets no-cache headers
   on purpose (live job-order/checklist data), and caching API responses
   here would resurface stale statuses. */

const CACHE_NAME = 'automan-static-v2';
const PRECACHE = [
    '/static/css/design-system.css',
    '/static/js/automan-nav.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/images/automan-logo.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(PRECACHE))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Cross-origin requests (CDNs, fonts, chatbot) are the browser's problem —
    // intercepting them here just turns their failures into SW console errors.
    if (url.origin !== self.location.origin) return;

    // Static assets: stale-while-revalidate — serve from cache immediately,
    // refresh the cached copy in the background so deploys propagate without
    // a manual hard-refresh.
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                const refresh = fetch(event.request).then((resp) => {
                    if (resp.ok) {
                        const clone = resp.clone();
                        caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
                    }
                    return resp;
                }).catch(() => cached);
                return cached || refresh.then(
                    (resp) => resp || new Response('Offline', { status: 503 })
                );
            })
        );
        return;
    }

    // Everything else (pages, /api/*): network only — never cache. Catch
    // network failures so they don't surface as uncaught SW errors.
    event.respondWith(
        fetch(event.request).catch(() =>
            new Response(
                'You appear to be offline. Please check your connection and try again.',
                { status: 503, headers: { 'Content-Type': 'text/plain' } }
            )
        )
    );
});
