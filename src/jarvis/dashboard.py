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


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("jarvis-mcp")
    except Exception:
        return "dev"


class DashboardApi:
    """Route table and handlers. dispatch() never raises: it maps
    DashboardError and unexpected exceptions onto (status, {"error": ...})."""

    def __init__(self) -> None:
        # Static routes, populated via _add by this task and Tasks 4-7:
        # path -> (handler, methods). Dynamic /api/repos/{slug}/... segments
        # resolve through _resolve_dynamic (Tasks 4-6).
        self._routes: dict[str, tuple[Any, frozenset[str]]] = {}
        self._add("/api/overview", frozenset({"GET"}), self._overview)

    def _add(self, path: str, methods: frozenset[str], handler: Any) -> None:
        self._routes[path] = (handler, methods)

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
        if path in self._routes:
            handler, methods = self._routes[path]
            return handler, methods
        resolved = self._resolve_dynamic(path)  # /api/repos/{slug}/... — Tasks 4-6
        if resolved is not None:
            return resolved
        raise DashboardError(404, f"not found: {path}")

    def _resolve_dynamic(self, path: str) -> tuple[Any, frozenset[str]] | None:
        return None  # dynamic segments arrive with Tasks 4-6

    def _overview(self, query, body):
        return 200, {"version": _version(), "dataDir": str(config.data_dir())}


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
