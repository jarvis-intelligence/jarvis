# jarvis dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `jarvis dashboard` — a localhost operator console (repos health + live logs + index/reindex/forget actions, tri-modal search with source viewer, 10-tool playground) as a product feature in jarvis-mcp 0.10.0.

**Architecture:** Stdlib `ThreadingHTTPServer` on `127.0.0.1` serving a vanilla-JS SPA from package data plus a JSON API. The API layer calls existing library seams directly — `Registry`, `jobs`, `QueryService` (via `server.py`'s singletons and helpers), `search`/`semantic`/`symbol_search`, `GraphStore` — and actions reuse `server._spawn_index` (the `indexRepo` detached-child seam) and a `forget_repo` extracted from `_cmd_forget`. No new storage, no new dependencies.

**Tech Stack:** Python 3.12+ stdlib (`http.server`, `json`, `urllib.parse`, `importlib.resources`), vanilla HTML/CSS/JS, pytest with `JARVIS_DATA_DIR` isolation.

**Specs:** [`../specs/2026-09-12-dashboard-design.md`](../specs/2026-09-12-dashboard-design.md) (product) · [`../specs/2026-09-12-dashboard-uiux-brief.md`](../specs/2026-09-12-dashboard-uiux-brief.md) (visual tokens — the CSS custom properties in its §9 are copied verbatim into `style.css`).

## Global Constraints

- Zero new pip dependencies; stdlib only in `dashboard.py`.
- Bind `127.0.0.1` exclusively — no `0.0.0.0`, no option to widen.
- Every request must pass the host-header guard (`Host: 127.0.0.1[:port]` or `localhost[:port]`); mutating POSTs with an `Origin` header must match too — else 403.
- HTTP boundary catches broad `Exception` → `{"error": "..."}` JSON with status 400/404/405/409/500; every catch logs to stderr. Mirrors `server.py`'s never-raise contract.
- `JARVIS_DASHBOARD_PORT` env override resolved centrally in `config.py`; invalid value degrades to default 6080 with a stderr warning.
- The dashboard never runs the indexing pipeline in-process: index/reindex go through `server._spawn_index`; forget through `index_cli.forget_repo`.
- Source viewer file access is confined to the repo's registered path: reject absolute paths, `..` traversal, symlink escapes.
- Every test touching registry/index state sets `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` before constructing anything.
- Style: `from __future__ import annotations`, `X | None` typing, module docstring stating provenance ("ported/simplified" convention — here: new module, state what it deliberately is NOT: no auth, no remote, no framework).
- Commits: Conventional Commits, lowercase imperative (`feat(dashboard): ...`, `refactor(index): ...`, `docs: ...`, `test(dashboard): ...`).
- The full gate `uv run pytest -m "not integration"` must stay green after every task.

## File Structure

```
Create: src/jarvis/dashboard.py               HTTP server, guard, routes, JSON API, actions
Create: src/jarvis/dashboard_assets/index.html  SPA shell — 4 tab views
Create: src/jarvis/dashboard_assets/app.js      fetch + render, polling, actions
Create: src/jarvis/dashboard_assets/style.css   xAI token sheet (UI brief §9)
Create: tests/test_dashboard.py                all dashboard tests
Create: docs/dashboard.md                      user-facing docs page
Modify: src/jarvis/config.py                   + DASHBOARD_DEFAULT_PORT, dashboard_port()
Modify: src/jarvis/index_cli.py                + forget_repo() extraction; + dashboard subcommand
Modify: pyproject.toml                         + [tool.setuptools.package-data]
Modify: scripts/check_wheel_contents.py        + expected dashboard assets (presence check)
Modify: README.md                              Dashboard section, CLI list
Modify: CHANGELOG.md                           0.10.0 entry
Modify: AGENTS.md                              command list + module map line
```

`dashboard.py` owns ALL serving; `server.py` is imported, not modified; `index_cli.py` gains only the extraction + subparser. Tests mirror the `tests/test_<module>.py` convention.

---

### Task 1: `config.dashboard_port()` — env-resolved port

**Files:**
- Modify: `src/jarvis/config.py` (after `get_connection`, ~line 117)
- Test: `tests/test_dashboard.py` (create)

**Interfaces:**
- Produces: `config.dashboard_port() -> int`; `config.DASHBOARD_DEFAULT_PORT: int` (= 6080). Used by Task 8's CLI default and Task 3's `serve()`.

- [ ] **Step 1: Write the failing test** — create `tests/test_dashboard.py`:

```python
"""Tests for jarvis.dashboard and config.dashboard_port (mirrors convention)."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis import config


def test_dashboard_port_default(monkeypatch):
    monkeypatch.delenv("JARVIS_DASHBOARD_PORT", raising=False)
    assert config.dashboard_port() == 6080


def test_dashboard_port_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_DASHBOARD_PORT", "9911")
    assert config.dashboard_port() == 9911


def test_dashboard_port_invalid_degrades(monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DASHBOARD_PORT", "not-a-port")
    assert config.dashboard_port() == 6080
    assert "JARVIS_DASHBOARD_PORT" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: 3 FAIL with `AttributeError: module 'jarvis.config' has no attribute 'dashboard_port'`

- [ ] **Step 3: Implement** — append to `src/jarvis/config.py` (imports `os`, `sys` already present; verify, add if missing):

```python
DASHBOARD_DEFAULT_PORT = 6080


def dashboard_port() -> int:
    """`JARVIS_DASHBOARD_PORT` overrides the localhost dashboard port; an
    invalid value degrades to the default rather than breaking `jarvis
    dashboard` (same contract as zoekt's port override)."""
    raw = os.environ.get("JARVIS_DASHBOARD_PORT", "")
    try:
        return int(raw) if raw else DASHBOARD_DEFAULT_PORT
    except ValueError:
        print(
            f"warning: invalid JARVIS_DASHBOARD_PORT {raw!r}; "
            f"using {DASHBOARD_DEFAULT_PORT}",
            file=sys.stderr,
        )
        return DASHBOARD_DEFAULT_PORT
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/config.py tests/test_dashboard.py
git commit -m "feat(dashboard): add env-resolved dashboard port to config"
```

---

### Task 2: Extract `forget_repo()` from `_cmd_forget`

**Files:**
- Modify: `src/jarvis/index_cli.py:1815-1868` (`_cmd_forget` becomes a wrapper)
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Produces: `index_cli.forget_repo(slug: str) -> tuple[bool, str]` — full forget body (lock, registry row, zoekt unpin, graph teardown, artifact sweeps, job-file clear). `(True, "forgot <slug>")` on success; `(False, reason)` for unknown repo / bad slug / lock held. Called by Task 6's `POST /api/repos/{slug}/forget`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_dashboard.py`:

```python
def test_forget_repo_removes_registration(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import index_cli
    from jarvis.registry import Registry

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("demo", str(tmp_path), "python", "abc123", "indexed")
    registry.close()

    ok, message = index_cli.forget_repo("demo")
    assert ok and message == "forgot demo"

    registry = Registry(config.data_dir() / "registry.db")
    assert registry.get("demo") is None
    registry.close()


def test_forget_repo_unknown_slug_fails_cleanly(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import index_cli

    ok, message = index_cli.forget_repo("nope")
    assert not ok and "no such repo" in message
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dashboard.py -k forget_repo -v`
Expected: 2 FAIL with `AttributeError: ... no attribute 'forget_repo'`

- [ ] **Step 3: Implement** — in `src/jarvis/index_cli.py`, replace `_cmd_forget` (lines 1815-1868) with:

```python
def forget_repo(slug: str) -> tuple[bool, str]:
    """The full `jarvis forget` body, shared with the dashboard's
    POST /api/repos/{slug}/forget. Returns (ok, message); callers decide
    how to surface it (CLI prints, dashboard JSONs)."""
    try:
        slug = config.repo_slug(slug)
    except ValueError as exc:
        return False, str(exc)
    try:
        with jobs.build_lock(slug):
            registry = Registry(config.data_dir() / "registry.db")
            try:
                entry = registry.get(slug)
                existed = registry.forget(slug)
            finally:
                registry.close()
            if not existed:
                return False, f"no such repo: {slug}"
            if entry is not None:
                _unpin_zoekt_repo_name(Path(entry.path))
            # Package-edge teardown (spec TSI-08): every package this repo
            # owned and every edge touching it dies with the registration.
            graph_store = GraphStore(config.data_dir() / "registry.db")
            try:
                graph_store.forget_repo(slug)
            finally:
                graph_store.close()
            index_dir = config.index_dir(slug)
            if index_dir.exists():
                shutil.rmtree(index_dir)
            _remove_zoekt_shards(slug)
            shutil.rmtree(config.lancedb_dir() / f"{slug}.lance", ignore_errors=True)
            # D-06: the scip-swift cache legitimately may not exist.
            shutil.rmtree(config.swift_cache_dir(slug), ignore_errors=True)
            # Launch record + index log are jarvis state too (D-06); the
            # lock FILE itself is preserved (see jobs.clear_job_files).
            jobs.clear_job_files(slug)
    except jobs.BuildLockHeld as exc:
        # Destroying a repo's row/graph/artifacts mid-run corrupts the run.
        return False, str(exc)
    return True, f"forgot {slug}"


def _cmd_forget(args: argparse.Namespace) -> int:
    ok, message = forget_repo(args.slug)
    if not ok:
        print(f"error: {message}", file=sys.stderr)
        return 1
    print(message)
    return 0
```

(Preserve the original comment blocks verbatim — they carry D-xx/TSI-xx decision IDs; the version above retains them.)

- [ ] **Step 4: Run the forget tests + the existing CLI forget tests**

Run: `uv run pytest tests/test_dashboard.py -k forget_repo -v && uv run pytest -m "not integration" -k forget -v`
Expected: all PASS (existing `tests/test_index_cli.py` forget behavior unchanged — same prints, same exit codes)

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_dashboard.py
git commit -m "refactor(index): extract forget_repo shared by CLI and dashboard"
```

---

### Task 3: Dashboard skeleton — server, host guard, JSON envelope, static assets

**Files:**
- Create: `src/jarvis/dashboard.py`
- Create: `src/jarvis/dashboard_assets/index.html`, `app.js`, `style.css` (placeholder-free minimal shells here; Task 9 fills the full views)
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Produces:
  - `DashboardError(status: int, message: str)` — raised by handlers
  - `DashboardApi.dispatch(method: str, path: str, query: dict[str, list[str]], body: dict) -> tuple[int, dict]` — route table; 404 unknown path, 405 wrong method
  - `make_handler(api: DashboardApi) -> type[BaseHTTPRequestHandler]`
  - `serve(port: int | None = None, *, open_browser: bool = True) -> None` — blocks; clean Ctrl-C
  - `assets_bytes(name: str) -> bytes` — `importlib.resources` loader for `dashboard_assets/<name>`
- Consumes: `config.dashboard_port()` (Task 1).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_dashboard.py`:

```python
# ---- test server harness -------------------------------------------------

import http.server
import json

from jarvis import dashboard


class _Server:
    """Ephemeral-port dashboard server on a private DashboardApi."""

    def __init__(self, api: dashboard.DashboardApi | None = None):
        self.api = api or dashboard.DashboardApi()
        handler = dashboard.make_handler(self.api)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def get(self, path: str, headers: dict | None = None):
        req = urllib.request.Request(self.url + path, headers=headers or {})
        return self._run(req)

    def post(self, path: str, body: dict, headers: dict | None = None):
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            self.url + path, data=payload, method="POST",
            headers={"Content-Type": "application/json", **(headers or {})})
        return self._run(req)

    def _run(self, req):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")


def test_host_header_guard_rejects_foreign_host(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.get("/api/overview", headers={"Host": "evil.com"})
        assert status == 403
        assert "error" in body


def test_host_header_guard_accepts_localhost(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, _ = srv.get("/api/overview", headers={"Host": "localhost"})
        assert status == 200


def test_unknown_api_path_is_404_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.get("/api/nope")
        assert status == 404 and body == {"error": "not found: /api/nope"}


def test_wrong_method_is_405(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.post("/api/overview", {})
        assert status == 405 and "error" in body


def test_index_served_with_html_content_type(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        with urllib.request.urlopen(srv.url + "/", timeout=10) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
            assert b"<html" in resp.read()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: FAILs — `ModuleNotFoundError: No module named 'jarvis.dashboard'`

- [ ] **Step 3: Implement** `src/jarvis/dashboard.py`:

```python
"""Localhost operator dashboard: a stdlib HTTP console over the registry,
jobs, and query seams, plus operator actions that spawn the same detached
index children as the MCP `indexRepo` tool.

Deliberately NOT: remote-accessible (binds 127.0.0.1 exclusively),
authenticated (single-tenant, same contract as the MCP server), or
framework-based (stdlib http.server; the frontend is framework-free).
The HTTP boundary mirrors server.py's never-raise contract: every broad
catch returns {"error": ...} JSON and logs to stderr.
"""

from __future__ import annotations

import importlib.resources
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from jarvis import config

_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}
_ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class DashboardError(Exception):
    """Handler-level failure carrying its HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def assets_bytes(name: str) -> bytes:
    root = importlib.resources.files("jarvis").joinpath("dashboard_assets")
    return root.joinpath(name).read_bytes()


class DashboardApi:
    """Route table and handlers. dispatch() never raises: it maps
    DashboardError and unexpected exceptions onto (status, {"error": ...})."""

    def dispatch(self, method: str, path: str,
                 query: dict[str, list[str]], body: dict[str, Any],
                 ) -> tuple[int, dict[str, Any]]:
        try:
            handler, methods = self._route(path)
            if method not in methods:
                raise DashboardError(405, f"method {method} not allowed")
            return handler(query, body)
        except DashboardError as exc:
            return exc.status, {"error": exc.message}
        except Exception as exc:  # Broad on purpose: the HTTP boundary never raises.
            print(f"dashboard: {method} {path}: {exc!r}", file=sys.stderr)
            return 500, {"error": str(exc)}

    def _route(self, path: str) -> tuple[Any, frozenset[str]]:
        from jarvis.dashboard_routes import ROUTES  # populated by Tasks 4-7

        routes = ROUTES  # dict[str, (methods, callable)]
        if path in routes:
            methods, fn = routes[path]
            return fn, methods
        # /api/repos/{slug}/... dynamic segments resolve in Task 4-6.
        resolved = self._resolve_dynamic(path)
        if resolved is not None:
            return resolved
        raise DashboardError(404, f"not found: {path}")

    def _resolve_dynamic(self, path: str):
        raise DashboardError(404, f"not found: {path}")

    # handlers appended by Tasks 4-7


def make_handler(api: DashboardApi) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet; errors still stderr
            pass

        # -- guard ---------------------------------------------------------
        def _guard(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in _ALLOWED_HOSTNAMES:
                self._send(403, {"error": f"forbidden Host {host!r}"})
                return False
            if self.command == "POST":
                origin = self.headers.get("Origin")
                if origin is not None:
                    origin_host = urlparse(origin).hostname
                    if origin_host not in _ALLOWED_HOSTNAMES:
                        self._send(403, {"error": f"forbidden Origin {origin!r}"})
                        return False
            return True

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            blob = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _serve_static(self, name: str) -> None:
            try:
                blob = assets_bytes(name)
            except FileNotFoundError:
                self._send(404, {"error": f"no asset {name}"})
                return
            ctype = _ASSET_TYPES.get("." + name.rsplit(".", 1)[-1], "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _dispatch(self, body: dict[str, Any]) -> None:
            if not self._guard():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/" or parsed.path == "/index.html":
                self._serve_static("index.html")
                return
            if not parsed.path.startswith("/api/"):
                name = parsed.path.lstrip("/")
                if name in {"app.js", "style.css"}:
                    self._serve_static(name)
                    return
                self._send(404, {"error": f"not found: {parsed.path}"})
                return
            query = parse_qs(parsed.query)
            status, payload = api.dispatch(self.command, unquote(parsed.path), query, body)
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._dispatch({})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "request body is not valid JSON"})
                return
            self._dispatch(body)

    return DashboardHandler


def serve(port: int | None = None, *, open_browser: bool = True) -> None:
    """Block serving the dashboard on 127.0.0.1. Ctrl-C shuts down cleanly."""
    resolved = port if port is not None else config.dashboard_port()
    api = DashboardApi()
    handler = make_handler(api)
    httpd = ThreadingHTTPServer(("127.0.0.1", resolved), handler)
    url = f"http://127.0.0.1:{resolved}"
    print(f"jarvis dashboard listening on {url} (Ctrl-C to stop)", flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
```

Note: `_route` references `dashboard_routes` which Tasks 4-7 will NOT create — instead, Tasks 4-7 register handler methods on `DashboardApi` and populate a `self._routes` dict in `__init__`. Simplify now: implement `_route` as a lookup against `self._routes: dict[str, tuple[frozenset[str], Any]]` built in `DashboardApi.__init__` (empty in this task) plus `_resolve_dynamic`. The `dashboard_routes` import above is replaced by:

```python
class DashboardApi:
    def __init__(self) -> None:
        # Populated by Tasks 4-7 via self._add(path, methods, handler).
        self._routes: dict[str, tuple[frozenset[str], Any]] = {}

    def _add(self, path: str, methods: frozenset[str], handler: Any) -> None:
        self._routes[path] = (handler, methods)
```

And asset shells — `index.html` (real content replaced by Task 9, but functional now):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>jarvis console</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header id="topbar"><span class="wordmark">jarvis <span class="accent">▮</span> console</span></header>
<main id="view"><p class="body-mid">loading…</p></main>
<script src="/app.js"></script>
</body>
</html>
```

`app.js`:

```js
"use strict";
async function getJSON(path) {
  const res = await fetch(path);
  return res.json();
}
async function refreshOverview() {
  try {
    const o = await getJSON("/api/overview");
    document.getElementById("view").textContent =
      `jarvis ${o.version || ""} — ${o.dataDir || ""}`;
  } catch (e) {
    document.getElementById("view").textContent = `error: ${e}`;
  }
}
refreshOverview();
```

`style.css` (tokens from UI brief §9 — these stay untouched through Task 9):

```css
:root {
  --canvas: #0a0a0a; --canvas-card: #191919; --canvas-soft: #1a1c20;
  --canvas-mid: #363a3f; --hairline: #212327;
  --ink: #ffffff; --body: #dadbdf; --body-mid: #82878d; --ink-hover: #fafaf7;
  --accent-sunset: #ff7a17; --accent-sunset-soft: #ffc285; --accent-breeze: #a0c3ec;
  --accent-dusk: #7c3aed; --accent-twilight: #c4b5fd;
  --status-ok: #ffffff; --status-partial: #ffc285; --status-degraded: #ff7a17;
  --status-failed: #ff5c5c; --status-live: #ff7a17; --stale: #ff5c5c;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--canvas); color: var(--body);
  font: 400 16px/24px system-ui, -apple-system, sans-serif; }
#topbar { height: 56px; border-bottom: 1px solid var(--hairline);
  display: flex; align-items: center; padding: 0 24px; }
.wordmark { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; letter-spacing: 1.2px; text-transform: uppercase; color: var(--ink); }
.accent { color: var(--accent-sunset); }
#view { max-width: 1440px; margin: 0 auto; padding: 24px 32px; }
.body-mid { color: var(--body-mid); }
```

- [ ] **Step 4: Run to verify the new tests pass** — `DashboardApi.__init__` registers exactly one route in this task: a minimal `/api/overview` (Task 4 upgrades it with zoekt state, repo count, and disk totals). Add to `DashboardApi`:

```python
    def _overview(self, query, body):
        return 200, {"version": _version(), "dataDir": str(config.data_dir())}

    def _version(self) -> str:
        try:
            from importlib.metadata import version
            return version("jarvis-mcp")
        except Exception:
            return "dev"
```

and in `__init__`: `self._add("/api/overview", frozenset({"GET"}), self._overview)`.

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: all PASS (8 new)

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/dashboard.py src/jarvis/dashboard_assets tests/test_dashboard.py
git commit -m "feat(dashboard): stdlib HTTP shell with localhost guard and asset serving"
```

---

### Task 4: Read APIs — overview, repos table, repo detail, graph

**Files:**
- Modify: `src/jarvis/dashboard.py` (extend `DashboardApi`)
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: `Registry(db_path).list() -> list[RegisteredRepo]` (fields: slug, path, language, commit_sha, last_indexed, status, scheme_override, semantic_indexed_at, semantic_include, language_override, tracked_files, status_origin, status_reason, status_stderr, scip_enabled, scip_state, scip_failure_reason, scip_failure_stderr, scip_failed_at_sha, semantic_declined); `registry.recovery_for(entry) -> str | None`; `server._service().get_index_status(repo, repo_path) -> (bool, FreshnessSnapshot)` (fields: commit, generated_at, stale, freshness, checked_at, generation); `server._indexing_fields(repo, indexed) -> dict`; `GraphStore(db_path).list_packages(repo=None) -> list[Package(id, repo, name)]`, `.get_dependents(package_id) -> list[Package]`; `config.index_dir(slug)`, `config.lancedb_dir()`, `config.data_dir()`.
- Produces (JSON shapes the frontend in Task 9 renders):
  - `GET /api/overview` → `{version, dataDir, zoektBase (str|null), zoektRunning (bool), repos: int, diskBytes: int}`
  - `GET /api/repos` → `{repos: [{slug, path, language, status, scipState, scipEnabled, semanticIndexedAt, semanticDeclined, lastIndexed, freshness: {commit, freshness, stale, generation}|null, indexing: {...}|null, recovery: str|null, storageBytes: {scip, zoekt, lance, total}}]}`
  - `GET /api/repos/{slug}` → repo row + `{snapshots: [{name, bytes, mtime, current}], capabilities: <getIndexStatus payload>, graph: {dependsOn: [str], dependedOnBy: [str]}, logPath}`
  - `GET /api/graph` → `{nodes: [{id, repo, name}], edges: [{from, to}]}` (from = dependent)

- [ ] **Step 1: Write the failing tests** — append:

```python
def _seed_registry(tmp_path: Path, status: str = "indexed") -> None:
    from jarvis.registry import Registry

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("demo", str(tmp_path / "repo"), "python", "abc123", status)
    registry.close()


def test_repos_lists_registry_rows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    with _Server() as srv:
        status, body = srv.get("/api/repos")
        assert status == 200
        row = next(r for r in body["repos"] if r["slug"] == "demo")
        assert row["language"] == "python"
        assert row["status"] == "indexed"
        assert row["storageBytes"]["total"] >= 0
        assert "freshness" in row  # may be null — key always present


def test_repo_detail_404_unknown_slug(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.get("/api/repos/ghost")
        assert status == 404 and "error" in body


def test_repo_detail_includes_snapshots_and_recovery(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path, status="degraded")
    # a published-looking snapshot so `current` resolution is exercised
    index_dir = config.index_dir("demo")
    index_dir.mkdir(parents=True)
    (index_dir / "index-deadbeef-1.db").write_bytes(b"x")
    (index_dir / "current").write_text("index-deadbeef-1.db")
    with _Server() as srv:
        status, body = srv.get("/api/repos/demo")
        assert status == 200
        assert body["snapshots"][0]["name"] == "index-deadbeef-1.db"
        assert body["snapshots"][0]["current"] is True
        assert body["recovery"]  # degraded rows derive a recovery command


def test_graph_returns_nodes_and_edges(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis.graph import GraphStore

    store = GraphStore(config.data_dir() / "registry.db")
    pkg = store.upsert_package(repo="demo", name="pip:demo")
    dep = store.upsert_package(repo="other", name="pip:dep")
    store.add_edge(from_package_id=dep, to_package_id=pkg)
    store.close()
    with _Server() as srv:
        status, body = srv.get("/api/graph")
        assert status == 200
        assert any(n["name"] == "pip:demo" for n in body["nodes"])
        assert {"from": dep, "to": pkg} in body["edges"]
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_dashboard.py -k "repos or graph" -v` → FAIL (routes missing → 404)

- [ ] **Step 3: Implement** — extend `dashboard.py`:

```python
def _dir_bytes(path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def _storage_sizes(slug: str) -> dict[str, int]:
    scip = _dir_bytes(config.index_dir(slug))
    zoekt = sum(
        f.stat().st_size
        for f in (config.data_dir() / ".zoekt").glob(f"{slug}_*")
        if f.is_file()
    ) if (config.data_dir() / ".zoekt").exists() else 0
    lance = _dir_bytes(config.lancedb_dir() / f"{slug}.lance")
    return {"scip": scip, "zoekt": zoekt, "lance": lance, "total": scip + zoekt + lance}
```


`DashboardApi` additions (registered in `__init__` after the Task 3 overview `_add`; `_overview` from Task 3 is upgraded here to include zoekt state, repo count, disk total):

```python
    def _overview(self, query, body):
        server = self._server_module()
        try:
            base = server._zoekt().base_url_if_running()
            zoekt_running = base is not None
        except Exception:
            base, zoekt_running = None, False
        data_dir = config.data_dir()
        disk = _dir_bytes(data_dir / "scip") + _dir_bytes(data_dir / ".zoekt") \
            + _dir_bytes(data_dir / "lancedb")
        registry = self._registry()
        try:
            repos = len(registry.list())
        finally:
            registry.close()
        return 200, {"version": _version(), "dataDir": str(data_dir),
                     "zoektBase": base, "zoektRunning": zoekt_running,
                     "repos": repos, "diskBytes": disk}

    def _registry(self):
        from jarvis.registry import Registry
        return Registry(config.data_dir() / "registry.db")

    def _repo_row(self, entry) -> dict[str, Any]:
        from jarvis.registry import recovery_for
        server = self._server_module()
        freshness: dict[str, Any] | None = None
        indexing: dict[str, Any] | None = None
        indexed = False
        try:
            indexed, snapshot = server._service().get_index_status(entry.slug, entry.path)
            freshness = {"commit": snapshot.commit, "freshness": snapshot.freshness.value,
                         "stale": snapshot.stale, "generation": snapshot.generation}
        except Exception:
            pass  # no published index or metadata — null freshness is honest
        try:
            fields = server._indexing_fields(entry.slug, indexed)
            indexing = fields.get("indexing")
        except Exception:
            pass
        return {
            "slug": entry.slug, "path": entry.path, "language": entry.language,
            "status": entry.status, "scipState": entry.scip_state,
            "scipEnabled": entry.scip_enabled,
            "semanticIndexedAt": entry.semantic_indexed_at.isoformat()
            if entry.semantic_indexed_at else None,
            "semanticDeclined": entry.semantic_declined,
            "lastIndexed": entry.last_indexed.isoformat(),
            "freshness": freshness, "indexing": indexing,
            "recovery": recovery_for(entry),
            "storageBytes": _storage_sizes(entry.slug),
        }

    def _repos(self, query, body):
        registry = self._registry()
        try:
            entries = registry.list()
        finally:
            registry.close()
        return 200, {"repos": [self._repo_row(e) for e in entries]}

    def _repo_entry_or_404(self, slug: str):
        registry = self._registry()
        try:
            entry = registry.get(slug)
        finally:
            registry.close()
        if entry is None:
            raise DashboardError(404, f"no such repo: {slug}")
        return entry

    def _repo_detail(self, slug: str, query, body):
        from jarvis.registry import recovery_for
        entry = self._repo_entry_or_404(slug)
        server = self._server_module()
        row = self._repo_row(entry)
        index_dir = config.index_dir(slug)
        try:
            current = (index_dir / "current").read_text().strip()
        except OSError:
            current = None
        snapshots = [
            {"name": f.name, "bytes": f.stat().st_size,
             "mtime": f.stat().st_mtime, "current": f.name == current}
            for f in sorted(index_dir.glob("index-*.db"), key=lambda p: -p.stat().st_mtime)
        ] if index_dir.exists() else []
        capabilities = server.get_index_status(slug, entry.path)  # full MCP payload
        graph_store = server._graph()
        try:
            own = graph_store.list_packages(repo=slug)
            depends_on = sorted(
                f"{p.repo}:{p.name}"
                for pkg in own
                for p in graph_store.get_dependents(pkg.id)  # placeholder, fixed below
            )
        finally:
            pass  # GraphStore is a lazy singleton in server; do not close it
        ...
```

Stop — the graph direction needs care. `get_dependents(package_id)` returns packages that **depend on** `package_id`. For "dependsOn" (what `slug` imports) and "dependedOnBy" (who imports `slug`'s packages), the edge table must be read directly. Add to `dashboard.py` a single SQL read (registry.db is a documented two-module store; `GraphStore` lacks a list-edges API, and adding one is out of scope):

```python
def _graph_edges() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(nodes, edges) from registry.db's packages/edges tables (read-only).

    GraphStore exposes no edge listing; this mirrors its documented schema
    (graph.py _SCHEMA: packages(id, repo, name), edges(from_id → to_id)
    where from depends on to)."""
    import sqlite3

    conn = sqlite3.connect(f"file:{config.data_dir() / 'registry.db'}?mode=ro", uri=True)
    try:
        nodes = [{"id": i, "repo": r, "name": n}
                 for i, r, n in conn.execute("SELECT id, repo, name FROM packages")]
        edges = [{"from": f, "to": t}
                 for f, t in conn.execute("SELECT from_id, to_id FROM edges")]
    finally:
        conn.close()
    return nodes, edges
