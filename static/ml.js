'use strict';
(() => {
  const hint = 'Один ZIP с predict.py и весами либо до 16 отдельных файлов. До 20 МиБ.';
  const form = document.getElementById('upload-form');
  const status = document.getElementById('status');
  const container = document.getElementById('models');
  let busy = false;

  function message(text, error = false) {
    status.textContent = text;
    status.classList.toggle('error', error);
  }
  function lock(value) {
    busy = value;
    document.querySelectorAll('button').forEach(button => { button.disabled = value || button.dataset.unavailable === 'true'; });
  }
  async function api(path = '', options = {}) {
    const response = await fetch('/api/v3/ml/models' + path, {
      ...options, cache: 'no-store', credentials: 'omit',
      headers: {'X-Model-Manager': '1', ...options.headers},
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : `Ошибка сервера: ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  async function reload() {
    container.setAttribute('aria-busy', 'true');
    try {
      const data = await api();
      const selected = document.getElementById('run-model').value;
      const modelSelect = document.getElementById('run-model');
      modelSelect.replaceChildren(element('option', 'Выберите модель'));
      modelSelect.firstChild.value = '';
      container.replaceChildren();
      if (!data.models.length) container.append(element('p', 'Комплектов пока нет. Загрузите файлы первого эксперимента.'));
      for (const row of data.models) {
        const card = document.createElement('article');
        card.append(element('h3', row.name), element('p', row.description));
        card.append(element('p', `${row.files.length} файлов · ${(row.size_bytes / 1024).toFixed(0)} КиБ · Сохранён`, 'muted'));
        card.append(element('p', row.files.map(file => file.name).join(', '), 'muted'));
        if (row.created_at) card.append(element('p', `Загружена: ${new Date(row.created_at).toLocaleString('ru-RU')}`, 'muted'));
        const actions = element('div', '', 'actions');
        const option = element('option', row.name + (row.runnable ? '' : ' — нужен predict.py'));
        option.value = row.id;
        option.disabled = !row.runnable;
        modelSelect.append(option);
        if (row.runnable) {
          const test = element('button', 'Испытать');
          test.type = 'button';
          test.addEventListener('click', () => {
            modelSelect.value = row.id;
            document.getElementById('run-title').scrollIntoView({behavior: 'smooth'});
            document.getElementById('test-file').focus();
          });
          actions.append(test);
        }
        const remove = element('button', 'Удалить', 'danger');
        remove.type = 'button';
        remove.addEventListener('click', () => {
          if (confirm(`Удалить комплект «${row.name}»?`)) action(async () => {
            await api(`/${row.id}`, {method: 'DELETE'});
            message('Комплект удалён.');
          });
        });
        actions.append(remove);
        card.append(actions);
        container.append(card);
      }
      if (Array.from(modelSelect.options).some(option => option.value === selected)) modelSelect.value = selected;
      if (data.models.length === 1 && data.models[0].runnable) modelSelect.value = data.models[0].id;
    } finally {
      container.setAttribute('aria-busy', 'false');
    }
  }
  async function action(callback) {
    if (busy) return;
    lock(true);
    try { await callback(); await reload(); }
    catch (error) { message(error.message, true); }
    finally { lock(false); }
  }
  document.getElementById('files').addEventListener('change', event => {
    const names = Array.from(event.target.files, file => file.name);
    document.getElementById('file-status').textContent = names.length ? names.join('\n') : hint;
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      const files = Array.from(document.getElementById('files').files);
      if (!files.length || files.length > 16 || new Set(files.map(file => file.name)).size !== files.length) {
        throw new Error('Выберите от 1 до 16 файлов с разными именами.');
      }
      if (files.reduce((sum, file) => sum + file.size, 0) > 20 * 1024 * 1024) throw new Error('Комплект превышает 20 МиБ.');
      message('Файлы загружаются…');
      const encoded = {};
      for (const file of files) {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
        encoded[file.name] = btoa(binary);
      }
      await api('', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        name: document.getElementById('name').value.trim(), description: document.getElementById('description').value, files: encoded,
      })});
      form.reset();
      document.getElementById('file-status').textContent = hint;
      message('Модель сохранена. Выберите данные ниже и нажмите «Запустить тест».');
    });
  });
  document.getElementById('refresh').addEventListener('click', () => action(async () => {}));
  let testData = null;
  let polling = false;
  async function runAPI(path = '', options = {}) {
    const response = await fetch('/api/v3/ml/runs' + path, {
      ...options, cache: 'no-store', credentials: 'omit',
      headers: {'X-Model-Manager': '1', ...options.headers},
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : `Ошибка проверки: ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  }
  const displayNumber = value => typeof value === 'number' ? value.toFixed(4) : '—';
  async function refreshRuns() {
    if (polling) return;
    polling = true;
    try {
      const data = await runAPI();
      const runtime = document.getElementById('runner-status');
      runtime.textContent = data.runner.ready ? 'Вычислитель готов · до 30 секунд · 512 МиБ · одна проверка одновременно' : (data.runner.message || 'Вычислитель не отвечает');
      document.getElementById('run-button').dataset.unavailable = String(!data.runner.ready);
      document.getElementById('run-button').disabled = busy || !data.runner.ready;
      const area = document.getElementById('runs');
      area.replaceChildren();
      if (!data.runs.length) area.append(element('p', 'Проверок пока нет.'));
      const names = {queued:'В очереди', running:'Выполняется', succeeded:'Завершено', failed:'Ошибка'};
      for (const row of data.runs) {
        const card = document.createElement('article');
        card.dataset.runId = row.id;
        card.append(element('h3', row.model_name), element('p', names[row.status] || row.status));
        if (row.wall_seconds != null) card.append(element('p', `Время: ${row.wall_seconds.toFixed(2)} с`, 'muted'));
        if (row.error) card.append(element('pre', row.error));
        if (row.result) {
          card.append(element('p', `Сравнено: ${row.result.compared_trips} · MAE: ${displayNumber(row.result.mae_l)} л · WAPE: ${displayNumber(row.result.wape_percent)} %`));
          const scroll = element('div', '', 'table-scroll');
          const table = document.createElement('table');
          const header = document.createElement('tr');
          ['Поездка','Прогноз, л','Контроль, л','Разница, л','Источник','Примечание'].forEach(label => header.append(element('th', label)));
          table.append(header);
          row.result.trips.forEach(trip => {
            const line = document.createElement('tr');
            [trip.trip_id || String(trip.index + 1), displayNumber(trip.estimated_fuel_l), displayNumber(trip.observed_fuel_l),
              displayNumber(trip.error_l), trip.reference_source || '—', trip.comparison_note || trip.prediction.status || ''].forEach(value => line.append(element('td', value)));
            table.append(line);
          });
          scroll.append(table);
          card.append(scroll);
          const raw = document.createElement('details');
          raw.append(element('summary', 'Полный ответ модели'), element('pre', JSON.stringify(row.result.trips.map(trip => trip.prediction), null, 2)));
          card.append(raw);
          card.append(element('p', 'Контрольные значения заявлены автором данных. Численная ошибка не является сертификацией точности.', 'muted'));
        }
        if (row.logs) {
          const details = document.createElement('details');
          details.append(element('summary', 'Вывод модели'), element('pre', row.logs));
          card.append(details);
        }
        const download = element('a', 'Скачать результат JSON');
        download.href = '/api/v3/ml/runs/' + row.id;
        download.download = `run-${row.id}.json`;
        card.append(download);
        if (!['queued','running'].includes(row.status)) {
          const remove = element('button', 'Удалить результат', 'danger');
          remove.type = 'button';
          remove.addEventListener('click', () => action(async () => {
            await runAPI('/' + row.id, {method:'DELETE'});
            await refreshRuns();
          }));
          const actions = element('div', '', 'actions');
          actions.append(remove);
          card.append(actions);
        }
        area.append(card);
      }
    } catch (error) { document.getElementById('runner-status').textContent = error.message; }
    finally { polling = false; }
  }
  document.getElementById('test-file').addEventListener('change', event => action(async () => {
    const file = event.target.files[0];
    testData = null;
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) throw new Error('Данные превышают 5 МиБ.');
    testData = JSON.parse(await file.text());
    document.getElementById('my-trip').value = '';
    document.getElementById('data-source').textContent = 'Выбран файл: ' + file.name;
  }));
  document.getElementById('example-data').addEventListener('click', () => action(async () => {
    const response = await fetch('/api/v3/ml/example-data', {cache:'no-store'});
    if (!response.ok) throw new Error('Не удалось получить пример данных');
    testData = await response.json();
    document.getElementById('test-file').value = '';
    document.getElementById('my-trip').value = '';
    document.getElementById('data-source').textContent = 'Синтетический пример для проверки интерфейса, не дорожной точности';
  }));
  document.getElementById('my-trip').addEventListener('change', () => {
    testData = null;
    document.getElementById('test-file').value = '';
    document.getElementById('data-source').textContent = 'Выбрана поездка из истории';
  });
  async function myTrips() {
    const headers = window.AutoXAKSession?.getAuthHeaders() || {};
    if (!headers.Authorization) return;
    try {
      const response = await fetch('/api/v2/trips?limit=50', {headers, cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const select = document.getElementById('my-trip');
      data.trips.filter(trip => trip.status === 'completed').forEach(trip => {
        const option = element('option', `${new Date(trip.started_at_ms).toLocaleString('ru-RU')} · ${trip.trip_id}`);
        option.value = trip.trip_id;
        select.append(option);
      });
    } catch {}
  }
  document.getElementById('run-form').addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      let data = testData;
      const trip = document.getElementById('my-trip').value;
      if (trip) {
        const response = await fetch('/api/v2/trips/' + encodeURIComponent(trip) + '/export', {
          headers: window.AutoXAKSession.getAuthHeaders(), cache:'no-store',
        });
        if (!response.ok) throw new Error('Не удалось получить выбранную поездку');
        const text = await response.text();
        if (new TextEncoder().encode(text).length > 5 * 1024 * 1024) throw new Error('Поездка превышает лимит 5 МиБ');
        data = JSON.parse(text);
      }
      if (!data) throw new Error('Выберите поездку, загрузите JSON или возьмите пример данных.');
      await runAPI('', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        model_id:document.getElementById('run-model').value, data,
      })});
      message('Проверка поставлена в очередь. Результат появится ниже автоматически.');
      await refreshRuns();
    });
  });
  action(async () => {});
  refreshRuns();
  myTrips();
  setInterval(refreshRuns, 1500);
})();
