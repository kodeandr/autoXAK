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
  const code = ['from fastapi import FastAPI','from fastapi.staticfiles import StaticFiles',
    'from app.api.ml import router','import uvicorn','app=FastAPI()','app.include_router(router)',
    'app.mount("/static",StaticFiles(directory="static"))',
    `uvicorn.run(app,host="127.0.0.1",port=${port},log_level="warning")`].join(';');
  const server = spawn(process.env.PYTHON_BIN || 'python', ['-c',code], {cwd:root,
    env:{...process.env,APP_ENV:'test',ML_UPLOAD_ENABLED:'true',ML_STORAGE_DIR:temporary},
    stdio:['ignore','pipe','pipe']});
  let logs = '';
  server.stdout.on('data', chunk => {logs += chunk;});
  server.stderr.on('data', chunk => {logs += chunk;});
  let browser;
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
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    const page=await browser.newPage({viewport:{width:390,height:844}});
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
    await page.waitForFunction(()=>document.querySelector('#runs').textContent.includes('Завершено'),{timeout:45000});
    let data=await (await fetch(base+'/api/v3/ml/runs')).json();
    assert.equal(data.runs[0].status,'succeeded',JSON.stringify(data.runs[0]));
    assert(data.runs[0].result.trips[0].estimated_fuel_l>0);
    assert.equal(data.runs[0].result.compared_trips,1);
    assert.equal(data.runs[0].result.accuracy_certified,false);
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
    await page.waitForFunction(()=>document.querySelector('#runs').textContent.includes('browser-visible-model-error'),{timeout:45000});
    data=await (await fetch(base+'/api/v3/ml/runs')).json();
    assert.equal(data.runs[0].status,'failed');
    assert(data.runs[0].error.includes('browser-visible-model-error'));
    assert.deepEqual(errors,[]);
    console.log('Workbench: one ZIP -> real Docker prediction -> control metrics -> persisted results; Python failure visible in browser; mobile layout passed.');
  } finally {
    if(browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
    docker(['rm','-f',controller],false);
    const leftovers=docker(['ps','-aq','--filter','label=autoxak.ml.volume='+volume],false).stdout.trim().split(/\s+/).filter(Boolean);
    if(leftovers.length) docker(['rm','-f',...leftovers],false);
    docker(['volume','rm',volume],false);
    fs.rmSync(temporary,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