```

Verify the exact column names against `graph.py` `_SCHEMA` before writing (they are `id`, `repo`, `name` / `from_id`, `to_id` — confirmed by `GraphStore.upsert_package`/`add_edge` usage in Task 4's test, which uses those kwargs).

`_repo_detail` graph block becomes:

```python
        nodes, edges = _graph_edges()
        own_ids = {n["id"] for n in nodes if n["repo"] == slug}
        by_id = {n["id"]: f"{n['repo']}:{n['name']}" for n in nodes}
        depends_on = sorted({by_id[e["to"]] for e in edges if e["from"] in own_ids})
        depended_on_by = sorted({by_id[e["from"]] for e in edges if e["to"] in own_ids})
        try:
            log_path = str(config.index_log(slug))
        except Exception:
            log_path = None
        row.update({"snapshots": snapshots, "capabilities": capabilities,
                    "graph": {"dependsOn": depends_on, "dependedOnBy": depended_on_by},
                    "logPath": log_path, "recovery": recovery_for(entry)})
        return 200, row

    def _graph(self_route_query, body):
        nodes, edges = _graph_edges()
        return 200, {"nodes": nodes, "edges": edges}
```

Dynamic resolution in `_resolve_dynamic`:

```python
    def _resolve_dynamic(self, path: str):
        parts = path.strip("/").split("/")  # ["api", "repos", ...]
        if len(parts) >= 3 and parts[:2] == ["api", "repos"]:
            slug = parts[2]
            if len(parts) == 3:
                return (lambda q, b, s=slug: self._repo_detail(s, q, b),
                        frozenset({"GET"}))
            # /api/repos/{slug}/log and /file arrive in Task 5;
            # /reindex and /forget in Task 6.
        return None
