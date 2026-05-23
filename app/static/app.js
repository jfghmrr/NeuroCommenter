// ============ tiny utils ============
const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));
const el = (tag, attrs = {}, children = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return e;
};

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...opts,
  });
  if (r.status === 401) {
    location.href = '/login';
    return;
  }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}

function toast(msg, kind = 'ok') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show ' + kind;
  clearTimeout(window._toastT);
  window._toastT = setTimeout(() => (t.className = ''), 3000);
}

// ============ tabs ============
$$('.tab').forEach(b => b.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.tab-pane').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  $('#tab-' + b.dataset.tab).classList.add('active');
  const fn = LOADERS[b.dataset.tab];
  if (fn) fn();
}));

// ============ header: status + run toggle ============
async function refreshStatus() {
  const settings = await api('/api/settings');
  const running = settings.running === '1';
  $('#status-pill').textContent = running ? 'работает' : 'остановлен';
  $('#status-pill').className = 'pill ' + (running ? 'pill-on' : 'pill-off');
  $('#toggleRun').textContent = running ? '■ Остановить' : '▶ Запустить';

  const tgst = await api('/api/tg/status');
  $('#me').textContent = tgst.me
    ? `${tgst.me.name}${tgst.me.username ? ' (@' + tgst.me.username + ')' : ''}`
    : 'не авторизован';
}

$('#toggleRun').onclick = async () => {
  const settings = await api('/api/settings');
  const newVal = settings.running === '1' ? '0' : '1';
  await api('/api/settings', { method: 'POST', body: JSON.stringify({ running: newVal }) });
  toast(newVal === '1' ? 'Запущен' : 'Остановлен');
  refreshStatus();
};

$('#logoutBtn').onclick = async () => {
  document.cookie = 'nc_token=; max-age=0; path=/';
  location.href = '/login';
};

// ============ DASHBOARD ============
async function loadDashboard() {
  const s = await api('/api/stats');
  $('#stats').innerHTML = '';
  const items = [
    ['Активных каналов', s.active_channels],
    ['Постов увидено', s.posts_seen],
    ['В очереди', s.scheduled],
    ['Откомментировано', s.commented],
    ['Ошибок', s.failed],
  ];
  for (const [l, v] of items) {
    $('#stats').appendChild(el('div', { class: 'stat' }, [
      el('div', { class: 'v' }, String(v)),
      el('div', { class: 'l' }, l),
    ]));
  }
  const posts = await api('/api/posts?limit=8');
  $('#dashFeed').innerHTML = '';
  posts.forEach(p => $('#dashFeed').appendChild(renderPost(p)));
}

// ============ TG ============
async function loadTG() {
  const st = await api('/api/tg/status');
  const info = $('#tg-info');
  info.innerHTML = '';
  const login = $('#tg-login-block');
  if (st.authorized && st.me) {
    info.appendChild(el('div', {}, [
      el('p', {}, `✅ Авторизован: ${st.me.name} (@${st.me.username || '—'}, id=${st.me.id})`),
      el('button', { class: 'btn-danger', onclick: async () => {
        if (!confirm('Точно выйти?')) return;
        await api('/api/tg/logout', { method: 'POST' });
        toast('Вышли');
        loadTG(); refreshStatus();
      }}, 'Выйти из Telegram'),
    ]));
    login.classList.add('hide');
  } else {
    info.appendChild(el('p', { class: 'muted' }, st.error || 'Не авторизован'));
    login.classList.remove('hide');
  }
}

$('#tg-send-code').onclick = async () => {
  $('#tg-err').textContent = '';
  const phone = $('#tg-phone').value.trim();
  if (!phone) return ($('#tg-err').textContent = 'Введи номер');
  try {
    const r = await api('/api/tg/send_code', { method: 'POST', body: JSON.stringify({ phone }) });
    if (!r.ok) return ($('#tg-err').textContent = r.error);
    $('#tg-code-block').classList.remove('hide');
    toast('Код отправлен');
  } catch (e) {
    $('#tg-err').textContent = e.message;
  }
};

