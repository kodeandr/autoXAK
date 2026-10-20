(function () {
    'use strict';
    const session = AutoXAKSession, store = AutoXAKOfflineStore, core = AutoXAKRecorder;
    const page = document.body.dataset.page, el = id => document.getElementById(id);
    let owner, vehicle, recorder, activeTrip, resultTrip, pendingStop, starting = false, ending = false, completing = false;
    let geoWatch = null, timer = null, wakeLock = null, releaseRecordingLock = null;
    let gpsCount = 0, motionCount = 0, latestGPS = null, lastSyncAt = 0;
    const synchronize = core.createSynchronizer(store, session.request, session.getUserId);
    const profileKey = () => `autoxak_vehicle_v2:${owner}`;
    const visible = (id, show) => { if (el(id)) el(id).hidden = !show; };
    function message(text, type = '') {
        el('message').textContent = text; el('message').className = `notice ${type}`;
        el('message').hidden = !text;
    }
    function recovery(error) {
        if (error.reloginRequired) {
            visible('auth-card', true);
            if (el('auth-status')) el('auth-status').textContent = error.message;
            return;
        }
        if (!error.recoveryRequired) return;
        visible('session-recovery', true);
    }
    function listen(id, event, action) {
        el(id)?.addEventListener(event, eventObject => {
            Promise.resolve().then(() => action(eventObject)).catch(error => { recovery(error); message(error.message, 'error'); });
        });
    }
    function vehicleName(profile) {
        return [profile?.make, profile?.model, profile?.displacement_l ? `${profile.displacement_l} л` : ''].filter(Boolean).join(' ') || 'Автомобиль без названия';
    }
    function networkStatus() {
        el('network-status').textContent = navigator.onLine ? 'Интернет доступен' : 'Без интернета';
    }
    function cachedProfile() {
        try { return JSON.parse(localStorage.getItem(profileKey()) || 'null'); } catch (_) { return null; }
    }
    async function loadProfile() {
        let cached = cachedProfile();
        vehicle = cached?.vehicle || null;
        if (navigator.onLine) {
            try {
                if (cached?.dirty) {
                    await session.request('/api/v2/vehicle', { method: 'PUT', body: JSON.stringify(cached.vehicle) }, owner);
                    cached.dirty = false; localStorage.setItem(profileKey(), JSON.stringify(cached));
                } else {
                    const data = await session.request('/api/v2/vehicle', {}, owner);
                    if (data.vehicle) {
                        vehicle = data.vehicle;
                        localStorage.setItem(profileKey(), JSON.stringify({ vehicle, dirty: false }));
                    }
                }
            } catch (error) { recovery(error); message(`Профиль на телефоне доступен. ${error.message}`, 'warning'); }
        }
        if (el('vehicle-name')) el('vehicle-name').textContent = vehicle ? vehicleName(vehicle) : 'Автомобиль не выбран';
    }
    async function sync() {
        if (!owner || !navigator.onLine) return;
        const run = async () => {
            const cached = cachedProfile();
            if (cached?.dirty) {
                await session.request('/api/v2/vehicle', { method: 'PUT', body: JSON.stringify(cached.vehicle) }, owner);
                // A newer edit in another tab must remain dirty.
                if (JSON.stringify(cachedProfile()) === JSON.stringify(cached)) {
                    localStorage.setItem(profileKey(), JSON.stringify({ ...cached, dirty: false }));
                }
            }
            return synchronize();
        };
        // One uploader per device, including multiple open pages. Server replay is also idempotent.
        if (navigator.locks) await navigator.locks.request(`autoxak-sync:${owner}`, run);
        else await run();
        if (resultTrip) {
            resultTrip = await store.get(owner, resultTrip.trip_id) || resultTrip;
            renderResult();
        }
    }
    async function acquireRecordingLock() {
        if (!navigator.locks) throw new Error('Для надёжной записи нужен современный браузер с поддержкой Web Locks. Откройте приложение по HTTPS в актуальном Chrome или Safari.');
        return new Promise((resolve, reject) => {
            navigator.locks.request(`autoxak-recording:${owner}`, { ifAvailable: true }, async lock => {
                if (!lock) { resolve(null); return; }
                await new Promise(release => resolve(release));
            }).catch(reject);
        });
    }
    function textElement(tag, text, className = '') {
        const node = document.createElement(tag); node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function fuelDescription(trip) {
        const result = trip.result || trip;
        const estimate = result.fuel_estimate;
        if (!estimate) return { amount: '— л', description: trip.state === 'awaiting_control'
            ? 'Запись завершена. Осталось отправить её на расчёт.' : 'Запись сохранена. Ожидаем отправку и расчёт.' };
        const statuses = {
            profile_incomplete: 'Для расчёта не хватает характеристик автомобиля.',
            unsupported_vehicle: 'Модель расхода пока доступна только для бензиновых автомобилей.',
            no_usable_gps: 'Недостаточно непрерывных GPS-данных с известной скоростью и точностью.',
            invalid_trip: 'Поездка слишком короткая или её временные границы некорректны.',
            model_unavailable: 'Модель временно недоступна. Исходная поездка сохранена.'
        };
        if (estimate.estimated_fuel_l === null || estimate.estimated_fuel_l === undefined) {
            return { amount: '— л', description: statuses[estimate.status] || 'Расход для этой записи недоступен.' };
        }
        const complete = estimate.complete_trip_fuel_l !== null && estimate.complete_trip_fuel_l !== undefined;
        return { amount: `${Number(estimate.estimated_fuel_l).toFixed(3)} л`, description: complete
            ? 'Оценочный расход за всю записанную поездку.' : 'Оценочный расход только на пригодных участках. Расход за всю поездку неизвестен.' };
    }
    function resultContent(trip) {
        const box = document.createElement('div'), info = fuelDescription(trip), detail = trip.result || trip;
        const estimate = detail.fuel_estimate;
        box.append(textElement('div', info.amount, 'trip-value'), textElement('p', info.description, 'muted'));
        if (trip.end_reason === 'interrupted' || detail.end_reason === 'interrupted') {
            box.append(textElement('p', 'Запись прервалась. Время окончания ограничено последними сохранёнными данными; последующее время не считается работой двигателя.', 'notice warning'));
        }
        if (estimate) {
            if (typeof estimate.coverage_fraction === 'number') {
                box.append(textElement('p', `Модель оценила ${(100 * estimate.coverage_fraction).toFixed(1)}% времени записи.`, 'muted'));
            }
            if (typeof estimate.distance_km === 'number') box.append(textElement('p', `Расстояние на пригодных участках: ${estimate.distance_km.toFixed(2)} км.`, 'muted'));
            if (estimate.missing_profile_fields?.length) {
                const names = { fuel_type: 'тип топлива', mass_kg: 'масса', displacement_l: 'объём двигателя', turbo: 'турбонаддув' };
                box.append(textElement('p', 'Нужно указать: ' + estimate.missing_profile_fields.map(name => names[name] || name).join(', ') + '. В этой поездке сохранены характеристики на момент старта.', 'muted'));
            }
            box.append(textElement('p', 'Это экспериментальная оценка, а не измерение бензина в баке. Точность на вашем автомобиле ещё проверяется.', 'muted'));
            if (estimate.quality_flags?.includes('outside_training_range')) {
                box.append(textElement('p', 'Часть режимов движения или характеристик выходит за диапазон обучения. К оценке следует относиться осторожно.', 'notice warning'));
            }
            const diagnostics = document.createElement('details');
            diagnostics.append(textElement('summary', 'Данные расчёта'));
            diagnostics.append(textElement('p', `Версия модели: ${estimate.model_version || 'не указана'}`, 'muted'));
            diagnostics.append(textElement('p', `Проверки качества: ${(estimate.quality_flags || []).join(', ') || 'дополнительных замечаний нет'}`, 'muted'));
            box.append(diagnostics);
        }
        return box;
    }
    async function dashboard() {
        const local = await store.list(owner);
        const latest = local.find(trip => trip.state !== 'recording');
        if (latest) {
            const info = fuelDescription(latest);
            el('last-fuel').textContent = info.amount; el('last-description').textContent = info.description;
        }
    }
    async function requestWakeLock() {
        if (recorder?.active && document.visibilityState === 'visible' && navigator.wakeLock) {
            try { wakeLock = await navigator.wakeLock.request('screen'); } catch (_) { /* User can still record without this optional capability. */ }
        }
    }
    function motionEvent(event) {
        if (!recorder?.active) return;
        const point = core.motionPoint(event, Date.now(), performance.timeOrigin);
        if ([point.ax, point.ay, point.az, point.gx, point.gy, point.gz].every(value => value === null)) return;
        if (recorder.add('motion', point)) {
            motionCount += 1;
            if (motionCount === 1 || motionCount % 50 === 0) {
                el('motion-status').textContent = `Датчики движения: ${motionCount} измерений · гироскоп ${point.gx === null && point.gy === null && point.gz === null ? 'недоступен' : 'доступен'}`;
            }
        }
    }
    function gpsEvent(position) {
        if (!recorder?.active) return;
        const point = core.gpsPoint(position);
        // A cached fix before Start or an impossible future fix cannot describe this session.
        if (point.timestamp_ms > Date.now() + 1 || !recorder.add('gps', point)) return;
        latestGPS = point; gpsCount += 1;
        el('gps-count').textContent = gpsCount;
        el('gps-status').textContent = `GPS: точность ${point.accuracy_m === null ? 'неизвестна' : `±${Math.round(point.accuracy_m)} м`}. ${point.speed_mps === null ? 'Скорость не получена.' : ''}`;
    }
    async function start() {
        if (starting || recorder?.active) return;
        starting = true; el('start-trip').disabled = true;
        try {
            if (owner !== session.getUserId()) throw new Error('Аккаунт изменился в другой вкладке. Обновите страницу перед записью.');
            if (!vehicle) throw new Error('Сначала сохраните автомобиль. Неизвестные характеристики можно оставить пустыми.');
            if (!window.isSecureContext || !navigator.geolocation) throw new Error('Для GPS нужен HTTPS и доступ к местоположению в браузере.');
            // iOS permission must be requested directly from the button gesture, before other awaits.
            let motionPermission = 'unavailable';
            if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
                try { motionPermission = await DeviceMotionEvent.requestPermission(); } catch (_) { motionPermission = 'denied'; }
            } else if (typeof DeviceMotionEvent !== 'undefined') motionPermission = 'granted';
            releaseRecordingLock = await acquireRecordingLock();
            if (!releaseRecordingLock) throw new Error('Поездка уже записывается в другой вкладке. Завершите её там.');
            const pending = (await store.list(owner)).find(trip => trip.state === 'awaiting_control' || trip.state === 'recording');
            if (pending) throw new Error('Сначала завершите предыдущую запись. Обновите страницу, чтобы продолжить.');
            const trip = core.createTrip(owner, vehicle, { app_version: '2.0.0-recording', user_agent: navigator.userAgent.slice(0, 1000) });
            await store.create(trip);
            activeTrip = trip; recorder = new core.Recorder(store, trip); pendingStop = null;
            gpsCount = 0; motionCount = 0; latestGPS = null;
            visible('start-card', false); visible('dashboard', false); visible('active-card', true);
            visible('result-card', false); visible('control-card', false); visible('storage-error', false);
            el('gps-count').textContent = '0'; el('speed').textContent = '—'; el('gps-status').textContent = 'Ожидаем GPS…';
            el('motion-status').textContent = motionPermission === 'granted' ? 'Ожидаем датчики движения…' : 'Датчики движения недоступны. GPS записывается отдельно.';
            if (motionPermission === 'granted') window.addEventListener('devicemotion', motionEvent);
            geoWatch = navigator.geolocation.watchPosition(gpsEvent, error => {
                el('gps-status').textContent = error.code === 1 ? 'Доступ к GPS запрещён. Разрешите местоположение в настройках браузера.' : 'GPS недоступен. Пропуск не считается остановкой.';
                latestGPS = null; el('speed').textContent = '—';
            }, { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 });
            timer = setInterval(tick, 1000); tick();
            navigator.storage?.persist?.().catch(() => {});
            await requestWakeLock();
            message('Запись началась. Телефон сохраняет измерения во время поездки.');
            sync().catch(error => { el('save-status').textContent = `Данные на телефоне. ${error.message}`; });
        } catch (error) {
            if (!recorder?.active) { releaseRecordingLock?.(); releaseRecordingLock = null; }
            throw error;
        } finally { starting = false; el('start-trip').disabled = false; }
    }
    function tick() {
        if (!recorder?.active) return;
        const elapsed = Math.max(0, Date.now() - activeTrip.started_at_ms);
        const seconds = Math.floor(elapsed / 1000);
        el('timer').textContent = `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
        el('speed').textContent = latestGPS && Date.now() - latestGPS.timestamp_ms <= 10000 && latestGPS.speed_mps !== null
            ? (latestGPS.speed_mps * 3.6).toFixed(0) : '—';
        if (latestGPS && Date.now() - latestGPS.timestamp_ms > 10000) el('gps-status').textContent = 'Свежих GPS-данных нет. Пропуск не считается остановкой.';
        if (elapsed >= core.MAX_DURATION_MS) { stop('duration_limit').catch(error => message(error.message, 'error')); return; }
        recorder.flush().then(() => { el('save-status').textContent = navigator.onLine ? 'Измерения сохранены на телефоне' : 'Измерения сохранены на телефоне · отправим после восстановления связи'; })
            .catch(() => { stop('interrupted').catch(error => message(error.message, 'error')); });
        if (Date.now() - lastSyncAt >= 15000) {
            lastSyncAt = Date.now();
            sync().catch(error => { el('save-status').textContent = `Запись на телефоне. ${error.message}`; });
        }
    }
    function stopSensors() {
        window.removeEventListener('devicemotion', motionEvent);
        if (geoWatch !== null) navigator.geolocation.clearWatch(geoWatch);
        geoWatch = null; clearInterval(timer); timer = null;
        wakeLock?.release().catch(() => {}); wakeLock = null;
    }
    async function stop(reason = 'user') {
        if (!recorder || ending) return;
        ending = true;
        // Preserve the first stop time, including if a storage failure requires a retry.
        pendingStop = pendingStop || { now: Date.now(), reason };
        recorder.active = false; stopSensors(); el('stop-trip').disabled = true;
        try {
            activeTrip = await recorder.stop(pendingStop.now, pendingStop.reason);
            releaseRecordingLock?.(); releaseRecordingLock = null;
            visible('storage-error', false); showControl(activeTrip);
        } catch (error) {
            visible('storage-error', true); visible('active-card', false);
            message(`Не удалось сохранить конец поездки: ${error.message}`, 'error');
        } finally { ending = false; el('stop-trip').disabled = false; }
    }
    function showControl(trip) {
        activeTrip = trip;
        visible('active-card', false); visible('start-card', false); visible('control-card', true); visible('dashboard', false);
        el('control-form').reset();
        el('completion-note').textContent = trip.end_reason === 'interrupted'
            ? 'Запись прервалась и закрыта по последнему сохранённому измерению. Контрольные показания можно добавить только за этот же интервал.'
            : 'Измерения остановлены. Можно добавить показания бортового компьютера за ту же поездку.';
    }
    async function complete(withControl) {
        if (!activeTrip || completing) return;
        let control = null;
        if (withControl) {
            const distance = Number(el('control-distance').value), average = Number(el('control-average').value);
            if (!(distance > 0 && distance <= 5000 && average > 0 && average <= 100) || !el('control-interval').checked) {
                throw new Error('Укажите оба положительных показания и подтвердите одинаковый интервал, либо выберите расчёт без них.');
            }
            control = { distance_km: distance, average_l_per_100km: average, source: 'trip_computer', same_interval: true };
        }
        completing = true;
        try {
        resultTrip = await core.prepareCompletion(store, owner, activeTrip.trip_id, control);
        visible('control-card', false); visible('result-card', true); renderResult();
        message(navigator.onLine ? 'Поездка сохранена. Отправляем для расчёта…' : 'Поездка сохранена на телефоне. Расчёт появится после отправки.');
        try { await sync(); if (resultTrip.state === 'synced') message('Поездка сохранена на сервере.'); }
        catch (error) { recovery(error); message(`Поездка остаётся на телефоне. ${error.message}`, 'warning'); }
        } finally { completing = false; }
    }
    function renderResult() {
        if (!resultTrip || !el('result-content')) return;
        el('result-content').replaceChildren(resultContent(resultTrip));
        visible('retry-sync', resultTrip.state !== 'synced');
    }
    function download(data, name) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
    async function exportTrip(id) {
        const local = await store.get(owner, id);
        const data = local ? await store.exportTrip(owner, id) : await session.request(`/api/v2/trips/${encodeURIComponent(id)}/export`, {}, owner);
        download(data, `autoxak-${id}.json`);
    }
    async function history() {
        const local = await store.list(owner), byId = new Map(local.map(trip => [trip.trip_id, trip]));
        if (navigator.onLine) {
            try {
                let offset = 0, batch;
                do {
                const data = await session.request(`/api/v2/trips?limit=200&offset=${offset}`, {}, owner);
                batch = data.trips || [];
                for (const trip of batch) {
                    const cached = byId.get(trip.trip_id);
                    byId.set(trip.trip_id, cached ? { ...cached, result: trip.fuel_estimate ? trip : cached.result } : trip);
                }
                offset += batch.length;
                } while (batch.length === 200);
            } catch (error) { recovery(error); message(`Показаны записи с этого телефона. ${error.message}`, 'warning'); }
        }
        const trips = [...byId.values()].sort((a, b) => b.started_at_ms - a.started_at_ms);
        el('trip-count').textContent = trips.length;
        el('trip-list').replaceChildren();
        if (!trips.length) el('trip-list').append(textElement('p', 'Поездок пока нет. Начните запись на главном экране.', 'muted'));
        for (const trip of trips) {
            const card = textElement('article', '', 'card');
            card.append(textElement('div', new Date(trip.started_at_ms).toLocaleString('ru-RU'), 'trip-date'));
            const statuses = { synced: 'Отправлена', pending: 'Ожидает отправки', recording: 'Запись не завершена', awaiting_control: 'Ожидает завершения' };
            card.append(textElement('div', `${vehicleName(trip.vehicle)} · ${statuses[trip.state] || 'На сервере'}`, 'trip-status'));
            card.append(resultContent(trip));
            const actions = textElement('div', '', 'actions');
            const exportButton = textElement('button', 'Выгрузить всю поездку', 'secondary');
            exportButton.addEventListener('click', () => exportTrip(trip.trip_id).catch(error => message(error.message, 'error')));
            actions.append(exportButton);
            if (trip.state === 'awaiting_control' || trip.state === 'recording') {
                const link = textElement('a', 'Завершить на главном экране →'); link.href = '/'; actions.append(link);
            }
            card.append(actions); el('trip-list').append(card);
        }
    }
    function populateProfile() {
        const values = { make: vehicle?.make, model: vehicle?.model, year: vehicle?.year, transmission: vehicle?.transmission,
            mass: vehicle?.mass_kg, displacement: vehicle?.displacement_l, 'fuel-type': vehicle?.fuel_type || 'unknown',
            turbo: typeof vehicle?.turbo === 'boolean' ? vehicle.turbo ? 'yes' : 'no' : 'unknown' };
        for (const [id, value] of Object.entries(values)) el(id).value = value ?? '';
    }
    async function saveProfile() {
        const number = id => el(id).value === '' ? null : Number(el(id).value);
        vehicle = { vehicle_id: vehicle?.vehicle_id || crypto.randomUUID(), make: el('make').value.trim() || null,
            model: el('model').value.trim() || null, year: number('year'), transmission: el('transmission').value.trim() || null,
            fuel_type: el('fuel-type').value, mass_kg: number('mass'), displacement_l: number('displacement'),
            turbo: el('turbo').value === 'unknown' ? null : el('turbo').value === 'yes' };
        localStorage.setItem(profileKey(), JSON.stringify({ vehicle, dirty: true }));
        message('Автомобиль сохранён на телефоне.');
        try { await sync(); if (navigator.onLine) message('Автомобиль сохранён. Можно начинать поездку.'); }
        catch (error) { message(`Автомобиль сохранён на телефоне. ${error.message}`, 'warning'); }
    }
    async function initialise() {
        networkStatus(); visible('retry-connection', false);
        try {
            const auth = await session.ensureAuthenticated(); owner = auth.userId;
            if (navigator.onLine) {
                try { await session.renew(); } catch (error) { recovery(error); message(error.message, 'warning'); }
            }
            await store.db(); await loadProfile();
            if (page === 'home') {
                const release = await acquireRecordingLock();
                if (release) {
                    try { await core.recoverInterrupted(store, owner); } finally { release(); }
                    const pending = (await store.list(owner)).find(trip => trip.state === 'awaiting_control');
                    if (pending) showControl(pending);
                } else message('В другой вкладке идёт запись. Завершайте её в той вкладке.', 'warning');
                el('start-trip').disabled = !release;
                await dashboard();
            } else if (page === 'setup') { populateProfile(); el('save-vehicle').disabled = false; }
            else if (page === 'history') await history();
            sync().then(() => page === 'history' ? history() : page === 'home' ? dashboard() : null)
                .catch(error => { recovery(error); message(`Локальные записи сохранены. ${error.message}`, 'warning'); });
        } catch (error) {
            recovery(error);
            message(`Не удалось подготовить приложение. ${error.message}`, 'error');
            visible('retry-connection', true);
        }
    }
    async function loginYandex() {
        const currentOwner = session.getUserId();
        const pending = currentOwner ? (await store.list(currentOwner)).filter(trip => trip.state !== 'synced') : [];
        const sameAccountRelogin = session.getProvider() === 'yandex';
        if (recorder?.active || pending.some(trip => trip.state === 'recording') || (!sameAccountRelogin && pending.length)) {
            throw new Error('Сначала завершите запись и отправьте поездки. Их можно выгрузить в истории. Смена аккаунта не переносит поездки автоматически.');
        }
        if (!currentOwner || !session.getToken()) { location.assign('/api/v1/auth/yandex/login'); return; }
        try {
            const data = await session.request('/api/v1/auth/yandex/start', { method: 'POST' }, currentOwner);
            const target = new URL(data.authorization_url);
            if (target.protocol !== 'https:' || target.hostname !== 'oauth.yandex.ru') throw new Error('Получен некорректный адрес входа.');
            location.assign(target.href);
        } catch (error) {
            if (error.reloginRequired || error.status === 401) location.assign('/api/v1/auth/yandex/login');
            else throw error;
        }
    }
    async function exportLocalBackup() {
        const legacy = {};
        for (const key of ['autoXAK_offline_trips', 'autoXAK_trip_details_map', 'autoXAK_local_history', 'autoXAK_car_name']) {
            const value = localStorage.getItem(key);
            if (value !== null) { try { legacy[key] = JSON.parse(value); } catch (_) { legacy[key] = value; } }
        }
        const owners = new Set([session.getUserId(), ...JSON.parse(localStorage.getItem('autoxak_previous_owners_v2') || '[]')].filter(Boolean));
        const trips = [];
        for (const previousOwner of owners) for (const trip of await store.list(previousOwner)) {
            trips.push(await store.exportTrip(previousOwner, trip.trip_id));
        }
        const legacyIndexedDB = await store.exportLegacy();
        download({ format: 'autoxak-local-backup-v2', exported_at_ms: Date.now(), legacy, legacy_indexeddb: legacyIndexedDB, trips }, 'autoxak-local-backup.json');
    }
    // Explicit migration only: never attach an old owner's queued recordings to a new identity.
    const recoveryBox = textElement('section', '', 'notice warning');
    recoveryBox.id = 'session-recovery'; recoveryBox.hidden = true;
    recoveryBox.append(textElement('p', 'Старая сессия не может подключиться. Сначала выгрузите локальные записи. Новая сессия сохранит их на телефоне, но не перенесёт в другой аккаунт.'));
    const backupButton = textElement('button', 'Выгрузить старые локальные записи', 'secondary');
    backupButton.addEventListener('click', () => exportLocalBackup().catch(error => message(error.message, 'error')));
    const resetButton = textElement('button', 'Выгрузить и начать новую сессию', 'secondary');
    resetButton.addEventListener('click', async () => {
        if (recorder?.active || releaseRecordingLock || (owner && (await store.list(owner)).some(trip => trip.state === 'recording'))) {
            message('Сначала завершите запись и выгрузите её.', 'warning'); return;
        }
        try { await exportLocalBackup(); await session.startNewSession(); location.reload(); }
        catch (error) { message(error.message, 'error'); }
    });
    const recoveryActions = textElement('div', '', 'actions'); recoveryActions.append(backupButton, resetButton);
    recoveryBox.append(recoveryActions); el('message').after(recoveryBox);
    const authCard = textElement('section', '', 'card'); authCard.id = 'auth-card';
    authCard.append(textElement('h2', 'Аккаунт'));
    const authStatus = textElement('p', session.getProvider() === 'yandex' ? 'Вход через Яндекс. Поездки связаны с этим аккаунтом.' : 'Сейчас поездки связаны с этим устройством. Вход через Яндекс позволяет открывать серверную историю на других устройствах.', 'muted');
    authStatus.id = 'auth-status'; authCard.append(authStatus);
    const loginButton = textElement('button', session.getProvider() === 'yandex' ? 'Войти через Яндекс снова' : 'Войти через Яндекс', 'secondary');
    loginButton.id = 'yandex-login'; loginButton.addEventListener('click', () => loginYandex().catch(error => { recovery(error); message(error.message, 'error'); }));
    authCard.append(loginButton); document.querySelector('main').append(authCard);
    // Prevent form default synchronously, while user-gesture sensor permission is still available.
    el('start-trip')?.addEventListener('click', () => start().catch(error => message(error.message, 'error')));
    listen('stop-trip', 'click', () => stop());
    el('control-form')?.addEventListener('submit', event => { event.preventDefault(); complete(true).catch(error => message(error.message, 'error')); });
    listen('skip-control', 'click', () => complete(false));
    listen('retry-save', 'click', () => stop(pendingStop?.reason || 'interrupted'));
    listen('retry-sync', 'click', async () => { await sync(); message('Отправка завершена.'); });
    listen('export-result', 'click', () => exportTrip(resultTrip.trip_id));
    listen('export-emergency', 'click', async () => {
        await recorder.chain;
        const data = await store.exportTrip(owner, activeTrip.trip_id);
        data.unsaved_after_storage_error = recorder.unsaved(); data.stopped_at_ms = pendingStop?.now;
        download(data, `autoxak-recovery-${activeTrip.trip_id}.json`);
    });
    listen('new-trip', 'click', async () => {
        visible('result-card', false); resultTrip = null; activeTrip = null; recorder = null;
        const pending = (await store.list(owner)).find(trip => trip.state === 'awaiting_control');
        if (pending) showControl(pending);
        else { visible('start-card', true); visible('dashboard', true); await dashboard(); }
    });
    listen('retry-connection', 'click', initialise);
    listen('sync-history', 'click', async () => { await sync(); await history(); });
    el('vehicle-form')?.addEventListener('submit', event => { event.preventDefault(); saveProfile().catch(error => message(error.message, 'error')); });
    listen('another-vehicle', 'click', () => { vehicle = null; populateProfile(); message('Новый автомобиль получит отдельный идентификатор после сохранения.'); });
    window.addEventListener('online', () => { networkStatus(); sync().then(() => page === 'history' ? history() : null).catch(error => message(error.message, 'warning')); });
    window.addEventListener('offline', networkStatus);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden' && recorder?.active) recorder.flush().catch(() => stop('interrupted'));
        else requestWakeLock();
    });
    window.addEventListener('beforeunload', event => {
        if (recorder?.active || (recorder && (recorder.gps.length || recorder.motion.length))) { event.preventDefault(); event.returnValue = ''; }
    });
    initialise();
})();
