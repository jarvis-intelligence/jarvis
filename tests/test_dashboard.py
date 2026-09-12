"""Tests for jarvis.dashboard and config.dashboard_port (mirrors convention)."""

from __future__ import annotations

import socket
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


def test_dashboard_port_out_of_range_degrades(monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DASHBOARD_PORT", "70000")
    assert config.dashboard_port() == 6080
    assert "JARVIS_DASHBOARD_PORT" in capsys.readouterr().err


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


def test_malformed_content_length_is_400_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        req = urllib.request.Request(
            srv.url + "/api/overview", data=b"{}", method="POST",
            headers={"Content-Length": "garbage"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, body = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            status, body = err.code, json.loads(err.read())
        assert status == 400 and "error" in body


def test_malformed_origin_is_rejected_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        status, body = srv.post("/api/overview", {}, headers={"Origin": "http://[::1"})
        assert status == 403 and "error" in body


def test_malformed_request_path_is_400_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with _Server() as srv:
        host, port = srv.httpd.server_address[:2]
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(b"GET http://x[ HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    data = b"".join(chunks)
    assert data.startswith(b"HTTP/1.1 400")
    assert b'"error"' in data