$('#tg-sign-in').onclick = async () => {
  $('#tg-err').textContent = '';
  const code = $('#tg-code').value.trim();
  const password = $('#tg-pwd').value;
  try {
    const r = await api('/api/tg/sign_in', {
      method: 'POST', body: JSON.stringify({ code, password: password || null })
    });
    if (!r.ok) {
      if (r.needs_password) {
        $('#tg-err').textContent = 'Нужен пароль 2FA — введи и нажми снова';
        return;
      }
      $('#tg-err').textContent = r.error || 'Ошибка';
      return;
    }
    toast('Готово! Авторизован');
    loadTG(); refreshStatus();
  } catch (e) {
    $('#tg-err').textContent = e.message;
  }
};

// ============ CHANNELS ============
async function loadChannels() {
  const rows = await api('/api/channels');
  const tb = $('#channels-tbody');
  tb.innerHTML = '';
  rows.forEach(r => {
    const tr = el('tr');
    const cb = el('input', { type: 'checkbox' });
    cb.checked = r.is_active;
    cb.onchange = async () => {
      try {
        await api(`/api/channels/${r.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ is_active: cb.checked }),
        });
        toast(cb.checked ? 'Включён' : 'Выключен');
      } catch (e) { toast(e.message, 'err'); cb.checked = !cb.checked; }
    };
    tr.appendChild(el('td', {}, cb));
    tr.appendChild(el('td', {}, r.title));
    tr.appendChild(el('td', {}, r.username ? '@' + r.username : '—'));

    const ti = el('input', { value: r.extra_prompt || '', placeholder: 'доп. контекст про этот канал (опц.)', style: 'width: 100%' });
    ti.onblur = async () => {
      try {
        await api(`/api/channels/${r.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ is_active: cb.checked, extra_prompt: ti.value }),
        });
      } catch (e) { toast(e.message, 'err'); }
    };
    tr.appendChild(el('td', {}, ti));

    const del = el('button', {
      class: 'btn-danger',
      onclick: async () => {
        if (!confirm('Удалить канал из базы? (с подписки в TG это НЕ снимет)')) return;
        await api(`/api/channels/${r.id}`, { method: 'DELETE' });
        loadChannels();
      },
    }, '×');
    tr.appendChild(el('td', {}, del));
    tb.appendChild(tr);
  });
}

$('#sync-channels').onclick = async () => {
  try {
    const r = await api('/api/channels/sync', { method: 'POST' });
    toast(`Найдено ${r.total} каналов, новых ${r.new}`);
    loadChannels();
  } catch (e) { toast(e.message, 'err'); }
};

// ============ AI KEYS ============
async function loadProviders() {
  const provs = await api('/api/providers');
  const sel = $('#prov-select');
  sel.innerHTML = '';
  const sel2 = $('#set-provider');
  sel2.innerHTML = '';
  provs.forEach(p => {
    sel.appendChild(el('option', { value: p.name }, `${p.name} (${p.default_model})`));
    sel2.appendChild(el('option', { value: p.name }, p.name));
  });
}

async function loadKeys() {
  const rows = await api('/api/keys');
  const tb = $('#keys-tbody');
  tb.innerHTML = '';
  rows.forEach(r => {
    const tr = el('tr');
    tr.appendChild(el('td', {}, el('span', { class: 'pill pill-info' }, r.provider)));
    tr.appendChild(el('td', {}, el('code', {}, r.key_masked)));
    const t = el('button', {
      class: 'btn-ghost',
      onclick: async () => { await api(`/api/keys/${r.id}/toggle`, { method: 'POST' }); loadKeys(); },
    }, r.is_active ? '✓ да' : '✗ нет');
    tr.appendChild(el('td', {}, t));
    tr.appendChild(el('td', {}, String(r.fail_count)));
    tr.appendChild(el('td', {}, r.last_error || '—'));
    tr.appendChild(el('td', {}, el('button', {
      class: 'btn-danger',
      onclick: async () => { if (confirm('Удалить ключ?')) { await api(`/api/keys/${r.id}`, { method: 'DELETE' }); loadKeys(); } },
    }, '×')));
    tb.appendChild(tr);
  });
}

