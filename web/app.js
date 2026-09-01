/* NotionSearch UI */
'use strict';

const $ = (id) => document.getElementById(id);
const PAGE_SIZE = 20;

const state = {
  q: '',
  sort: 'relevance',
  edited: '',
  object: '',
  parent: '',
  facets: new Set(),
  offset: 0,
  total: 0,
  loading: false,
  syncTimer: null,
  syncHint: null,
};

/* ── helpers ─────────────────────────────────────────── */

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Meilisearch wraps matches in [[hl]] sentinels. Escape first, then swap in
// <mark> — page content is untrusted and may itself contain HTML.
function highlight(s) {
  return esc(s).replaceAll('[[hl]]', '<mark>').replaceAll('[[/hl]]', '</mark>');
}

function show(screen) {
  ['loading', 'screen-login', 'screen-setup', 'screen-app'].forEach((id) =>
    $(id).classList.toggle('hidden', id !== screen));
}

function toast(msg, isError = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('err', isError);
  el.classList.remove('hidden');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 3200);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

function timeAgo(iso) {
  if (!iso) return 'never';
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 60) return 'just now';
  const units = [[60, 'minute'], [24, 'hour'], [7, 'day'], [4.35, 'week'], [12, 'month']];
  let value = secs / 60, label = 'minute';
  for (const [step, name] of units) {
    if (value < step) break;
    value /= step; label = name;
  }
  const n = Math.floor(value);
  return `${n} ${label}${n === 1 ? '' : 's'} ago`;
}

const debounce = (fn, ms) => {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
};

/* ── boot ────────────────────────────────────────────── */

async function boot() {
  let status;
  try {
    status = await api('/api/status');
  } catch (err) {
    show('loading');
    $('loading').innerHTML = `<div class="empty"><h3>Can't reach the server</h3>
      <p class="muted">${esc(err.message)}</p></div>`;
    return;
  }

  if (status.auth_required && !status.signed_in) { show('screen-login'); return; }
  if (!status.configured) { show('screen-setup'); return; }

  show('screen-app');
  $('logout-section').classList.toggle('hidden', !status.auth_required);
  applyStatus(status);

  state.syncHint = status.sync?.hint || null;
  if (status.sync?.running) startSyncPolling();
  else if (!status.last_sync_at) {
    // Freshly connected but never synced: get her straight to content.
    toast('Starting your first sync…');
    triggerSync('incremental');
  }
  search(true);
}

function applyStatus(s) {
  $('set-bot').textContent = s.bot_name || '—';
  $('set-workspace').textContent = s.workspace || '—';
  $('set-count').textContent = s.page_count != null
    ? `${s.page_count.toLocaleString()} (${s.database_count || 0} databases)` : '—';
  $('set-lastsync').textContent = timeAgo(s.last_sync_at);
  if (!s.search_ready) toast('Search engine is still starting up…', true);
}

/* ── login & setup ───────────────────────────────────── */

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('login-error');
  err.classList.add('hidden');
  try {
    await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password: $('login-password').value }),
    });
    boot();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove('hidden');
  }
});

$('setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('setup-submit');
  const err = $('setup-error');
  err.classList.add('hidden');
  btn.disabled = true;
  btn.textContent = 'Checking with Notion…';
  try {
    const res = await api('/api/config/notion', {
      method: 'POST',
      body: JSON.stringify({ token: $('setup-token').value }),
    });
    toast(`Connected to ${res.workspace || 'Notion'}`);
    boot();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Connect to Notion';
  }
});

/* ── search ──────────────────────────────────────────── */

function buildQuery(offset) {
  const p = new URLSearchParams();
  p.set('q', state.q);
  p.set('limit', PAGE_SIZE);
  p.set('offset', offset);
  p.set('sort', state.sort);
  if (state.edited) p.set('edited', state.edited);
  if (state.object) p.set('object', state.object);
  if (state.parent) p.set('parent', state.parent);
  state.facets.forEach((f) => p.append('facet', f));
  return p.toString();
}

async function search(reset = true) {
  if (state.loading) return;
  state.loading = true;
  if (reset) state.offset = 0;

  try {
    const data = await api(`/api/search?${buildQuery(state.offset)}`);
    state.total = data.total;
    renderResults(data, reset);
    if (reset) renderFacets(data.facets);
    updateClearButton();
  } catch (err) {
    $('results').innerHTML = `<div class="empty"><h3>Search failed</h3>
      <p class="muted">${esc(err.message)}</p></div>`;
    $('results-meta').textContent = '';
  } finally {
    state.loading = false;
  }
}

