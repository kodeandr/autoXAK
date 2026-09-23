/**
 * autoXAK Offline-First Storage Engine (IndexedDB)
 * Транзакционная очередь для гарантированной доставки телеметрии.
 */
const AutoXAKOfflineStore = (() => {
    const DB_NAME = "autoXAK_storage";
    const DB_VERSION = 1;
    const STORE_TRIPS = "pending_trips";

    let dbInstance = null;

    function openDB() {
        return new Promise((resolve, reject) => {
            if (dbInstance) return resolve(dbInstance);

            const req = indexedDB.open(DB_NAME, DB_VERSION);

            req.onupgradeneeded = (e) => {
                const db = e.target.result;
                if (!db.objectStoreNames.contains(STORE_TRIPS)) {
                    const store = db.createObjectStore(STORE_TRIPS, { keyPath: "session_id" });
                    store.createIndex("status", "status", { unique: false });
                    store.createIndex("created_at", "created_at", { unique: false });
                }
            };

            req.onsuccess = (e) => {
                dbInstance = e.target.result;
                resolve(dbInstance);
            };

            req.onerror = (e) => reject(e.target.error);
        });
    }

    async function savePendingTrip(payload) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_TRIPS, "readwrite");
            const store = tx.objectStore(STORE_TRIPS);
            const record = {
                session_id: payload.session_id,
                user_id: payload.user_id,
                car_id: payload.car_id,
                telemetry_stream: payload.telemetry_stream,
                created_at: Date.now(),
                status: "PENDING",
                attempts: 0
            };
            const req = store.put(record);
            req.onsuccess = () => resolve(record);
            req.onerror = () => reject(req.error);
        });
    }

    async function removePendingTrip(sessionId) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_TRIPS, "readwrite");
            const store = tx.objectStore(STORE_TRIPS);
            const req = store.delete(sessionId);
            req.onsuccess = () => resolve(true);
            req.onerror = () => reject(req.error);
        });
    }

    async function getPendingTrips() {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_TRIPS, "readonly");
            const store = tx.objectStore(STORE_TRIPS);
            const index = store.index("status");
            const req = index.getAll("PENDING");
            req.onsuccess = () => resolve(req.result || []);
            req.onerror = () => reject(req.error);
        });
    }

    async function countPendingTrips() {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_TRIPS, "readonly");
            const store = tx.objectStore(STORE_TRIPS);
            const index = store.index("status");
            const req = index.count("PENDING");
            req.onsuccess = () => resolve(req.result || 0);
            req.onerror = () => reject(req.error);
        });
    }

    async function syncPendingTrips(callbacks = {}) {
        if (!navigator.onLine) return;
        const pending = await getPendingTrips();
        if (pending.length === 0) return;

        for (const trip of pending) {
            try {
                const res = await fetch('/api/v1/telemetry/session', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...AutoXAKSession.getAuthHeaders()
                    },
                    body: JSON.stringify({
                        session_id: trip.session_id,
                        user_id: trip.user_id,
                        car_id: trip.car_id,
                        telemetry_stream: trip.telemetry_stream
                    })
                });

                if (res.ok) {
                    const data = await res.json();
                    await removePendingTrip(trip.session_id);
                    if (callbacks.onTripSynced) {
                        callbacks.onTripSynced(data);
                    }
                } else if (res.status === 422) {
                    // Невалидные данные (слишком короткая сессия) — удаляем, чтобы не блокировать очередь
                    await removePendingTrip(trip.session_id);
                }
            } catch (err) {
                // Сеть оборвалась в процессе — прерываем цикл до следующего online-события
                break;
            }
        }

        if (callbacks.onSyncComplete) {
            callbacks.onSyncComplete();
        }
    }

    return {
        savePendingTrip,
        removePendingTrip,
        getPendingTrips,
        countPendingTrips,
        syncPendingTrips
    };
})();
