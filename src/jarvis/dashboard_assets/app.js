/* jarvis console — frontend contract: Open Design prototype (visual/layout/interaction)
   + docs/superpowers/specs/2026-09-12-dashboard-uiux-brief.md (tokens/components).
   All data comes from the local /api surface (dashboard.py). No fixtures, no network
   beyond the origin. Dynamic HTML is built exclusively through esc()-escaped template
   literals or textContent assignments. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------- State ---------- */
const state = {
  repos: [],            // latest /api/repos rows
  overview: null,       // latest /api/overview payload
  selectedRepo: '',     // slug for the detail view
  detailData: null,     // latest /api/repos/{slug} payload
  logs: new Map(),      // slug -> accumulated log text
  logOffsets: new Map(),// slug -> next byte offset
  collapsedLogs: new Set(),
  previousStatuses: new Map(), // slug -> last status (transition toasts)
  bannerTimer: null,
  activeTool: null,
  tools: [],
  lastFocused: null,
  searchRepo: 'all',
};
let pollTimer = null;
let pollEpoch = 0;

/* ---------- API helper ---------- */
async function api(path, options = {}) {
  try {
    const response = await fetch(path, options);
    let payload = {};
    try { payload = await response.json(); } catch { payload = {}; }
    return { status: response.status, ok: response.ok, payload };
  } catch (error) {
    return { status: 0, ok: false, payload: { error: String(error) } };
  }
}

