/* Device credentials are shared by all v2 pages; never invent a user on network failure. */
(function (root) {
    'use strict';
    const TOKEN = 'autoXAK_jwt_token', OWNER = 'autoXAK_user_id', DEVICE = 'autoXAK_device_id';
    let handshake = null;
    function getUserId() { return localStorage.getItem(OWNER); }
    function getToken() { return localStorage.getItem(TOKEN); }
    function getProvider() { return localStorage.getItem('autoXAK_auth_provider') || (getUserId()?.startsWith('ya_') ? 'yandex' : 'device'); }
    function rememberOwner(previous) {
        let owners;
        try { owners = JSON.parse(localStorage.getItem('autoxak_previous_owners_v2') || '[]'); } catch (_) { owners = []; }
        if (previous && !owners.includes(previous)) owners.push(previous);
        localStorage.setItem('autoxak_previous_owners_v2', JSON.stringify(owners));
    }
    function getDeviceId() {
        let id = localStorage.getItem(DEVICE);
        if (!id) {
            id = crypto.randomUUID().replaceAll('-', '');
            localStorage.setItem(DEVICE, id);
        }
        return id;
    }
    async function ensureAuthenticated(force = false) {
        if (!force && getToken() && getUserId()) return { token: getToken(), userId: getUserId() };
        if (getProvider() === 'yandex') {
            const error = new Error('Вход через Яндекс истёк. Войдите снова; поездки сохранены на этом телефоне.');
            error.reloginRequired = true; throw error;
        }
        if (handshake) return handshake;
        handshake = (async () => {
            const oldOwner = getUserId();
            const requestedDevice = getDeviceId();
            const response = await fetch('/api/v1/auth/handshake', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_id: requestedDevice })
            });
            if (!response.ok) {
                const error = new Error('Не удалось подключить устройство. Проверьте интернет.');
                error.recoveryRequired = response.status === 422 || response.status === 401;
                throw error;
            }
            const data = await response.json();
            if (!data.token || !data.user_id) throw new Error('Сервер не вернул данные входа.');
            if (getUserId() !== oldOwner || getDeviceId() !== requestedDevice) throw new Error('Аккаунт изменился; обновите страницу.');
            if (oldOwner && oldOwner !== data.user_id) {
                const error = new Error('Старая сессия устройства больше не поддерживается. Локальные поездки не отправлены в другой аккаунт.');
                error.recoveryRequired = true; throw error;
            }
            localStorage.setItem(TOKEN, data.token);
            localStorage.setItem(OWNER, data.user_id);
            localStorage.setItem('autoXAK_auth_provider', 'device');
            localStorage.setItem('autoXAK_is_guest', 'true');
            return { token: data.token, userId: data.user_id };
        })();
        try { return await handshake; } finally { handshake = null; }
    }
    async function request(path, options = {}, owner = getUserId()) {
        let auth = await ensureAuthenticated();
        if (owner && auth.userId !== owner) throw new Error('Поездка принадлежит другому пользователю.');
        const send = () => {
            if (getUserId() !== auth.userId) throw new Error('Аккаунт изменился; отправка остановлена.');
            return fetch(path, { ...options,
                headers: { 'Content-Type': 'application/json', ...(options.headers || {}), Authorization: `Bearer ${auth.token}` }
            });
        };
        let response = await send();
        if (response.status === 401) {
            const refreshed = await ensureAuthenticated(true);
            if (owner && refreshed.userId !== owner) throw new Error('Владелец поездки не совпадает с аккаунтом.');
            auth = refreshed;
            response = await send();
        }
        if (!response.ok) {
            let detail;
            try { detail = (await response.json()).detail; } catch (_) { /* HTTP status is sufficient. */ }
            const error = new Error(typeof detail === 'string' ? detail : `Сервер не принял запрос (${response.status}). Запись сохранена на телефоне.`);
            error.status = response.status;
            throw error;
        }
        return response.status === 204 ? null : response.json();
    }
    async function startNewSession() {
        if (handshake) { try { await handshake; } catch (_) { /* The user explicitly chose recovery. */ } }
        const previous = getUserId();
        const reset = () => {
            if (getUserId() !== previous) throw new Error('Аккаунт изменился; обновите страницу.');
            rememberOwner(previous);
            localStorage.setItem(DEVICE, crypto.randomUUID().replaceAll('-', ''));
            localStorage.removeItem(TOKEN); localStorage.removeItem(OWNER);
            localStorage.setItem('autoXAK_auth_provider', 'device');
            return ensureAuthenticated();
        };
        if (previous && navigator.locks) return navigator.locks.request(`autoxak-recording:${previous}`, { ifAvailable: true }, lock => {
            if (!lock) throw new Error('В другой вкладке идёт запись. Сначала завершите её.');
            return reset();
        });
        return reset();
    }
    async function renew() {
        const expectedOwner = getUserId();
        const data = await request('/api/v1/auth/me', {}, expectedOwner);
        if (data.user_id !== expectedOwner || getUserId() !== expectedOwner || !data.token) throw new Error('Аккаунт изменился. Обновите страницу.');
        localStorage.setItem(TOKEN, data.token);
        localStorage.setItem('autoXAK_is_guest', String(data.is_guest));
        return data;
    }
    async function acceptOAuthSession(data) {
        if (!data || typeof data.token !== 'string' || typeof data.user_id !== 'string' || data.auth_provider !== 'yandex') {
            throw new Error('Сервер не вернул корректную сессию Яндекса.');
        }
        const previous = getUserId();
        const apply = async () => {
            if (getUserId() !== previous) throw new Error('Аккаунт изменился; повторите вход.');
            if (previous && root.AutoXAKOfflineStore) {
                const trips = await root.AutoXAKOfflineStore.list(previous);
                // Reauthenticating the SAME account is needed to upload its pending trips.
                if (previous !== data.user_id && trips.some(trip => trip.state !== 'synced')) {
                    throw new Error('Сначала завершите запись и отправьте поездки прежнего аккаунта. Они не будут автоматически перенесены в Яндекс. Откройте главную страницу или выгрузите их в истории.');
                }
            }
            if (getUserId() !== previous) throw new Error('Аккаунт изменился; повторите вход.');
            if (previous !== data.user_id) rememberOwner(previous);
            localStorage.setItem(TOKEN, data.token); localStorage.setItem(OWNER, data.user_id);
            localStorage.setItem('autoXAK_auth_provider', 'yandex');
            localStorage.setItem('autoXAK_is_guest', 'false');
        };
        if (previous && navigator.locks) {
            await navigator.locks.request(`autoxak-recording:${previous}`, { ifAvailable: true }, async lock => {
                if (!lock) throw new Error('В другой вкладке идёт запись. Сначала завершите её.');
                await apply();
            });
        } else await apply();
    }
    root.AutoXAKSession = { ensureAuthenticated, getUserId, getToken, getProvider, getDeviceId, request, startNewSession, renew, acceptOAuthSession,
        getAuthHeaders: () => getToken() ? { Authorization: `Bearer ${getToken()}` } : {} };
    if ('serviceWorker' in navigator) window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
    });
})(globalThis);
