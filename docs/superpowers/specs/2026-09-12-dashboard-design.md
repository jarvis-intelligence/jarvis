# jarvis dashboard — Localhost Operator Console

**Date:** 2026-09-12
**Status:** Approved design (brainstorming session, all sections user-approved)
**Target release:** 0.10.0

## Problem

jarvis's only operator surfaces are `jarvis list` / `jarvis status` terminal
tables and raw MCP JSON. The data to visualize is rich and already local —
registry rows with status/freshness/SCIP state/degradation causes
(`registry.py`), in-flight job state and logs (`jobs.py`), per-snapshot
metadata, the package graph, per-tool capability coverage, storage footprint,
and three searchable stores — but nothing renders it.

Market context (2026-09): Sourcegraph — the commercial benchmark — added zoekt
index-status observability to a debug API in 7.5 and ships a full web UI; Kilo
Code ships zero UI; the MCP Inspector established the zero-install
localhost-browser-UI pattern for MCP servers. No local-first code-intelligence
MCP server ships an operator surface. A localhost dashboard is both the
missing observability layer and a differentiator.

## Goal

One command — `jarvis dashboard` — opens a browser console on localhost that
combines:

1. **Operator control panel** — repo health, in-flight jobs with live log
   tails, actions (index / reindex / forget).
2. **Local code-search UI** — tri-modal search over the user's indexes with
   click-through source viewer (a local mini-Sourcegraph).
3. **Tool playground** — invoke all 10 MCP tools with typed inputs, inspect
   JSON responses and latency.

## Decisions (user-approved in this session)

| Decision | Choice |
|---|---|
| Primary purpose | All-in-one (health + search + playground) |
| Serving | Built-in `jarvis dashboard` subcommand; stdlib HTTP; zero new pip deps |
| Mutations | Full operator actions (index / reindex / forget) |
| Ship scope | Product feature — PyPI wheel, README-documented |
| Architecture | Approach A: stdlib HTTP shell + JSON API + vanilla JS SPA |

## Non-goals