/* ---------- Formatting ---------- */
function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return '—';
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}
function relTime(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (isNaN(then)) return esc(iso);
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 45) return 'just now';
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 86400 * 14) return `${Math.round(seconds / 86400)} days ago`;
  return new Date(iso).toISOString().slice(0, 10);
}
function stamp() {
  const now = new Date();
  return `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
}

/* ---------- Status chips ---------- */
const statusColorClass = { indexing:'dot-live', indexed:'dot', partial:'dot-partial', degraded:'dot-degraded', failed:'dot-failed' };
const statusTextClass = { indexing:'status-live', indexed:'status-ok', partial:'status-partial', degraded:'status-degraded', failed:'status-failed' };
function statusChip(status) {
  const color = statusColorClass[status] || 'dot';
  const text = statusTextClass[status] || 'status-muted';
  const label = esc(status || 'unknown');
  return `<span class="status ${text}"><span class="dot ${color}" aria-hidden="true"></span>${label}</span>`;
}

/* ---------- SVG icons ---------- */
const svgIcon = {
  reindex:'<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.7-3.8"/><path d="M13 2v3h-3"/></svg>',
  menu:'<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="13" cy="8" r="1"/></svg>',
  chevron:'<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg>',
  check:'<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8l4 4 6-7"/></svg>',
  x:'<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>'
};

/* ---------- Toasts and clipboard ---------- */
function toast(message, detail = '') {
  const node = document.createElement('article');
  node.className = 'toast';
  node.textContent = message;
  if (detail) {
    const line = document.createElement('span');
    line.className = 'toast-detail';
    line.textContent = detail;
    node.append(line);
  }
  $('#toastRegion').append(node);
  setTimeout(() => node.remove(), 4000);
}
async function copyText(text, acknowledgement = 'copied') {
  try { await navigator.clipboard.writeText(text); toast(acknowledgement, text); }
  catch { toast('clipboard unavailable', text); }
}
document.addEventListener('click', event => {
  const button = event.target.closest('[data-copy]');
  if (button) copyText(button.dataset.copy);
});

/* ---------- Shell: overview + status strip ---------- */
function renderOverview(overview) {
  if (!overview) return;
  state.overview = overview;
  $('#appVersion').textContent = overview.version || '';
  $('#dataDirText').textContent = overview.dataDir || '';
  $('#repoCount').textContent = `${overview.repos ?? state.repos.length} repos`;
  $('#diskTotal').textContent = fmtBytes(overview.diskBytes);
  $('#zoektStrip').textContent = overview.zoektRunning
    ? `${(overview.zoektBase || ':6070').replace('http://127.0.0.1', ':')} RUNNING`
    : 'OFFLINE';
  $('#zoektRuntime').innerHTML = overview.zoektRunning
    ? '<span class="status status-ok"><span class="dot" aria-hidden="true"></span>zoekt live</span>'
    : '<span class="status status-muted"><span class="dot dot-offline" aria-hidden="true"></span>zoekt offline</span>';
}

/* ---------- Hash routing + ARIA tablist ---------- */
const viewNames = ['repos', 'detail', 'search', 'playground'];
const ROUTES = { '#/repos': 'repos', '#/search': 'search', '#/playground': 'playground' };
function routeFromHash() {
  const hash = location.hash || '#/repos';
  if (hash.startsWith('#/repo/')) return { view: 'detail', slug: decodeURIComponent(hash.slice('#/repo/'.length)) };
  return { view: ROUTES[hash] || 'repos', slug: '' };
}
function hashForView(view) {
  if (view === 'detail') return state.selectedRepo ? `#/repo/${encodeURIComponent(state.selectedRepo)}` : '#/repos';
  for (const [hash, name] of Object.entries(ROUTES)) if (name === view) return hash;
  return '#/repos';
}
const visibleTabs = () => viewNames.map(name => $(`#tab-${name}`)).filter(tab => tab && !tab.hidden);
function syncDetailTab() {
  $('#tab-detail').hidden = !state.selectedRepo || !state.repos.some(repo => repo.slug === state.selectedRepo);
}
function showView(view, { updateHash = true } = {}) {
  if (view === 'detail' && !state.selectedRepo) {
    view = 'repos';
  }
  syncDetailTab();
  if (view === 'detail' && $('#tab-detail').hidden) view = 'repos';
  viewNames.forEach(name => {
    $(`#view-${name}`).hidden = name !== view;
    const tab = $(`#tab-${name}`);
    if (!tab) return;
    const selected = name === view;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  if (updateHash) {
    const target = hashForView(view);
    if (location.hash !== target) { location.hash = target; return; } // hashchange re-enters
  }
  if (view === 'repos') renderRepos();
  if (view === 'detail') void loadDetail(state.selectedRepo);
  if (view === 'playground') void ensureTools();
  schedulePolling();
}
function activateView(view) {
  if (view === 'detail') {
    if (!state.selectedRepo && state.repos.length) state.selectedRepo = state.repos[0].slug;
  }
  showView(view);
}
function focusAndActivateTab(tab) {
  tab.focus();
  activateView(tab.dataset.view);
}
$$('.tab').forEach(tab => tab.addEventListener('click', () => focusAndActivateTab(tab)));
$('.tab-nav').addEventListener('keydown', event => {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
  if (!keys.includes(event.key)) return;
  const tabs = visibleTabs();
  const currentIndex = tabs.findIndex(tab => tab.getAttribute('aria-selected') === 'true');
  let nextIndex = currentIndex;
  if (event.key === 'ArrowLeft') nextIndex = currentIndex <= 0 ? tabs.length - 1 : currentIndex - 1;
  if (event.key === 'ArrowRight') nextIndex = currentIndex >= tabs.length - 1 ? 0 : currentIndex + 1;
  if (event.key === 'Home') nextIndex = 0;
  if (event.key === 'End') nextIndex = tabs.length - 1;
  event.preventDefault();
  focusAndActivateTab(tabs[nextIndex]);
});
window.addEventListener('hashchange', () => {
  const { view, slug } = routeFromHash();
  if (view === 'detail' && slug) state.selectedRepo = slug;
  showView(view, { updateHash: false });
});

/* ---------- Polling ---------- */
function anyIndexing() {
  return state.repos.some(repo => repo.status === 'indexing');
}
function schedulePolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  const epoch = ++pollEpoch;
  const delay = anyIndexing() ? 2000 : 10000;
  pollTimer = setTimeout(async () => {
    if (epoch !== pollEpoch) return;
    await refreshRepos();
    await refreshLogs();
    if (epoch !== pollEpoch) return;
    schedulePolling();
  }, delay);
}
async function refreshRepos() {
  const { payload } = await api('/api/repos');
  if (!payload.repos) return;
  announceTransitions(payload.repos);
  state.repos = payload.repos;
  if (state.banner && !anyIndexing()) { state.banner = null; renderBanner(); }
  if (currentView() === 'repos') renderRepos();
  if (currentView() === 'detail' && state.selectedRepo) {
    const row = state.repos.find(repo => repo.slug === state.selectedRepo);
    if (row && row.status === 'indexing') void loadDetail(state.selectedRepo, { updateHash: false });
  }
  populateRepoFilter();
}
function announceTransitions(repos) {
  repos.forEach(repo => {
    const previous = state.previousStatuses.get(repo.slug);
    if (previous === 'indexing' && repo.status === 'indexed') {
      toast(`indexed ${repo.slug}`, repo.freshness && repo.freshness.generation ? `generation ${repo.freshness.generation}` : '');
    }
    state.previousStatuses.set(repo.slug, repo.status);
  });
}
async function refreshLogs() {
  for (const repo of state.repos.filter(item => item.status === 'indexing')) {
    const slug = repo.slug;
    const offset = state.logOffsets.get(slug) || 0;
    const { payload } = await api(`/api/repos/${encodeURIComponent(slug)}/log?offset=${offset}`);
    if (payload.chunk) {
      state.logs.set(slug, (state.logs.get(slug) || '') + payload.chunk);
      state.logOffsets.set(slug, payload.nextOffset);
    }
    const well = $(`#log-${CSS.escape(slug)}`);
    if (well && currentView() === 'repos') {
      well.innerHTML = logWellContent(slug);
      well.scrollTop = well.scrollHeight;
    }
    if (currentView() === 'detail' && state.selectedRepo === slug) {
      const detailWell = $('#detailLogWell');
      if (detailWell) { detailWell.textContent = state.logs.get(slug) || 'waiting for index output…'; detailWell.scrollTop = detailWell.scrollHeight; }
    }
  }
}
function currentView() {
  const { view } = routeFromHash();
  return $('#view-detail').hidden === false ? 'detail' : view;
}

/* ---------- Banner ---------- */
function renderBanner() {
  const node = $('#conflictBanner');
  node.textContent = state.banner || '';
  node.hidden = !state.banner;
}
function conflict(message, { sticky = false } = {}) {
  state.banner = message || null;
  renderBanner();
  if (state.bannerTimer) { clearTimeout(state.bannerTimer); state.bannerTimer = null; }
  if (state.banner && !sticky) {
    state.bannerTimer = setTimeout(() => { state.banner = null; renderBanner(); }, 4000);
  }
}

/* ---------- Repos table ---------- */
function langChip(language) {
  const short = { python:'py', typescript:'ts', java:'java', kotlin:'kt', swift:'swift', ruby:'rb', go:'go' }[language] || (language || '').slice(0, 2);
  return `<span class="chip">${esc(short)}</span>`;
}
function freshnessCell(row) {
  const fresh = row.freshness;
  if (!fresh || !fresh.commit) return '<span class="muted">—</span>';
  if (fresh.stale) return '<span class="status status-stale"><span class="dot dot-stale" aria-hidden="true"></span>stale</span>';
  return '<span class="status status-ok"><span class="dot" aria-hidden="true"></span>fresh</span>';
}
function scipCell(row) {
  if (!row.scipState) return '<span class="muted">—</span>';
  const failed = row.scipState === 'failed';
  return `<span class="${failed ? 'status-degraded' : 'muted'}">${esc(row.scipState)}</span>`;
}
function logWellContent(slug) {
  const text = state.logs.get(slug) || 'waiting for index output…';
  const lines = text.split('\n').filter((line, index, all) => !(index === all.length - 1 && line === ''));
  const last = lines.length - 1;
  return `${lines.map((line, index) => index === last
    ? `<span class="log-line">${esc(line)}<span class="cursor" aria-hidden="true"></span><span class="streaming live-label">streaming</span></span>`
    : `<span class="log-line">${esc(line)}</span>`).join('')}`;
}
function repoRow(row) {
  const slug = row.slug;
  const indexing = row.status === 'indexing';
  const collapsed = state.collapsedLogs.has(slug);
  const size = row.storageBytes && row.storageBytes.total > 0 ? fmtBytes(row.storageBytes.total) : '—';
  return `
    <tr data-slug="${esc(slug)}">
      <td>${statusChip(row.status)}</td>
      <td><span class="repo-slug">${esc(slug)}</span></td>
      <td>${langChip(row.language)}</td>
      <td>${freshnessCell(row)}</td>
      <td>${scipCell(row)}</td>
      <td class="muted">${row.semanticIndexedAt ? 'on' : 'off'}</td>
      <td class="mono ink">${size}</td>
      <td class="mono">${esc(relTime(row.lastIndexed))}</td>
      <td class="actions">
        <div class="row-actions">
          ${indexing ? `<button class="btn icon-pill" type="button" data-action="toggle-log" data-slug="${esc(slug)}" aria-expanded="${collapsed ? 'false' : 'true'}" aria-label="Toggle live log for ${esc(slug)}">${svgIcon.chevron}</button>` : ''}
          <button class="btn icon-pill" type="button" data-action="reindex" data-slug="${esc(slug)}" aria-label="Reindex ${esc(slug)}" ${indexing ? 'disabled' : ''}>${svgIcon.reindex}</button>
          <button class="btn icon-pill" type="button" data-action="menu" data-slug="${esc(slug)}" aria-haspopup="true" aria-expanded="false" aria-label="More actions for ${esc(slug)}">${svgIcon.menu}</button>
        </div>
      </td>
    </tr>
    ${indexing ? `<tr class="log-row" data-log-for="${esc(slug)}"><td colspan="9"><div class="log-shell ${collapsed ? '' : 'open'}"><div class="log-well" id="log-${esc(slug)}" tabindex="0" aria-label="Live index log for ${esc(slug)}">${logWellContent(slug)}</div></div></td></tr>` : ''}`;
}
function renderRepos() {
  const body = $('#reposBody');
  body.innerHTML = state.repos.map(repoRow).join('');
  const hasRepos = state.repos.length > 0;
  $('#reposEmpty').hidden = hasRepos;
  $('#reposTableWrap').hidden = !hasRepos;
  state.repos.forEach(repo => {
    if (repo.status !== 'indexing') return;
    const well = $(`#log-${CSS.escape(repo.slug)}`);
    if (well) { well.scrollTop = well.scrollHeight; }
  });
  renderOverview({ ...(state.overview || {}), repos: state.repos.length });
}
$('#reposBody').addEventListener('click', event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const slug = button.dataset.slug;
  if (button.dataset.action === 'toggle-log') {
    const open = !state.collapsedLogs.has(slug);
    if (open) state.collapsedLogs.add(slug); else state.collapsedLogs.delete(slug);
    const row = $(`tr[data-log-for="${CSS.escape(slug)}"] .log-shell`);
    row?.classList.toggle('open', !state.collapsedLogs.has(slug));
    button.setAttribute('aria-expanded', String(!state.collapsedLogs.has(slug)));
  }
  if (button.dataset.action === 'reindex') void reindexRepo(slug, button);
  if (button.dataset.action === 'menu') openMenu(slug, button);
});
async function reindexRepo(slug, button) {
  if (button) button.disabled = true; // final-review fix: no double-spawn before the next poll re-render
  const { status, payload } = await api(`/api/repos/${encodeURIComponent(slug)}/reindex`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (status === 202) {
    conflict('');
    state.logOffsets.delete(slug); state.logs.delete(slug);
    toast('reindex started', slug);
  } else if (status === 409) {
    conflict(`already indexing (pid ${payload.pid ?? '?'})`);
  } else {
    conflict(payload.error || `reindex failed (${status})`);
  }
  await refreshRepos();
}

/* ---------- Overflow menu ---------- */
let openMenuButton = null;
function closeMenu() { $('.context-menu')?.remove(); if (openMenuButton) { openMenuButton.setAttribute('aria-expanded', 'false'); openMenuButton = null; } }
function openMenu(slug, button) {
  closeMenu();
  openMenuButton = button; button.setAttribute('aria-expanded', 'true');
  const menu = document.createElement('div');
  menu.className = 'context-menu'; menu.role = 'menu';
  menu.innerHTML = `
    <button class="menu-item" role="menuitem" data-menu="detail">view detail</button>
    <button class="menu-item" role="menuitem" data-menu="copy">copy CLI command</button>
    <button class="menu-item destructive" role="menuitem" data-menu="forget">forget…</button>`;
  const rect = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(rect.right - 180, innerWidth - 190))}px`;
  menu.style.top = `${rect.bottom + 6}px`;
  menu.addEventListener('click', event => {
    const action = event.target.closest('[data-menu]')?.dataset.menu;
    if (!action) return;
    closeMenu();
    if (action === 'detail') { state.selectedRepo = slug; activateView('detail'); }
    if (action === 'copy') copyText(`jarvis reindex ${slug}`, 'CLI command copied');
    if (action === 'forget') openForget(slug);
  });
  document.body.append(menu);
  menu.querySelector('button').focus();
}
document.addEventListener('click', event => { if (!event.target.closest('.context-menu') && !event.target.closest('[data-action="menu"]')) closeMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') { closeMenu(); closeTopOverlay(); } });
addEventListener('scroll', closeMenu, true);

/* ---------- New repo panel ---------- */
const newPanel = $('#newRepoPanel'), newToggle = $('#newRepoToggle');
newToggle.addEventListener('click', () => {
  const open = newPanel.hidden;
  newPanel.hidden = !open;
  newToggle.setAttribute('aria-expanded', String(open));
  if (open) $('#repoPath').focus();
});
$('#newRepoCancel').addEventListener('click', () => { newPanel.hidden = true; newToggle.setAttribute('aria-expanded', 'false'); });
$('#repoSemantic').addEventListener('change', event => { $('#repoSemanticState').textContent = event.target.checked ? 'on' : 'off'; });
$('#newRepoForm').addEventListener('submit', async event => {
  event.preventDefault();
  const path = $('#repoPath').value.trim();
  const valid = /^(~\/|\/).+/.test(path);
  const errorNode = $('#repoPathError');
  errorNode.textContent = 'Enter an absolute or ~-relative repository path.';
  errorNode.hidden = valid;
  if (!valid) { $('#repoPath').focus(); return; }
  const scipChoice = $('#repoScip').value;
  const body = {
    path,
    semantic: $('#repoSemantic').checked,
    scip: scipChoice === 'on' ? true : scipChoice === 'off' ? false : null,
  };
  const { status, payload } = await api('/api/repos/index', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (status === 202) {
    newPanel.hidden = true; newToggle.setAttribute('aria-expanded', 'false');
    event.target.reset(); $('#repoSemantic').checked = false; $('#repoSemanticState').textContent = 'off';
    state.previousStatuses.set(payload.repo, 'indexing');
    toast('index started', payload.repo);
    await refreshRepos();
    if (!state.selectedRepo) state.selectedRepo = payload.repo;
    syncDetailTab();
  } else if (status === 409) {
    conflict(`already indexing (pid ${payload.pid ?? '?'})`);
  } else {
    errorNode.textContent = payload.error || `index failed (${status})`;
    errorNode.hidden = false;
  }
});

/* ---------- Repo detail ---------- */
async function loadDetail(slug, { updateHash = false } = {}) {
  if (!slug) { showView('repos'); return; }
  const { status, payload } = await api(`/api/repos/${encodeURIComponent(slug)}`);
  if (status === 404 || !payload.slug) {
    conflict(payload.error || `no such repo: ${slug}`);
    state.selectedRepo = '';
    showView('repos');
    return;
  }
  state.detailData = payload;
  if (updateHash) {
    const target = `#/repo/${encodeURIComponent(slug)}`;
    if (location.hash !== target) { location.hash = target; return; }
  }
  renderDetail(payload);
}
function providerLabel(providers) {
  if (!providers || !providers.length) return '—';
  return providers.map(p => p === 'scip' ? 'SCIP' : p === 'tree-sitter' ? 'Tree-sitter' : p).join('/');
}
function renderDetail(data) {
  const row = state.repos.find(repo => repo.slug === data.slug) || data;
  const capabilities = data.capabilities || {};
  const tools = capabilities.tools || {};
  const lastRun = data.last_index_run || capabilities.last_index_run || {};
  const storage = data.storageBytes || { scip: 0, zoekt: 0, lance: 0, total: 0 };
  const stores = [['scip', storage.scip], ['zoekt', storage.zoekt], ['lance', storage.lance]];
  const maxStorage = Math.max(...stores.map(([, value]) => value), 1);
  const largest = stores.reduce((a, b) => b[1] > a[1] ? b : a, ['', 0])[0];
  const generation = row.freshness && row.freshness.generation ? row.freshness.generation : null;
  const snapshots = (data.snapshots || []).map(snapshot => {
    const match = /index-[^-]+-(\d+)\.db$/.exec(snapshot.name);
    return { ...snapshot, gen: match ? match[1] : snapshot.name };
  });
  const recovery = ['degraded', 'failed', 'partial'].includes(row.status);
  const cause = lastRun.reason || (capabilities.navigation && capabilities.navigation.reason) || '';
  const command = lastRun.recovery || data.recovery || `jarvis reindex ${data.slug}`;

  $('#detailEyebrow').textContent = `── repo · ${data.slug}`;
  $('#detailTitle').textContent = data.slug;
  const toolRows = Object.entries(tools).map(([name, capability]) => capRow(name, providerLabel(capability.providers), capability.available, capability.reason)).join('');
  const searchCap = capabilities.search || {};
  const semanticCap = capabilities.semantic || {};
  $('#detailContent').innerHTML = `
    <div class="card span-12"><div style="display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);flex-wrap:wrap;">
      <p class="mono ink" style="margin:0;">${esc(data.slug)}</p>
      <p class="mono muted" style="margin:0;display:flex;align-items:center;gap:var(--space-4);">${statusChip(row.status)}<span>${generation ? `generation ${esc(generation)}` : ''}</span><span>${storage.total > 0 ? fmtBytes(storage.total) : '—'}</span></p>
    </div></div>
    <div class="card span-4"><p class="eyebrow">── snapshots</p><div style="margin-top:var(--space-4);">${snapshots.length ? snapshots.map(s => `<div class="snapshot-row ${s.current ? 'current' : 'muted'}"><span>${esc(s.gen)}</span><span>${s.current ? '<span class="tag">current</span>' : fmtBytes(s.bytes)}</span></div>`).join('') : '<p class="muted">no snapshots on disk</p>'}</div></div>
    <div class="card span-8"><p class="eyebrow">── capabilities</p><div style="margin-top:var(--space-4);">
      ${toolRows}
      ${capRow('searchCode', 'Zoekt', Boolean(searchCap.available), searchCap.reason || (searchCap.available ? null : 'no zoekt shards on disk'))}
      ${capRow('semanticSearch', 'vectors', Boolean(semanticCap.available), semanticCap.reason || null)}
    </div></div>
    ${recovery ? `<div class="card span-5" style="border-left:2px solid var(--status-degraded);"><p class="eyebrow">── recovery</p><p class="recovery-cause" style="margin-top:var(--space-4);"><strong>cause:</strong> ${esc(cause || 'published with documented gaps')}</p><div class="command-well"><span id="recoveryCommand">${esc(command)}</span><button class="btn icon-pill" data-copy="${esc(command)}" aria-label="Copy recovery command">${svgIcon.check}</button></div></div>` : ''}
    <div class="card ${recovery ? 'span-7' : 'span-12'}"><p class="eyebrow">── package graph</p>
      <div class="dep-list" style="margin-top:var(--space-4);">
        <div><span class="muted">depends on (${(data.graph?.dependsOn || []).length}):</span> ${(data.graph?.dependsOn || []).map(esc).join(' · ') || '—'}</div>
        <div><span class="muted">depended on by (${(data.graph?.dependedOnBy || []).length}):</span> ${(data.graph?.dependedOnBy || []).map(esc).join(' · ') || '—'}</div>
      </div>
      <button class="btn" style="margin-top:var(--space-5);" id="openGraph">view graph →</button>
    </div>
    <div class="card span-5"><p class="eyebrow">── storage</p><div style="margin-top:var(--space-4);">${stores.map(([name, value]) => `<div class="store-row"><span class="ink">${esc(name)}</span><div class="bar-track"><div class="bar-fill ${name === largest ? 'largest' : ''}" style="width:${Math.max(2, Math.round(value / maxStorage * 100))}%"></div></div><span class="muted">${fmtBytes(value)}</span></div>`).join('')}</div></div>
    <div class="card span-7"><p class="eyebrow">── index log</p><div class="log-well detail-log" id="detailLogWell" style="margin:var(--space-4) 0 0;" tabindex="0">${esc(state.logs.get(data.slug) || '') || '<span class="muted">no log output yet</span>'}</div></div>`;
  $('#openGraph')?.addEventListener('click', () => void openGraph(data.slug));
}
function capRow(name, provider, ok, reason) {
  return `<div class="cap-row"><span class="ink">${esc(name)}</span><span class="muted">${esc(provider)}</span><span class="${ok ? 'state-ok' : 'state-miss'}" title="${esc(reason || '')}">${ok ? svgIcon.check : svgIcon.x}${reason ? ` ${esc(reason)}` : ''}</span></div>`;
}

