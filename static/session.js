/** Authentication and service-worker lifecycle shared by all pages. */
const AutoXAKSession = window.AutoXAKSession = (() => {
    let authentication = null;
    let profileSync = null;
    const key = (name) => `autoXAK_${name}`;
    const getToken = () => localStorage.getItem(key('jwt_token')) || '';
    const getUserId = () => localStorage.getItem(key('user_id')) || '';

    // The deadline covers the response body as well as connection setup.
    async function fetchJSON(url, options = {}, timeoutMs = 8000) {
        const controller = new AbortController();
        const abort = () => controller.abort();
        if (options.signal?.aborted) abort();
        else options.signal?.addEventListener('abort', abort, {once:true});
        const timeout = setTimeout(abort, timeoutMs);
        try {
            const response = await fetch(url, {...options, signal:controller.signal});
            const data = response.ok ? await response.json() : null;
            return {response, data};
        } finally {
            clearTimeout(timeout);
            options.signal?.removeEventListener('abort', abort);
        }
    }

    function usableToken(token, userId) {
        try {
            const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            const claims = JSON.parse(atob(part.padEnd(Math.ceil(part.length / 4) * 4, '=')));
            return claims.sub === userId && Number.isFinite(claims.exp) && claims.exp * 1000 > Date.now();
        } catch (_) { return false; }
    }

    function storeSession(data) {
        const previous = getUserId();
        if (previous && previous !== data.user_id) {
            for (const name of ['trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile', 'fuel_price', 'fuel_pass', 'dashboard_cache']) {
                localStorage.removeItem(key(name));
            }
        }
        localStorage.setItem(key('jwt_token'), data.token);
        localStorage.setItem(key('user_id'), data.user_id);
        localStorage.setItem(key('is_guest'), String(data.is_guest !== false));
        localStorage.setItem(key('user_name'), data.name || 'Гость');
        return {token: data.token, userId: data.user_id, isGuest: data.is_guest !== false, name: data.name || 'Гость'};
    }

    async function serverAcceptsToken(token) {
        if (!navigator.onLine) return true;
        try {
            const {response, data} = await fetchJSON('/api/v1/auth/me', {
                headers: {'Authorization': `Bearer ${token}`},
                cache: 'no-store'
            }, 5000);
            if (response.ok && getToken() === token && data.user_id === getUserId() && usableToken(data.token, data.user_id)) {
                localStorage.setItem(key('jwt_token'), data.token);
            }
            // Only an explicit rejection invalidates the local session. Network
            // outages and server failures must preserve offline recording access.
            return response.status !== 401;
        } catch (_) { return true; }
    }

    async function authenticate(options) {
        const token = getToken(), userId = getUserId();
        if (usableToken(token, userId) && await serverAcceptsToken(token)) {
            return {token:getToken(), userId, isGuest: localStorage.getItem(key('is_guest')) !== 'false', name: localStorage.getItem(key('user_name')) || 'Гость'};
        }
        localStorage.removeItem(key('jwt_token'));
        if (userId && !options.newGuest) {
            if (navigator.onLine) window.location.replace('/login');
            throw new Error('Для восстановления сессии необходимо войти заново');
        }
        const {response, data} = await fetchJSON('/api/v1/auth/guest', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        if (!response.ok) throw new Error(`Не удалось создать сессию: HTTP ${response.status}`);
        return storeSession(data);
    }

    function ensureAuthenticated(options = {}) {
        if (!authentication) {
            authentication = authenticate(options).finally(() => { authentication = null; });
        }
        return authentication;
    }

    function getAuthHeaders() {
        const token = getToken();
        return token ? {'Authorization': `Bearer ${token}`} : {};
    }

    async function syncProfileOnce() {
        const saved = localStorage.getItem(key('pending_profile'));
        if (!saved) return true;
        try {
            const pending = JSON.parse(saved);
            if (pending.user_id !== getUserId() || !navigator.onLine) return false;
            const {response} = await fetchJSON(`/api/v1/users/${encodeURIComponent(pending.user_id)}/profile`, {
                method:'POST', headers:{'Content-Type':'application/json', ...getAuthHeaders()},
                body:JSON.stringify(pending)});
            if (!response.ok) return false;
            if (localStorage.getItem(key('pending_profile')) === saved) localStorage.removeItem(key('pending_profile'));
            return true;
        } catch (_) {return false;}
    }
    function syncPendingProfile() {
        if (!profileSync) {
            const owner=getUserId();
            const run=async()=>{
                while (localStorage.getItem(key('pending_profile'))) {
                    if (owner!==getUserId() || !(await syncProfileOnce())) return false;
                }
                return true;
            };
            profileSync=(navigator.locks ? navigator.locks.request('autoxak-profile:'+owner,run) : run()).finally(()=>{profileSync=null;});
        }
        return profileSync;
    }
    async function saveProfile(payload) {
        if (payload.user_id !== getUserId()) throw new Error('Session changed during profile save');
        localStorage.setItem(key('pending_profile'), JSON.stringify(payload));
        return syncPendingProfile();
    }

    function clearSession() {
        for (const name of ['jwt_token', 'user_id', 'is_guest', 'user_name', 'trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile', 'fuel_price', 'fuel_pass', 'dashboard_cache']) localStorage.removeItem(key(name));
    }

    async function loginWithYandex() {
        const token = getToken();
        const accepted = usableToken(token, getUserId()) && await serverAcceptsToken(token);
        if (!accepted) localStorage.removeItem(key('jwt_token'));
        if (accepted && !(await syncPendingProfile())) throw new Error("Сначала синхронизируйте изменения автомобиля");
        const {response, data} = await fetchJSON('/api/v1/auth/yandex/login', {
            method: 'POST', headers: usableToken(getToken(), getUserId()) ? getAuthHeaders() : {}
        });
        if (!response.ok) throw new Error('Вход через Яндекс сейчас недоступен');
        window.location.assign(data.url);
    }

    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) return;
        try {
            const registrations = await navigator.serviceWorker.getRegistrations();
            for (const registration of registrations) {
                const worker = registration.active || registration.waiting || registration.installing;
                if (worker && new URL(worker.scriptURL).pathname === '/static/sw.js') await registration.unregister();
            }
            await navigator.serviceWorker.register('/sw.js', {scope: '/'});
        } catch (error) { console.warn('[PWA]', error); }
    }
    if (document.readyState === 'complete') registerServiceWorker();
    else window.addEventListener('load', registerServiceWorker, {once: true});

    return {fetchJSON, saveProfile, syncPendingProfile, adoptSession: storeSession, ensureAuthenticated, clearSession, loginWithYandex, getAuthHeaders, getUserId, getToken,
            isGuest: () => localStorage.getItem(key('is_guest')) !== 'false'};
})();