$('#add-key').onclick = async () => {
  const provider = $('#prov-select').value;
  const key = $('#prov-key').value.trim();
  if (!key) return toast('Введи ключ', 'err');
  try {
    await api('/api/keys', { method: 'POST', body: JSON.stringify({ provider, key }) });
    $('#prov-key').value = '';
    toast('Добавлен');
    loadKeys();
  } catch (e) { toast(e.message, 'err'); }
};

// ============ PROXY ============
async function loadProxies() {
  const rows = await api('/api/proxies');
  const tb = $('#proxy-tbody');
  tb.innerHTML = '';
  rows.forEach(r => {
    const tr = el('tr');
    tr.appendChild(el('td', {}, el('code', {}, r.url)));
    tr.appendChild(el('td', {}, r.is_active ? '✓' : '✗'));
    tr.appendChild(el('td', {}, r.last_check_ok == null ? '?' : (r.last_check_ok ? 'OK' : 'FAIL')));
    tr.appendChild(el('td', {}, String(r.fail_count)));
    const act = el('td');
    act.appendChild(el('button', {
      onclick: async () => { const x = await api(`/api/proxies/${r.id}/check`, { method: 'POST' }); toast('Прокси: ' + (x.ok ? 'OK' : 'FAIL'), x.ok ? 'ok' : 'err'); loadProxies(); },
    }, 'Проверить'));
    act.appendChild(el('button', {
      class: 'btn-danger',
      onclick: async () => { if (confirm('Удалить?')) { await api(`/api/proxies/${r.id}`, { method: 'DELETE' }); loadProxies(); } },
    }, '×'));
    tr.appendChild(act);
    tb.appendChild(tr);
  });
}

$('#add-proxy').onclick = async () => {
  const url = $('#proxy-url').value.trim();
  if (!url) return;
  try {
    await api('/api/proxies', { method: 'POST', body: JSON.stringify({ url }) });
    $('#proxy-url').value = '';
    toast('Прокси добавлен');
    loadProxies();
  } catch (e) { toast(e.message, 'err'); }
};

// ============ PERSONA ============
async function loadPersona() {
  const s = await api('/api/settings');
  $('#persona-text').value = s.persona_prompt || '';
}
$('#save-persona').onclick = async () => {
  await api('/api/settings', { method: 'POST', body: JSON.stringify({ persona_prompt: $('#persona-text').value }) });
  toast('Сохранено');
};
$('#reset-persona').onclick = async () => {
  $('#persona-text').value = `Ты — владелец онлайн-школы программирования для детей (возраст 7–17 лет).
Школа учит детей Python, Scratch, веб-разработке, Minecraft-моддингу, играм, ИИ.

Когда комментируешь чужие посты:
- Пиши КОРОТКО (1–3 предложения, максимум 350 символов).
- Звучи как живой человек, а не как реклама.
- Если пост по теме (образование детей, IT, программирование, родительство, развитие) —
  ответь по сути, добавь полезную мысль или мягко поделись опытом школы.
  ОДИН раз из 5 коммов можно естественно упомянуть, что ведёшь школу.
- Если пост вне темы — отвечай как обычный читатель: согласие, эмоция, наблюдение.
- Никогда не вставляй ссылки, эмодзи можно 0–1 шт.
- НЕ начинай со слов "Согласен", "Интересно", "Спасибо за пост" — это палится.
- Пиши на том же языке, что и пост.
- НИКАКИХ хештегов, призывов "пишите в личку", "переходите по ссылке".`;
};