/* ---------- Search ---------- */
function highlight(text, query) {
  const safe = esc(text);
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean).slice(0, 4);
  if (!terms.length) return safe;
  const escapedTerms = terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return safe.replace(new RegExp(`(${escapedTerms.join('|')})`, 'gi'), '<mark>$1</mark>');
}
function populateRepoFilter() {
  const select = $('#searchRepo');
  const current = state.searchRepo;
  const options = ['<option value="all">all repos</option>']
    .concat(state.repos.map(repo => `<option value="${esc(repo.slug)}">${esc(repo.slug)}</option>`));
  select.innerHTML = options.join('');
  select.value = [...select.options].some(option => option.value === current) ? current : 'all';
  state.searchRepo = select.value;
}
$('#searchRepo').addEventListener('change', event => { state.searchRepo = event.target.value; });
$('#searchForm').addEventListener('submit', async event => {
  event.preventDefault();
  const query = $('#searchQuery').value.trim();
  $('#searchEmpty').hidden = Boolean(query);
  $('#searchResults').hidden = !query;
  if (!query) return;
  $('#searchLatency').textContent = '…';
  const repo = state.searchRepo === 'all' ? '' : state.searchRepo;
  const { payload } = await api(`/api/search?q=${encodeURIComponent(query)}${repo ? `&repo=${encodeURIComponent(repo)}` : ''}`);
  $('#searchLatency').textContent = `${payload.elapsedMs ?? '?'} ms`;
  renderSearchColumn('lexicalResults', renderLexicalHits(payload, query), payload.lexicalError);
  renderSearchColumn('semanticResults', renderSemanticHits(payload, query), payload.semanticError);
  renderSearchColumn('symbolResults', renderSymbolHits(payload, query), payload.symbolsError);
});
function renderSearchColumn(id, html, error) {
  $(`#${id}`).innerHTML = html || (error
    ? `<p class="no-results">signal unavailable: ${esc(error)}</p>`
    : '<p class="no-results">no matches</p>');
}
function renderLexicalHits(payload, query) {
  const lexical = payload.lexical;
  if (!lexical) return '';
  return lexical.hits.map(hit => `
    <button class="hit" data-source-repo="${esc(hit.repo)}" data-source-path="${esc(hit.path)}" data-source-line="${esc(hit.lineNumber)}">
      <span class="hit-path">${highlight(`${hit.path}:${hit.lineNumber}`, query)}</span>
      <code class="snippet">${highlight(hit.lineText, query)}</code>
    </button>`).join('') || '';
}
function renderSemanticHits(payload, query) {
  const semantic = payload.semantic;
  if (!semantic || !Array.isArray(semantic.results)) return '';
  return semantic.results.map(hit => `
    <button class="hit" data-source-repo="${esc(hit.repo)}" data-source-path="${esc(hit.filePath)}" data-source-line="${esc(hit.startLine)}" data-source-end="${esc(hit.endLine)}">
      <span class="hit-path">${highlight(`${hit.filePath}:${hit.startLine}`, query)}</span>
      <code class="snippet">${highlight(hit.content || '', query)}</code>
      <span class="hit-meta">score ${esc(Number(hit.score).toFixed(2))} · ${(hit.sources || []).join('+') || 'vector'}</span>
    </button>`).join('') || '';
}
function renderSymbolHits(payload, query) {
  const symbols = payload.symbols;
  if (!symbols) return '';
  return symbols.map(hit => `
    <button class="hit" data-source-repo="${esc(state.searchRepo)}" data-source-path="${esc(hit.path)}" data-source-line="${esc(hit.startLine)}" data-source-end="${esc(hit.endLine)}">
      <span class="hit-path">${highlight(hit.name, query)}</span>
      <span class="hit-meta">${esc(hit.kind)} · ${highlight(`${hit.path}:${hit.startLine}`, query)}</span>
    </button>`).join('') || '';
}