function renderResults(data, reset) {
  const list = $('results');
  if (reset) list.innerHTML = '';

  $('results-meta').textContent = data.total
    ? `${data.total.toLocaleString()} result${data.total === 1 ? '' : 's'} in ${data.processing_ms}ms`
    : '';

  if (!data.hits.length && reset) {
    list.innerHTML = state.q
      ? `<div class="empty"><h3>Nothing found for “${esc(state.q)}”</h3>
           <p class="muted">Try fewer words, or clear the filters on the left.</p></div>`
      : state.syncHint
        ? `<div class="empty"><h3>Notion returned no pages</h3>
             <p class="muted">${esc(state.syncHint)}</p></div>`
        : `<div class="empty"><h3>No pages synced yet</h3>
             <p class="muted">Press <b>Sync</b> above to pull your Notion content in.</p></div>`;
    $('results-more').innerHTML = '';
    return;
  }

  const html = data.hits.map((hit) => {
    const f = hit._formatted || hit;
    const snippet = (f.content || '').trim() || (f.property_text || '').trim();
    const tags = (hit.facets || []).slice(0, 4)
      .map((t) => `<span class="tag">${esc(t)}</span>`).join('');
    return `
      <button class="hit" data-id="${esc(hit.notion_id)}">
        <div class="hit-title">${hit.icon ? esc(hit.icon) + ' ' : ''}${highlight(f.title)}</div>
        ${hit.breadcrumb ? `<div class="hit-crumb">${esc(hit.breadcrumb)}</div>` : ''}
        ${snippet ? `<div class="hit-snippet">${highlight(snippet)}</div>` : ''}
        ${tags ? `<div class="hit-props">${tags}</div>` : ''}
      </button>`;
  }).join('');

  list.insertAdjacentHTML('beforeend', html);

  const shown = state.offset + data.hits.length;
  $('results-more').innerHTML = shown < data.total
    ? `<button class="ghost" id="btn-more" style="margin-top:18px">Show more (${(data.total - shown).toLocaleString()} left)</button>`
    : '';
  const more = $('btn-more');
  if (more) more.onclick = () => { state.offset = shown; search(false); };
}

function renderFacets(facets = {}) {
  const parents = facets.parent_title || {};
  const props = facets.facets || {};

  const rows = (obj, key, active, limit) => Object.entries(obj)
    .filter(([name]) => name && name.trim())
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([name, count]) => `
      <div class="facet ${active(name) ? 'active' : ''}" data-${key}="${esc(name)}">
        <span class="name">${esc(name)}</span><span class="count">${count}</span>
      </div>`).join('');

  const parentHtml = rows(parents, 'parent', (n) => state.parent === n, 12);
  const propHtml = rows(props, 'facet', (n) => state.facets.has(n), 20);

  $('facet-parents').innerHTML = parentHtml;
  $('facet-props').innerHTML = propHtml;
  $('group-location').classList.toggle('hidden', !parentHtml);
  $('group-props').classList.toggle('hidden', !propHtml);
}

function updateClearButton() {
  const active = state.edited || state.object || state.parent || state.facets.size;
  $('btn-clear').classList.toggle('hidden', !active);
}

/* ── filter interactions ─────────────────────────────── */

$('q').addEventListener('input', debounce((e) => {
  state.q = e.target.value;
  search(true);
}, 160));

$('sort').addEventListener('change', (e) => { state.sort = e.target.value; search(true); });

function wireChips(containerId, key) {
  $(containerId).addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    state[key] = chip.dataset[key];
    [...chip.parentElement.children].forEach((c) => c.classList.toggle('active', c === chip));
    search(true);
  });
}
wireChips('chips-edited', 'edited');
wireChips('chips-object', 'object');

$('facet-parents').addEventListener('click', (e) => {
  const el = e.target.closest('.facet');
  if (!el) return;
  // parent_title is a display name; the API filters on it directly.
  state.parent = state.parent === el.dataset.parent ? '' : el.dataset.parent;
  search(true);
});

$('facet-props').addEventListener('click', (e) => {
  const el = e.target.closest('.facet');
  if (!el) return;
  const value = el.dataset.facet;
  state.facets.has(value) ? state.facets.delete(value) : state.facets.add(value);
  search(true);
});

$('btn-clear').addEventListener('click', () => {
  state.edited = state.object = state.parent = '';
  state.facets.clear();
  document.querySelectorAll('.chips').forEach((group) => {
    [...group.children].forEach((c, i) => c.classList.toggle('active', i === 0));
  });
  search(true);
});

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault();
    $('q').focus();
  }
  if (e.key === 'Escape') {
    closeDrawer('preview');
    closeDrawer('settings');
  }
});

