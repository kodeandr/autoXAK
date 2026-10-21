const CACHE_NAME = 'autoxak-shell-v2';
const STATIC_ASSETS = ['/', '/setup', '/history', '/login', '/static/session.js', '/static/offline_store.js', '/static/manifest.json', '/static/icon-192.png', '/static/icon-512.png'];
self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('autoxak-shell-') && key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/') || !STATIC_ASSETS.includes(url.pathname)) return;
    // Fresh HTML/JS keeps client and API versions in step; cached shell survives offline.
    event.respondWith(fetch(event.request).then(async response => {
        if (response.ok && !response.redirected) {
            const cache = await caches.open(CACHE_NAME);
            try {await cache.put(event.request, response.clone());} catch (_) {}
        }
        return response;
    }).catch(async () => (await caches.match(event.request)) || (await caches.match(url.pathname)) || Response.error()));
});
