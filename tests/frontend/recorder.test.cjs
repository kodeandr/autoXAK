const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('../../static/recorder.js');

class MemoryStore {
    constructor() { this.trips = new Map(); this.parts = []; this.failAppend = false; }
    key(owner, id) { return `${owner}:${id}`; }
    async create(trip) { this.trips.set(this.key(trip.owner_id, trip.trip_id), structuredClone(trip)); }
    async get(owner, id) { return structuredClone(this.trips.get(this.key(owner, id))); }
    async list(owner) { return [...this.trips.values()].filter(trip => trip.owner_id === owner).map(trip => structuredClone(trip)); }
    async patch(owner, id, changes) { Object.assign(this.trips.get(this.key(owner, id)), changes); return this.get(owner, id); }
    async append(owner, id, payload) {
        if (this.failAppend) throw new Error('QuotaExceededError');
        const trip = this.trips.get(this.key(owner, id));
        const chunk = { owner_id: owner, trip_id: id, seq: trip.next_seq++, payload: structuredClone(payload) };
        trip.last_recorded_at_ms = Math.max(trip.last_recorded_at_ms, ...[...payload.gps, ...payload.motion].map(point => point.timestamp_ms));
        trip.gps_count += payload.gps.length; trip.motion_count += payload.motion.length;
        this.parts.push(chunk); return chunk;
    }
    async complete(owner, id, control = null) {
        const trip = this.trips.get(this.key(owner, id));
        if (!trip || trip.state !== 'awaiting_control') throw new Error('Сначала завершите запись поездки.');
        trip.completion = { ended_at_ms: trip.ended_at_ms, chunk_count: trip.next_seq, end_reason: trip.end_reason };
        if (control) trip.completion.control = structuredClone(control);
        trip.state = 'pending'; return this.get(owner, id);
    }
    async chunks(owner, id, from = 0, limit = Infinity) { return this.parts.filter(part => part.owner_id === owner && part.trip_id === id && part.seq >= from).slice(0, limit); }
}
const profile = { vehicle_id: 'car-1', fuel_type: 'gasoline', mass_kg: 1500, displacement_l: 2, turbo: true };
const epoch = 1792400000000;
async function recording(owner = 'alice', id = 'trip-1') {
    const store = new MemoryStore();
    const trip = core.createTrip(owner, profile, {}, epoch, id);
    await store.create(trip);
    return { store, trip, recorder: new core.Recorder(store, trip) };
}

test('all >3000 readings persist in bounded chunks; stop drains and rejects later events', async () => {
    const { store, recorder } = await recording();
    for (let index = 0; index < 4201; index++) {
        recorder.add('motion', { timestamp_ms: epoch + index, ax: index });
        if (index % 300 === 0) await recorder.flush();
    }
    recorder.add('gps', { timestamp_ms: epoch + 4200, speed_mps: 0 });
    const ended = await recorder.stop(epoch + 4300);
    assert.equal(ended.ended_at_ms, epoch + 4300);
    assert.equal(ended.state, 'awaiting_control');
    assert.equal(recorder.add('motion', { timestamp_ms: epoch + 4301 }), false);
    assert.equal(store.parts.reduce((sum, part) => sum + part.payload.motion.length, 0), 4201);
    assert.equal(store.parts.reduce((sum, part) => sum + part.payload.gps.length, 0), 1);
    assert.ok(store.parts.every(part => part.payload.gps.length + part.payload.motion.length <= 1000));
    assert.deepEqual(store.parts.map(part => part.seq), store.parts.map((_, index) => index));
});

test('GPS missing speed remains unknown; actual stationary zero remains measured zero', () => {
    const position = { timestamp: epoch + 123, coords: { latitude: 55, longitude: 37, accuracy: 7, speed: null } };
    assert.deepEqual(core.gpsPoint(position), { timestamp_ms: epoch + 123, latitude: 55, longitude: 37, accuracy_m: 7, speed_mps: null, speed_source: 'missing' });
    position.coords.speed = 0;
    assert.equal(core.gpsPoint(position).speed_source, 'gps');
    assert.equal(core.gpsPoint(position).speed_mps, 0);
});

test('motion preserves real event time, gravity choice and rotationRate axis mapping', () => {
    const event = { timeStamp: 320, interval: 16, acceleration: { x: null, y: null, z: null },
        accelerationIncludingGravity: { x: 1, y: 2, z: 9.8 }, rotationRate: { alpha: 3, beta: 4, gamma: 5 } };
    const point = core.motionPoint(event, epoch + 330, epoch);
    assert.equal(point.timestamp_ms, epoch + 320);
    assert.deepEqual([point.ax, point.ay, point.az, point.gx, point.gy, point.gz], [1, 2, 9.8, 4, 5, 3]);
    assert.equal(point.acceleration_includes_gravity, true);
    event.acceleration = { x: 0, y: 1, z: 2 };
    assert.equal(core.motionPoint(event, epoch + 330, epoch).acceleration_includes_gravity, false);
});