```

Register in `__init__`: `self._add("/api/repos", frozenset({"GET"}), self._repos)` and `self._add("/api/graph", frozenset({"GET"}), self._graph)`.

- [ ] **Step 4: Run** — `uv run pytest tests/test_dashboard.py -v` → all PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): overview, repos, detail, and graph read APIs"
```

---

### Task 5: Log tail + confined source viewer

**Files:**
- Modify: `src/jarvis/dashboard.py`
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: `config.index_log(slug)` path; repo's registered `entry.path` as the confinement root.
- Produces:
  - `GET /api/repos/{slug}/log?offset=N` → `{chunk: str, nextOffset: int, size: int}` (offset beyond EOF → empty chunk; missing log → `{chunk: "", nextOffset: 0, size: 0}`)
  - `GET /api/repos/{slug}/file?p=<rel>&start=N&end=M` → `{path, start, end, lines: [str]}` — `p` must be relative, resolved under `entry.path`, no `..`, no absolute; symlink check: `(root/p).resolve()` must stay under `root.resolve()`.

- [ ] **Step 1: Failing tests:**

```python
def test_log_tail_streams_by_offset(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    log = config.index_log("demo")
    log.write_bytes(b"first line\nsecond line\n")
    with _Server() as srv:
        status, body = srv.get("/api/repos/demo/log?offset=0")
        assert status == 200 and body["chunk"].startswith("first line")
        mid = body["nextOffset"]
        status, body2 = srv.get(f"/api/repos/demo/log?offset={mid}")
        assert status == 200 and body2["chunk"].startswith("second line")
        assert body2["nextOffset"] == log.stat().st_size


def test_log_tail_missing_log_is_empty_not_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    with _Server() as srv:
        status, body = srv.get("/api/repos/demo/log?offset=0")
        assert status == 200 and body == {"chunk": "", "nextOffset": 0, "size": 0}


def test_source_viewer_serves_confined_slice(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("one\ntwo\nthree\n")
    _seed_registry(tmp_path)
    with _Server() as srv:
        status, body = srv.get("/api/repos/demo/file?p=mod.py&start=2&end=3")
        assert status == 200
        assert body["lines"] == ["two", "three"]


def test_source_viewer_rejects_traversal(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    with _Server() as srv:
        for bad in ("../escape.py", "/etc/passwd", "a/../../b.py"):
            status, body = srv.get(f"/api/repos/demo/file?p={urllib.parse.quote(bad)}")
            assert status == 400 and "error" in body, bad
```

