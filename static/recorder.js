(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.AutoXAKRecorder = api;
})(globalThis, function () {
    'use strict';
    const MAX_DURATION_MS = 24 * 60 * 60 * 1000;
    const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
    function gpsPoint(position) {
        const c = position.coords;
        const speed = finite(c.speed);
        return { timestamp_ms: Math.round(position.timestamp), latitude: c.latitude, longitude: c.longitude,
            speed_mps: speed !== null && speed >= 0 ? speed : null,
            speed_source: speed !== null && speed >= 0 ? 'gps' : 'missing', accuracy_m: finite(c.accuracy) };
    }
    function motionPoint(event, now = Date.now(), origin = 0) {
        const withoutGravity = event.acceleration;
        const hasLinear = withoutGravity && ['x', 'y', 'z'].every(axis => finite(withoutGravity[axis]) !== null);
        const acceleration = (hasLinear ? withoutGravity : event.accelerationIncludingGravity) || {};
        const rotation = event.rotationRate || {};
        const eventTime = finite(event.timeStamp);
        const candidate = eventTime === null ? now : eventTime > 1e12 ? eventTime : origin + eventTime;
        const timestamp = Math.min(now, Math.abs(candidate - now) < 60_000 ? candidate : now);
        return { timestamp_ms: Math.round(timestamp), event_timestamp_ms: eventTime,
            ax: finite(acceleration.x), ay: finite(acceleration.y), az: finite(acceleration.z),
            gx: finite(rotation.beta), gy: finite(rotation.gamma), gz: finite(rotation.alpha),
            acceleration_includes_gravity: !hasLinear, interval_ms: finite(event.interval) };
    }
    function createTrip(owner, vehicle, device, now = Date.now(), id = globalThis.crypto.randomUUID()) {
        return { owner_id: owner, trip_id: id, started_at_ms: now, ended_at_ms: null,
            last_recorded_at_ms: now, vehicle: structuredClone(vehicle), device: structuredClone(device),
            state: 'recording', next_seq: 0, uploaded_count: 0, start_synced: false,
            gps_count: 0, motion_count: 0, completion: null, result: null };
    }
    class Recorder {
        constructor(store, trip) {
            this.store = store; this.trip = trip; this.active = true;
            this.gps = []; this.motion = []; this.chain = Promise.resolve();
        }
        add(kind, point) {
            if (!this.active || !['gps', 'motion'].includes(kind)) return false;
            if (point.timestamp_ms < this.trip.started_at_ms || point.timestamp_ms > this.trip.started_at_ms + MAX_DURATION_MS) return false;
            this[kind].push(point); return true;
        }
        flush() {
            const work = async () => {
                while (this.gps.length || this.motion.length) {
                    const gps = this.gps.splice(0, 500);
                    const motion = this.motion.splice(0, 1000 - gps.length);
                    try { await this.store.append(this.trip.owner_id, this.trip.trip_id, { gps, motion }); }
                    catch (error) { this.gps.unshift(...gps); this.motion.unshift(...motion); throw error; }
                }
            };
            const task = this.chain.then(work, work);
            this.chain = task.catch(() => {});
            return task;
        }
        async stop(now = Date.now(), reason = 'user') {
            this.active = false;
            const ended = Math.min(this.trip.started_at_ms + MAX_DURATION_MS, Math.max(now, this.trip.started_at_ms));
            await this.flush();
            this.trip = await this.store.patch(this.trip.owner_id, this.trip.trip_id,
                { ended_at_ms: ended, end_reason: reason, state: 'awaiting_control' });
            return this.trip;
        }
        unsaved() { return { gps: [...this.gps], motion: [...this.motion] }; }
    }
    async function recoverInterrupted(store, owner, now = Date.now()) {
        const recovered = [];
        for (const trip of await store.list(owner)) {
            if (trip.state !== 'recording') continue;
            const ended = Math.min(now, trip.started_at_ms + MAX_DURATION_MS, trip.last_recorded_at_ms);
            recovered.push(await store.patch(owner, trip.trip_id, { state: 'awaiting_control',
                ended_at_ms: Math.max(ended, trip.started_at_ms), end_reason: 'interrupted' }));
        }
        return recovered;
    }
    async function prepareCompletion(store, owner, id, control = null) {
        return store.complete(owner, id, control);
    }
    function createSynchronizer(store, request, getOwner) {
        let running = null;
        return function synchronize() {
            if (running) return running;
            running = (async () => {
                const owner = getOwner();
                if (!owner) return [];
                const results = [];
                for (const snapshot of await store.list(owner)) {
                    if (getOwner() !== owner) throw new Error('Аккаунт изменился; отправка остановлена.');
                    if (snapshot.state === 'synced') continue;
                    const base = `/api/v2/trips/${encodeURIComponent(snapshot.trip_id)}`;
                    if (!snapshot.start_synced) {
                        await request('/api/v2/trips', { method: 'POST', body: JSON.stringify({
                            trip_id: snapshot.trip_id, started_at_ms: snapshot.started_at_ms,
                            vehicle: snapshot.vehicle, device: snapshot.device }) }, owner);
                        await store.patch(owner, snapshot.trip_id, { start_synced: true });
                    }
                    let next = snapshot.uploaded_count;
                    // Bound memory on reconnect after a long offline recording.
                    while (next < snapshot.next_seq) {
                        const chunks = await store.chunks(owner, snapshot.trip_id, next, 20);
                        if (!chunks.length) throw new Error('Не найден сохранённый блок поездки. Выгрузите запись для проверки.');
                        for (const chunk of chunks) {
                            if (chunk.seq >= snapshot.next_seq) break;
                            if (getOwner() !== owner) throw new Error('Аккаунт изменился; отправка остановлена.');
                            if (chunk.seq !== next) throw new Error('Нарушена последовательность блоков поездки.');
                            await request(`${base}/chunks/${chunk.seq}`, { method: 'PUT', body: JSON.stringify(chunk.payload) }, owner);
                            next = chunk.seq + 1;
                            await store.patch(owner, snapshot.trip_id, { uploaded_count: next });
                        }
                    }
                    const current = await store.get(owner, snapshot.trip_id);
                    if (current.state === 'pending' && current.uploaded_count === current.next_seq) {
                        const result = await request(`${base}/complete`, { method: 'POST', body: JSON.stringify(current.completion) }, owner);
                        await store.patch(owner, current.trip_id, { result, state: 'synced' });
                        results.push(result);
                    }
                }
                return results;
            })().finally(() => { running = null; });
            return running;
        };
    }
    return { MAX_DURATION_MS, gpsPoint, motionPoint, createTrip, Recorder, recoverInterrupted, prepareCompletion, createSynchronizer };
});