test('quota failure retains unsaved data and does not falsely finish; retry has no duplicates', async () => {
    const { store, recorder } = await recording();
    recorder.add('gps', { timestamp_ms: epoch + 1000, speed_mps: 1 });
    store.failAppend = true;
    await assert.rejects(recorder.stop(epoch + 2000), /Quota/);
    assert.equal((await store.get('alice', 'trip-1')).state, 'recording');
    assert.equal(recorder.unsaved().gps.length, 1);
    store.failAppend = false;
    await recorder.stop(epoch + 2000);
    assert.equal(store.parts.length, 1);
    assert.equal((await store.get('alice', 'trip-1')).ended_at_ms, epoch + 2000);
});

test('reload interruption ends at durable reading, not at hours-later reload', async () => {
    const { store, recorder } = await recording();
    recorder.add('gps', { timestamp_ms: epoch + 1000 });
    await recorder.flush();
    recorder.add('gps', { timestamp_ms: epoch + 2000 }); // Lost in a process crash, not durable.
    const [recovered] = await core.recoverInterrupted(store, 'alice', epoch + 4 * 3600000);
    assert.equal(recovered.ended_at_ms, epoch + 1000);
    assert.equal(recovered.end_reason, 'interrupted');
    assert.equal(recovered.state, 'awaiting_control');
});

test('sync resumes exact chunks after lost acknowledgment, finalizes only once drained, and is owner-bound', async () => {
    const { store, recorder } = await recording();
    recorder.add('gps', { timestamp_ms: epoch + 1000, speed_mps: 5 });
    await recorder.stop(epoch + 2000);
    await core.prepareCompletion(store, 'alice', 'trip-1');
    await store.create(core.createTrip('bob', profile, {}, epoch, 'bobs-trip'));
    const calls = []; let fail = true;
    const request = async (path, options, owner) => {
        calls.push({ path, body: options.body, owner });
        if (path.endsWith('/chunks/0') && fail) { fail = false; throw new Error('connection lost after upload'); }
        return { trip_id: 'trip-1', fuel_estimate: { status: 'research_estimate', estimated_fuel_l: .01 } };
    };
    const sync = core.createSynchronizer(store, request, () => 'alice');
    await assert.rejects(sync(), /connection lost/);
    assert.equal((await store.get('alice', 'trip-1')).uploaded_count, 0);
    assert.equal(calls.some(call => call.path.endsWith('/complete')), false);
    const firstRetry = sync(), secondRetry = sync();
    assert.equal(firstRetry, secondRetry);
    await firstRetry;
    const chunkCalls = calls.filter(call => call.path.endsWith('/chunks/0'));
    assert.equal(chunkCalls.length, 2);
    assert.equal(chunkCalls[0].body, chunkCalls[1].body);
    assert.equal(calls.filter(call => call.path.endsWith('/complete')).length, 1);
    assert.ok(calls.every(call => call.owner === 'alice' && !call.path.includes('bobs-trip')));
    assert.equal((await store.get('alice', 'trip-1')).state, 'synced');
});

test('control input is frozen at completion, before network upload', async () => {
    const { store, recorder } = await recording();
    await recorder.stop(epoch + 1000);
    await core.prepareCompletion(store, 'alice', 'trip-1', { distance_km: 12, average_l_per_100km: 8, source: 'trip_computer', same_interval: true });
    assert.equal((await store.get('alice', 'trip-1')).completion.control.distance_km, 12);
    await assert.rejects(core.prepareCompletion(store, 'alice', 'trip-1', null), /завершите/);
});

test('offline synchronization pages saved chunks instead of loading the entire trip', async () => {
    const { store, recorder } = await recording();
    for (let index = 0; index < 45; index++) {
        recorder.add('gps', { timestamp_ms: epoch + index * 1000 });
        await recorder.flush();
    }
    await recorder.stop(epoch + 46000);
    await core.prepareCompletion(store, 'alice', 'trip-1');
    const read = store.chunks.bind(store), pages = [];
    store.chunks = async (...args) => { const page = await read(...args); pages.push({ limit: args[3], count: page.length }); return page; };
    let uploaded = 0;
    await core.createSynchronizer(store, async path => {
        if (path.includes('/chunks/')) uploaded++;
        return { trip_id: 'trip-1' };
    }, () => 'alice')();
    assert.equal(uploaded, 45);
    assert.deepEqual(pages.map(page => page.count), [20, 20, 5]);
    assert.ok(pages.every(page => page.limit === 20));
});