(add `import urllib.parse` to the test module's imports.)

- [ ] **Step 2: Run** — expect the two new route families to 404.

- [ ] **Step 3: Implement:**

```python
    def _log_tail(self, slug: str, query, body):
        self._repo_entry_or_404(slug)  # 404 before touching the filesystem
        log = config.index_log(slug)
        try:
            offset = int(query.get("offset", ["0"])[0])
        except ValueError:
            raise DashboardError(400, "offset must be an integer")
        if offset < 0:
            offset = 0
        try:
            size = log.stat().st_size
        except OSError:
            return 200, {"chunk": "", "nextOffset": 0, "size": 0}
        with log.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read(64_000).decode(errors="replace")
        return 200, {"chunk": chunk, "nextOffset": min(offset + len(chunk.encode()), size), "size": size}

    def _source_slice(self, slug: str, query, body):
        entry = self._repo_entry_or_404(slug)
        rel = query.get("p", [""])[0]
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise DashboardError(400, f"invalid path {rel!r}")
        root = Path(entry.path).resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise DashboardError(400, f"path escapes repo root: {rel}")
        if not target.is_file():
            raise DashboardError(404, f"no such file: {rel}")
        try:
            start = int(query.get("start", ["1"])[0])
            end = int(query.get("end", [str(start + 200)])[0])
        except ValueError:
            raise DashboardError(400, "start/end must be integers")
        with target.open(errors="replace") as fh:
            lines = fh.readlines()
        selected = [ln.rstrip("\n") for ln in lines[max(start - 1, 0):max(end, 0)]]
        return 200, {"path": rel, "start": start, "end": end, "lines": selected}
```

Extend `_resolve_dynamic` for the 4-segment forms:

```python
            if len(parts) == 4 and parts[3] == "log":
                return (lambda q, b, s=slug: self._log_tail(s, q, b), frozenset({"GET"}))
            if len(parts) == 4 and parts[3] == "file":
                return (lambda q, b, s=slug: self._source_slice(s, q, b), frozenset({"GET"}))
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_dashboard.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat(dashboard): log tail and path-confined source viewer"` (with `src/jarvis/dashboard.py tests/test_dashboard.py`)

---

### Task 6: Actions — index, reindex, forget

**Files:**
- Modify: `src/jarvis/dashboard.py`
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: `server._spawn_index(path, *, semantic: bool, scip: bool | None) -> dict` (payload keys on success: repo/path/status/state/pid/log[, reindex]; `alreadyRunning: True` when locked; `{"error": ...}` on preflight failure); `index_cli.forget_repo(slug) -> (bool, str)` (Task 2).
- Produces:
  - `POST /api/repos/index` body `{path, semantic?}` → 202 `{repo, path, state, pid, log}`; error payload from `_spawn_index` → 400; `alreadyRunning` → 409 with pid.
  - `POST /api/repos/{slug}/reindex` body `{}` → looks up `entry.path`, same mapping.
  - `POST /api/repos/{slug}/forget` body `{"confirm": "<slug>"}` → 200 `{"ok": true, "message": "forgot <slug>"}`; wrong/missing confirm → 400; `forget_repo` lock-held → 409; unknown slug → 404.

- [ ] **Step 1: Failing tests:**

```python
def test_index_action_maps_spawn_payload(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _run_git(repo, "init", "config user.email t@t", "config user.name t", "add a.py", "commit -m x")
    from jarvis import dashboard, server

    def fake_spawn(path, *, semantic, scip):
        return {"repo": "repo", "path": str(repo), "status": "indexing",
                "state": "starting", "pid": 4242, "log": "/tmp/x.log"}

    monkeypatch.setattr(server, "_spawn_index", fake_spawn)
    with _Server() as srv:
        status, body = srv.post("/api/repos/index", {"path": str(repo)})
        assert status == 202 and body["pid"] == 4242


def test_reindex_conflict_maps_to_409(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    from jarvis import dashboard, server

    def fake_spawn(path, *, semantic, scip):
        return {"repo": "demo", "alreadyRunning": True, "state": "running", "pid": 99}

    monkeypatch.setattr(server, "_spawn_index", fake_spawn)
    with _Server() as srv:
        status, body = srv.post("/api/repos/demo/reindex", {})
        assert status == 409 and body["pid"] == 99


def test_forget_requires_typed_confirm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    with _Server() as srv:
        status, body = srv.post("/api/repos/demo/forget", {"confirm": "wrong"})
        assert status == 400 and "confirm" in body["error"]


def test_forget_executes_and_reports(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    _seed_registry(tmp_path)
    from jarvis import index_cli

    calls = []
    monkeypatch.setattr(index_cli, "forget_repo",
                        lambda slug: calls.append(slug) or (True, "forgot demo"))
    with _Server() as srv:
        status, body = srv.post("/api/repos/demo/forget", {"confirm": "demo"})
        assert status == 200 and body == {"ok": True, "message": "forgot demo"}
        assert calls == ["demo"]
```

`_run_git` helper (add once near the top of the test file):

```python
def _run_git(repo: Path, *commands: str) -> None:
    import subprocess

    for command in commands:
        subprocess.run(
            ["git", "-C", str(repo), *command.split()],
            check=True, capture_output=True,
        )
```

- [ ] **Step 2: Run** — 404s expected.

- [ ] **Step 3: Implement:**

```python
    def _spawn_or_error(self, path: str, semantic: bool, scip: bool | None):
        server = self._server_module()
        payload = server._spawn_index(path, semantic=semantic, scip=scip)
        if "error" in payload:
            raise DashboardError(400, payload["error"])
        if payload.get("alreadyRunning"):
            payload.pop("alreadyRunning")
            return 409, payload
        return 202, payload

    def _index_action(self, query, body):
        path = body.get("path")
        if not path or not isinstance(path, str):
            raise DashboardError(400, "body must include a string 'path'")
        semantic = bool(body.get("semantic", False))
        scip = body.get("scip") if body.get("scip") is None else bool(body["scip"])
        return self._spawn_or_error(path, semantic, scip)

    def _reindex_action(self, slug: str, query, body):
        entry = self._repo_entry_or_404(slug)
        return self._spawn_or_error(entry.path, bool(body.get("semantic", False)),
                                    body.get("scip"))

    def _forget_action(self, slug: str, query, body):
        self._repo_entry_or_404(slug)
        if body.get("confirm") != slug:
            raise DashboardError(400, f"confirm must be the exact slug {slug!r}")
        from jarvis import index_cli
        ok, message = index_cli.forget_repo(slug)
        if not ok:
            status = 409 if "lock" in message.lower() else 400
            raise DashboardError(status, message)
        return 200, {"ok": True, "message": message}
```

Register `/api/repos/index` (POST) in `__init__`; extend `_resolve_dynamic`:

```python
            if len(parts) == 4 and parts[3] == "reindex":
                return (lambda q, b, s=slug: self._reindex_action(s, q, b),
                        frozenset({"POST"}))
            if len(parts) == 4 and parts[3] == "forget":
                return (lambda q, b, s=slug: self._forget_action(s, q, b),
                        frozenset({"POST"}))
```

Note: `index_cli.forget_repo` is imported as `from jarvis import index_cli` at call time (deferred — pulls the whole pipeline), which also keeps the Task 6 monkeypatch seam (`index_cli.forget_repo`) intact.

- [ ] **Step 4: Run** — `uv run pytest tests/test_dashboard.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat(dashboard): index, reindex, and typed-confirm forget actions"`

---

### Task 7: Search + tool playground

**Files:**
- Modify: `src/jarvis/dashboard.py`
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: `search_zoekt(base_url, query) -> ZoektSearchResult` (attrs: hits[repo/path/line_number/line_text], total_matches, file_count); `semantic.semantic_search(slug, query, limit, zoekt_base_url=..., scip_conn=...) -> dict`; `symbol_search.search_symbols(conn, query, limit) -> list[SymbolHit(file_path, start_line, end_line, dotted_path, kind)]`; the 10 sync tool functions in `server.py`: `document_symbols(repo, path)`, `go_to_definition(repo, symbol)`, `find_references(repo, symbol)`, `call_hierarchy(repo, symbol)`, `type_hierarchy(repo, symbol)`, `get_index_status(repo, repo_path=None)`, `search_code(query, repo=None)`, `semantic_search_tool(repo, query, limit=10)`, `blast_radius_tool(repo, symbol_or_package)`, `index_repo_tool(path, semantic=False, scip=None)`.
- Produces:
  - `GET /api/search?q=&repo=` → `{query, repo, elapsedMs, lexical: {hits, total, truncated}|null, semantic: {...}|null, symbols: [...]|null, lexicalError?, semanticError?, symbolsError?}`. Without `repo`: lexical only (semantic/symbol signals are per-repo) + `"scoped": false`.
  - `GET /api/tools` → `{tools: [{name, description, params: [{name, type, required, default}]}]}` (10 entries; description = first docstring line).
  - `POST /api/tools/{name}/invoke` body = tool kwargs → `{result: <tool payload>, elapsedMs}`; unknown tool → 404; missing required param → 400.

- [ ] **Step 1: Failing tests:**

```python
def test_tool_catalog_lists_ten_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.get("/api/tools")
        assert status == 200
        names = {t["name"] for t in body["tools"]}
        assert names == {"documentSymbols", "goToDefinition", "findReferences",
                         "callHierarchy", "typeHierarchy", "getIndexStatus",
                         "searchCode", "semanticSearch", "blastRadius", "indexRepo"}


def test_tool_invoke_calls_server_function(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import server

    def fake_get_status(repo, repo_path=None):
        return {"repo": repo, "indexed": False}

    monkeypatch.setattr(server, "get_index_status", fake_get_status)
    with _Server() as srv:
        status, body = srv.post("/api/tools/getIndexStatus/invoke", {"repo": "demo"})
        assert status == 200
        assert body["result"] == {"repo": "demo", "indexed": False}


def test_tool_invoke_unknown_tool_404(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.post("/api/tools/nope/invoke", {})
        assert status == 404


def test_tool_invoke_missing_required_param_400(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.post("/api/tools/findReferences/invoke", {})
        assert status == 400 and "repo" in body["error"]


def test_search_degrades_each_signal_independently(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import dashboard, server
    from jarvis.search import ZoektHit, ZoektSearchResult

    class FakeLifecycle:
        def ensure_running(self):
            return "http://zoekt"

    monkeypatch.setattr(server, "_zoekt_lifecycle", FakeLifecycle())

    import jarvis.search as search_mod
    monkeypatch.setattr(search_mod, "search_zoekt",
                        lambda base, q: ZoektSearchResult(
                            hits=[ZoektHit(repo="demo", path="a.py",
                                           line_number=3, line_text="hit line")],
                            total_matches=1, file_count=1))
    with _Server() as srv:
        status, body = srv.get("/api/search?q=hit&repo=demo")
        assert status == 200
        assert body["lexical"]["hits"][0]["path"] == "a.py"
        assert body.get("semanticError") or body["semantic"] is None  # degraded, not crashed
        assert "elapsedMs" in body
```

(Dataclass names verified against `src/jarvis/search.py:28,47`: `ZoektHit(repo, path, line_number, line_text)` and `ZoektSearchResult(hits, total_matches, file_count)` — the exact types `search_zoekt` returns.)

- [ ] **Step 2: Run** — expect failures.

- [ ] **Step 3: Implement:**

```python
_TOOL_NAMES = {
    "documentSymbols": "document_symbols",
    "goToDefinition": "go_to_definition",
    "findReferences": "find_references",
    "callHierarchy": "call_hierarchy",
    "typeHierarchy": "type_hierarchy",
    "getIndexStatus": "get_index_status",
    "searchCode": "search_code",
    "semanticSearch": "semantic_search_tool",
    "blastRadius": "blast_radius_tool",
    "indexRepo": "index_repo_tool",
}
```

```python
    def _tool_catalog(self, query, body):
        import inspect
        server = self._server_module()
        tools = []
        for mcp_name, attr in _TOOL_NAMES.items():
            fn = getattr(server, attr)
            doc = (fn.__doc__ or "").strip().splitlines()[0]
            params = []
            signature = inspect.signature(fn)
            for pname, param in signature.parameters.items():
                annotation = param.annotation
                type_name = (getattr(annotation, "__name__", None)
                             or str(annotation).replace("typing.", "")
                             or "str")
                if pname in ("repo_path", "repo", "path", "symbol", "query",
                             "symbol_or_package"):
                    type_name = "str"
                params.append({"name": pname, "type": type_name,
                               "required": param.default is inspect.Parameter.empty,
                               "default": None if param.default is inspect.Parameter.empty
                               else param.default})
            tools.append({"name": mcp_name, "description": doc, "params": params})
        return 200, {"tools": tools}

    def _tool_invoke(self, name: str, query, body):
        import inspect
        server = self._server_module()
        attr = _TOOL_NAMES.get(name)
        if attr is None:
            raise DashboardError(404, f"no such tool: {name}")
        fn = getattr(server, attr)
        signature = inspect.signature(fn)
        kwargs = dict(body)
        for pname, param in signature.parameters.items():
            if param.default is inspect.Parameter.empty and pname not in kwargs:
                raise DashboardError(400, f"missing required parameter {pname!r}")
        kwargs = {k: v for k, v in kwargs.items() if k in signature.parameters}
        import time
        t0 = time.perf_counter()
        result = fn(**kwargs)  # same code path the MCP client drives
        return 200, {"result": result,
                     "elapsedMs": round((time.perf_counter() - t0) * 1000, 1)}

    def _search(self, query, body):
        import time
        q = (query.get("q", [""])[0] or "").strip()
        if not q:
            raise DashboardError(400, "query parameter 'q' is required")
        repo = query.get("repo", [None])[0]
        server = self._server_module()
        out: dict[str, Any] = {"query": q, "repo": repo, "scoped": repo is not None,
                               "lexical": None, "semantic": None, "symbols": None}
        t0 = time.perf_counter()
        try:
            from jarvis.search import search_zoekt
            base = server._zoekt().ensure_running()
            result = search_zoekt(base, f"r:{repo} {q}" if repo else q)
            out["lexical"] = {
                "hits": [{"repo": h.repo, "path": h.path, "lineNumber": h.line_number,
                          "lineText": h.line_text} for h in result.hits],
                "total": result.total_matches,
                "truncated": result.total_matches > len(result.hits),
            }
        except Exception as exc:
            out["lexicalError"] = str(exc)
        if repo is not None:
            try:
                from jarvis import semantic
                out["semantic"] = semantic.semantic_search(
                    repo, q, 10,
                    zoekt_base_url=server._zoekt_base_url_or_none(),
                    scip_conn=server._scip_conn_or_none(repo))
            except Exception as exc:
                out["semanticError"] = str(exc)
            try:
                from jarvis.symbol_search import search_symbols
                conn = server._scip_conn_or_none(repo)
                if conn is not None:
                    out["symbols"] = [
                        {"path": h.file_path, "startLine": h.start_line,
                         "endLine": h.end_line, "name": h.dotted_path, "kind": h.kind}
                        for h in search_symbols(conn, q)
                    ]
            except Exception as exc:
                out["symbolsError"] = str(exc)
        out["elapsedMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return 200, out
```

Register `GET /api/search`, `GET /api/tools`; extend `_resolve_dynamic` for `/api/tools/{name}/invoke` (POST). Reuse `_server_module()` so the monkeypatch seams (`server._zoekt_lifecycle`, `server.get_index_status`, …) stay live.

- [ ] **Step 4: Run** — `uv run pytest tests/test_dashboard.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -m "feat(dashboard): tri-modal search and MCP tool playground endpoints"`

---

### Task 8: CLI subcommand `jarvis dashboard`

**Files:**
- Modify: `src/jarvis/index_cli.py` (parser ~line 2070 after `forget_parser`; `_cmd_dashboard` near `_cmd_watch`)
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: `dashboard.serve(port, *, open_browser)` (Task 3).
- Produces: `jarvis dashboard [--port N] [--no-open]`; parser entry `build_parser()` includes subparser `dashboard` with `func=_cmd_dashboard`.

- [ ] **Step 1: Failing test:**

```python
def test_cli_wires_dashboard_subcommand():
    from jarvis import index_cli

    parser = index_cli.build_parser()
    args = parser.parse_args(["dashboard", "--port", "1234", "--no-open"])
    assert args.func is index_cli._cmd_dashboard
    assert args.port == 1234 and args.no_open is True


def test_cmd_dashboard_delegates_to_serve(monkeypatch):
    from jarvis import dashboard, index_cli

    calls = {}
    monkeypatch.setattr(dashboard, "serve",
                        lambda port, open_browser: calls.update(port=port, open=open_browser))
    rc = index_cli._cmd_dashboard(
        index_cli.build_parser().parse_args(["dashboard", "--no-open"]))
    assert rc == 0
    assert calls == {"port": None, "open": False}
```

- [ ] **Step 2: Run** — parser-level AttributeError expected.

- [ ] **Step 3: Implement** — in `build_parser()` after the `forget` subparser:

```python
    dashboard_parser = subparsers.add_parser(
        "dashboard", help="serve the localhost operator dashboard")
    dashboard_parser.add_argument("--port", type=int, default=None,
                                  help="port (default: JARVIS_DASHBOARD_PORT or 6080)")
    dashboard_parser.add_argument("--no-open", action="store_true",
                                  help="do not open the browser automatically")
    dashboard_parser.set_defaults(func=_cmd_dashboard)
```

and the command (imports deferred so the CLI stays fast):

```python
def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the localhost dashboard (blocks until Ctrl-C)."""
    from jarvis import dashboard

    try:
        dashboard.serve(args.port, open_browser=not args.no_open)
    except OSError as exc:  # e.g. port already in use
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
```

`index_cli` must import `dashboard` lazily (as shown) — never at module top — so `jarvis list` doesn't pay for it.

- [ ] **Step 4: Manual smoke + tests**

Run: `uv run pytest tests/test_dashboard.py -k cli -v` → PASS
Run: `uv run jarvis dashboard --port 6199 --no-open & sleep 1; curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:6199/ ; kill %1` → expect `200`.

- [ ] **Step 5: Commit** — `git commit -m "feat(dashboard): jarvis dashboard CLI subcommand"`

---

### Task 9: Frontend — four views (xAI token sheet)

**Files:**
- Modify: `src/jarvis/dashboard_assets/index.html`, `app.js`, `style.css` (replace Task 3 shells)
- Test: `tests/test_dashboard.py` (append asset sanity)

**Interfaces:**
- Consumes: every API from Tasks 4-7; CSS custom properties from UI brief §9 (already in `style.css` Task 3; extend, don't rename).
- Produces: hash-routed SPA — `#/repos` (default), `#/repo/<slug>`, `#/search`, `#/playground` — implementing the UI brief §4 wireframes: status chips with dot+label, live log pane with 2s polling while any row `indexing` (else 10s), index-new-repo inline form, typed-confirm forget modal, tri-modal search columns with click-through source viewer overlay, playground tool rail + param form + JSON well, repo detail cards (snapshots/capabilities/recovery/graph/storage/log).

- [ ] **Step 1: Failing sanity test** — append:

```python
def test_assets_are_wired_and_served(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    html = dashboard.assets_bytes("index.html").decode()
    assert 'src="/app.js"' in html and 'href="/style.css"' in html
    js = dashboard.assets_bytes("app.js").decode()
    for route in ("#/repos", "#/search", "#/playground"):
        assert route in js
    css = dashboard.assets_bytes("style.css").decode()
    for token in ("--canvas:", "--accent-sunset:", "--status-failed:"):
        assert token in css
    with _Server() as srv:
        for name, ctype in (("app.js", "text/javascript"), ("style.css", "text/css")):
            with urllib.request.urlopen(f"{srv.url}/{name}", timeout=10) as resp:
                assert resp.status == 200
                assert ctype in resp.headers["Content-Type"]
```

- [ ] **Step 2: Run** — FAIL (shells lack routes).

- [ ] **Step 3: Implement** the three assets. `index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>jarvis console</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header id="topbar">
  <span class="wordmark">jarvis <span class="accent">▮</span> console</span>
  <span id="top-right" class="top-meta"></span>
</header>
<nav id="tabs">
  <a href="#/repos" data-tab="repos">Repos</a>
  <a href="#/repo" data-tab="repo" hidden>Repo detail</a>
  <a href="#/search" data-tab="search">Search</a>
  <a href="#/playground" data-tab="playground">Playground</a>
</nav>
<div id="status-strip" class="mono-md"></div>
<main id="view"></main>
<div id="overlay" hidden></div>
<script src="/app.js"></script>
</body>
</html>
```

`app.js` — full implementation (~230 lines). Structure it as: `state` (overview/repos cache, poll timer), `api(path, opts)` fetch wrapper, `route()` on hashchange dispatching to `viewRepos()/viewRepo(slug)/viewSearch()/viewPlayground()`, render helpers (`el(tag, attrs, children)`), polling loop (`anyIndexing ? 2000 : 10000`, restarting on transitions, log-pane fetch via `/log?offset=`), actions (`index form submit`, `reindex` → 409 banner on conflict, `forget` modal with typed-slug gate), search (`GET /api/search`, three columns, hit → source viewer via `/file?p=&start=&end=` in `#overlay`), playground (`GET /api/tools`, left rail, form from `params`, `POST invoke`, JSON well with `<details>` folding via `renderJson`). Key behavioral details the implementation MUST honor (from UI brief §6): no layout shift (fixed `min-width` on size/time cells), toasts bottom-right auto-dismiss 4s, `prefers-reduced-motion` disables pulse (CSS), conflict = inline banner not dialog, forget success removes row with 150ms fade.

Write it as real, complete vanilla JS — no framework, no eval, no innerHTML with unsanitized API strings (build DOM via `el()`; escape text nodes by construction). The `statusChip(status)` helper maps: `indexing → dot.pulse + LABEL`, `indexed → status-ok`, `partial → status-partial`, `degraded → status-degraded`, `failed → status-failed`.

`style.css` — extend Task 3 tokens with component rules (~130 lines): `#tabs` (44px, active = 2px `--accent-sunset` underline), `#status-strip` (36px `--canvas-soft`), `.repos-table` (48px rows, `--hairline` dividers, header `--canvas-soft` + mono-eyebrow), `.chip-dot` (6px, `.pulse` animation `@media (prefers-reduced-motion: reduce) { .pulse { animation: none } }`), `.pill` variants (primary white fill `#0a0a0a` text / outline `rgba(255,255,255,.25)` / destructive `--status-failed` border), `.well` (`--canvas-mid`, 8px radius, mono, no wrap, scroll), `.banner` (2px semantic left rule), `.modal` + `#overlay` (scrim `rgba(0,0,0,.6)`), `.toast`, search 3-column grid `1fr 1fr 0.6fr`, source viewer gutter (48px, `--body-mid`, highlight band `--accent-sunset` at 12% alpha + 2px left rule), playground rail (240px). All values verbatim from UI brief §2/§5.

- [ ] **Step 4: Run tests + manual browser verification**

Run: `uv run pytest tests/test_dashboard.py -v` → PASS
Run: `uv run jarvis dashboard --no-open & sleep 1; open http://127.0.0.1:6080/` — verify in browser: four tabs render against the real `~/.jarvis` (repos table, detail, search, playground), then kill. This is the success-criteria check from the spec §"Success criteria" items 1, 4, 5.

- [ ] **Step 5: Commit** — `git commit -m "feat(dashboard): four-view SPA frontend on the xAI token sheet"`

---

### Task 10: Packaging — package-data + wheel-guard expectation

**Files:**
- Modify: `pyproject.toml` (after `[tool.setuptools]` block, ~line 132)
- Modify: `scripts/check_wheel_contents.py`
- Test: `tests/test_check_wheel_contents.py` (append; the file already loads the script via `importlib.util.spec_from_file_location` and defines a `_wheel(tmp_path, names)` synthetic-zip helper — reuse both)

**Interfaces:**
- Produces: wheels ship `jarvis/dashboard_assets/{index.html,app.js,style.css}`; `check_wheel_contents.check` reports missing assets as problems (positive expectation — a package-data regression fails the release guard rather than shipping an asset-less dashboard).

Important factual correction to the spec (verified against the current script): `check_wheel_contents.py` only flags readable *source* suffixes (`.py/.pyx/.pxd/.c`) and missing `ALLOWED_SOURCE` — HTML/JS/CSS pass through untouched today. So the change is a **presence check**, not a carve-out from a failure. Amend the spec's packaging bullet to match (one-line edit, same commit).

- [ ] **Step 1: Failing test** — in `tests/test_check_wheel_contents.py`, following its existing load pattern:

```python
def test_missing_dashboard_asset_is_reported(tmp_path):
    lean = _wheel(tmp_path, ["jarvis/__init__.py", "jarvis/scip_pb2.py",
                             "jarvis/query.cpython-312-darwin.so"])
    problems = check_module.check(str(lean))
    assert any("dashboard assets" in p for p in problems)


def test_dashboard_assets_present_passes(tmp_path):
    full = _wheel(tmp_path, ["jarvis/__init__.py", "jarvis/scip_pb2.py",
                             "jarvis/query.cpython-312-darwin.so",
                             "jarvis/dashboard_assets/index.html",
                             "jarvis/dashboard_assets/app.js",
                             "jarvis/dashboard_assets/style.css"])
    assert check_module.check(str(full)) == []
```

- [ ] **Step 2: Run** — lean case FAILs (no asset expectation yet).

- [ ] **Step 3: Implement** — `pyproject.toml`:

```toml
[tool.setuptools.package-data]
jarvis = ["dashboard_assets/*.html", "dashboard_assets/*.js", "dashboard_assets/*.css"]
```

`scripts/check_wheel_contents.py`:

```python
ALLOWED_SOURCE = {"jarvis/__init__.py", "jarvis/scip_pb2.py"}
EXPECTED_ASSETS = {
    "jarvis/dashboard_assets/index.html",
    "jarvis/dashboard_assets/app.js",
    "jarvis/dashboard_assets/style.css",
}
SOURCE_SUFFIXES = (".py", ".pyx", ".pxd", ".c")
```

and in `check()`, after the `missing` computation:

```python
    missing_assets = sorted(EXPECTED_ASSETS - set(names))
    if missing_assets:
        problems.append(f"{wheel_path}: missing dashboard assets: {missing_assets}")
```

Update the module docstring sentence to mention the asset presence expectation. Spec amendment: change the packaging bullet's parenthetical from "today it would fail the wheel on any non-code file" to "the guard gains a *presence* expectation for the three assets — a package-data regression fails the release check".

- [ ] **Step 4: Run + real wheel verification**

Run: `uv run pytest tests/test_check_wheel_contents.py -v` → PASS
Run: `JARVIS_COMPILE=1 uv run build --wheel -o dist/ 2>/dev/null || uv run python -m build --wheel -o dist/` then `python scripts/check_wheel_contents.py dist/*.whl && zipinfo -1 dist/*.whl | grep dashboard_assets` → 3 asset paths listed, guard prints verified.

- [ ] **Step 5: Commit** — `git commit -m "build: ship dashboard assets in wheels and guard their presence"`

---

### Task 11: Docs — README, docs page, CHANGELOG, AGENTS

**Files:**
- Modify: `README.md`, `docs/dashboard.md` (create), `CHANGELOG.md`, `AGENTS.md`

**Interfaces:** none (docs only). Content requirements:

- [ ] **Step 1: README** — add a "Dashboard" section after "Indexing a repo" with: one-command intro (`jarvis dashboard` → `http://127.0.0.1:6080`), the four views in one sentence each, `JARVIS_DASHBOARD_PORT` + `--port`/`--no-open`, a localhost-only/no-auth security note, and one screenshot reference `docs/assets/dashboard.png` (placeholder-free: either capture a real screenshot during Task 9's manual verification and commit it, or omit the image entirely — do not commit a broken link). Add `jarvis dashboard` to the CLI surface list in the "Indexing a repo" code block. Add `docs/dashboard.md` to the Documentation links list.
- [ ] **Step 2: docs/dashboard.md** — short page: launch, views, actions (including the typed-slug forget confirm), security model (host guard, path confinement), env vars, troubleshooting (port in use, assets missing after wheel install → `pip install --force-reinstall`).
- [ ] **Step 3: CHANGELOG.md** — new `## [0.10.0] - unreleased` section at top: `### Added` — `jarvis dashboard` with a 4-bullet summary (views, actions reuse of indexRepo spawn seam, zero new deps, xAI-styled assets in the wheel). Match the existing entry style (bold lead sentence per bullet).
- [ ] **Step 4: AGENTS.md** — CLI surface list gains `uv run jarvis dashboard [--port N] [--no-open]`; Key Directories gains a `dashboard.py`/`dashboard_assets/` line in the `src/jarvis/` map ("stdlib localhost console; imports server singletons; never spawns the pipeline in-process").
- [ ] **Step 5: Verify docs build claims** — every link/file referenced exists (`ls docs/dashboard.md`, grep README for `mcp-name` marker intact on line 3). Run the full gate: `uv run pytest -m "not integration" -rs` → green.
- [ ] **Step 6: Commit** — `git commit -m "docs: document the dashboard command, views, and release notes"`

---

## Self-Review (completed during planning)

**Spec coverage:** every API line in the spec's HTTP table maps to Tasks 4-7; actions → Task 6; security model (host guard, origin, path confinement) → Tasks 3/5 + tests; packaging section → Task 10 (with the one factual correction noted inline); testing section → per-task tests, ephemeral-port stdlib server, monkeypatched Popen-equivalent (`server._spawn_index` seam); docs/release → Task 11; success criteria 1/4/5 verified in Task 9 Step 4, 2/3 in Task 6 tests, 6 in Tasks 8/10. Port/env/config → Task 1. The `dashboard dies with the command` requirement → Task 3 `serve()` + Task 8 blocking command.

**Placeholder scan:** no TBD/TODO; every code step carries real code. Both facts originally left as "read-then-adjust" notes were resolved during planning and baked in: the Zoekt result dataclasses are `ZoektHit`/`ZoektSearchResult` (`src/jarvis/search.py:28,47`), and `tests/test_check_wheel_contents.py` already provides the `_wheel(tmp_path, names)` synthetic-zip helper (its line 13) which Task 10's tests reuse verbatim.

**Type consistency:** `DashboardApi.dispatch(method, path, query, body) -> (int, dict)` used by Task 3's handler; `_add(path, methods, handler)` registration signature consistent across Tasks 3-7; `forget_repo(slug) -> (bool, str)` identical in Tasks 2/6; `dashboard.serve(port, *, open_browser)` identical in Tasks 3/8; `_TOOL_NAMES` map keys match the `test_tool_catalog` assertion set; `config.dashboard_port()` referenced by both Task 1 tests and Task 8 CLI default.
