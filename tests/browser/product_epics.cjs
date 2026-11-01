const {chromium, webkit} = require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.AUTOXAK_BASE_URL||'http://127.0.0.1:8000';
const output=process.env.AUTOXAK_SCREENSHOTS||'/tmp/autoxak-product-screens';
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844}}), page=await context.newPage(), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__alerts=[];window.alert=s=>__alerts.push(s);});
  await page.goto(base+'/login');
  await page.getByRole('button',{name:'Войти',exact:true}).click();
  await page.fill('#auth-login','ab12');await page.fill('#auth-password','short');await page.click('#auth-submit');
  assert.match(await page.locator('#login-error').innerText(),/5–15/);
  await page.evaluate(()=>showAuthForm('register'));
  const login='Audit'+Math.random().toString(36).replace(/[^a-z]/g,'').slice(0,9).padEnd(8,'z');
  await page.fill('#auth-login',login);await page.fill('#auth-password','Strong!Pass');
  await page.click('#toggle-password');assert.equal(await page.locator('#auth-password').getAttribute('type'),'text');
  await page.click('#auth-submit');await page.waitForURL(base+'/');
  await page.waitForFunction(()=>typeof requestStartTrip==='function');
  const identity=await page.evaluate(()=>({user:AutoXAKSession.getUserId(),headers:AutoXAKSession.getAuthHeaders()}));
  for(const size of [{width:320,height:568},{width:390,height:844},{width:414,height:896},{width:768,height:1024},{width:1440,height:900},{width:844,height:390}]){
   await page.setViewportSize(size);
   for(const path of ['/','/setup','/history']) {
    await page.goto(base+path);await page.waitForLoadState('networkidle');
    const g=await page.evaluate(()=>{const box=document.querySelector('.mobile-container').getBoundingClientRect(),nav=document.querySelector('nav').getBoundingClientRect();return {width:box.width,x:box.x,overflow:document.documentElement.scrollWidth>innerWidth,navBottom:nav.bottom,h:innerHeight,navCount:document.querySelectorAll('nav a').length,bg:getComputedStyle(document.body).backgroundColor};});
    assert.equal(g.width,Math.min(size.width,414));assert.equal(g.overflow,false);assert.equal(g.navCount,3);assert(Math.abs(g.navBottom-g.h)<2);assert.equal(g.bg,'rgb(18, 20, 23)');
    await page.screenshot({path:`${output}/${size.width}x${size.height}-${path==='/'?'home':path.slice(1)}.png`});
   }
  }
  await page.setViewportSize({width:390,height:844});await page.goto(base+'/setup?edit=1');
  await page.waitForFunction(()=>document.getElementById('select-make').options.length>0);
  await page.selectOption('#select-make',{label:'Haval'});
  await page.selectOption('#select-trim','haval_jolion_15t_4wd');await page.fill('#registry-fuel','8.5');
  await page.evaluate(()=>saveRegistryVehicle());
  const profile=await(await page.request.get(`${base}/api/v1/users/${identity.user}/profile`,{headers:identity.headers})).json();
  assert.equal(profile.car_id,'haval_jolion_15t_4wd');assert.equal(profile.fuel_consumption,8.5);
  await page.goto(base+'/');await page.waitForLoadState('networkidle');
  await page.evaluate(()=>{
   let clock=Date.now(),gps;Date.now=()=>clock;
   window.__permissionCalls=0;
   Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>{__permissionCalls++;return Promise.resolve('granted');}});
   Object.defineProperty(navigator,'geolocation',{configurable:true,value:{watchPosition:callback=>{gps=callback;return 0;},clearWatch:()=>{}}});
   window.__record=()=>{
    let meters=0, speed=0;
    for(let i=0;i<=3000;i++){
     const t=i*.02;const ax=t>=5&&t<7?3:t>=12&&t<14?-3:0;
     speed=Math.max(0,speed+ax*.02);meters+=speed*.02;
     if(i%50===0)gps({timestamp:clock,coords:{latitude:55.75+meters/111194.9266,longitude:37.61,accuracy:5,speed}});
     const event=new Event('devicemotion');const z=(i>=500&&i<506)?5:0;
     Object.defineProperties(event,{acceleration:{value:{x:ax,y:0,z}},accelerationIncludingGravity:{value:{x:ax,y:0,z:9.80665+z}},rotationRate:{value:{alpha:0,beta:0,gamma:0}},interval:{value:20}});
     window.dispatchEvent(event);clock+=20;
    }
   };
  });
  await page.click('[onclick="requestStartTrip()"]');await page.waitForFunction(()=>tripActive);
  await page.evaluate(()=>__record());
  assert.equal(await page.evaluate(()=>motionSamples.length),3001);
  assert.equal(await page.evaluate(()=>liveBumpsCount),1);
  const slider=page.locator('#swipe-thumb'), container=page.locator('#swipe-container');
  await slider.scrollIntoViewIfNeeded();const thumb=await slider.boundingBox(),track=await container.boundingBox();
  await page.mouse.move(thumb.x+15,thumb.y+20);await page.mouse.down();await page.mouse.move(track.x+track.width-10,thumb.y+20,{steps:14});await page.mouse.up();
  await page.waitForSelector('#summary-modal.active');
  const result=await page.evaluate(()=>finishedResult);
  assert(result && Number.isFinite(result.cost_savings_rub));assert(result.cost_savings_rub>0);
  assert(result.harsh_accel_count>=1 && result.harsh_brake_count>=1);assert(result.road_anomalies_count>=1);
  assert.equal(await page.locator('#sum-smooth').innerText(),`${result.smooth_score}/100`);
  const raw=await(await page.request.get(`${base}/api/v2/trips/${result.session_id}/export`,{headers:identity.headers})).json();
  assert.equal(raw.point_counts.motion,3001);assert.equal(raw.point_counts.gps,61);
  const before=await(await page.request.get(`${base}/api/v1/users/${identity.user}/dashboard`,{headers:identity.headers})).json();assert.equal(before.total_trips,1);assert.equal(before.total_savings_rub,result.cost_savings_rub);
  await page.screenshot({path:output+'/trip-summary.png'});
  await page.click('[onclick="claimToPiggyBank()"]');await page.waitForFunction(()=>!document.getElementById('summary-modal').classList.contains('active'));
  const after=await(await page.request.get(`${base}/api/v1/users/${identity.user}/dashboard`,{headers:identity.headers})).json();
  assert.equal(after.total_trips,1);assert.equal(after.total_savings_rub,result.cost_savings_rub);
  await page.goto(base+'/history');await page.waitForSelector('.trip-row-card');await page.click('.btn-trip-details');
  assert.equal(await page.locator('#det-smooth').innerText(),`${result.smooth_score}/100`);
  assert.deepEqual(errors,[]);console.log('Product epics: credentials, 18 responsive screens, PostgreSQL garage, 50 Hz synthetic capture, moving shock, maneuvers, pointer finish, raw retention, atomic credits, history passed');
  await context.close();
  // Real touch input from Chromium's device layer exercises Pointer Events and
  // setPointerCapture, in addition to the mouse swipe above.
  const touchContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const touchPage=await touchContext.newPage();
  await touchPage.addInitScript(()=>{
   Object.defineProperty(navigator,'geolocation',{configurable:true,value:{watchPosition:()=>0,clearWatch:()=>{}}});
   if(typeof DeviceMotionEvent!=='undefined')Object.defineProperty(DeviceMotionEvent,'requestPermission',{configurable:true,value:()=>Promise.resolve('granted')});
  });
  await touchPage.goto(base+'/login');await touchPage.click('.btn-guest');await touchPage.waitForURL(base+'/');
  await touchPage.click('[onclick="requestStartTrip()"]');await touchPage.waitForFunction(()=>tripActive);
  const touchThumb=await touchPage.locator('#swipe-thumb').boundingBox(), touchTrack=await touchPage.locator('#swipe-container').boundingBox();
  const device=await touchContext.newCDPSession(touchPage), y=touchThumb.y+touchThumb.height/2;
  await device.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:touchThumb.x+15,y,id:1}]});
  await device.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:touchTrack.x+touchTrack.width-8,y,id:1}]});
  await device.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await touchPage.waitForSelector('#summary-modal.active');
  assert.equal(await touchPage.locator('#sum-savings-rub').innerText(),'+0.00 ₽');
  await touchContext.close();console.log('Touch device swipe: Pointer Events completion passed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