/* ── preview drawer ──────────────────────────────────── */

$('results').addEventListener('click', async (e) => {
  const hit = e.target.closest('.hit');
  if (!hit) return;
  try {
    const page = await api(`/api/page/${hit.dataset.id}`);
    $('pv-title').textContent = `${page.icon || ''} ${page.title}`.trim();
    $('pv-meta').textContent =
      `${page.breadcrumb || 'Top level'} · edited ${timeAgo(page.last_edited_time)}`;
    $('pv-open').href = page.url || '#';

    let props = {};
    try { props = JSON.parse(page.properties || '{}'); } catch { /* ignore */ }
    $('pv-props').innerHTML = Object.entries(props).map(([k, v]) =>
      `<span class="tag">${esc(k)}: ${esc(Array.isArray(v) ? v.join(', ') : v)}</span>`).join('');

    $('pv-content').textContent = page.content?.trim() || 'No text content on this page.';
    $('preview').classList.remove('hidden');
  } catch (err) {
    toast(err.message, true);
  }
});

function closeDrawer(id) { $(id).classList.add('hidden'); }
document.querySelectorAll('[data-close-preview]').forEach((el) =>
  el.addEventListener('click', () => closeDrawer('preview')));
document.querySelectorAll('[data-close-settings]').forEach((el) =>
  el.addEventListener('click', () => closeDrawer('settings')));

/* ── settings ────────────────────────────────────────── */

$('btn-settings').addEventListener('click', async () => {
  $('settings').classList.remove('hidden');
  try {
    applyStatus(await api('/api/status'));
    const runs = await api('/api/sync/history?limit=5');
    $('sync-history').innerHTML = runs.map((r) =>
      `<div>${timeAgo(r.started_at)} · ${esc(r.status)} · ${r.updated || 0} updated${
        r.error ? ` · <span style="color:var(--danger)">${esc(r.error)}</span>` : ''}</div>`).join('');
  } catch { /* drawer still usable */ }
});

$('btn-disconnect').addEventListener('click', async () => {
  if (!confirm('Remove your API key and delete everything synced to this computer?\n\nYour Notion pages are not touched.')) return;
  try {
    await api('/api/config/notion', { method: 'DELETE' });
    closeDrawer('settings');
    boot();
  } catch (err) { toast(err.message, true); }
});

$('btn-logout').addEventListener('click', async () => {
  await api('/api/auth/logout', { method: 'POST' });
  location.reload();
});

/* ── sync ────────────────────────────────────────────── */

async function triggerSync(mode) {
  try {
    await api('/api/sync', { method: 'POST', body: JSON.stringify({ mode }) });
    startSyncPolling();
  } catch (err) {
    toast(err.message, true);
  }
}

$('btn-sync').addEventListener('click', () => triggerSync('incremental'));
$('btn-sync-2').addEventListener('click', () => { closeDrawer('settings'); triggerSync('incremental'); });
$('btn-rebuild').addEventListener('click', () => {
  if (!confirm('Re-read every page from Notion? This is slower but fixes stale or missing results.')) return;
  closeDrawer('settings');
  triggerSync('full');
});
$('btn-cancel-sync').addEventListener('click', async () => {
  try { await api('/api/sync/cancel', { method: 'POST' }); } catch { /* already done */ }
});

function startSyncPolling() {
  clearInterval(state.syncTimer);
  $('syncbar').classList.remove('hidden');
  state.syncTimer = setInterval(pollSync, 900);
  pollSync();
}

async function pollSync() {
  let s;
  try { s = await api('/api/sync/status'); } catch { return; }

  $('sync-fill').style.width = `${s.percent}%`;
  $('sync-text').textContent = s.running
    ? `${s.phase}${s.total ? ` — ${s.processed}/${s.total}` : ''}`
    : s.phase;

  if (s.running) return;

  clearInterval(state.syncTimer);
  setTimeout(() => $('syncbar').classList.add('hidden'), 2200);

  if (s.status === 'ok') {
    if (s.hint) {
      // A sync that finds nothing is the commonest setup problem and looks
      // identical to success; say what to do about it.
      state.syncHint = s.hint;
      toast('Sync finished, but Notion returned no pages', true);
    } else {
      state.syncHint = null;
      toast(`Sync complete — ${s.updated} page${s.updated === 1 ? '' : 's'} updated`);
    }
    search(true);
  } else if (s.status === 'error') {
    toast(s.error || 'Sync failed', true);
  } else if (s.status === 'cancelled') {
    toast('Sync cancelled');
  }
}

boot();
