/**
 * autoXAK Session & Multi-Tenancy Manager + PWA Registration
 */
const AutoXAKSession = (() => {
    const STORAGE_KEY_TOKEN = "autoXAK_jwt_token";
    const STORAGE_KEY_USER_ID = "autoXAK_user_id";
    const STORAGE_KEY_DEVICE = "autoXAK_device_id";

    function getOrCreateDeviceId() {
        let devId = localStorage.getItem(STORAGE_KEY_DEVICE);
        if (!devId) {
            devId = (crypto.randomUUID && typeof crypto.randomUUID === 'function')
                ? crypto.randomUUID().replace(/-/g, '')
                : 'dev' + Math.random().toString(36).substring(2, 14) + Date.now().toString(36);
            localStorage.setItem(STORAGE_KEY_DEVICE, devId);
        }
        return devId;
    }

    async function ensureAuthenticated() {
        let token = localStorage.getItem(STORAGE_KEY_TOKEN);
        let userId = localStorage.getItem(STORAGE_KEY_USER_ID);

        if (token && userId) {
            return { token, userId };
        }

        const deviceId = getOrCreateDeviceId();
        try {
            const res = await fetch('/api/v1/auth/handshake', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_id: deviceId })
            });

            if (res.ok) {
                const data = await res.json();
                localStorage.setItem(STORAGE_KEY_TOKEN, data.token);
                localStorage.setItem(STORAGE_KEY_USER_ID, data.user_id);
                return { token: data.token, userId: data.user_id };
            }
        } catch (e) {
            console.error('[Session] Ошибка авторизации устройства:', e);
        }

        return { token: token || "", userId: userId || "test_user_01" };
    }

    function getAuthHeaders() {
        const token = localStorage.getItem(STORAGE_KEY_TOKEN);
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    function registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/static/sw.js')
                    .then((reg) => console.log('[PWA] ServiceWorker активен:', reg.scope))
                    .catch((err) => console.warn('[PWA] Ошибка ServiceWorker:', err));
            });
        }
    }

    registerServiceWorker();

    return {
        ensureAuthenticated,
        getAuthHeaders,
        getUserId: () => localStorage.getItem(STORAGE_KEY_USER_ID) || "test_user_01",
        getToken: () => localStorage.getItem(STORAGE_KEY_TOKEN) || ""
    };
})();
