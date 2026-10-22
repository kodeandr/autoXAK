const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.AUTOXAK_BASE_URL || 'http://127.0.0.1:8000';

(async()=>{
    const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
    try {
        const context = await browser.newContext();
        const page = await context.newPage();
        const errors=[];
        page.on('pageerror',error=>errors.push(error.message));
        await page.goto(base+'/login');
        await page.click('.btn-guest');
        await page.waitForURL(base+'/');
        await page.waitForFunction(()=>window.AutoXAKOfflineStore);
        const headers=await page.evaluate(()=>AutoXAKSession.getAuthHeaders());
        const owner=await page.evaluate(()=>AutoXAKSession.getUserId());
        await page.evaluate(()=>{
            let clock=Date.now(),gps;
            Date.now=()=>clock;
            window.__advance=ms=>{clock+=ms;};
            Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>Promise.resolve('granted')});
            Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
                watchPosition:callback=>{gps=callback;return 0;},clearWatch:()=>{}
            }});
            window.__gps=speed=>gps({timestamp:clock,coords:{latitude:55.75,longitude:37.61,accuracy:5,speed}});
            window.__motion=(gravity,linear=[0,0,0],rotation=0,withLinear=true)=>{
                clock+=20;
                const event=new Event('devicemotion');
                Object.defineProperties(event,{
                    acceleration:{value:withLinear?{x:linear[0],y:linear[1],z:linear[2]}:null},
                    accelerationIncludingGravity:{value:{x:gravity[0]+linear[0],y:gravity[1]+linear[1],z:gravity[2]+linear[2]}},
                    rotationRate:{value:{alpha:rotation,beta:0,gamma:0}},interval:{value:20}
                });
                window.dispatchEvent(event);
            };
        });
        const start=async()=>{await page.click('[onclick="requestStartTrip()"]');await page.waitForFunction(()=>tripActive);};
        await start();
        const geometry=await page.evaluate(()=>{
            const counts=[];
            for(const up of [[0,0,1],[0,1,0],[1,0,0],[0.6,0.8,0]]) {
                const g=up.map(value=>value*9.81);
                for(let i=0;i<70;i++) __motion(g);
                const before=liveBumpsCount;
                for(let i=0;i<5;i++) __motion(g,up.map(value=>value*5));
                const point=telemetryStream.at(-1);
                counts.push({difference:liveBumpsCount-before,horizontal:Math.hypot(point.ax,point.ay),vertical:point.az-9.81});
                for(let i=0;i<70;i++) __motion(g);
            }
            const beforeRotation=liveBumpsCount;
            for(let i=0;i<100;i++) {
                const angle=i*Math.PI/100;
                __motion([0,9.81*Math.sin(angle),9.81*Math.cos(angle)],[0,0,0],90);
            }
            // Gravity-inclusive devices must also work in portrait orientation.
            for(let i=0;i<80;i++) __motion([0,9.81,0],[0,0,0],0,false);
            const beforeFallback=liveBumpsCount;
            for(let i=0;i<5;i++) __motion([0,9.81,0],[0,5,0],0,false);
            return {counts,beforeRotation,afterRotation:beforeFallback,fallback:liveBumpsCount-beforeFallback};
        });
        assert(geometry.counts.every(value=>value.difference===1));
        assert(geometry.counts.every(value=>value.horizontal<0.001 && Math.abs(value.vertical-5)<0.001));
        assert.equal(geometry.afterRotation,geometry.beforeRotation);
        assert.equal(geometry.fallback,1);
        assert.equal(await page.locator('#trip-live-savings').innerText(),'+0.00 ₽');
        await page.evaluate(()=>finishActiveTrip());
        assert.equal(await page.locator('#sum-bumps').innerText(),'5');
        assert.equal(await page.locator('#sum-savings-rub').innerText(),'+0.00 ₽');
        assert.equal(await page.locator('#sum-offline-banner').evaluate(el=>el.classList.contains('visible')),false);
        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));

        const before=(await (await page.request.get(`${base}/api/v1/users/${owner}/dashboard`,{headers})).json());
        await start();
        await page.evaluate(()=>{
            __gps(10);
            for(let i=0;i<1000;i++) {
                __motion([0,9.81,0]);
                if((i+1)%50===0) __gps(10);
            }
        });
        const previewResponse=page.waitForResponse('**/api/v1/telemetry/preview');
        await page.evaluate(()=>updateLiveEstimate());
        const preview=await (await previewResponse).json();
        assert(preview.cost_savings_rub>0);
        assert.equal(await page.locator('#trip-live-savings').innerText(),`+${preview.cost_savings_rub.toFixed(2)} ₽`);
        assert.deepEqual(await (await page.request.get(`${base}/api/v1/users/${owner}/dashboard`,{headers})).json(),before);
        await page.evaluate(()=>finishActiveTrip());
        assert.equal(await page.locator('#sum-savings-rub').innerText(),`+${preview.cost_savings_rub.toFixed(2)} ₽`);
        const after=await (await page.request.get(`${base}/api/v1/users/${owner}/dashboard`,{headers})).json();
        assert.equal(after.total_trips,before.total_trips+1);

        // A response for a finished recording must not update the next trip.
        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
        await start();
        await page.evaluate(()=>{__gps(10);for(let i=0;i<100;i++) __motion([0,9.81,0]);});
        let releaseOldPreview;
        const heldPreview=new Promise(resolve=>{releaseOldPreview=resolve;});
        await page.route('**/api/v1/telemetry/preview',async route=>{
            const payload=route.request().postDataJSON();
            await heldPreview;
            await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
                session_id:payload.session_id,status:'PREVIEW',cost_savings_rub:999
            })}).catch(()=>{});
        });
        const requested=page.waitForRequest('**/api/v1/telemetry/preview');
        await page.evaluate(()=>{window.__oldPreview=updateLiveEstimate();});
        await requested;
        await page.evaluate(()=>finishActiveTrip());
        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
        await start();
        releaseOldPreview();
        await page.evaluate(()=>window.__oldPreview);
        assert.equal(await page.locator('#trip-live-savings').innerText(),'+0.00 ₽');
        await page.unroute('**/api/v1/telemetry/preview');
        await page.evaluate(()=>finishActiveTrip());

        // Long recordings retain their raw samples while bounding preview uploads.
        await page.evaluate(()=>document.getElementById('summary-modal').classList.remove('active'));
        await start();
        await page.evaluate(()=>{
            __gps(10);
            for(let i=0;i<6500;i++) {__motion([0,9.81,0]);if((i+1)%50===0) __gps(10);}
        });
        const bounded=page.waitForRequest('**/api/v1/telemetry/preview');
        await page.evaluate(()=>updateLiveEstimate());
        const sampled=(await bounded).postDataJSON();
        assert(sampled.telemetry_stream.length<=6000);
        assert(sampled.sampling_stride>1);
        assert.equal(await page.evaluate(()=>telemetryStream.length),6500);
        assert.equal(sampled.telemetry_stream.at(-1).t,await page.evaluate(()=>telemetryStream.at(-1).t));
        await page.evaluate(()=>finishActiveTrip());
        assert.deepEqual(errors,[]);
        console.log('Live metrics: portrait/landscape/tilted impulses without GPS, rotation veto, gravity fallback, zero stationary money, hidden quality banner and non-crediting shared-model preview passed');
        await context.close();
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
