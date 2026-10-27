'use strict';
(() => {
  const expected = ['catboost_direct.cbm', 'catboost_residual.cbm', 'model_card.json', 'provenance.json'];
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
      container.replaceChildren();
      for (const row of data.models) {
        const active = row.id === data.active_model_id;
        const card = document.createElement('article');
        if (active) card.append(element('span', 'Применяется', 'badge'));
        card.append(element('h3', row.name), element('p', row.description));
        card.append(element('p', `Версия: ${row.model_version} · ${(row.size_bytes / 1024).toFixed(0)} КиБ`, 'muted'));
        if (row.created_at) card.append(element('p', `Загружена: ${new Date(row.created_at).toLocaleString('ru-RU')}`, 'muted'));
        const actions = element('div', '', 'actions');
        const activate = element('button', row.builtin ? 'Вернуться к встроенной' : 'Применить');
        activate.type = 'button';
        activate.disabled = active;
        activate.dataset.unavailable = String(active);
        activate.addEventListener('click', () => action(async () => {
          await api(`/${row.id}/activate`, {method: 'POST'});
          message(`Применяется модель «${row.name}». Старые результаты сохранены.`);
        }));
        actions.append(activate);
        if (!row.builtin) {
          const remove = element('button', 'Удалить', 'danger');
          remove.type = 'button';
          remove.disabled = active;
          remove.dataset.unavailable = String(active);
          if (active) remove.title = 'Сначала примените другую модель';
          remove.addEventListener('click', () => {
            if (confirm(`Удалить модель «${row.name}»?`)) action(async () => {
              await api(`/${row.id}`, {method: 'DELETE'});
              message('Модель удалена.');
            });
          });
          actions.append(remove);
        }
        card.append(actions);
        container.append(card);
      }
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
    document.getElementById('file-status').textContent = expected.map(name => `${names.includes(name) ? '✓' : '○'} ${name}`).join('\n');
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      const files = Array.from(document.getElementById('files').files);
      if (files.length !== 4 || new Set(files.map(file => file.name)).size !== 4 || files.some(file => !expected.includes(file.name))) {
        throw new Error('Выберите ровно четыре файла с указанными именами.');
      }
      if (files.reduce((sum, file) => sum + file.size, 0) > 20 * 1024 * 1024) throw new Error('Комплект превышает 20 МиБ.');
      message('Файлы загружаются и проверяются. Это может занять до 30 секунд…');
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
      document.getElementById('file-status').textContent = expected.join(', ') + '. До 20 МиБ на комплект.';
      message('Комплект проверен и сохранён. Нажмите «Применить», чтобы использовать его в тестовой среде.');
    });
  });
  document.getElementById('refresh').addEventListener('click', () => action(async () => {}));
  action(async () => {});
})();
