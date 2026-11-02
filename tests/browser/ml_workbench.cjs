/* End-to-end: real API, Docker controller, candidate container and browser. */
const {chromium} = require('playwright');
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'autoxak-workbench-'));
  const suffix = path.basename(temporary).toLowerCase();
  const volume = suffix + '-data', controller = suffix + '-controller';
  function docker(args, check = true) {
    const result = spawnSync('docker', ['--host=unix:///var/run/docker.sock', ...args], {encoding:'utf8', timeout:20000});
    if (check && result.status !== 0) throw new Error(result.stderr || String(result.error));
    return result;
  }
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const artifacts = path.resolve(root, process.env.ML_TEST_ARTIFACTS || 'playwright-artifacts/ml');
  fs.mkdirSync(artifacts, {recursive:true});
  const code = ['from fastapi import FastAPI','from fastapi.staticfiles import StaticFiles',
    'from app.api.ml import router','import uvicorn','app=FastAPI()','app.include_router(router)',
    'app.get("/ready")(lambda: {"status":"ok"})',
    'app.mount("/static",StaticFiles(directory="static"))',
    `uvicorn.run(app,host="127.0.0.1",port=${port},log_level="warning")`].join(';');
  let logs = '';
  function startServer() {
    const child = spawn(process.env.PYTHON_BIN || 'python', ['-c',code], {cwd:root,
      env:{...process.env,APP_ENV:'test',ML_UPLOAD_ENABLED:'true',ML_STORAGE_DIR:temporary},
      stdio:['ignore','pipe','pipe']});
    child.stdout.on('data', chunk => {logs = (logs + chunk).slice(-20000);});
    child.stderr.on('data', chunk => {logs = (logs + chunk).slice(-20000);});
    return child;
  }
  async function stopServer() {
    if (server.exitCode !== null || server.signalCode !== null) return;
    server.kill('SIGTERM');
    await new Promise(resolve => server.once('exit',resolve));
  }
  async function waitForServer() {
    for (let attempt=0;attempt<100;attempt++) {
      try { if ((await fetch(base+'/ready')).ok) return; } catch {}
      if (server.exitCode !== null) throw new Error(logs);
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error('API did not restart: '+logs);
  }
  let server = startServer();
  let browser, page;
  try {
    docker(['volume','create','--driver','local','--opt','type=none','--opt','o=bind','--opt','device='+temporary,volume]);
    docker(['run','-d','--name',controller,'--user',`${process.getuid()}:${process.getgid()}`,
      '--group-add',String(fs.statSync('/var/run/docker.sock').gid), '--network=none','--read-only','--cap-drop=ALL',
      '--security-opt=no-new-privileges','--tmpfs','/tmp:rw,noexec,nosuid,size=16m',
      '--mount',`type=volume,src=${volume},dst=/var/lib/autoxak/ml`,
      '--mount','type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
      '-e','ML_STORAGE_DIR=/var/lib/autoxak/ml','-e','ML_STORAGE_VOLUME='+volume,
      '-e','ML_RUN_IMAGE=autoxak-ml-runtime:qa','autoxak-ml-controller:qa']);
    let ready = false;
    for(let attempt=0;attempt<100;attempt++) {
      try {if((await (await fetch(base+'/api/v3/ml/runs')).json()).runner.ready){ready=true;break;}} catch {}
      if(server.exitCode!==null) throw new Error(logs);
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    const debug = docker(['logs',controller],false);
    assert(ready, debug.stdout + debug.stderr + logs + (fs.existsSync(path.join(temporary,'runner.json')) ? fs.readFileSync(path.join(temporary,'runner.json'),'utf8') : ' no heartbeat'));
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    page=await browser.newPage({viewport:{width:390,height:844}});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/ml');
    const archive=Buffer.from(await (await fetch(base+'/api/v3/ml/example-package')).arrayBuffer());
    await page.locator('#name').fill('Browser executable model');
    await page.locator('#files').setInputFiles({name:'model.zip',mimeType:'application/zip',buffer:archive});
    await page.getByRole('button',{name:'Загрузить',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Модель сохранена'));
    await page.getByRole('button',{name:'Взять пример данных'}).click();
    await page.waitForFunction(()=>!document.getElementById('run-button').disabled);
    await page.getByRole('button',{name:'Запустить тест'}).click();
    await page.waitForFunction(()=>document.querySelector('#runs').textContent.includes('Завершено'),null,{timeout:45000});
    let data=await (await fetch(base+'/api/v3/ml/runs')).json();
    assert.equal(data.runs[0].status,'succeeded',JSON.stringify(data.runs[0]));
    assert(data.runs[0].result.trips[0].estimated_fuel_l>0);
    assert.equal(data.runs[0].result.compared_trips,1);
    assert.equal(data.runs[0].result.accuracy_certified,false);
    assert.match(data.runs[0].runtime_image_id,/^sha256:[0-9a-f]{64}$/);
    const successful = data.runs[0];
    assert(await page.locator('#runs table').count());
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#runs').textContent.includes('Завершено'));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.locator('#name').fill('Failing model');
    await page.locator('#files').setInputFiles({name:'predict.py',mimeType:'text/x-python',
      buffer:Buffer.from('def predict_trip(recording): raise ValueError("browser-visible-model-error")')});
    await page.getByRole('button',{name:'Загрузить',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Модель сохранена'));
    const models=await (await fetch(base+'/api/v3/ml/models')).json();
    await page.locator('#run-model').selectOption(models.models.find(model=>model.name==='Failing model').id);
    await page.getByRole('button',{name:'Взять пример данных'}).click();
    await page.waitForFunction(()=>!document.getElementById('run-button').disabled);
    await page.getByRole('button',{name:'Запустить тест'}).click();
    await page.waitForFunction(()=>document.querySelector('#runs').textContent.includes('browser-visible-model-error'),null,{timeout:45000});
    data=await (await fetch(base+'/api/v3/ml/runs')).json();
    assert.equal(data.runs[0].status,'failed');
    assert(data.runs[0].error.includes('browser-visible-model-error'));
    assert.equal(data.runs[0].result,null);
    const failed = data.runs[0];
    await page.screenshot({path:path.join(artifacts,'results-before-restart.png'),fullPage:true});

    // Stop the real controller, then enqueue within its 10-second heartbeat window.
    // New API and controller processes must recover the same queue and saved results.
    docker(['stop','--time','1',controller]);
    const fixture=await (await fetch(base+'/api/v3/ml/example-data')).json();
    const queued=[];
    for (let index=0;index<2;index++) {
      const response=await fetch(base+'/api/v3/ml/runs', {method:'POST',
        headers:{'Content-Type':'application/json','X-Model-Manager':'1'},
        body:JSON.stringify({model_id:successful.model_id,data:{...fixture,trip_id:'queue-'+index}})});
      assert.equal(response.status,202);
      const row=await response.json();
      assert.equal(row.status,'queued');
      queued.push(row);
    }
    await stopServer();
    server=startServer();
    await waitForServer();
    for (const expected of [successful,failed,...queued]) {
      const persisted=await (await fetch(base+'/api/v3/ml/runs/'+expected.id)).json();
      assert.deepEqual(persisted,expected);
    }
    docker(['start',controller]);
    let completed=[];
    for (let attempt=0;attempt<180;attempt++) {
      completed=await Promise.all(queued.map(async row=>(await fetch(base+'/api/v3/ml/runs/'+row.id)).json()));
      assert(!completed.some(row=>row.status==='failed'),JSON.stringify(completed));
      if(completed.every(row=>row.status==='succeeded')) break;
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    assert(completed.every(row=>row.status==='succeeded'), 'Persisted queue did not finish');
    assert(completed[1].started_at>=completed[0].finished_at, 'Queue must execute sequentially');
    for (let index=0;index<2;index++) {
      assert.equal(completed[index].input_sha256,queued[index].input_sha256);
      assert.deepEqual(completed[index].model_files,queued[index].model_files);
      assert.equal(completed[index].runtime_image_id,successful.runtime_image_id);
    }
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#runs').textContent.includes('browser-visible-model-error'));
    assert.equal(await page.locator('#runs table').count(),3);
    await page.screenshot({path:path.join(artifacts,'results-after-restart.png'),fullPage:true});
    // Exercise the same standalone command used by test_ml.sh against the real runner.
    const smoke=spawnSync(process.env.PYTHON_BIN||'python',
      ['scripts/ml_smoke.py','--base-url',base,'--run-synthetic'],{cwd:root,encoding:'utf8',timeout:110000});
    assert.equal(smoke.status,0,smoke.stdout+smoke.stderr);
    const smokeReport=JSON.parse(smoke.stdout);
    assert.equal(smokeReport.status,'succeeded');
    fs.writeFileSync(path.join(artifacts,'report.json'),JSON.stringify({
      runtime_image_id:successful.runtime_image_id, successful,failed,queued,completed,
      smoke:smokeReport,api_restarted:true,controller_restarted:true,browser_errors:errors,
      synthetic_only:true,full_application_database_readiness_tested:false,
    },null,2));
    assert.deepEqual(errors,[]);
    console.log('Workbench passed: ZIP upload, real container, metrics, visible Python failure, queue recovery, API/controller restart, mobile browser and synthetic smoke.');
  } catch(error) {
    if(page) await page.screenshot({path:path.join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});
    throw error;
  } finally {
    fs.writeFileSync(path.join(artifacts,'api.log'),logs);
    const controllerLogs=docker(['logs',controller],false);
    fs.writeFileSync(path.join(artifacts,'controller.log'),(controllerLogs.stdout||'')+(controllerLogs.stderr||''));
    if(browser) await browser.close();
    await stopServer();
    docker(['rm','-f',controller],false);
    const leftovers=docker(['ps','-aq','--filter','label=autoxak.ml.volume='+volume],false).stdout.trim().split(/\s+/).filter(Boolean);
    if(leftovers.length) docker(['rm','-f',...leftovers],false);
    docker(['volume','rm',volume],false);
    fs.rmSync(temporary,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
