/** Pending trips are acknowledged only after the IndexedDB transaction commits. */
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
    function saveDraftTrip(payload) {
        return transaction('readwrite', store => store.put({...payload, created_at: Date.now(), status: 'RECORDING'}));
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
        return transaction('readwrite', store => store.put({session_id, user_id: AutoXAKSession.getUserId(), value}), CONTROLS);
    }
    async function syncControls(owner) {
        const controls = await transaction('readonly', store => store.getAll(), CONTROLS);
        for (const control of controls || []) {
            if (control.user_id !== owner || AutoXAKSession.getUserId() !== owner) continue;
            const response = await fetch(`/api/v1/trips/${encodeURIComponent(control.session_id)}/board-computer`, {method: 'POST',
                headers: {'Content-Type': 'application/json', ...AutoXAKSession.getAuthHeaders()},
                body: JSON.stringify({board_computer_consumption_l100km: control.value})});
            if (response.ok) await transaction('readwrite', store => store.delete(control.session_id), CONTROLS);
        }
    }
    async function countPendingTrips() { return (await getPendingTrips()).length; }

    async function synchronize(callbacks) {
        if (!navigator.onLine) return;
        const owner = AutoXAKSession.getUserId();
        for (const trip of await getPendingTrips()) {
            if (owner !== AutoXAKSession.getUserId()) break;
            try {
                const {created_at, status, attempts, ...payload} = trip;
                const controller = new AbortController();
                const timeout = setTimeout(()=>controller.abort(),15000);
                let response;
                try { response = await fetch('/api/v1/telemetry/session', {method: 'POST',
                    headers: {'Content-Type': 'application/json', ...AutoXAKSession.getAuthHeaders()}, body: JSON.stringify(payload), signal:controller.signal});
                } finally {clearTimeout(timeout);}
                if (!response.ok) {
                    // Retain a rejected record without preventing independent trips
                    // from syncing. Stop on authentication/rate/server failures.
                    if ([400,409,413,422].includes(response.status)) continue;
                    break;
                }
                const result = await response.json();
                await removePendingTrip(trip.session_id);
                if (callbacks.onTripSynced) await callbacks.onTripSynced(result, trip);
            } catch (_) { break; }
        }
        try { await syncControls(owner); } catch (_) {}
        if (callbacks.onSyncComplete) await callbacks.onSyncComplete();
    }
    function syncPendingTrips(callbacks = {}) {
        if (!syncing) syncing = synchronize(callbacks).finally(() => { syncing = null; });
        return syncing;
    }
    return {reassignOwner, savePendingControl, saveDraftTrip, recoverInterruptedTrips, savePendingTrip, removePendingTrip, getPendingTrips, countPendingTrips, syncPendingTrips};
})();
