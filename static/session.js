/** Authentication and service-worker lifecycle shared by all pages. */
const AutoXAKSession = window.AutoXAKSession = (() => {
    let authentication = null;
    const key = (name) => `autoXAK_${name}`;
    const getToken = () => localStorage.getItem(key('jwt_token')) || '';
    const getUserId = () => localStorage.getItem(key('user_id')) || '';

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
            for (const name of ['trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile']) {
                localStorage.removeItem(key(name));
            }
        }
        localStorage.setItem(key('jwt_token'), data.token);
        localStorage.setItem(key('user_id'), data.user_id);
        localStorage.setItem(key('is_guest'), String(data.is_guest !== false));
        localStorage.setItem(key('user_name'), data.name || 'Гость');
        return {token: data.token, userId: data.user_id, isGuest: data.is_guest !== false, name: data.name || 'Гость'};
    }

    async function authenticate(options) {
        const token = getToken(), userId = getUserId();
        if (usableToken(token, userId)) {
            return {token, userId, isGuest: localStorage.getItem(key('is_guest')) !== 'false', name: localStorage.getItem(key('user_name')) || 'Гость'};
        }
        if (userId && !options.newGuest) {
            if (navigator.onLine) window.location.replace('/login');
            throw new Error('Для восстановления сессии необходимо войти заново');
        }
        const response = await fetch('/api/v1/auth/guest', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        if (!response.ok) throw new Error(`Не удалось создать сессию: HTTP ${response.status}`);
        return storeSession(await response.json());
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

    async function syncPendingProfile() {
        const saved = localStorage.getItem(key('pending_profile'));
        if (!saved) return true;
        try {
            const pending = JSON.parse(saved);
            if (pending.user_id !== getUserId() || !navigator.onLine) return false;
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 8000);
            try {
                const response = await fetch(`/api/v1/users/${encodeURIComponent(pending.user_id)}/profile`, {
                    method:'POST', headers:{'Content-Type':'application/json', ...getAuthHeaders()},
                    body:JSON.stringify(pending), signal:controller.signal});
                if (!response.ok) return false;
                if (localStorage.getItem(key('pending_profile')) === saved) localStorage.removeItem(key('pending_profile'));
                return true;
            } finally {clearTimeout(timeout);}
        } catch (_) {return false;}
    }
    async function saveProfile(payload) {
        if (payload.user_id !== getUserId()) throw new Error('Session changed during profile save');
        localStorage.setItem(key('pending_profile'), JSON.stringify(payload));
        return syncPendingProfile();
    }

    function clearSession() {
        for (const name of ['jwt_token', 'user_id', 'is_guest', 'user_name', 'trip_details_map', 'offline_trips', 'local_history', 'profile', 'car_id', 'car_name', 'selected_car_id', 'selected_car_name', 'pending_profile']) localStorage.removeItem(key(name));
    }

    async function loginWithYandex() {
        if (!(await syncPendingProfile())) throw new Error("Сначала синхронизируйте изменения автомобиля");
        const response = await fetch('/api/v1/auth/yandex/login', {
            method: 'POST', headers: usableToken(getToken(), getUserId()) ? getAuthHeaders() : {}
        });
        if (!response.ok) throw new Error('Вход через Яндекс сейчас недоступен');
        const data = await response.json();
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

    return {saveProfile, syncPendingProfile, adoptSession: storeSession, ensureAuthenticated, clearSession, loginWithYandex, getAuthHeaders, getUserId, getToken,
            isGuest: () => localStorage.getItem(key('is_guest')) !== 'false'};
})();