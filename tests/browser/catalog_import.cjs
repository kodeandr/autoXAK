const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const base=process.env.AUTOXAK_BASE_URL||'http://127.0.0.1:8000';
const fixture=(...args)=>JSON.parse(execFileSync(process.env.AUTOXAK_PYTHON||'python',[path.join(__dirname,'catalog_fixture.py'),...args],{encoding:'utf8',cwd:path.join(__dirname,'../..')}));
(async()=>{
 const setup=fixture('prepare');
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext(),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>{window.__alerts=[];window.alert=text=>__alerts.push(text);});
  await page.goto(base+'/login');await page.click('.btn-guest');await page.waitForURL(base+'/');
  const identity=await page.evaluate(()=>({user:AutoXAKSession.getUserId(),headers:AutoXAKSession.getAuthHeaders()}));
  const profileUrl=base+'/api/v1/users/'+identity.user+'/profile';
  await page.goto(base+'/setup?edit=1');
  await page.waitForFunction(()=>[...document.getElementById('select-make').options].some(o=>o.text==='Toyota'));
  await page.selectOption('#select-make',{label:'Toyota'});
  await page.waitForFunction(()=>[...document.getElementById('select-model').options].some(o=>o.text.startsWith('Corolla')));
  await page.selectOption('#select-model',{label:'Corolla — параметры вручную'});
  assert.equal(await page.locator('#select-trim').isDisabled(),true);
  assert.equal(await page.inputValue('#registry-fuel'),'');
  await page.evaluate(()=>saveRegistryVehicle());
  assert.match((await page.evaluate(()=>__alerts)).at(-1),/расход/);
  assert.equal((await(await page.request.get(profileUrl,{headers:identity.headers})).json()).car_id,'haval_jolion_15t_4wd');
  await page.fill('#registry-fuel','8.5');await page.evaluate(()=>saveRegistryVehicle());
  const profile=await(await page.request.get(profileUrl,{headers:identity.headers})).json();
  assert(profile.car_id.startsWith('catalog_model_'));assert.equal(profile.car_name,'Toyota Corolla');assert.equal(profile.fuel_consumption,8.5);
  await page.reload();await page.waitForFunction(id=>document.getElementById('select-model').value===id,profile.car_id);
  assert.equal(await page.inputValue('#registry-fuel'),'8.5');
  const payload={session_id:crypto.randomUUID(),user_id:identity.user,car_id:profile.car_id,
   telemetry_stream:Array.from({length:100},(_,i)=>({t:i*.02,ax:0,ay:0,az:9.80665,speed:10,lat:55.75,lon:37.61}))};
  const trip=await page.request.post(base+'/api/v1/telemetry/session',{headers:identity.headers,data:payload});
  assert.equal(trip.status(),200);const result=await trip.json();assert(result.cost_savings_rub>0);
  await page.goto(base+'/');await page.waitForLoadState('networkidle');
  assert.match(await page.locator('#cockpit-car-name').innerText(),/Corolla/);
  fixture('deactivate');
  await page.goto(base+'/setup');await page.waitForLoadState('networkidle');
  assert.match(await page.locator('#garage-current-car-name').innerText(),/Corolla/);
  payload.session_id=crypto.randomUUID();
  const second=await(await page.request.post(base+'/api/v1/telemetry/session',{headers:identity.headers,data:payload})).json();
  assert.equal(second.cost_savings_rub,result.cost_savings_rub);
  await page.goto(base+'/history');await page.waitForSelector('.trip-row-card');assert.equal(await page.locator('.trip-row-card').count(),2);
  // A slow model request must preserve a legacy trim and fuel edited meanwhile.
  fixture('activate',setup.snapshot_id);
  await context.clearCookies();await page.evaluate(()=>AutoXAKSession.clearSession());
  await page.goto(base+'/login');await page.click('.btn-guest');await page.waitForURL(base+'/');
  let release;const held=new Promise(resolve=>release=resolve);
  await page.route('**/catalog/taxonomy/models?*',async route=>{await held;await route.continue();});
  await page.goto(base+'/setup?edit=1');await page.waitForSelector('#registry-fuel');
  await page.waitForFunction(()=>document.getElementById('registry-fuel').value!=='');
  await page.fill('#registry-fuel','9.2');const trim=await page.inputValue('#select-trim');
  release();await page.waitForFunction(()=>catalogTree.find(m=>m.id==='haval').taxonomyLoaded===taxonomySnapshot);
  assert.equal(await page.inputValue('#registry-fuel'),'9.2');assert.equal(await page.inputValue('#select-trim'),trim);
  assert.deepEqual(errors,[]);await context.close();
  console.log('Catalog: taxonomy selection, required fuel, profile reload, money/history, deactivation and slow-request preservation passed');
 }finally{await browser.close();fixture('cleanup',setup.snapshot_id,setup.previous_active_snapshot||'null');}
})().catch(error=>{console.error(error);process.exit(1);});