- No remote or multi-user access; `127.0.0.1` binding only, no flag to widen.
- No authn/authz (single-tenant, consistent with the repo's no-auth design).
- No WebSocket / SSE — polling only.
- No JS build step, framework, or npm dependency.
- No writes to any store the CLI/MCP paths own (the dashboard spawns the
  existing pipeline as a child; it never runs the pipeline in-process).
- No metrics history beyond what `registry.db` already keeps (no time-series).

## Architecture

### Module layout

```
src/jarvis/dashboard.py            ~450 lines — HTTP server, routes, actions
src/jarvis/dashboard_assets/
    index.html                     SPA shell — 4 tab views
    app.js                         vanilla JS, fetch + render, ~600 lines
    style.css                      dark terminal aesthetic, no framework
```

- CLI: `jarvis dashboard [--port 6080] [--no-open]` added to `index_cli.py`.
  Default opens the browser (`webbrowser.open`); Ctrl-C stops; the dashboard
  dies with the command — nothing persistent, no daemon.
- Port: default `6080` (next to zoekt's `6070`); `JARVIS_DASHBOARD_PORT` env
  override resolved centrally in `config.py` (matches the `JARVIS_`-prefixed
  env convention). Invalid values degrade to the default with a stderr
  warning, like every other env override in the codebase.

### Reads — existing seams only, zero new storage

| Source | Feeds |
|---|---|
| `registry.Registry` | repo table: slug/path/language/status/scip_state/semantic/last_indexed/recovery |
| `jobs.job_state` + `read_launch_record` | in-flight runs: state, pid, log path |
| `query.QueryService` (mirrors `server.py`'s `_service()` lazy singleton, `server.py:72`) | freshness snapshots, capabilities, all nav tools |
| `search` (ZoektLifecycle) + `semantic` + `symbol_search` | tri-modal search page |
| `graph.GraphStore` | package graph / blast radius |
| filesystem (read-only) | storage footprint per store, job log tails, source viewer |

## Views

### 1. Repos (home)

One row per indexed repo: status chip (`indexing` → pulse animation;
`indexed` green / `partial` yellow / `degraded` orange / `failed` red),
language, freshness (published commit vs `git rev-parse HEAD` of the
registered path; "stale +12 commits" in red), SCIP state, semantic on/off,
storage size, last indexed time. Row actions: **Reindex**, **Forget**
(typed-slug confirm modal). A row in `indexing` state shows an expanding live
log-tail pane under it. Header strip: jarvis version, data dir,
zoekt-webserver state, total disk usage. Top form: **Index new repo**
(path + optional slug / language / scheme / semantic flags).

### 2. Repo detail

Snapshot generation + history of retired versions; capabilities card
(per-tool providers from `getIndexStatus`, syntax extraction counts);
degradation cause + the exact recovery command; package-graph neighbors
(up/down dependents, 2-hop — `graph.blast_radius`); storage breakdown
(SCIP db / zoekt shards / lance table); full index log viewer.

### 3. Search

One query box → three columns: Zoekt matches, `semanticSearch` fused
results, symbol hits. Each hit: file path, line range, code snippet.
Click-through opens the in-app **source viewer**: reads the file from the
repo's registered path, renders with line numbers, highlights the hit's
range, anchor-links to it.

### 4. Playground

Pick any of the 10 MCP tools → input form generated from the tool's
parameter documentation → execute → pretty-printed JSON + latency. Calls the
same `@mcp.tool` wrapper functions `server.py` defines (imported, never
re-implemented), so the playground exercises the exact code path an agent
does, including `{error}` payload rendering.

## HTTP API (all JSON under `/api/`)

```
GET  /api/overview                    header: version, data_dir, zoekt state, disk totals
GET  /api/repos                       registry rows + freshness + job state (poll 2s while
                                      any row is `indexing`, else 10s)
GET  /api/repos/{slug}                detail: capabilities, snapshots, storage, graph, recovery
GET  /api/repos/{slug}/log?offset=N   job log tail → {chunk, next_offset}
GET  /api/repos/{slug}/file?p=&start=&end=   source-viewer slice (path-confined)
POST /api/repos/{slug}/reindex        → 202 {ok, job_state}
POST /api/repos/{slug}/forget         → 200 (body must be {"confirm": "<slug>"})
POST /api/repos/index                 {path, slug?, language?, scheme?, semantic?} → 202
GET  /api/search?q=&repo=             tri-modal: zoekt + fused semantic + symbols
GET  /api/graph                       all packages/edges → client-side SVG
GET  /api/tools                       tool catalog: name, description, params
POST /api/tools/{name}/invoke         tool args JSON → tool result + latency_ms
```

Static assets from `dashboard_assets/` with correct content-type and
`Cache-Control: no-cache`.

## Data flow for actions

`POST /reindex` and `POST /index` reuse the exact spawn seam `indexRepo` uses
(`server.py:674-693`): `jobs.lock_state` probe → `write_launch_record` →
`subprocess.Popen(start_new_session=True, stdout → log file)` → return 202
immediately. The dashboard never runs the pipeline in-process. The in-flight
`_launched` Popen bookkeeping is shared logic extracted from `server.py`, not
duplicated. `BuildLockHeld` maps to HTTP 409 with the holder pid.
`jobs.job_state`'s first-match evaluation (states: `running`, `starting`,
`failed-at-startup`, `abandoned`) reports progress; the frontend polls
`/api/repos`.

## Security model

- Bind `127.0.0.1` only — never `0.0.0.0`; no option to change.
- **Host-header / DNS-rebinding guard**: every request must carry
  `Host: 127.0.0.1[:port]` or `localhost[:port]`, else 403. Mutating POSTs
  additionally require `Origin`, when present, to match. No token beyond
  this — single-tenant by design.
- **Source viewer path confinement**: `file?p=` resolves against the repo's
  registered path only; rejects absolute paths, `..` traversal, and symlink
  escapes; serves only files within the registered repo.
- Destructive ops limited to Forget (typed-slug confirm required) and
  Reindex (idempotent-by-lock).

## Error handling

Repo two-tier rule, mirrored: handler internals raise typed errors; the HTTP
boundary catches broad `Exception` → `{"error": "..."}` JSON with correct
status — 400 validation, 404 unknown slug/tool, 405 wrong method, 409 lock
held, 500 bug. Broad on purpose, same contract as `server.py`; every catch
logs to stderr. Frontend renders errors inline per panel, never a blank
screen.

## Packaging

**Current baseline: the wheel ships no data files at all.** `pyproject.toml`
has only `[tool.setuptools] package-dir` + `packages` — no
`[tool.setuptools.package-data]`, no `include-package-data`, and no MANIFEST
(`pyproject.toml:126-132`). Nothing outside `.py`/compiled modules reaches
the wheel today, so asset shipping is a concrete packaging change, not an
existing mechanism:

- **Add to `pyproject.toml`** a new section:
  `[tool.setuptools.package-data] jarvis = ["dashboard_assets/*.html",
  "dashboard_assets/*.js", "dashboard_assets/*.css"]`.
  The `package-data` mapping includes files in built wheels directly —
  `include-package-data`/MANIFEST are not needed for it. No new package
  entry in `packages`; the asset dir has no `.py` files so it needs no
  `__init__.py`.
- Cython interplay: `StripCompiledSources` (`setup.py:15-27`) overrides only
  `build_py.find_package_modules` — data files flow through a different
  discovery path — so assets pass through compiled wheels untouched.
  `dashboard.py` itself is picked up by the flat `src/jarvis/*.py` glob
  (`setup.py:39`) and compiles like every other module. No setup.py change.
- `scripts/check_wheel_contents.py` gains a carve-out allowing
  `dashboard_assets/*` as expected non-code wheel payload (today it would
  fail the wheel on any non-code file).
- Assets load via `importlib.resources.files("jarvis") /
  "dashboard_assets"` — identical behavior dev vs wheel.
- Verification: `python scripts/check_wheel_contents.py dist/*.whl` plus a
  `zipinfo dist/*.whl | grep dashboard_assets` line in the release checklist.

## Testing

`tests/test_dashboard.py` (mirroring convention), all under
`JARVIS_DATA_DIR` isolation:

- Handler-level unit tests with synthetic registry + fake QueryService
  fixtures (same seams as `test_server_tools.py`): status chips, 409
  lock-held mapping, host-header guard (403 on `Host: evil.com`),
  path-confinement rejections, log-offset tailing, `{"confirm": slug}`
  enforcement.
- End-to-end through a real `ThreadingHTTPServer` on an ephemeral port +
  stdlib `urllib` in-test (no new test deps).
- Action spawn tests monkeypatch `subprocess.Popen` (existing seam).
- Playground `invoke` tested against the fake-QueryService fixtures.

CI: existing `test.yml` gate covers it (`pytest -m "not integration"`).

## Docs & release

- README: new "Dashboard" section (one command, screenshot, port + env var)
  and the command in the CLI surface list.
- `docs/`: short dashboard page linked from README's Documentation section.
- CHANGELOG under 0.10.0.
- AGENTS.md command list gains `jarvis dashboard`.

## Success criteria

1. `uv tool install jarvis-mcp && jarvis dashboard` → browser opens, repos
   render from the real `~/.jarvis`.
2. Index a repo from the form → row pulses → log tail streams → flips to
   `indexed`.
3. Reindex while running → 409 surfaced as "already indexing (pid N)".
4. Search + click-through source viewer works on a real index.
5. Playground invokes all 10 tools, renders `{error}` payloads identically
   to MCP.
6. `uv run pytest -m "not integration"` green; built wheel contains
   `dashboard_assets/*`.
