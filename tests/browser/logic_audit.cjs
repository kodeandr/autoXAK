const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';

(async()=>{
    const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
    try {
        const context = await browser.newContext();
        const page = await context.newPage(), errors=[];
        page.on('pageerror', error=>errors.push(error.message));
        await page.goto(base+'/login');
        await page.click('.btn-guest');
        await page.waitForURL(base+'/');
        await page.waitForLoadState('networkidle');
        const identity=await page.evaluate(()=>({user:AutoXAKSession.getUserId(),headers:AutoXAKSession.getAuthHeaders()}));
        const profileUrl=`${base}/api/v1/users/${identity.user}/profile`;
        await page.request.post(profileUrl,{headers:identity.headers,data:{fuel_price_rub:91.25}});
        await page.goto(base+'/setup');
        await page.waitForLoadState('networkidle');
        await page.evaluate(()=>saveManualVehicle());
        assert.equal((await (await page.request.get(profileUrl,{headers:identity.headers})).json()).fuel_price_rub,91.25);
        await page.request.post(profileUrl,{headers:identity.headers,data:{car_id:'haval_jolion_15t_4wd',car_name:'Updated elsewhere'}});
        await page.reload();
        await page.waitForFunction(()=>document.getElementById('garage-current-car-name').innerText==='Updated elsewhere');

        await page.goto(base+'/');
        await page.waitForLoadState('networkidle');
        await page.evaluate(()=>{
            const original=window.fetch;
            window.__restoreProfileFetch=()=>{window.fetch=original;};
            window.fetch=async(url,options)=>{
                if(String(url).endsWith('/profile') && options.method==='POST' && JSON.parse(options.body).car_name==='Older edit') {
                    await new Promise(resolve=>{window.__releaseProfile=resolve;});
                }
                return original(url,options);
            };
            window.__olderProfile=AutoXAKSession.saveProfile({user_id:AutoXAKSession.getUserId(),car_name:'Older edit'});
        });
        await page.waitForFunction(()=>!!window.__releaseProfile);
        await page.evaluate(async()=>{
            const latest=AutoXAKSession.saveProfile({user_id:AutoXAKSession.getUserId(),car_name:'Latest edit'});
            __releaseProfile();
            await Promise.all([latest,__olderProfile]);
            __restoreProfileFetch();
        });
        assert.equal((await (await page.request.get(profileUrl,{headers:identity.headers})).json()).car_name,'Latest edit');
        assert.equal(await page.evaluate(()=>localStorage.getItem('autoXAK_pending_profile')),null);
        const cachedCockpit=await page.evaluate(async()=>{
            localStorage.setItem('autoXAK_dashboard_cache',JSON.stringify({user_id:AutoXAKSession.getUserId(),totalRub:12.34,totalTrips:3,ghostStatus:'базовый лихач'}));
            const original=window.fetch;
            window.fetch=async()=>{throw new Error('No connection');};
            try {
                await syncCockpitWithBackend();
                return {amount:document.getElementById('cockpit-savings-val').innerText,trips:document.getElementById('cockpit-trips-count').innerText};
            } finally {window.fetch=original;}
        });
        assert.deepEqual(cachedCockpit,{amount:'+12.34 ₽',trips:'3'});
        const timeout=await page.evaluate(async()=>{
            const original=window.fetch;
            let aborted=false;
            window.fetch=async (url, options)=>new Response(new ReadableStream({start(controller){
                options.signal.addEventListener('abort',()=>{aborted=true;controller.error(new DOMException('Timed out','AbortError'));},{once:true});
            }}),{headers:{'Content-Type':'application/json'}});
            const start=performance.now();
            try {await AutoXAKSession.fetchJSON('/body-never-finishes',{},30);return {aborted:false};}
            catch(error){return {aborted,elapsed:performance.now()-start};}
            finally {window.fetch=original;}
        });
        assert(timeout.aborted && timeout.elapsed<1000,JSON.stringify(timeout));

        // A delayed draft must never turn a completed pending trip into RECORDING.
        const id=await page.evaluate(async()=>{
            const now=Date.now(), id=crypto.randomUUID();
            const body={session_id:id,user_id:AutoXAKSession.getUserId(),car_id:currentCarId,
                telemetry_stream:[],raw_recording:{started_at_ms:now-1000,ended_at_ms:now,gps:[],motion:[]}};
            await AutoXAKOfflineStore.savePendingTrip(body);
            await AutoXAKOfflineStore.saveDraftTrip(body);
            return id;
        });
        assert((await page.evaluate(()=>AutoXAKOfflineStore.getPendingTrips())).some(trip=>trip.session_id===id));

        // History must recover a durable trip even without the optional localStorage list.
        await page.route('**/api/v1/telemetry/session',route=>route.fulfill({status:503,body:'unavailable'}));
        await page.goto(base+'/history');
        await page.waitForFunction(id=>loadedTrips.some(trip=>trip.session_id===id),id,{timeout:12000}).catch(async error=>{console.error(await page.evaluate(async()=>({loaded:loadedTrips,pending:await AutoXAKOfflineStore.getPendingTrips(),cache:localStorage.getItem('autoXAK_local_history')})),errors);throw error;});
        await page.unroute('**/api/v1/telemetry/session');
        await page.evaluate(()=>AutoXAKOfflineStore.syncPendingTrips());
        await page.evaluate(()=>fetchHistory());
        assert.equal(await page.evaluate(id=>loadedTrips.filter(trip=>trip.session_id===id).length,id),1);
        await context.setOffline(true);
        await page.evaluate(()=>fetchHistory());
        assert.equal(await page.evaluate(id=>loadedTrips.filter(trip=>trip.session_id===id).length,id),1);
        await context.setOffline(false);

        // Editing a control while its old value is in flight preserves the new revision.
        await page.evaluate(async id=>{
            await AutoXAKOfflineStore.savePendingControl(id,8);
            const original=window.fetch;
            window.__controlValues=[];
            window.fetch=async (url,options)=>{
                if(String(url).includes('/board-computer')) {
                    __controlValues.push(JSON.parse(options.body).board_computer_consumption_l100km);
                    if(__controlValues.length===1) await new Promise(resolve=>{window.__releaseControl=resolve;});
                }
                return original(url,options);
            };
            window.__controlSync=AutoXAKOfflineStore.syncPendingTrips();
        },id);
        await page.waitForFunction(()=>!!window.__releaseControl);
        await page.evaluate(async id=>{
            await AutoXAKOfflineStore.savePendingControl(id,9);
            __releaseControl();
            await __controlSync;
            await AutoXAKOfflineStore.syncPendingTrips();
        },id);
        assert.deepEqual(await page.evaluate(()=>__controlValues),[8,9]);

        // All pages use one fixed end time and explicit all-time history.
        const ends=[];
        await page.route('**/analytics/period?*',async route=>{
            const params=new URL(route.request().url()).searchParams;
            assert.equal(params.get('all_time'),'true');
            ends.push(params.get('end_date'));
            const offset=Number(params.get('offset'));
            const trips=Array.from({length:offset===0?200:1},(_,i)=>({session_id:'paged-'+(offset+i),created_at:'2026-01-01T00:00:00Z',total_savings_rub:1}));
            await route.fulfill({json:{summary:{total_trips:201,total_savings_rub:201},trips}});
        });
        await page.evaluate(()=>fetchHistory());
        assert.equal(await page.evaluate(()=>loadedTrips.filter(trip=>trip.session_id.startsWith('paged-')).length),201);
        assert.equal(new Set(ends).size,1);
        await page.unroute('**/analytics/period?*');

        await page.goto(base+'/');
        await page.waitForLoadState('networkidle');
        const stale=await page.evaluate(async()=>{
            window.__fixes=[];
            Object.defineProperty(navigator,'geolocation',{configurable:true,value:{watchPosition(callback){__fixes.push(callback);return __fixes.length;},clearWatch(){}}});
            await beginActiveTrip();
            const originalOwner=recordingOwnerId,originalCar=recordingCarId;
            currentCarId='changed-during-recording';
            await saveActiveDraft();
            await finishActiveTrip();
            const id=lastFinishedSessionId;
            document.getElementById('summary-modal').classList.remove('active');
            await beginActiveTrip();
            __fixes[0]({timestamp:Date.now(),coords:{latitude:55.75,longitude:37.61,accuracy:5,speed:10}});
            const count=gpsSamples.length;
            await finishActiveTrip();
            return {id,count,originalOwner,originalCar};
        });
        assert.equal(stale.count,0);
        const saved=await page.request.get(`${base}/api/v2/trips/${stale.id}/export`,{headers:identity.headers});
        assert.equal((await saved.json()).vehicle.vehicle_id,stale.originalCar);
        assert.deepEqual(errors,[]);
        console.log('Browser: fuel price preservation, remote garage update, response-body deadline, finalized draft, durable/offline history, control revision, pagination, stale GPS/session snapshot passed');
        await context.close();
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
