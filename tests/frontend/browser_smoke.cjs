/* Optional real-browser smoke: start uvicorn on 8765, then run with Playwright installed.
 * PLAYWRIGHT_MODULE can point to a bundled installation; BROWSER_CHANNEL defaults to msedge.
 * Uses an isolated browser context, a real API/IndexedDB and explicitly synthetic GPS/motion.
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8765';
async function until(check) {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
        if (await check()) return;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Timed out waiting for durable browser/API state');
}

(async () => {
    const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const errors = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.addInitScript(() => {
        const actualNow = Date.now.bind(Date);
        let offset = 0;
        try { offset = Number(sessionStorage.getItem('smoke-clock-offset') || 0); } catch (_) { /* about:blank has an opaque origin. */ }
        Date.now = () => actualNow() + offset;
        let callback = null, stopped = 0;
        window.addEventListener('devicemotion', event => { if (event.isTrusted) event.stopImmediatePropagation(); }, true);
        Object.defineProperty(navigator, 'geolocation', { value: {
            watchPosition(success) { callback = success; return 42; },
            clearWatch() { callback = null; stopped++; }
        } });
        window.smokeSensors = {
            advance(ms) { offset += ms; sessionStorage.setItem('smoke-clock-offset', String(offset)); },
            gps(speed = 10) { callback?.({ timestamp: Date.now(), coords: { latitude: 55.75, longitude: 37.61,
                accuracy: 4, speed } }); },
            motion(count) {
                for (let index = 0; index < count; index++) {
                    const event = new Event('devicemotion');
                    Object.defineProperties(event, {
                        timeStamp: { value: Date.now() }, interval: { value: 20 },
                        acceleration: { value: { x: .1, y: .2, z: .3 } },
                        rotationRate: { value: { alpha: 1, beta: 2, gamma: 3 } }
                    });
                    window.dispatchEvent(event);
                }
            }, get stopped() { return stopped; }
        };
    });
    const page = await context.newPage();
    try {
        await page.goto(`${base}/setup`);
        await page.locator('#save-vehicle').waitFor({ state: 'visible' });
        await page.waitForFunction(() => !document.getElementById('save-vehicle').disabled);
        await page.locator('#make').fill('Browser test');
        await page.locator('#model').fill('Synthetic gasoline');
        await page.locator('#fuel-type').selectOption('gasoline');
        await page.locator('#displacement').fill('2.0');
        await page.locator('#mass').fill('1500');
        await page.locator('#turbo').selectOption('yes');
        await page.locator('#save-vehicle').click();
        await page.waitForFunction(() => document.getElementById('message').textContent.includes('Можно начинать'));
        await page.goto(base);
        await page.waitForFunction(() => !document.getElementById('start-trip').disabled);
        await page.locator('#start-trip').click();
        await page.locator('#active-card').waitFor({ state: 'visible' });
        await context.setOffline(true);
        await page.evaluate(() => {
            for (let index = 0; index < 92; index++) {
                smokeSensors.gps(index === 40 ? null : 10);
                smokeSensors.motion(50);
                smokeSensors.advance(1000);
            }
        });
        await until(() => page.evaluate(async () => {
            const [trip] = await AutoXAKOfflineStore.list(AutoXAKSession.getUserId());
            return trip.motion_count === 4600 && trip.gps_count === 92;
        }));
        const id = await page.evaluate(async () => (await AutoXAKOfflineStore.list(AutoXAKSession.getUserId()))[0].trip_id);
        await page.locator('#stop-trip').click();
        await page.locator('#control-card').waitFor({ state: 'visible' });
        assert.equal(await page.evaluate(() => smokeSensors.stopped), 1);
        await page.locator('#skip-control').click();
        await page.locator('#result-card').waitFor({ state: 'visible' });
        const pending = await page.evaluate(async id => AutoXAKOfflineStore.get(AutoXAKSession.getUserId(), id), id);
        assert.equal(pending.state, 'pending');
        assert.equal(pending.motion_count, 4600);
        const offlineExport = await page.evaluate(async id => AutoXAKOfflineStore.exportTrip(AutoXAKSession.getUserId(), id), id);
        assert.equal(offlineExport.chunks.reduce((sum, part) => sum + part.motion.length, 0), 4600);
        assert.ok(offlineExport.chunks.some(part => part.gps.some(point => point.speed_source === 'missing' && point.speed_mps === null)));
        await context.setOffline(false);
        await page.locator('#retry-sync').click();
        await until(() => page.evaluate(async id => (await AutoXAKOfflineStore.get(AutoXAKSession.getUserId(), id)).state === 'synced', id));
        const remote = await page.evaluate(id => AutoXAKSession.request(`/api/v2/trips/${id}/export`), id);
        assert.equal(remote.motion.length, 4600);
        assert.equal(remote.gps.length, 92);
        assert.equal(remote.motion[0].gx, 2);
        assert.equal(remote.motion[0].gy, 3);
        assert.equal(remote.motion[0].gz, 1);
        assert.equal(remote.fuel_estimate.status, 'research_estimate');
        assert.ok(remote.fuel_estimate.estimated_fuel_l > 0);
        assert.equal(remote.fuel_estimate.complete_trip_fuel_l, null);
        assert.equal(remote.savings_status, 'unavailable');
        if (process.env.SMOKE_SCREENSHOT) await page.screenshot({ path: path.resolve(process.env.SMOKE_SCREENSHOT), fullPage: true });
        await page.locator('#new-trip').click();
        await page.locator('#start-trip').click();
        await page.locator('#active-card').waitFor({ state: 'visible' });
        await context.setOffline(true);
        await page.evaluate(() => { smokeSensors.advance(2000); smokeSensors.gps(0); smokeSensors.motion(10); });
        await until(() => page.evaluate(async () => (await AutoXAKOfflineStore.list(AutoXAKSession.getUserId()))[0].motion_count === 10));
        const interrupted = await page.evaluate(async () => (await AutoXAKOfflineStore.list(AutoXAKSession.getUserId()))[0]);
        await context.setOffline(false);
        await page.reload();
        await page.locator('#control-card').waitFor({ state: 'visible' });
        const recovered = await page.evaluate(id => AutoXAKOfflineStore.get(AutoXAKSession.getUserId(), id), interrupted.trip_id);
        assert.equal(recovered.end_reason, 'interrupted');
        assert.equal(recovered.ended_at_ms, interrupted.last_recorded_at_ms);
        await page.locator('#skip-control').click();
        await until(() => page.evaluate(async id => (await AutoXAKOfflineStore.get(AutoXAKSession.getUserId(), id)).state === 'synced', interrupted.trip_id));
        await page.goto(`${base}/history`);
        await page.waitForFunction(() => Number(document.getElementById('trip-count').textContent) >= 2);
        assert.equal(await page.locator('.trip-date').count(), 2);
        const simultaneousCompletion = await page.evaluate(async () => {
            const owner = AutoXAKSession.getUserId(), store = AutoXAKOfflineStore;
            const trip = AutoXAKRecorder.createTrip(owner, { vehicle_id: 'cas-test' }, {}, Date.now(), crypto.randomUUID());
            await store.create(trip);
            await store.patch(owner, trip.trip_id, { state: 'awaiting_control', ended_at_ms: trip.started_at_ms + 1, end_reason: 'user' });
            const outcomes = await Promise.allSettled([
                AutoXAKRecorder.prepareCompletion(store, owner, trip.trip_id, { distance_km: 1 }),
                AutoXAKRecorder.prepareCompletion(store, owner, trip.trip_id, { distance_km: 2 })
            ]);
            const saved = await store.get(owner, trip.trip_id);
            await store.patch(owner, trip.trip_id, { state: 'synced', result: { trip_id: trip.trip_id } });
            const late = await Promise.allSettled([AutoXAKRecorder.prepareCompletion(store, owner, trip.trip_id)]);
            return { outcomes: outcomes.map(item => item.status), saved, late: late[0].status,
                final: await store.get(owner, trip.trip_id) };
        });
        assert.deepEqual(simultaneousCompletion.outcomes.sort(), ['fulfilled', 'rejected']);
        assert.equal(simultaneousCompletion.late, 'rejected');
        assert.equal(simultaneousCompletion.final.state, 'synced');
        assert.deepEqual(simultaneousCompletion.final.completion, simultaneousCompletion.saved.completion);
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ status: 'passed', gps: remote.gps.length, motion: remote.motion.length,
            fuel_status: remote.fuel_estimate.status, estimated_fuel_l: remote.fuel_estimate.estimated_fuel_l,
            coverage_fraction: remote.fuel_estimate.coverage_fraction, cases: ['offline durable recording', 'full export',
                'real model inference', 'missing GPS speed', 'sensor stop', 'reload recovery', 'history', 'atomic concurrent completion'] }, null, 2));
    } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