/* ---------- Source viewer ---------- */
let currentSourcePath = '';
async function openSource(repo, path, startLine, endLine) {
  const line = Number(startLine) || 1;
  const end = Number(endLine) || line;
  const start = Math.max(1, line - 4);
  const finish = end + 4;
  currentSourcePath = `${repo}:${path}`;
  $('#sourcePath').textContent = currentSourcePath;
  $('#sourceCode').innerHTML = '<span class="muted">loading…</span>';
  openOverlay($('#sourceOverlay'), $('#closeSource'));
  const { payload } = await api(`/api/repos/${encodeURIComponent(repo)}/file?p=${encodeURIComponent(path)}&start=${start}&end=${finish}`);
  if (payload.error || !payload.lines) {
    $('#sourceCode').innerHTML = `<span class="muted">${esc(payload.error || 'source unavailable')}</span>`;
    return;
  }
  $('#sourceCode').innerHTML = payload.lines.map((text, index) => {
    const no = payload.start + index;
    const highlighted = no >= line && no <= end;
    return `<div class="code-line ${highlighted ? 'highlighted' : ''}"><span class="line-no">${no}</span><span class="line-body">${esc(text)}</span></div>`;
  }).join('');
  requestAnimationFrame(() => $('#sourceCode .highlighted')?.scrollIntoView({ block: 'center' }));
}
$('#copySourcePath').addEventListener('click', () => copyText(currentSourcePath, 'source path copied'));
$('#closeSource').addEventListener('click', () => closeOverlay($('#sourceOverlay')));
$('#searchResults').addEventListener('click', event => {
  const button = event.target.closest('[data-source-path]');
  if (button) openSource(button.dataset.sourceRepo, button.dataset.sourcePath, button.dataset.sourceLine, button.dataset.sourceEnd);
});

