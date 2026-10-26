const CACHE_NAME = 'autoxak-shell-v4';
const STATIC_ASSETS = ['/', '/setup', '/history', '/login', '/static/session.js', '/static/offline_store.js', '/static/manifest.json', '/static/icon-192.png', '/static/icon-512.png'];
self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('autoxak-shell-') && key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'GET' || url.origin !== self.location.origin || !STATIC_ASSETS.includes(url.pathname)) return;
    event.respondWith((async () => {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 7000);
        let fallback;
        try {
            const cache = await caches.open(CACHE_NAME);
            // Queries do not create unbounded duplicate copies of the app shell.
            fallback = await cache.match(url.pathname);
            const response = await fetch(event.request, {signal:controller.signal});
            if (!response.ok || response.redirected) return fallback || response;
            // Finish reading under the same deadline: headers alone are insufficient.
            const body = await response.arrayBuffer();
            const headers = new Headers(response.headers);
            // fetch already decoded gzip/br; these headers describe wire bytes.
            headers.delete('content-encoding');
            headers.delete('content-length');
            headers.delete('transfer-encoding');
            const complete = new Response(body, {
                status:response.status, statusText:response.statusText, headers
            });
            try {await cache.put(url.pathname, complete.clone());} catch (_) {}
            return complete;
        } catch (_) {
            return fallback || Response.error();
        } finally {clearTimeout(timeout);}
    })());
});