// ============ POSTS FEED ============
function renderPost(p) {
  const dot = el('span', { class: 'pill pill-' + ({
    commented: 'on', scheduled: 'warn', failed: 'off', new: 'info', skipped: 'off', processing: 'warn',
  }[p.status] || 'info') }, p.status);

  const wrap = el('div', { class: 'post' });
  wrap.appendChild(el('div', { class: 'head' }, [
    el('div', { class: 'title' }, `${p.channel_title}${p.channel_username ? ' (@' + p.channel_username + ')' : ''}`),
    dot,
  ]));
  wrap.appendChild(el('div', { class: 'body' }, p.text || '(без текста)'));

  if (p.comment) {
    const box = el('div', { class: 'comment-box' });
    const ta = el('textarea', { class: 'comment-edit', html: '' });
    ta.value = p.comment.text;
    box.appendChild(el('div', { class: 'muted' }, `AI: ${p.comment.provider} / ${p.comment.model} ${p.comment.sent_at ? '· отправлен ' + new Date(p.comment.sent_at).toLocaleString() : ''}`));
    box.appendChild(ta);
    if (p.comment.error) box.appendChild(el('div', { class: 'error' }, '⚠ ' + p.comment.error));

    const actions = el('div', { class: 'actions' });
    if (!p.comment.sent_at) {
      actions.appendChild(el('button', {
        class: 'btn-primary',
        onclick: async () => {
          const r = await api(`/api/posts/${p.id}/approve`, { method: 'POST', body: JSON.stringify({ text: ta.value }) });
          if (r.ok) { toast('Отправлен'); loadPosts(); }
          else toast(r.error || 'Ошибка', 'err');
        },
      }, '✓ Отправить'));
      actions.appendChild(el('button', {
        class: 'btn-danger',
        onclick: async () => { await api(`/api/posts/${p.id}/skip`, { method: 'POST' }); loadPosts(); },
      }, 'Пропустить'));
    }
    box.appendChild(actions);
    wrap.appendChild(box);
  } else if (p.skip_reason) {
    wrap.appendChild(el('div', { class: 'muted' }, '⤷ ' + p.skip_reason));
  }
  return wrap;
}

async function loadPosts() {
  const filter = $('#post-filter').value;
  const url = '/api/posts?limit=60' + (filter ? '&status=' + filter : '');
  const rows = await api(url);
  const feed = $('#feed');
  feed.innerHTML = '';
  if (rows.length === 0) feed.appendChild(el('p', { class: 'muted' }, 'Пусто. Подожди появления постов в активных каналах.'));
  rows.forEach(p => feed.appendChild(renderPost(p)));
}
$('#reload-posts').onclick = loadPosts;
$('#post-filter').onchange = loadPosts;

// ============ SETTINGS ============
async function loadSettings() {
  const s = await api('/api/settings');
  $('#set-provider').value = s.preferred_provider || 'groq';
  $('#set-model').value = s.preferred_model || '';
  $('#set-manual').checked = s.manual_approval === '1';
}
$('#save-settings').onclick = async () => {
  await api('/api/settings', { method: 'POST', body: JSON.stringify({
    preferred_provider: $('#set-provider').value,
    preferred_model: $('#set-model').value.trim(),
    manual_approval: $('#set-manual').checked ? '1' : '0',
  }) });
  toast('Сохранено');
};

// ============ tab loaders + init ============
const LOADERS = {
  dashboard: loadDashboard,
  tg: loadTG,
  channels: loadChannels,
  ai: async () => { await loadProviders(); await loadKeys(); },
  proxy: loadProxies,
  persona: loadPersona,
  posts: loadPosts,
  settings: async () => { await loadProviders(); await loadSettings(); },
};

(async function init() {
  try {
    await refreshStatus();
    await loadDashboard();
    setInterval(refreshStatus, 15000);
    // авто-обновление активной вкладки posts/dashboard
    setInterval(() => {
      const active = $('.tab.active').dataset.tab;
      if (active === 'posts') loadPosts();
      if (active === 'dashboard') loadDashboard();
    }, 20000);
  } catch (e) {
    if (e.message && e.message.includes('401')) location.href = '/login';
    else toast(e.message, 'err');
  }
})();