/* ---------- Playground ---------- */
async function ensureTools() {
  if (state.tools.length) { renderToolRail(); renderToolForm(); return; }
  const { payload } = await api('/api/tools');
  state.tools = payload.tools || [];
  if (!state.activeTool && state.tools.length) state.activeTool = state.tools[0].name;
  renderToolRail();
  renderToolForm();
}
function renderToolRail() {
  $('#toolRail').innerHTML = state.tools.map(tool =>
    `<button class="tool-button ${tool.name === state.activeTool ? 'active' : ''}" role="option" aria-selected="${tool.name === state.activeTool}" data-tool="${esc(tool.name)}" title="${esc(tool.description)}">${esc(tool.name)}</button>`).join('');
}
$('#toolRail').addEventListener('click', event => {
  const button = event.target.closest('[data-tool]');
  if (!button) return;
  state.activeTool = button.dataset.tool;
  renderToolRail(); renderToolForm();
});
function controlFor(param) {
  const name = esc(param.name);
  const type = (param.type || '').toLowerCase();
  const required = Boolean(param.required);
  const value = param.default;
  let control = '';
  if (type.includes('bool')) {
    control = `<label class="switch"><input type="checkbox" data-param="${name}" ${value === true ? 'checked' : ''}><span class="muted">${value === true ? 'true' : 'false'}</span></label>`;
  } else if (type.includes('int') || type.includes('number')) {
    control = `<input class="input mono" data-param="${name}" type="number" value="${esc(value ?? '')}" ${required ? 'required' : ''} autocomplete="off">`;
  } else {
    control = `<input class="input mono" data-param="${name}" type="text" value="${esc(value ?? '')}" ${required ? 'required' : ''} autocomplete="off">`;
  }
  return `<div class="field"><label class="label" for="param-${name}">${name}${required ? '' : ' <span class="muted">(optional)</span>'}</label>${control}<p class="helper">${esc(param.type || 'str')} · ${required ? 'required' : 'optional'}</p></div>`;
}
function renderToolForm() {
  const tool = state.tools.find(item => item.name === state.activeTool);
  $('#activeToolName').textContent = tool ? tool.name : '';
  $('#toolParams').innerHTML = tool ? tool.params.map(param => controlFor(param)).join('') : '';
  $('#toolLatency').hidden = true;
  $('#toolResponse').className = 'response-well';
  $('#toolResponse').innerHTML = '<span class="muted">Run a tool to inspect its JSON response.</span>';
  const run = $('#runTool'); run.disabled = false; run.textContent = 'run ⏎';
}
$('#toolForm').addEventListener('submit', async event => {
  event.preventDefault();
  const run = $('#runTool');
  if (run.disabled) return;
  const tool = state.tools.find(item => item.name === state.activeTool);
  if (!tool) return;
  const args = {};
  $$('#toolParams [data-param]').forEach(input => {
    args[input.dataset.param] = input.type === 'checkbox'
      ? input.checked
      : (input.type === 'number' ? Number(input.value) : input.value);
  });
  const missing = tool.params.filter(param => param.required && (args[param.name] === '' || args[param.name] === undefined || args[param.name] === null));
  if (missing.length) {
    const well = $('#toolResponse');
    well.className = 'response-well error';
    well.innerHTML = `<pre style="margin:0;">{\n  "error": "missing required parameter ${esc(missing[0].name)}"\n}</pre>`;
    return;
  }
  run.disabled = true; run.textContent = 'running…'; $('#toolLatency').hidden = true;
  const { status, payload } = await api(`/api/tools/${encodeURIComponent(state.activeTool)}/invoke`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) });
  run.disabled = false; run.textContent = 'run ⏎';
  const isError = status !== 200 || (payload.result && payload.result.error);
  $('#toolLatency').textContent = `${payload.elapsedMs ?? '?'} ms`;
  $('#toolLatency').hidden = false;
  renderJSONResponse(payload.result ?? payload, Boolean(isError));
});
function jsonScalar(value) {
  if (typeof value === 'string') return `<span class="json-string">"${esc(value)}"</span>`;
  if (typeof value === 'number') return `<span class="json-number">${value}</span>`;
  if (typeof value === 'boolean') return `<span class="json-bool">${value}</span>`;
  if (value === null || value === undefined) return '<span class="json-null">null</span>';
  return esc(String(value));
}
function jsonNode(key, value, depth = 0) {
  const label = key === null ? '' : `<span class="json-key">"${esc(key)}"</span>: `;
  if (value === null || value === undefined) return `<div>${label}<span class="json-null">null</span></div>`;
  if (Array.isArray(value) || typeof value === 'object') {
    const empty = Array.isArray(value) ? '[]' : '{}';
    const open = Array.isArray(value) ? '[' : '{';
    const close = Array.isArray(value) ? ']' : '}';
    const entries = Object.entries(value);
    if (!entries.length || depth >= 2) return `<div>${label}${empty}</div>`;
    return `<details class="json-node" open><summary>${label}${open}</summary>${entries.map(([childKey, childValue]) => jsonNode(childKey, childValue, depth + 1)).join('')}<div>${close}</div></details>`;
  }
  return `<div>${label}${jsonScalar(value)}</div>`;
}
function renderJSONResponse(result, isError) {
  const well = $('#toolResponse');
  well.className = `response-well ${isError ? 'error' : ''}`;
  well.innerHTML = Object.entries(result || {}).map(([key, value]) => jsonNode(key, value)).join('')
    || '<span class="muted">empty response</span>';
}

