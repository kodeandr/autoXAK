const CACHE_NAME = 'autoxak-shell-v2';
const STATIC_ASSETS = ['/', '/setup', '/history', '/static/app.css', '/static/session.js',
    '/static/offline_store.js', '/static/recorder.js', '/static/app.js', '/static/manifest.json',
    '/static/icon-192.png', '/static/icon-512.png'];
self.addEventListener('install', event => {
    // Do not replace a running recorder. The new worker activates after old pages close.
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
});
self.addEventListener('activate', event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('autoxak-shell-') && key !== CACHE_NAME)
        .map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
    if (!STATIC_ASSETS.includes(url.pathname)) return;
    event.respondWith(fetch(event.request).then(response => {
        if (response.ok) {
            const copy = response.clone();
            event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)));
        }
        return response;
    }).catch(() => caches.match(url.pathname)));
});
