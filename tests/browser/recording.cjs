const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';

(async()=>{
    const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
    try {
        const context = await browser.newContext();
        await context.addInitScript(()=>{
            window.__alerts=[];
            window.alert=message=>window.__alerts.push(message);
        });
        const page = await context.newPage();
        const errors=[];
        page.on('pageerror',error=>errors.push(error.message));
        await page.goto(base+'/login');
        await page.click('.btn-guest');
        await page.waitForURL(base+'/');
        await page.waitForFunction(()=>window.AutoXAKSession && window.AutoXAKOfflineStore);
        const identity=await page.evaluate(()=>({user:AutoXAKSession.getUserId(),headers:AutoXAKSession.getAuthHeaders()}));

        async function installSensors(permission='granted') {
            await page.evaluate(permission=>{
                let clock=Date.now(),fix,error;
                window.__sensorStats={permissionCalls:0,watches:0,initialPositionCalls:0,cleared:[]};
                Date.now=()=>clock;
                Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>{
                    if(!navigator.userActivation.isActive) return Promise.reject(new Error('No user gesture'));
                    __sensorStats.permissionCalls++;
                    return Promise.resolve(permission);
                }});
                Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
                    getCurrentPosition:()=>{__sensorStats.initialPositionCalls++;throw new Error('Duplicate permission request');},
                    watchPosition:(callback,onError)=>{fix=callback;error=onError;return __sensorStats.watches++;},
                    clearWatch:id=>__sensorStats.cleared.push(id)
                }});
                window.__advance=ms=>{clock+=ms;};
                window.__gps=(accuracy=5,speed=10)=>fix({timestamp:clock,coords:{latitude:55.75,longitude:37.61,accuracy,speed}});
                window.__gpsError=()=>error({code:1});
                window.__motion=count=>{
                    for(let i=0;i<count;i++) {
                        clock+=20;
                        const event=new Event('devicemotion');
                        Object.defineProperties(event,{acceleration:{value:null},accelerationIncludingGravity:{value:{x:0.1,y:0,z:9.81}},
                            rotationRate:{value:{alpha:3,beta:1,gamma:2}},interval:{value:20}});
                        window.dispatchEvent(event);
                    }
                };
            },permission);
        }
        async function start() {
            await page.click('[onclick="requestStartTrip()"]');
            await page.waitForFunction(()=>tripActive);
        }
        async function finish() {
            await page.evaluate(()=>finishActiveTrip());
            const id=await page.evaluate(()=>lastFinishedSessionId);
            const response=await page.request.get(`${base}/api/v2/trips/${id}/export`,{headers:identity.headers});
            assert.equal(response.status(),200,await response.text());
            return response.json();
        }
        async function closeSummary() {
            await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
        }

        await installSensors();
        await start();
        await page.evaluate(()=>{__gps(200);__motion(120);});
        const notRecoveredWhileActive=await page.evaluate(async()=>{
            await saveActiveDraft();
            await AutoXAKOfflineStore.recoverInterruptedTrips();
            return AutoXAKOfflineStore.countPendingTrips();
        });
        assert.equal(notRecoveredWhileActive,0);
        const inaccurate=await finish();
        assert.deepEqual(inaccurate.point_counts,{gps:1,motion:120});
        assert.equal(inaccurate.gps[0].accuracy_m,200);
        assert(inaccurate.fuel_estimate.capture_quality.quality_flags.includes('insufficient_accurate_gps'));
        assert.equal(await page.locator('#sum-offline-banner').evaluate(element=>element.classList.contains('visible')),false);
        await closeSummary();

        await start();
        await page.evaluate(()=>{__gpsError();__motion(60);});
        const withoutGps=await finish();
        assert.deepEqual(withoutGps.point_counts,{gps:0,motion:60});
        const stats=await page.evaluate(()=>__sensorStats);
        assert.equal(stats.permissionCalls,1);
        assert.equal(stats.initialPositionCalls,0);
        assert.equal(stats.watches,2);
        assert.deepEqual(stats.cleared,[0,1]);
        assert.deepEqual(await page.evaluate(()=>__alerts),[]);

        await page.goto(base+'/');
        await page.waitForFunction(()=>window.AutoXAKSession && !startingTrip);
        await installSensors('denied');
        await start();
        await page.evaluate(()=>{__gps();__advance(1000);__gps();});
        const gpsOnly=await finish();
        assert.deepEqual(gpsOnly.point_counts,{gps:2,motion:0});
        assert.equal(gpsOnly.fuel_estimate.capture_quality.mode,'gps_only');
        assert.equal(gpsOnly.fuel_estimate.capture_quality.permissions.motion,'denied');
        assert.equal(await page.locator('#sum-distance').innerText(),'10 м');
        await closeSummary();

        await context.setOffline(true);
        await start();
        await page.evaluate(()=>{__gps();__advance(1000);__gps();});
        await page.evaluate(()=>finishActiveTrip());
        assert.equal(await page.evaluate(()=>AutoXAKOfflineStore.countPendingTrips()),1);
        await context.setOffline(false);
        await page.evaluate(()=>AutoXAKOfflineStore.syncPendingTrips());
        assert.equal(await page.evaluate(()=>AutoXAKOfflineStore.countPendingTrips()),0);
        await closeSummary();

        // A failed local save must keep the original finished payload for retry.
        await start();
        await page.evaluate(()=>{
            __gps();__advance(1000);__gps();
            const original=AutoXAKOfflineStore.savePendingTrip;
            let fail=true;
            AutoXAKOfflineStore.savePendingTrip=async payload=>{
                if(fail){fail=false;throw new Error('Injected storage failure');}
                return original(payload);
            };
        });
        await page.evaluate(()=>finishActiveTrip());
        assert.equal(await page.evaluate(()=>!!pendingFinishedPayload),true);
        const retryId=await page.evaluate(()=>pendingFinishedPayload.session_id);
        const retried=await finish();
        assert.equal(retried.trip_id,retryId);
        assert.deepEqual(retried.point_counts,{gps:2,motion:0});
        assert.equal(await page.evaluate(()=>pendingFinishedPayload),null);

        const interruptedId=await page.evaluate(async()=>{
            const id=crypto.randomUUID(),now=Date.now();
            await AutoXAKOfflineStore.saveDraftTrip({session_id:id,user_id:AutoXAKSession.getUserId(),car_id:currentCarId,
                telemetry_stream:[],raw_recording:{started_at_ms:now-5000,ended_at_ms:now,end_reason:'interrupted',
                    gps:[],motion:[{timestamp_ms:now-1000,gx:1,acceleration_includes_gravity:false}]}});
            await AutoXAKOfflineStore.recoverInterruptedTrips();
            await AutoXAKOfflineStore.syncPendingTrips();
            return id;
        });
        const interrupted=await page.request.get(`${base}/api/v2/trips/${interruptedId}/export`,{headers:identity.headers});
        assert.equal(interrupted.status(),200);
        assert.equal((await interrupted.json()).end_reason,'interrupted');

        await closeSummary();
        await start();
        await page.evaluate(()=>{__advance(86400000+2000);__gps();});
        await page.waitForFunction(()=>!tripActive && !finishingTrip);
        const limitedId=await page.evaluate(()=>lastFinishedSessionId);
        const limited=await page.request.get(`${base}/api/v2/trips/${limitedId}/export`,{headers:identity.headers});
        const limitedData=await limited.json();
        assert.equal(limited.status(),200);
        assert.equal(limitedData.end_reason,'duration_limit');
        assert.equal(limitedData.ended_at_ms-limitedData.started_at_ms,86400000);

        const queuePair=await page.evaluate(async()=>{
            const good=crypto.randomUUID(),bad='!!!rejected-'+crypto.randomUUID(),now=Math.round(performance.timeOrigin+performance.now());
            const payload={user_id:AutoXAKSession.getUserId(),car_id:currentCarId,telemetry_stream:[],
                raw_recording:{started_at_ms:now,ended_at_ms:now,gps:[],motion:[]}};
            await AutoXAKOfflineStore.savePendingTrip({...payload,session_id:bad});
            await AutoXAKOfflineStore.savePendingTrip({...payload,session_id:good});
            return {good,bad};
        });
        await page.route('**/api/v1/telemetry/session',route=>{
            if(route.request().postDataJSON().session_id===queuePair.bad) return route.fulfill({status:422,contentType:'application/json',body:'{}'});
            return route.continue();
        });
        await page.evaluate(()=>AutoXAKOfflineStore.syncPendingTrips());
        assert.deepEqual(await page.evaluate(async()=>(await AutoXAKOfflineStore.getPendingTrips()).map(item=>item.session_id)),[queuePair.bad]);
        await page.unroute('**/api/v1/telemetry/session');
        await page.evaluate(id=>AutoXAKOfflineStore.removePendingTrip(id),queuePair.bad);

        const history=await page.request.get(`${base}/api/v1/users/${identity.user}/analytics/period`,{headers:identity.headers});
        assert.equal((await history.json()).trips.length,8);
        assert.deepEqual(errors,[]);
        console.log('Recording: iOS gesture, one GPS watch, independent IMU, weak GPS, denied motion, GPS-only history, offline retry and failed-save recovery passed');
        await context.close();
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
