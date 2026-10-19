/**
 * autoXAK Session & Multi-Tenancy Manager (Yandex ID + Guest Mode)
 */
const AutoXAKSession = (() => {
    const STORAGE_KEY_TOKEN = "autoXAK_jwt_token";
    const STORAGE_KEY_USER_ID = "autoXAK_user_id";
    const STORAGE_KEY_DEVICE = "autoXAK_device_id";
    const STORAGE_KEY_IS_GUEST = "autoXAK_is_guest";
    const STORAGE_KEY_USER_NAME = "autoXAK_user_name";

    function getOrCreateDeviceId() {
        let devId = localStorage.getItem(STORAGE_KEY_DEVICE);
        if (!devId) {
            devId = (crypto.randomUUID && typeof crypto.randomUUID === "function")
                ? crypto.randomUUID().replace(/-/g, "")
                : "dev" + Math.random().toString(36).substring(2, 14) + Date.now().toString(36);
            localStorage.setItem(STORAGE_KEY_DEVICE, devId);
        }
        return devId;
    }

    async function ensureAuthenticated() {
        let token = localStorage.getItem(STORAGE_KEY_TOKEN);
        let userId = localStorage.getItem(STORAGE_KEY_USER_ID);

        if (token && userId) {
            return {
                token,
                userId,
                isGuest: localStorage.getItem(STORAGE_KEY_IS_GUEST) !== "false",
                name: localStorage.getItem(STORAGE_KEY_USER_NAME) || (userId.startsWith("ya_") ? "Яндекс ID" : "Гость")
            };
        }

        const deviceId = getOrCreateDeviceId();
        try {
            const res = await fetch("/api/v1/auth/handshake", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ device_id: deviceId })
            });

            if (res.ok) {
                const data = await res.json();
                localStorage.setItem(STORAGE_KEY_TOKEN, data.token);
                localStorage.setItem(STORAGE_KEY_USER_ID, data.user_id);
                localStorage.setItem(STORAGE_KEY_IS_GUEST, data.is_guest ? "true" : "false");
                localStorage.setItem(STORAGE_KEY_USER_NAME, data.name || (data.is_guest ? "Гость" : "Яндекс ID"));
                return {
                    token: data.token,
                    userId: data.user_id,
                    isGuest: data.is_guest,
                    name: data.name
                };
            }
        } catch (e) {
            console.error("[Session] Ошибка синхронизации сессии:", e);
        }

        return { token: token || "", userId: userId || "guest_offline", isGuest: true, name: "Гость" };
    }

    function loginWithYandex() {
        const currentId = localStorage.getItem(STORAGE_KEY_USER_ID) || "";
        window.location.href = `/api/v1/auth/yandex/login?guest_id=${encodeURIComponent(currentId)}`;
    }

    function getAuthHeaders() {
        const token = localStorage.getItem(STORAGE_KEY_TOKEN);
        return token ? { "Authorization": `Bearer ${token}` } : {};
    }

    function registerServiceWorker() {
        if ("serviceWorker" in navigator) {
            window.addEventListener("load", () => {
                navigator.serviceWorker.register("/static/sw.js")
                    .then((reg) => console.log("[PWA] ServiceWorker активен:", reg.scope))
                    .catch((err) => console.warn("[PWA] Ошибка ServiceWorker:", err));
            });
        }
    }

    registerServiceWorker();

    return {
        ensureAuthenticated,
        loginWithYandex,
        getAuthHeaders,
        getUserId: () => localStorage.getItem(STORAGE_KEY_USER_ID) || "guest_offline",
        getToken: () => localStorage.getItem(STORAGE_KEY_TOKEN) || "",
        isGuest: () => localStorage.getItem(STORAGE_KEY_IS_GUEST) !== "false"
    };
})();