/* ---------- Package graph ---------- */
async function openGraph(slug) {
  const { payload } = await api('/api/graph');
  const nodes = payload.nodes || [];
  const edges = payload.edges || [];
  if (!nodes.length) { toast('package graph empty', 'no packages registered'); return; }
  $('#graphTitle').textContent = `${slug} neighborhood`;
  const own = new Set(nodes.filter(node => node.repo === slug).map(node => node.id));
  const label = node => (node.name || node.id || '').replace(/^.*(\/|:)/, '') || node.repo;
  // Deterministic layout: focal packages at center, the rest ringed outward by index.
  const positioned = nodes.map((node, index) => {
    if (own.has(node.id)) return { ...node, x: 50, y: 52 };
    const angle = (index / Math.max(1, nodes.length - 1)) * 2 * Math.PI - Math.PI / 2;
    const ring = index % 2 ? 40 : 26;
    return { ...node, x: 50 + ring * Math.cos(angle), y: 50 + ring * 0.82 * Math.sin(angle) };
  });
  const nodeMap = new Map(positioned.map(node => [node.id, node]));
  const adjacency = new Map(nodes.map(node => [node.id, new Set()]));
  edges.forEach(({ from, to }) => { adjacency.get(from)?.add(to); adjacency.get(to)?.add(from); });
  const stage = $('#graphStage');
  const svg = `<svg class="graph-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${edges.map(({ from, to }) => {
    const a = nodeMap.get(from), b = nodeMap.get(to);
    if (!a || !b) return '';
    return `<line class="graph-edge" data-from="${esc(from)}" data-to="${esc(to)}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
  }).join('')}</svg>`;
  const nodeHTML = positioned.map(node =>
    `<button class="graph-node ${own.has(node.id) ? 'focal' : ''}" data-node="${esc(node.id)}" style="left:${node.x}%;top:${node.y}%;" aria-label="${esc(label(node))} package">${esc(label(node))}</button>`).join('');
  stage.innerHTML = svg + nodeHTML;
  const highlight = nodeId => {
    const near = new Set([nodeId]);
    adjacency.get(nodeId)?.forEach(id => near.add(id));
    adjacency.get(nodeId)?.forEach(id => adjacency.get(id)?.forEach(child => near.add(child)));
    $$('.graph-node', stage).forEach(node => node.classList.toggle('near', !node.classList.contains('focal') && near.has(node.dataset.node)));
    $$('.graph-edge', stage).forEach(edge => edge.classList.toggle('near', near.has(edge.dataset.from) && near.has(edge.dataset.to)));
  };
  const reset = () => { $$('.near', stage).forEach(node => node.classList.remove('near')); };
  $$('.graph-node', stage).forEach(node => {
    node.addEventListener('mouseenter', () => highlight(node.dataset.node));
    node.addEventListener('focus', () => highlight(node.dataset.node));
    node.addEventListener('mouseleave', reset);
    node.addEventListener('blur', reset);
  });
  openOverlay($('#graphOverlay'), $('#closeGraph'));
}
$('#closeGraph').addEventListener('click', () => closeOverlay($('#graphOverlay')));

/* ---------- Overlay plumbing ---------- */
function openOverlay(overlay, focusTarget) {
  state.lastFocused = document.activeElement;
  overlay.hidden = false;
  (focusTarget || overlay.querySelector('button,input,[tabindex]'))?.focus();
}
function closeOverlay(overlay) {
  overlay.hidden = true;
  if (state.lastFocused?.isConnected) state.lastFocused.focus();
  state.lastFocused = null;
}
function closeTopOverlay() {
  const open = [$('#sourceOverlay'), $('#graphOverlay'), $('#forgetOverlay')].find(overlay => !overlay.hidden);
  if (open) closeOverlay(open);
}
[$('#sourceOverlay'), $('#graphOverlay'), $('#forgetOverlay')].forEach(overlay =>
  overlay.addEventListener('click', event => { if (event.target === overlay) closeOverlay(overlay); }));

/* ---------- Forget flow ---------- */
let forgetTarget = '';
function openForget(slug) {
  forgetTarget = slug;
  $('#forgetSlugLabel').textContent = slug;
  $('#forgetTitle').textContent = `Forget ${slug}?`;
  $('#forgetConfirm').value = '';
  $('#confirmForget').disabled = true;
  openOverlay($('#forgetOverlay'), $('#forgetConfirm'));
}
$('#forgetConfirm').addEventListener('input', event => { $('#confirmForget').disabled = event.target.value !== forgetTarget; });
$('#cancelForget').addEventListener('click', () => closeOverlay($('#forgetOverlay')));
$('#confirmForget').addEventListener('click', async () => {
  const row = $(`#reposBody tr[data-slug="${CSS.escape(forgetTarget)}"]`);
  const { status, payload } = await api(`/api/repos/${encodeURIComponent(forgetTarget)}/forget`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: forgetTarget }) });
  if (status === 200) {
    if (row) { row.classList.add('fade-out'); }
    setTimeout(async () => {
      state.logs.delete(forgetTarget); state.logOffsets.delete(forgetTarget);
      state.collapsedLogs.delete(forgetTarget); state.previousStatuses.delete(forgetTarget);
      if (state.selectedRepo === forgetTarget) state.selectedRepo = '';
      await refreshRepos();
      syncDetailTab();
      showView(currentView(), { updateHash: false });
    }, 150);
    closeOverlay($('#forgetOverlay'));
    toast(`forgot ${forgetTarget}`, 'index removed');
  } else {
    closeOverlay($('#forgetOverlay'));
    conflict(payload.error || `forget failed (${status})`);
  }
});

/* ---------- Initialize ---------- */
void (async function init() {
  const [{ payload: overview }, { payload: reposPayload }] = await Promise.all([api('/api/overview'), api('/api/repos')]);
  renderOverview(overview);
  state.repos = reposPayload.repos || [];
  state.repos.forEach(repo => state.previousStatuses.set(repo.slug, repo.status));
  populateRepoFilter();
  syncDetailTab();
  const { view, slug } = routeFromHash();
  if (view === 'detail' && slug) state.selectedRepo = slug;
  showView(view === 'detail' && !slug ? 'repos' : view, { updateHash: false });
  renderRepos();
  schedulePolling();
})();
