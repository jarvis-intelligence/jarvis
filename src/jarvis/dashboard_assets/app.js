"use strict";

const routes = {
  repos: "#/repos",
  search: "#/search",
  playground: "#/playground",
};

const state = {
  overview: null,
  repos: [],
  pollTimer: null,
  pollEpoch: 0,
  logOffsets: new Map(),
  logs: new Map(),
  collapsedLogs: new Set(),
  previousStatuses: new Map(),
  indexForm: false,
  banner: null,
  selectedTool: null,
  search: null,
};

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2).toLowerCase(), value);
    else if (name === "checked" || name === "disabled" || name === "hidden") node[name] = value;
    else node.setAttribute(name, String(value));
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options) {
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...((options || {}).headers || {}) },
      ...options,
    });
    const payload = await response.json();
    return { ...payload, _status: response.status };
  } catch (error) {
    return { error: `request failed: ${error.message}`, _status: 0 };
  }
}

function title(eyebrow, heading, action) {
  return el("div", { class: "view-heading" }, [
    el("div", { class: "heading-line" }, [el("span", { class: "mono-eyebrow", text: `── ${eyebrow}` }), action]),
    el("h1", { text: heading }),
  ]);
}

function statusChip(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const name = normalized === "indexing" ? "INDEXING" : normalized.toUpperCase();
  const className = {
    indexing: "status-live",
    indexed: "status-ok",
    partial: "status-partial",
    degraded: "status-degraded",
    failed: "status-failed",
  }[normalized] || "status-degraded";
  return el("span", { class: `status-chip ${className}` }, [
    el("span", { class: `chip-dot ${normalized === "indexing" ? "pulse" : ""}`, "aria-hidden": "true" }),
    el("span", { class: "mono-eyebrow", text: name }),
  ]);
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, unit)).toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function relativeTime(value) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
  return `${Math.floor(seconds / 86400)} days ago`;
}

function button(text, className, onClick, extra) {
  return el("button", { type: "button", class: `pill ${className || "outline"}`, onClick, ...(extra || {}) }, text);
}

function inlineError(message) {
  return el("div", { class: "banner failed", role: "alert", text: message || "request failed" });
}

function toast(message, detail) {
  const notice = el("div", { class: "toast", role: "status" }, [
    el("span", { text: message }), detail && el("span", { class: "mono-md", text: detail }),
  ]);
  document.body.append(notice);
  window.setTimeout(() => notice.remove(), 4000);
}

