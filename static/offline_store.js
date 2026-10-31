
// Авто-сжатие пакета перед записью в IndexedDB (защита от переполнения квоты смартфона)
function _compactTripForLocalStore(payload) {
    if (!payload || typeof payload !== 'object') return payload;
    const copy = Object.assign({}, payload);
    if (copy.raw_recording && Array.isArray(copy.telemetry_stream) && copy.telemetry_stream.length >= 50) {
        delete copy.raw_recording;
    }
    if (Array.isArray(copy.telemetry_stream) && copy.telemetry_stream.length > 3000) {
        copy.telemetry_stream = copy.telemetry_stream.filter((_, idx) => idx % 2 === 0);
    }
    return copy;
}

﻿/** Pending trips are acknowledged only after the IndexedDB transaction commits. */
const AutoXAKOfflineStore = window.AutoXAKOfflineStore = (() => {
    const DB_NAME = 'autoXAK_storage', STORE = 'pending_trips', CONTROLS = 'pending_controls';
    let opening = null, syncing = null;

    function openDB() {
        if (opening) return opening;
        opening = new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, 2);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(CONTROLS)) db.createObjectStore(CONTROLS, {keyPath: "session_id"});
                if (!db.objectStoreNames.contains(STORE)) {
                    const store = db.createObjectStore(STORE, {keyPath: 'session_id'});
                    store.createIndex('status', 'status');
                    store.createIndex('created_at', 'created_at');
                }
            };
            request.onsuccess = () => {
                const db = request.result;
                db.onversionchange = () => { db.close(); opening = null; };
                resolve(db);
            };
            request.onerror = () => { opening = null; reject(request.error); };
            request.onblocked = () => { opening = null; reject(new Error('IndexedDB is blocked by another tab')); };
        });
        return opening;
    }

    async function transaction(mode, operation, name = STORE) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(name, mode);
            let result;
            const request = operation(tx.objectStore(name));
            request.onsuccess = () => { result = request.result; };
            tx.oncomplete = () => resolve(result);
            tx.onerror = () => reject(tx.error || request.error);
            tx.onabort = () => reject(tx.error || new Error('IndexedDB transaction aborted'));
        });
    }

    function savePendingTrip(payload) {
        return transaction('readwrite', store => store.put({...payload, created_at: Date.now(), status: 'PENDING'}));
    }
    async function saveDraftTrip(payload) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE, 'readwrite'), store = tx.objectStore(STORE);
            const request = store.get(payload.session_id);
            request.onsuccess = () => {
                // A delayed write from another tab must not undo finalization.
                if (request.result?.status !== 'PENDING') store.put({...payload, created_at:Date.now(), status:'RECORDING'});
            };
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error || new Error('Draft save failed'));
        });
    }
    async function recoverInterruptedTrips() {
        const drafts = await transaction('readonly', store => store.index('status').getAll('RECORDING'));
        let active = new Set();
        if (navigator.locks) {
            try {
                const locks = await navigator.locks.query();
                active = new Set([...locks.held,...locks.pending].map(lock=>lock.name));
            } catch (_) {return;}
        }
        for (const draft of drafts || []) {
            if (active.has('autoxak-recorder:'+draft.session_id)) continue;
            if (draft.user_id === AutoXAKSession.getUserId() && (draft.raw_recording || (Array.isArray(draft.telemetry_stream) && draft.telemetry_stream.length >= 50))) await savePendingTrip(draft);
        }
    }
    function removePendingTrip(id) { return transaction('readwrite', store => store.delete(id)); }
    async function getPendingTrips() {
        const all = await transaction('readonly', store => store.index('status').getAll('PENDING'));
        const owner = AutoXAKSession.getUserId();
        return (all || []).filter(trip => trip.user_id === owner);
    }
    async function reassignOwner(previous, next) {
        if (AutoXAKSession.getUserId() !== previous) throw new Error('Session changed during account merge');
        const db = await openDB();
        await new Promise((resolve, reject) => {
            const tx = db.transaction([STORE, CONTROLS], 'readwrite');
            for (const name of [STORE, CONTROLS]) {
                const request = tx.objectStore(name).openCursor();
                request.onsuccess = () => {
                    const cursor = request.result;
                    if (!cursor) return;
                    if (cursor.value.user_id === previous) cursor.update({...cursor.value, user_id: next});
                    cursor.continue();
                };
            }
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error || new Error('Account merge storage failed'));
        });
    }
    async function savePendingControl(session_id, value) {
        const revision = globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}:${Math.random()}`;
        return transaction('readwrite', store => store.put({session_id, user_id: AutoXAKSession.getUserId(), value, revision}), CONTROLS);
    }
    async function acknowledgeControl(control) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(CONTROLS, 'readwrite'), store = tx.objectStore(CONTROLS);
            const request = store.get(control.session_id);
            request.onsuccess = () => {
                const current = request.result;
                if (current && current.user_id === control.user_id && current.revision === control.revision && current.value === control.value) store.delete(control.session_id);
            };
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => reject(tx.error || new Error('Control acknowledgement failed'));
        });
    }
    async function syncControls(owner) {
        const controls = await transaction('readonly', store => store.getAll(), CONTROLS);
        for (const control of controls || []) {
            if (control.user_id !== owner || AutoXAKSession.getUserId() !== owner) continue;
            const {response} = await AutoXAKSession.fetchJSON(`/api/v1/trips/${encodeURIComponent(control.session_id)}/board-computer`, {method: 'POST',
                headers: {'Content-Type': 'application/json', ...AutoXAKSession.getAuthHeaders()},
                body: JSON.stringify({board_computer_consumption_l100km: control.value})});
            if (response.ok) await acknowledgeControl(control);
            else if ([401,403,429].includes(response.status) || response.status >= 500) break;
        }
    }
    async function countPendingTrips() { return (await getPendingTrips()).length; }

    function rememberSyncedTrip(result, trip) {
        if (AutoXAKSession.getUserId() !== trip.user_id) return;
        try {
            const offline = JSON.parse(localStorage.getItem('autoXAK_offline_trips') || '[]');
            localStorage.setItem('autoXAK_offline_trips', JSON.stringify(offline.filter(item => item.session_id !== trip.session_id)));
            const map = JSON.parse(localStorage.getItem('autoXAK_trip_details_map') || '{}');
            const details = {user_id:trip.user_id, session_id:result.session_id,
                created_at:new Date(trip.raw_recording?.started_at_ms ?? trip.created_at ?? Date.now()).toISOString(),
                duration_seconds:result.duration_seconds, distance_km:result.distance_km,
                total_savings_rub:result.cost_savings_rub, hard_accel_count:result.harsh_accel_count,
                hard_brake_count:result.harsh_brake_count, bumps_count:result.road_anomalies_count,
                idle_minutes:Math.round((result.traffic_jam_seconds || 0)/60), capture_quality:result.metrics?.capture_quality,math_status:result.metrics?.math_status};
            if (trip.session_id !== result.session_id) delete map[trip.session_id];
            map[result.session_id] = details;
            localStorage.setItem('autoXAK_trip_details_map', JSON.stringify(map));
            const archive = JSON.parse(localStorage.getItem('autoXAK_local_history') || '[]');
            localStorage.setItem('autoXAK_local_history', JSON.stringify([details,...archive.filter(item=>item.user_id===trip.user_id && ![trip.session_id,result.session_id].includes(item.session_id))]));
        } catch (_) { /* The server acknowledgement remains authoritative if cache storage is full. */ }
    }

    async function synchronize(callbacks) {
        if (!navigator.onLine) return;
        const owner = AutoXAKSession.getUserId();
        for (const trip of await getPendingTrips()) {
            if (owner !== AutoXAKSession.getUserId()) break;
            try {
                const {created_at, status, attempts, ...payload} = trip;
                const {response, data:result} = await AutoXAKSession.fetchJSON('/api/v1/telemetry/session', {method: 'POST',
                    headers: {'Content-Type': 'application/json', ...AutoXAKSession.getAuthHeaders()}, body: JSON.stringify(payload)}, 15000);
                if (!response.ok) {
                    // Retain a rejected record without preventing independent trips
                    // from syncing. Stop on authentication/rate/server failures.
                    if ([400,409,413,422].includes(response.status)) continue;
                    break;
                }
                await removePendingTrip(trip.session_id);
                rememberSyncedTrip(result, trip);
                if (owner === AutoXAKSession.getUserId() && callbacks.onTripSynced) await callbacks.onTripSynced(result, trip);
            } catch (_) { break; }
        }
        try { await syncControls(owner); } catch (_) {}
        if (owner === AutoXAKSession.getUserId() && callbacks.onSyncComplete) await callbacks.onSyncComplete();
    }
    function syncPendingTrips(callbacks = {}) {
        if (!syncing) {
            const owner = AutoXAKSession.getUserId();
            const run = () => synchronize(callbacks);
            syncing = (navigator.locks ? navigator.locks.request('autoxak-sync:'+owner, run) : run()).finally(() => { syncing = null; });
        }
        return syncing;
    }
    return {rememberSyncedTrip, reassignOwner, savePendingControl, saveDraftTrip, recoverInterruptedTrips, savePendingTrip, removePendingTrip, getPendingTrips, countPendingTrips, syncPendingTrips};
})();