function sessionSandbox(values, fetch) {
    const entries = new Map(Object.entries(values));
    const context = vm.createContext({ fetch, crypto: require('node:crypto').webcrypto,
        localStorage: { getItem: key => entries.get(key) || null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) },
        navigator: {}, window: { addEventListener() {} } });
    vm.runInContext(fs.readFileSync(require.resolve('../../static/session.js'), 'utf8'), context);
    return { session: context.AutoXAKSession, entries };
}

test('all API calls include Bearer; refresh never reassigns old-owner data', async () => {
    const seen = [];
    const { session, entries } = sessionSandbox({ autoXAK_jwt_token: 'old-token', autoXAK_user_id: 'old-owner', autoXAK_device_id: 'd'.repeat(32) }, async (path, options) => {
        seen.push({ path, options });
        return path.includes('handshake') ? { ok: true, json: async () => ({ token: 'new-token', user_id: 'new-owner' }) } : { ok: false, status: 401 };
    });
    await assert.rejects(session.request('/api/v2/trips', {}, 'old-owner'), error => error.recoveryRequired === true);
    assert.equal(seen[0].options.headers.Authorization, 'Bearer old-token');
    assert.equal(seen.filter(call => call.path === '/api/v2/trips').length, 1);
    assert.equal(entries.get('autoXAK_user_id'), 'old-owner');
    assert.equal(entries.get('autoXAK_jwt_token'), 'old-token');
});

test('explicit identity recovery remembers former owner, preserves trip data, and uses 128-bit device ID', async () => {
    const { session, entries } = sessionSandbox({ autoXAK_jwt_token: 'expired', autoXAK_user_id: 'legacy-owner', autoXAK_device_id: 'short',
        autoXAK_offline_trips: '[{"session_id":"preserve-me"}]' }, async (_path, options) => {
        assert.match(JSON.parse(options.body).device_id, /^[a-f0-9]{32}$/);
        return { ok: true, json: async () => ({ token: 'fresh', user_id: 'new-owner' }) };
    });
    await session.startNewSession();
    assert.equal(entries.get('autoXAK_user_id'), 'new-owner');
    assert.deepEqual(JSON.parse(entries.get('autoxak_previous_owners_v2')), ['legacy-owner']);
    assert.equal(entries.get('autoXAK_offline_trips'), '[{"session_id":"preserve-me"}]');
});

test('a cross-tab account switch before fetch cannot send old trip data using the new account token', async () => {
    let requests = 0;
    const { session, entries } = sessionSandbox({ autoXAK_jwt_token: 'alice-token', autoXAK_user_id: 'alice' }, async () => { requests++; return { ok: true, json: async () => ({}) }; });
    const pending = session.request('/api/v2/trips', { method: 'POST', body: '{"trip_id":"alice-trip"}' }, 'alice');
    entries.set('autoXAK_user_id', 'bob'); entries.set('autoXAK_jwt_token', 'bob-token');
    await assert.rejects(pending, /Аккаунт изменился/);
    assert.equal(requests, 0);
});

test('expired Yandex access requires relogin and never falls back to a device account', async () => {
    const calls = [];
    const { session, entries } = sessionSandbox({ autoXAK_jwt_token: 'expired', autoXAK_user_id: 'ya_123', autoXAK_auth_provider: 'yandex' }, async path => {
        calls.push(path); return { ok: false, status: 401 };
    });
    await assert.rejects(session.request('/api/v2/trips', {}, 'ya_123'), error => error.reloginRequired === true);
    assert.deepEqual(calls, ['/api/v2/trips']);
    assert.equal(entries.get('autoXAK_user_id'), 'ya_123');
});

test('a delayed device handshake cannot overwrite an account selected in another tab', async () => {
    let finish;
    const { session, entries } = sessionSandbox({}, () => new Promise(resolve => { finish = resolve; }));
    const pending = session.ensureAuthenticated();
    entries.set('autoXAK_user_id', 'ya_selected'); entries.set('autoXAK_jwt_token', 'yandex-token');
    finish({ ok: true, json: async () => ({ token: 'device-token', user_id: 'device-owner' }) });
    await assert.rejects(pending, /Аккаунт изменился/);
    assert.equal(entries.get('autoXAK_user_id'), 'ya_selected');
    assert.equal(entries.get('autoXAK_jwt_token'), 'yandex-token');
});