function clearPolling() {
  state.pollEpoch += 1;
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function schedulePolling(indexing, refresh) {
  clearPolling();
  const epoch = state.pollEpoch;
  const tick = async () => {
    await refresh();
    if (epoch === state.pollEpoch) state.pollTimer = window.setTimeout(tick, indexing() ? 2000 : 10000);
  };
  state.pollTimer = window.setTimeout(tick, indexing() ? 2000 : 10000);
}

async function refreshShell() {
  const overview = await api("/api/overview");
  if (!overview.error) state.overview = overview;
  renderShell();
}

function renderShell() {
  const overview = state.overview || {};
  const top = document.getElementById("top-right");
  const strip = document.getElementById("status-strip");
  top.replaceChildren(
    el("span", { text: overview.version ? `v${overview.version}` : "" }),
    el("span", { class: overview.zoektRunning ? "top-live" : "" }, ["● ", overview.zoektRunning ? "zoekt live" : "zoekt idle"]),
  );
  strip.replaceChildren(
    el("span", { class: "body-mid", text: "data " }), el("span", { class: "instrument", text: overview.dataDir || "—" }),
    el("span", { class: "strip-sep", text: " · " }), el("span", { class: "body-mid", text: "repos " }), el("span", { class: "instrument", text: String(overview.repos || 0) }),
    el("span", { class: "strip-sep", text: " · " }), el("span", { class: "body-mid", text: "disk " }), el("span", { class: "instrument", text: formatBytes(overview.diskBytes) }),
    el("span", { class: "strip-sep", text: " · " }), el("span", { class: "body-mid", text: "zoekt " }), el("span", { class: "instrument", text: overview.zoektBase || "offline" }),
  );
}

function setActiveTab(name, slug) {
  for (const tab of document.querySelectorAll("#tabs a")) tab.classList.toggle("active", tab.dataset.tab === name);
  const detail = document.querySelector('[data-tab="repo"]');
  detail.hidden = !slug;
  if (slug) detail.href = `#/repo/${encodeURIComponent(slug)}`;
}

function currentRoute() {
  const hash = location.hash || routes.repos;
  const parts = hash.replace(/^#\//, "").split("/");
  return { name: parts[0] || "repos", slug: parts.slice(1).map(decodeURIComponent).join("/") };
}

async function route() {
  clearPolling();
  await refreshShell();
  const { name, slug } = currentRoute();
  if (name === "repo" && slug) return viewRepo(slug);
  if (name === "search") return viewSearch();
  if (name === "playground") return viewPlayground();
  if (location.hash !== routes.repos) location.hash = routes.repos;
  return viewRepos();
}

function freshCell(row) {
  const freshness = row.freshness;
  if (!freshness) return el("span", { class: "body-mid", text: "—" });
  if (freshness.stale) return el("span", { class: "stale", text: `● stale +${freshness.stale}` });
  return el("span", { class: "body-mid", text: "● fresh" });
}

function languageChip(language) {
  return el("span", { class: "language-chip", text: String(language || "—").slice(0, 5) });
}

function repoCell(content, className) { return el("div", { class: `table-cell ${className || ""}` }, content); }

function repoRow(row) {
  const isIndexing = row.status === "indexing";
  const status = el("div", { class: "repo-status" }, [statusChip(row.status)]);
  if (isIndexing) status.append(button(
    state.collapsedLogs.has(row.slug) ? "⌄" : "⌃",
    "outline icon-pill log-toggle",
    () => {
      state.collapsedLogs.has(row.slug) ? state.collapsedLogs.delete(row.slug) : state.collapsedLogs.add(row.slug);
      renderRepos();
    },
    { "aria-label": `${state.collapsedLogs.has(row.slug) ? "Expand" : "Collapse"} log for ${row.slug}` },
  ));
  const rowElement = el("div", { class: "repo-row", "data-slug": row.slug }, [
    repoCell(status, "status-cell"),
    repoCell(el("a", { href: `#/repo/${encodeURIComponent(row.slug)}`, class: "repo-link", text: row.slug }), "repo-cell"),
    repoCell(languageChip(row.language)),
    repoCell(freshCell(row)),
    repoCell(el("span", { class: row.scipState === "failed" ? "status-degraded" : "body-mid", text: row.scipState || "—" })),
    repoCell(el("span", { class: "body-mid", text: row.semanticIndexedAt ? "on" : row.semanticDeclined ? "off" : "—" })),
    repoCell(el("span", { class: "mono-md", text: formatBytes(row.storageBytes && row.storageBytes.total) }), "cell-size"),
    repoCell(el("span", { class: "mono-md", text: relativeTime(row.lastIndexed) }), "cell-time"),
    repoCell(el("div", { class: "row-actions" }, [
      button("⟳", "outline icon-pill", () => reindex(row)),
      overflowMenu(row),
    ])),
  ]);
  if (isIndexing && !state.collapsedLogs.has(row.slug)) rowElement.append(logPane(row.slug));
  return rowElement;
}

function overflowMenu(row) {
  const menu = el("details", { class: "overflow-menu" }, [el("summary", { class: "pill outline icon-pill", text: "⋯", "aria-label": `Actions for ${row.slug}` })]);
  const list = el("div", { class: "menu-list" }, [
    el("a", { href: `#/repo/${encodeURIComponent(row.slug)}`, text: "view detail" }),
    el("button", { type: "button", text: "copy CLI command", onClick: () => copyText(`jarvis reindex ${row.slug}`) }),
    el("button", { type: "button", class: "menu-danger", text: "forget…", onClick: () => forgetModal(row) }),
  ]);
  menu.append(list);
  return menu;
}

function logPane(slug) {
  const log = state.logs.get(slug) || "";
  return el("div", { class: "log-pane" }, [
    el("pre", { class: "well log-well", text: log || "waiting for index output…" }),
    el("span", { class: "streaming mono-eyebrow" }, [el("span", { class: "accent", text: "▌" }), " STREAMING"]),
  ]);
}

function indexForm() {
  const path = el("input", { name: "path", required: true, placeholder: "/path/to/repository", class: "mono-input" });
  const slug = el("input", { name: "slug", placeholder: "optional slug", class: "mono-input" });
  const language = el("select", { name: "language" }, ["auto", "python", "typescript", "ruby", "java"].map(value => el("option", { value, text: value })));
  const scheme = el("select", { name: "scheme" }, [el("option", { value: "scip", text: "SCIP baseline" }), el("option", { value: "none", text: "no SCIP" })]);
  const semantic = el("input", { type: "checkbox", name: "semantic" });
  const error = el("p", { class: "field-error", hidden: true });
  const form = el("form", { class: "index-form", onsubmit: async event => {
    event.preventDefault(); error.hidden = true;
    const payload = { path: path.value.trim(), slug: slug.value.trim(), language: language.value, semantic: semantic.checked, scip: scheme.value === "scip" };
    const result = await api("/api/repos/index", { method: "POST", body: JSON.stringify(payload) });
    if (result.error) { error.textContent = result.error; error.hidden = false; return; }
    state.indexForm = false; toast("index started", payload.path); await renderRepos();
  } }, [
    el("label", { text: "repository path" }, path),
    el("label", { text: "slug" }, slug),
    el("label", { text: "language" }, language),
    el("label", { text: "index scheme" }, scheme),
    el("label", { class: "toggle-label" }, [semantic, " semantic search"]), error,
    el("div", { class: "form-actions" }, [el("button", { type: "submit", class: "pill primary", text: "Run index" }), button("Cancel", "link-pill", () => { state.indexForm = false; renderRepos(); })]),
  ]);
  return form;
}

async function reindex(row) {
  const result = await api(`/api/repos/${encodeURIComponent(row.slug)}/reindex`, { method: "POST", body: "{}" });
  if (result._status === 409) { state.banner = `already indexing${result.pid ? ` (pid ${result.pid})` : ""}`; await renderRepos(); return; }
  if (result.error) { state.banner = result.error; await renderRepos(); return; }
  toast("reindex started", row.slug); await renderRepos();
}

function forgetModal(row) {
  const typed = el("input", { class: "mono-input", placeholder: `type ${row.slug} to confirm` });
  const confirm = el("button", { type: "button", class: "pill destructive", text: "Forget", disabled: true });
  const modal = el("section", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "forget-title" }, [
    el("h2", { id: "forget-title", text: `Forget ${row.slug}?` }),
    el("p", { text: "Removes the index, search shards, and registry entry. The repository on disk is untouched." }),
    typed,
    el("div", { class: "modal-actions" }, [button("Cancel", "outline", closeOverlay), confirm]),
  ]);
  typed.addEventListener("input", () => { confirm.disabled = typed.value === row.slug ? false : true; });
  confirm.addEventListener("click", async () => {
    const result = await api(`/api/repos/${encodeURIComponent(row.slug)}/forget`, { method: "POST", body: JSON.stringify({ confirm: typed.value }) });
    if (result.error) { modal.append(inlineError(result.error)); return; }
    closeOverlay();
    const oldRow = document.querySelector(`[data-slug="${CSS.escape(row.slug)}"]`);
    if (oldRow) { oldRow.classList.add("leaving"); window.setTimeout(() => oldRow.remove(), 150); }
    toast("repository forgotten", row.slug); window.setTimeout(renderRepos, 160);
  });
  openOverlay(modal);
  typed.focus();
}

function openOverlay(content) {
  const overlay = document.getElementById("overlay");
  overlay.replaceChildren(content);
  overlay.hidden = false;
}
function closeOverlay() { const overlay = document.getElementById("overlay"); overlay.hidden = true; overlay.replaceChildren(); }

async function renderRepos() {
  const response = await api("/api/repos");
  if (currentRoute().name !== "repos") return;
  if (response.error) { document.getElementById("view").replaceChildren(title("INDEXED REPOSITORIES", "Repos"), inlineError(response.error)); return; }
  for (const row of response.repos || []) {
    if (state.previousStatuses.get(row.slug) === "indexing" && row.status === "indexed") {
      toast("indexed", row.slug);
      window.setTimeout(() => {
        state.collapsedLogs.add(row.slug);
        renderRepos();
      }, 1500);
    }
    state.previousStatuses.set(row.slug, row.status);
  }
  state.repos = response.repos || [];
  const trigger = button("+ index new repo", "outline", () => { state.indexForm = !state.indexForm; renderRepos(); });
  const content = [title("INDEXED REPOSITORIES", "Repos", trigger)];
  if (state.banner) content.push(el("div", { class: "banner degraded", text: state.banner }));
  if (state.indexForm) content.push(indexForm());
  if (!state.repos.length) {
    content.push(el("section", { class: "empty-state" }, [el("h2", { text: "No repositories indexed" }), el("p", { text: "Run " }), el("code", { text: "jarvis index /path/to/repo" }), el("span", { text: " or use the button above." })]));
  } else {
    const table = el("section", { class: "repos-table", role: "table" });
    table.append(el("div", { class: "repo-header", role: "row" }, ["status", "repo", "lang", "freshness", "SCIP", "sem", "size", "last indexed", "actions"].map((label, index) => repoCell(el("span", { class: "mono-eyebrow", text: label }), index === 6 ? "cell-size" : index === 7 ? "cell-time" : ""))));
    for (const row of state.repos) table.append(repoRow(row));
    content.push(table);
  }
  document.getElementById("view").replaceChildren(...content);
}

async function refreshLiveLogs() {
  const indexing = state.repos.filter(row => row.status === "indexing");
  await Promise.all(indexing.map(async row => {
    const offset = state.logOffsets.get(row.slug) || 0;
    const result = await api(`/api/repos/${encodeURIComponent(row.slug)}/log?offset=${offset}`);
    if (!result.error) {
      state.logOffsets.set(row.slug, result.nextOffset || 0);
      if (result.chunk) state.logs.set(row.slug, (state.logs.get(row.slug) || "") + result.chunk);
    }
  }));
}

async function viewRepos() {
  setActiveTab("repos");
  await renderRepos();
  await refreshLiveLogs();
  await renderRepos();
  schedulePolling(() => state.repos.some(row => row.status === "indexing"), async () => { await renderRepos(); await refreshLiveLogs(); await renderRepos(); });
}

function card(label, children, className) { return el("section", { class: `panel ${className || ""}` }, [el("h2", { class: "mono-eyebrow", text: label }), ...(children || [])]); }

function storageCard(storage) {
  const items = Object.entries(storage || {}).filter(([name]) => name !== "total");
  const max = Math.max(1, ...items.map(([, bytes]) => Number(bytes || 0)));
  return card("STORAGE", items.map(([name, bytes]) => el("div", { class: "storage-row" }, [
    el("span", { class: "mono-md", text: name }),
    el("span", { class: "storage-track" }, el("span", { class: `storage-fill ${Number(bytes) === max ? "largest" : ""}`, style: `width:${Math.round(Number(bytes || 0) / max * 100)}%` })),
    el("span", { class: "mono-md", text: formatBytes(bytes) }),
  ])));
}

function capabilitiesCard(capabilities) {
  const entries = Array.isArray(capabilities) ? capabilities.map(value => [value.name || value.tool || "capability", value]) : Object.entries(capabilities || {});
  return card("CAPABILITIES", entries.length ? entries.map(([name, value]) => {
    const available = typeof value === "object" ? value.available !== false : Boolean(value);
    const provider = typeof value === "object" ? value.provider || value.source || "" : "";
    const reason = typeof value === "object" ? value.reason || "" : "";
    return el("div", { class: "capability-row" }, [el("span", { class: "mono-md", text: name }), el("span", { class: "body-mid", text: provider }), el("span", { class: available ? "status-ok" : "status-failed", text: available ? "✓" : `✗ ${reason}` })]);
  }) : [el("span", { class: "body-mid", text: "no capability data" })]);
}

async function fetchFullLog(slug) {
  const log = await api(`/api/repos/${encodeURIComponent(slug)}/log?offset=0`);
  return log.error ? log.error : log.chunk || "no index log";
}

async function viewRepo(slug) {
  setActiveTab("repo", slug);
  const view = document.getElementById("view");
  view.replaceChildren(title(`REPO · ${slug}`, slug), el("div", { class: "skeleton detail-skeleton" }));
  const detail = await api(`/api/repos/${encodeURIComponent(slug)}`);
  if (detail.error) { view.replaceChildren(title(`REPO · ${slug}`, slug), inlineError(detail.error)); return; }
  const logText = await fetchFullLog(slug);
  const heading = title(`REPO · ${slug}`, slug);
  heading.append(el("div", { class: "detail-status" }, [statusChip(detail.status), el("span", { class: "mono-md", text: `generation ${detail.freshness && detail.freshness.generation || "—"} · ${formatBytes(detail.storageBytes && detail.storageBytes.total)}` })]));
  const snapshots = card("SNAPSHOTS", (detail.snapshots || []).length ? detail.snapshots.map(snapshot => el("div", { class: `snapshot-row ${snapshot.current ? "current" : ""}` }, [el("span", { class: "mono-md", text: snapshot.name }), el("span", { class: "body-mid", text: snapshot.current ? "current" : relativeTime(snapshot.mtime * 1000) })])) : [el("span", { class: "body-mid", text: "no snapshots" })]);
  const recovery = detail.recovery && (detail.status === "degraded" || detail.status === "failed" || detail.status === "partial") ? card("RECOVERY", [el("p", { text: detail.recovery }), el("code", { class: "recovery-command", text: `jarvis reindex ${slug}` }), button("copy", "outline", () => copyText(`jarvis reindex ${slug}`))]) : null;
  const graph = card("PACKAGE GRAPH", [
    el("p", { class: "mono-md", text: `depends on (${(detail.graph && detail.graph.dependsOn || []).length}): ${(detail.graph && detail.graph.dependsOn || []).join(" · ") || "—"}` }),
    el("p", { class: "mono-md", text: `depended on by (${(detail.graph && detail.graph.dependedOnBy || []).length}): ${(detail.graph && detail.graph.dependedOnBy || []).join(" · ") || "—"}` }),
    el("button", { type: "button", class: "graph-link", text: "view graph →", onClick: () => graphOverlay(slug) }),
  ]);
  const grid = el("div", { class: "detail-grid" }, [snapshots, capabilitiesCard(detail.capabilities), recovery, graph, storageCard(detail.storageBytes), card("INDEX LOG", [el("pre", { class: "well full-log", text: logText })])].filter(Boolean));
  view.replaceChildren(heading, grid);
  if (detail.status === "indexing") schedulePolling(() => true, () => viewRepo(slug));
}

function resultPath(hit) { return hit.path || hit.filePath || hit.file_path || "—"; }
function resultLine(hit) { return hit.lineNumber || hit.startLine || hit.start_line || hit.line || 1; }
function hitRepo(hit, fallback) { return hit.repo || hit.repository || fallback; }

function searchHit(hit, repo, semantic) {
  const path = resultPath(hit); const line = resultLine(hit);
  const click = () => { const hitRepoName = hitRepo(hit, repo); if (hitRepoName && path !== "—") sourceOverlay(hitRepoName, path, line, hit.endLine || hit.end_line || line); };
  const snippet = hit.lineText || hit.snippet || hit.text || hit.name || "";
  return el("button", { type: "button", class: "hit-card", onClick: click }, [
    el("span", { class: "mono-md hit-path", text: `${path}:${line}` }),
    el("code", { text: snippet }),
    semantic && el("span", { class: "body-mid", text: `score ${hit.score ?? "—"} · ${hit.provenance || "vector+lex"}` }),
    hit.kind && el("span", { class: "body-mid", text: `${hit.kind} · ${path}` }),
  ]);
}

function searchColumn(label, identity, hits, error, repo, semantic) {
  const content = [el("h2", { class: "mono-eyebrow", text: label })];
  if (error) content.push(inlineError(error));
  else if (!hits || !hits.length) content.push(el("p", { class: "body-mid", text: "no matches" }));
  else content.push(...hits.map(hit => searchHit(hit, repo, semantic)));
  return el("section", { class: `search-column ${identity}` }, content);
}

async function executeSearch(form, query, repo) {
  const params = new URLSearchParams({ q: query.value.trim() });
  if (repo.value) params.set("repo", repo.value);
  const result = await api(`/api/search?${params}`);
  state.search = result;
  renderSearchForm(form, query.value, repo.value);
}

function renderSearchForm(existingForm, initialQuery, initialRepo) {
  const query = el("input", { type: "search", value: initialQuery || "", placeholder: "search code, symbols, or meaning", "aria-label": "Search query" });
  const repo = el("select", { "aria-label": "Repository filter" }, [el("option", { value: "", text: "all repos" }), ...state.repos.map(row => el("option", { value: row.slug, text: row.slug }))]);
  repo.value = initialRepo || "";
  const form = el("form", { class: "search-form", onsubmit: event => { event.preventDefault(); if (query.value.trim()) executeSearch(form, query, repo); } }, [query, repo, el("button", { class: "pill primary", type: "submit", text: "⌕", "aria-label": "Search" })]);
  existingForm.replaceWith(form);
  const results = document.getElementById("search-results");
  results.replaceChildren();
  if (!state.search) { results.append(el("p", { class: "search-ghost", text: "search code, symbols, or meaning" })); return; }
  if (state.search.error) { results.append(inlineError(state.search.error)); return; }
  const lexical = state.search.lexical && (state.search.lexical.hits || state.search.lexical.results) || [];
  const semantic = state.search.semantic && (state.search.semantic.hits || state.search.semantic.results || state.search.semantic) || [];
  const symbols = state.search.symbols || [];
  results.append(el("div", { class: "search-meta mono-md", text: `${state.search.elapsedMs ?? "—"} ms · fused` }));
  results.append(el("div", { class: "search-grid" }, [
    searchColumn("LEXICAL · ZOEKT", "lexical", lexical, state.search.lexicalError, repo.value, false),
    searchColumn("SEMANTIC · FUSED", "semantic", Array.isArray(semantic) ? semantic : [], state.search.semanticError, repo.value, true),
    searchColumn("SYMBOLS", "symbols", symbols, state.search.symbolsError, repo.value, false),
  ]));
}

async function viewSearch() {
  setActiveTab("search");
  const view = document.getElementById("view");
  const placeholder = el("form", { class: "search-form" });
  view.replaceChildren(placeholder, el("div", { id: "search-results" }));
  if (!state.repos.length) { const response = await api("/api/repos"); if (!response.error) state.repos = response.repos || []; }
  renderSearchForm(placeholder, state.search && state.search.query, state.search && state.search.repo);
}

async function sourceOverlay(repo, path, start, end) {
  const result = await api(`/api/repos/${encodeURIComponent(repo)}/file?p=${encodeURIComponent(path)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
  if (result.error) { openOverlay(el("section", { class: "modal source-modal" }, [button("✕", "outline close-button", closeOverlay), inlineError(result.error)])); return; }
  const lines = el("pre", { class: "source-lines" });
  (result.lines || []).forEach((line, index) => {
    const number = Number(result.start || start) + index;
    lines.append(el("span", { class: `source-line ${number >= start && number <= end ? "range-hit" : ""}` }, [el("span", { class: "line-number", text: String(number) }), el("span", { class: "line-text", text: line })]));
  });
  const modal = el("section", { class: "modal source-modal", role: "dialog", "aria-modal": "true" }, [
    el("header", { class: "modal-header" }, [el("span", { class: "mono-md", text: result.path }), button("copy path", "outline", () => copyText(result.path)), button("✕", "outline close-button", closeOverlay)]), lines,
  ]);
  openOverlay(modal);
  const active = modal.querySelector(".range-hit"); if (active) active.scrollIntoView({ block: "center" });
}

function renderJson(value) {
  if (value === null || typeof value !== "object") return el("span", { class: "json-value", text: JSON.stringify(value) });
  const details = el("details", { class: "json-node", open: true });
  const isArray = Array.isArray(value); const entries = Object.entries(value);
  details.append(el("summary", { text: `${isArray ? "[" : "{"} ${entries.length} ${isArray ? "items" : "keys"}` }));
  const body = el("div", { class: "json-children" });
  for (const [key, nested] of entries) body.append(el("div", { class: "json-entry" }, [el("span", { class: "json-key", text: isArray ? "" : `${key}: ` }), renderJson(nested)]));
  details.append(body);
  return details;
}

async function viewPlayground() {
  setActiveTab("playground");
  const view = document.getElementById("view");
  view.replaceChildren(title("MCP TOOLING", "Playground"), el("div", { class: "skeleton playground-skeleton" }));
  const catalog = await api("/api/tools");
  if (catalog.error) { view.replaceChildren(title("MCP TOOLING", "Playground"), inlineError(catalog.error)); return; }
  const tools = catalog.tools || []; state.selectedTool ||= tools[0] && tools[0].name;
  const rail = el("aside", { class: "tool-rail" }, [el("h2", { class: "mono-eyebrow", text: "TOOLS" })]);
  const panel = el("section", { class: "tool-panel" });
  const selectTool = name => {
    state.selectedTool = name;
    for (const entry of rail.querySelectorAll("button")) entry.classList.toggle("active", entry.dataset.tool === name);
    renderToolPanel(panel, tools.find(tool => tool.name === name));
  };
  for (const tool of tools) rail.append(el("button", { type: "button", class: tool.name === state.selectedTool ? "active" : "", "data-tool": tool.name, text: tool.name, onClick: () => selectTool(tool.name) }));
  view.replaceChildren(title("MCP TOOLING", "Playground"), el("div", { class: "playground-layout" }, [rail, panel]));
  selectTool(state.selectedTool);
}

function renderToolPanel(panel, tool) {
  if (!tool) { panel.replaceChildren(el("p", { class: "body-mid", text: "no tools available" })); return; }
  const form = el("form", { class: "tool-form" });
  const fields = [];
  for (const param of tool.params || []) {
    let input;
    const type = String(param.type || "str").toLowerCase();
    if (type.includes("bool")) input = el("input", { type: "checkbox", name: param.name, checked: Boolean(param.default) });
    else input = el("input", { name: param.name, required: param.required, type: type.includes("int") || type.includes("float") || type.includes("number") ? "number" : "text", value: param.default ?? "", class: "mono-input" });
    fields.push({ param, input });
    form.append(el("label", {}, [el("span", { class: "mono-md", text: param.name }), input, el("small", { class: "body-mid", text: `${param.type || "str"}${param.required ? " · required" : ""}` })]));
  }
  const run = el("button", { type: "submit", class: "pill primary", text: "run ⏎" });
  const latency = el("span", { class: "mono-md body-mid" });
  const response = el("div", { class: "well json-well" }, el("span", { class: "body-mid", text: "response appears here" }));
  form.append(el("div", { class: "run-row" }, [run, latency]));
  form.addEventListener("submit", async event => {
    event.preventDefault(); run.disabled = true; run.textContent = "running…";
    const body = {};
    for (const { param, input } of fields) {
      if (input.type === "checkbox") body[param.name] = input.checked;
      else if (input.value !== "" || param.required) body[param.name] = input.type === "number" ? Number(input.value) : input.value;
    }
    const result = await api(`/api/tools/${encodeURIComponent(tool.name)}/invoke`, { method: "POST", body: JSON.stringify(body) });
    response.classList.toggle("error-json", Boolean(result.error));
    response.replaceChildren(renderJson(result.error ? { error: result.error } : result.result));
    latency.textContent = result.elapsedMs === undefined ? "" : `${result.elapsedMs} ms`;
    run.disabled = false; run.textContent = "run ⏎";
  });
  panel.replaceChildren(el("h2", { text: tool.name }), el("p", { class: "body-mid", text: tool.description || "" }), form, response);
}

function svgEl(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
  return node;
}

async function graphOverlay(focalRepo) {
  const result = await api("/api/graph");
  if (result.error) { openOverlay(el("section", { class: "modal" }, [button("✕", "outline close-button", closeOverlay), inlineError(result.error)])); return; }
  const nodes = result.nodes || []; const edges = result.edges || []; const positions = new Map(); const neighbors = new Map();
  const width = 900; const height = 600; const cx = width / 2; const cy = height / 2; const radius = Math.min(width, height) * 0.35;
  nodes.forEach((node, index) => { positions.set(node.id, { x: cx + radius * Math.cos(index / Math.max(nodes.length, 1) * Math.PI * 2), y: cy + radius * Math.sin(index / Math.max(nodes.length, 1) * Math.PI * 2) }); neighbors.set(node.id, new Set()); });
  for (const edge of edges) { neighbors.get(edge.from)?.add(edge.to); neighbors.get(edge.to)?.add(edge.from); }
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "graph-svg", role: "img", "aria-label": "Package graph" });
  const showNeighborhood = id => {
    const visible = new Set([id]); let frontier = new Set([id]);
    for (let hop = 0; hop < 2; hop += 1) { const next = new Set(); for (const item of frontier) for (const neighbor of neighbors.get(item) || []) { visible.add(neighbor); next.add(neighbor); } frontier = next; }
    for (const group of svg.querySelectorAll(".graph-node")) group.classList.toggle("muted", !visible.has(group.dataset.node));
    for (const line of svg.querySelectorAll(".graph-edge")) line.classList.toggle("highlighted", visible.has(line.dataset.from) && visible.has(line.dataset.to));
  };
  const clearNeighborhood = () => { for (const group of svg.querySelectorAll(".muted")) group.classList.remove("muted"); for (const line of svg.querySelectorAll(".highlighted")) line.classList.remove("highlighted"); };
  for (const edge of edges) { const from = positions.get(edge.from), to = positions.get(edge.to); if (from && to) svg.append(svgEl("line", { x1: from.x, y1: from.y, x2: to.x, y2: to.y, class: "graph-edge", "data-from": edge.from, "data-to": edge.to })); }
  for (const node of nodes) {
    const point = positions.get(node.id); const group = svgEl("g", { class: `graph-node ${node.repo === focalRepo ? "focal" : ""}`, "data-node": node.id, tabindex: "0" });
    group.append(svgEl("rect", { x: point.x - 66, y: point.y - 15, width: 132, height: 30, rx: 15 }));
    const text = svgEl("text", { x: point.x, y: point.y + 4, "text-anchor": "middle" }); text.textContent = `${node.repo}:${node.name}`; group.append(text);
    group.addEventListener("pointerenter", () => showNeighborhood(node.id)); group.addEventListener("pointerleave", clearNeighborhood);
    group.addEventListener("focus", () => showNeighborhood(node.id)); group.addEventListener("blur", clearNeighborhood); svg.append(group);
  }
  openOverlay(el("section", { class: "modal graph-modal", role: "dialog", "aria-modal": "true" }, [el("header", { class: "modal-header" }, [el("h2", { text: "Package graph" }), button("✕", "outline close-button", closeOverlay)]), svg]));
}

async function copyText(value) {
  try { await navigator.clipboard.writeText(value); toast("copied", value); }
  catch { toast("copy unavailable", value); }
}

document.getElementById("overlay").addEventListener("click", event => { if (event.target.id === "overlay") closeOverlay(); });
window.addEventListener("hashchange", route);
route();
