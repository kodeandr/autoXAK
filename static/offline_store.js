/* Every chunk and its sequence number commit in the same IndexedDB transaction. */
(function (root) {
    'use strict';
    let opening;
    function db() {
        if (opening) return opening;
        opening = new Promise((resolve, reject) => {
            const request = indexedDB.open('autoxak_recorder_v2', 1);
            request.onupgradeneeded = () => {
                const connection = request.result;
                connection.createObjectStore('trips', { keyPath: ['owner_id', 'trip_id'] }).createIndex('owner', 'owner_id');
                connection.createObjectStore('chunks', { keyPath: ['owner_id', 'trip_id', 'seq'] });
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => { opening = null; reject(request.error); };
            request.onblocked = () => reject(new Error('Закройте другие вкладки АвтоХАК и повторите.'));
        });
        return opening;
    }
    async function transact(stores, mode, execute) {
        const connection = await db();
        return new Promise((resolve, reject) => {
            const tx = connection.transaction(stores, mode);
            let value, error;
            const done = result => { value = result; };
            tx.oncomplete = () => resolve(value);
            tx.onabort = () => reject(error || tx.error || new Error('Запись на телефон прервана.'));
            tx.onerror = () => { error = tx.error; };
            const fail = caught => { error = caught; tx.abort(); };
            try { execute(tx, done, fail); } catch (caught) { fail(caught); }
        });
    }
    function create(trip) {
        return transact(['trips'], 'readwrite', (tx, done) => {
            tx.objectStore('trips').add(trip); done(trip);
        });
    }
    function get(owner, id) {
        return transact(['trips'], 'readonly', (tx, done) => {
            tx.objectStore('trips').get([owner, id]).onsuccess = event => done(event.target.result);
        });
    }
    function list(owner) {
        return transact(['trips'], 'readonly', (tx, done) => {
            tx.objectStore('trips').index('owner').getAll(owner).onsuccess = event =>
                done(event.target.result.sort((a, b) => b.started_at_ms - a.started_at_ms));
        });
    }
    function patch(owner, id, changes) {
        return transact(['trips'], 'readwrite', (tx, done) => {
            const store = tx.objectStore('trips');
            store.get([owner, id]).onsuccess = event => {
                const trip = event.target.result;
                if (!trip) { tx.abort(); return; }
                Object.assign(trip, changes);
                store.put(trip); done(trip);
            };
        });
    }
    function append(owner, id, payload) {
        return transact(['trips', 'chunks'], 'readwrite', (tx, done) => {
            const store = tx.objectStore('trips');
            store.get([owner, id]).onsuccess = event => {
                const trip = event.target.result;
                if (!trip || trip.state !== 'recording') { tx.abort(); return; }
                const points = [...payload.gps, ...payload.motion];
                if (points.length > 1000 || !points.length) { tx.abort(); return; }
                const chunk = { owner_id: owner, trip_id: id, seq: trip.next_seq, payload };
                tx.objectStore('chunks').add(chunk);
                trip.next_seq += 1;
                trip.last_recorded_at_ms = Math.max(trip.last_recorded_at_ms, ...points.map(p => p.timestamp_ms));
                trip.gps_count += payload.gps.length;
                trip.motion_count += payload.motion.length;
                store.put(trip); done(chunk);
            };
        });
    }
    function complete(owner, id, control = null) {
        // Read, validate and freeze completion atomically across browser tabs.
        return transact(['trips'], 'readwrite', (tx, done, fail) => {
            const store = tx.objectStore('trips');
            store.get([owner, id]).onsuccess = event => {
                const trip = event.target.result;
                if (!trip || trip.state !== 'awaiting_control') {
                    fail(new Error('Поездка уже отправлена на расчёт или ещё записывается. Обновите страницу.'));
                    return;
                }
                trip.completion = { ended_at_ms: trip.ended_at_ms, chunk_count: trip.next_seq, end_reason: trip.end_reason };
                if (control) trip.completion.control = structuredClone(control);
                trip.state = 'pending'; store.put(trip); done(trip);
            };
        });
    }
    function chunks(owner, id, fromSequence = 0, limit = undefined) {
        return transact(['chunks'], 'readonly', (tx, done) => {
            const range = IDBKeyRange.bound([owner, id, fromSequence], [owner, id, Number.MAX_SAFE_INTEGER]);
            tx.objectStore('chunks').getAll(range, limit).onsuccess = event => done(event.target.result);
        });
    }
    async function exportTrip(owner, id) {
        const trip = await get(owner, id);
        if (!trip) throw new Error('На телефоне нет этой поездки.');
        const parts = await chunks(owner, id);
        return { format: 'autoxak-raw-v2', units: { speed: 'm/s', acceleration: 'm/s2', gyro: 'deg/s',
            gyro_axes: 'x=beta,y=gamma,z=alpha', timestamps: 'epoch_ms', event_timestamp: 'browser_event_ms',
            coordinates: 'degrees', accuracy: 'm', interval: 'ms' }, trip, chunks: parts.map(part => ({ seq: part.seq, ...part.payload })) };
    }
    async function exportLegacy() {
        if (!indexedDB.databases) return { status: 'database_inventory_unavailable', trips: [] };
        const databases = await indexedDB.databases();
        if (!databases.some(item => item.name === 'autoXAK_storage')) return { status: 'absent', trips: [] };
        return new Promise((resolve, reject) => {
            const request = indexedDB.open('autoXAK_storage');
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const connection = request.result;
                if (!connection.objectStoreNames.contains('pending_trips')) {
                    connection.close(); resolve({ status: 'absent', trips: [] }); return;
                }
                const tx = connection.transaction(['pending_trips'], 'readonly');
                let trips = [];
                tx.objectStore('pending_trips').getAll().onsuccess = event => { trips = event.target.result; };
                tx.oncomplete = () => { connection.close(); resolve({ status: 'exported', trips }); };
                tx.onabort = () => { connection.close(); reject(tx.error); };
            };
        });
    }
    root.AutoXAKOfflineStore = { db, create, get, list, patch, append, complete, chunks, exportTrip, exportLegacy };
})(globalThis);
