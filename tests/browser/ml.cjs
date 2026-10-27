/* Standalone model UI: no PostgreSQL, external identities or user account. */
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'autoxak-ml-browser-'));
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const code = [
    'from fastapi import FastAPI', 'from fastapi.staticfiles import StaticFiles',
    'from app.api.ml import router', 'import uvicorn', 'import importlib.util',
    'assert importlib.util.find_spec("catboost") is None',
    'app=FastAPI()', 'app.include_router(router)',
    'app.mount("/static", StaticFiles(directory="static"), name="static")',
    `uvicorn.run(app,host="127.0.0.1",port=${port},log_level="warning")`,
  ].join(';');
  const server = spawn(process.env.PYTHON_BIN || 'python', ['-c', code], {
    cwd: root, env: {...process.env, APP_ENV: 'test', ML_UPLOAD_ENABLED: 'true', ML_STORAGE_DIR: temporary,
      OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1'}, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  server.stdout.on('data', chunk => { logs += chunk; });
  server.stderr.on('data', chunk => { logs += chunk; });
  let browser;
  try {
    let ready = false;
    for (let attempt = 0; attempt < 80; attempt++) {
      if (server.exitCode !== null) throw new Error(logs);
      try { if ((await fetch(base + '/ml')).ok) { ready = true; break; } } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert(ready, 'UI server failed to start: ' + logs);
    browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
      headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/ml');
    await page.waitForFunction(() => document.getElementById('models').textContent.includes('Комплектов пока нет'));
    await page.locator('#name').fill('Проверка через браузер');
    await page.locator('#files').setInputFiles([
      {name: 'future-model.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([1, 2, 3, 4])},
      {name: 'experiment.json', mimeType: 'application/json', buffer: Buffer.from('{"experiment":"not-trained"}')},
    ]);
    await page.getByRole('button', {name: 'Загрузить', exact: true}).click();
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('Комплект сохранён'));
    let candidate = page.locator('article').filter({hasText: 'Проверка через браузер'});
    assert.equal(await candidate.count(), 1);
    assert.equal(await page.getByRole('button', {name: 'Применить', exact: true}).count(), 0);
    await page.reload();
    candidate = page.locator('article').filter({hasText: 'Проверка через браузер'});
    await candidate.waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    page.once('dialog', dialog => dialog.accept());
    await candidate.getByRole('button', {name: 'Удалить'}).click();
    await page.waitForFunction(() => !document.querySelector('#models').textContent.includes('Проверка через браузер'));
    assert.deepEqual(errors, []);
    console.log('Browser: anonymous artifact upload, persistence, delete and mobile layout passed without CatBoost or activation.');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => { if (server.exitCode !== null) resolve(); else server.once('exit', resolve); });
    fs.rmSync(temporary, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
