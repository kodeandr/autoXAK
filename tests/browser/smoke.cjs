const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';
(async () => {
    const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
    try {
        const context = await browser.newContext();
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/login');
        await page.click('.btn-guest');
        await page.waitForURL(base + '/');
        await page.waitForFunction(() => localStorage.getItem('autoXAK_jwt_token'));
        const identity = await page.evaluate(() => ({user: AutoXAKSession.getUserId(), headers: AutoXAKSession.getAuthHeaders()}));
        assert(identity.user.startsWith('guest_'));
        await page.request.post(`${base}/api/v1/users/${identity.user}/profile`, {headers:identity.headers, data:{current_oil_wear_percent:82, service_cost_rub:15000}});
        await page.goto(base + '/setup?edit=1');
        await page.waitForFunction(() => window.AutoXAKSession && document.getElementById('select-trim').options.length > 0);
        await page.click('#tab-btn-manual');
        await page.fill('#input-custom-name','Audit Car');
        await page.evaluate(() => saveManualVehicle());
        const response = await page.request.get(`${base}/api/v1/users/${identity.user}/profile`, {headers:identity.headers});
        const profile = await response.json();
        assert(profile.car_id.startsWith('custom_'), JSON.stringify(profile));
        assert.equal(profile.current_oil_wear_percent,82);
        assert.equal(profile.service_cost_rub,15000);
        assert.equal(profile.car_name,'Audit Car');
        await page.goto(base + '/history');
        await page.waitForFunction(() => document.querySelector('.empty-history'));
        const registration = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
        assert.equal(registration, base + '/');
        await page.goto(base + '/');
        await page.waitForLoadState('networkidle');
        // Exercise real IndexedDB transactions and a rejected upload in Chromium.
        const queue = await page.evaluate(async () => {
            const owner = AutoXAKSession.getUserId();
            const session_id = crypto.randomUUID();
            const telemetry_stream = Array.from({length: 3000}, (_, i) => ({t:i*0.02, ax:0, ay:0, az:9.81, speed:10, lat:55.75, lon:37.61}));
            const payload = {session_id, user_id:owner, car_id:'haval_jolion_15t', telemetry_stream};
            await AutoXAKOfflineStore.savePendingTrip({...payload, session_id:'foreign-trip', user_id:'another-owner'});
            await AutoXAKOfflineStore.savePendingTrip(payload);
            const pending = await AutoXAKOfflineStore.getPendingTrips();
            return {id:session_id, count:pending.length, own:pending.every(trip=>trip.user_id===owner)};
        });
        assert.equal(queue.count, 1);
        assert.equal(queue.own, true);
        await page.route('**/api/v1/telemetry/session', route=>route.fulfill({status:422, contentType:'application/json', body:'{"detail":"invalid"}'}));
        await page.evaluate(()=>AutoXAKOfflineStore.syncPendingTrips());
        assert.equal(await page.evaluate(()=>AutoXAKOfflineStore.countPendingTrips()),1);
        await page.unroute('**/api/v1/telemetry/session');
        await page.evaluate(async id => {
            await AutoXAKOfflineStore.savePendingControl(id,8.7);
            await AutoXAKOfflineStore.syncPendingTrips();
        },queue.id);
        assert.equal(await page.evaluate(()=>AutoXAKOfflineStore.countPendingTrips()),0);
        const control = await page.request.get(`${base}/api/v1/users/${identity.user}/trips`, {headers:identity.headers});
        assert((await control.json()).trips.some(trip=>trip.session_id===queue.id));
        const aborted = await page.evaluate(async () => {
            const original = IDBObjectStore.prototype.put;
            let requestSucceeded = false;
            IDBObjectStore.prototype.put = function(value, ...args) {
                const request = original.call(this,value,...args);
                if (value.session_id==='abort-check') request.addEventListener('success',()=>{requestSucceeded=true;this.transaction.abort();});
                return request;
            };
            try {
                await AutoXAKOfflineStore.savePendingTrip({session_id:'abort-check',user_id:AutoXAKSession.getUserId(),telemetry_stream:[]});
                return {rejected:false,requestSucceeded};
            } catch (_) { return {rejected:true,requestSucceeded}; }
            finally {IDBObjectStore.prototype.put=original;}
        });
        assert.deepEqual(aborted,{rejected:true,requestSucceeded:true});
        const recovered = await page.evaluate(async () => {
            const id = crypto.randomUUID();
            const telemetry_stream = Array.from({length:50},(_,i)=>({t:i*0.02,ax:0,ay:0,az:9.81,speed:0}));
            await AutoXAKOfflineStore.saveDraftTrip({session_id:id,user_id:AutoXAKSession.getUserId(),car_id:'haval_jolion_15t',telemetry_stream});
            await AutoXAKOfflineStore.recoverInterruptedTrips();
            const found=(await AutoXAKOfflineStore.getPendingTrips()).some(trip=>trip.session_id===id);
            await AutoXAKOfflineStore.removePendingTrip(id);
            return found;
        });
        assert.equal(recovered,true);
        console.log('Browser: owner-isolated queue, 422 retention, successful retry/control, transaction-abort durability, interrupted recording recovery passed');
        await page.route(`**/api/v1/users/${identity.user}/profile`, route=>route.fulfill({status:503,body:'unavailable'}));
        const deferred = await page.evaluate(async () => AutoXAKSession.saveProfile({user_id:AutoXAKSession.getUserId(),car_name:'Deferred Car'}));
        assert.equal(deferred,false);
        await page.unroute(`**/api/v1/users/${identity.user}/profile`);
        assert.equal(await page.evaluate(()=>AutoXAKSession.syncPendingProfile()),true);
        const resumedProfile = await page.request.get(`${base}/api/v1/users/${identity.user}/profile`, {headers:identity.headers});
        assert.equal((await resumedProfile.json()).car_name,'Deferred Car');
        const sensors = await page.evaluate(async () => {
            let clock = Date.now(), tick, cleared=null, lastAlert='';
            const originalNow=Date.now, originalInterval=window.setInterval;
            Date.now=()=>clock;
            window.alert=message=>{lastAlert=message;};
            window.setInterval=(callback,ms)=>{if(ms===1000) tick=callback;return originalInterval(callback,ms);};
            let gps;
            Object.defineProperty(navigator,'geolocation',{configurable:true,value:{watchPosition:callback=>{gps=callback;return 0;},clearWatch:id=>{cleared=id;}}});
            try {
                await beginActiveTrip();
                gps({timestamp:clock,coords:{latitude:55.75,longitude:37.61,accuracy:5,speed:1}});
                clock+=1000;
                gps({timestamp:clock,coords:{latitude:55.750009,longitude:37.61,accuracy:5,speed:1}});
                const afterGps=liveDistanceKm;
                tick();
                const afterTimer=liveDistanceKm;
                const empty=new Event('devicemotion');
                window.dispatchEvent(empty);
                const emptyCount=telemetryStream.length;
                for(let i=0;i<49;i++) {
                    clock+=20;
                    const event=new Event('devicemotion');
                    Object.defineProperties(event,{acceleration:{value:{x:0.1,y:0,z:0}},accelerationIncludingGravity:{value:{x:0.1,y:0,z:9.81}},rotationRate:{value:{alpha:0,beta:0,gamma:0}}});
                    window.dispatchEvent(event);
                }
                await finishActiveTrip();
                return {afterGps,afterTimer,emptyCount,samples:telemetryStream.length,cleared,hasAlert:!!lastAlert};
            } finally {Date.now=originalNow;window.setInterval=originalInterval;}
        });
        assert.equal(sensors.afterGps,0.001);
        assert.equal(sensors.afterTimer,sensors.afterGps);
        assert.equal(sensors.emptyCount,0);
        assert.equal(sensors.samples,49);
        assert.equal(sensors.cleared,0);
        assert.equal(sensors.hasAlert,true);
        console.log('Browser: profile recovery after 503, single GPS distance integration, no fabricated IMU samples, geolocation watcher 0 cleanup passed');
        assert.deepEqual(errors, []);
        console.log('Browser: guest login, authenticated garage/manual profile, preserved wear/settings, history, root service worker passed');
        await context.close();
    } finally {await browser.close();}
})().catch(error => {console.error(error);process.exit(1)});
