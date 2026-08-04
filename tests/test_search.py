"""Tests for search.py: real JSON-decoding logic against zoekt-webserver's
response shape (mocked via httpx.MockTransport — mirrors a real instance's
`POST /api/search` verified shape, see search.py's docstring), plus the
lifecycle manager's spawn/health-check/pidfile/stop behavior against a fake
zoekt-webserver script (no real Zoekt binary required for this suite)."""

from __future__ import annotations

import base64
import os
import stat
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from codeintel.search import ZoektHit, ZoektLifecycle, ZoektUnavailableError, search_zoekt, zoekt_repo_documents


def _zoekt_response(request: httpx.Request) -> httpx.Response:
    encoded_line = base64.b64encode(b"def greet(name):").decode()
    return httpx.Response(
        200,
        json={
            "Result": {
                "Files": [
                    {
                        "Repository": "toy-repo",
                        "FileName": "toy/greeter.py",
                        "LineMatches": [{"LineNumber": 5, "Line": encoded_line}],
                    }
                ]
            }
        },
    )


def _error_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="internal error")


def test_search_zoekt_decodes_base64_line_and_returns_hits():
    client = httpx.Client(transport=httpx.MockTransport(_zoekt_response))
    hits = search_zoekt("http://localhost:6070", "greet", client=client)
    assert hits == [ZoektHit(repo="toy-repo", path="toy/greeter.py", line_number=5, line_text="def greet(name):")]


def test_search_zoekt_no_hits_returns_empty_list():
    def empty_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Result": {"Files": []}})

    client = httpx.Client(transport=httpx.MockTransport(empty_response))
    assert search_zoekt("http://localhost:6070", "no-such-query", client=client) == []


def test_search_zoekt_raises_unavailable_on_http_error():
    client = httpx.Client(transport=httpx.MockTransport(_error_response))
    with pytest.raises(ZoektUnavailableError):
        search_zoekt("http://localhost:6070", "greet", client=client)


_FAKE_ZOEKT_SCRIPT = textwrap.dedent(
    """\
    #!PYTHON_SHEBANG_PLACEHOLDER
    import http.server
    import socketserver
    import sys

    port = int(sys.argv[sys.argv.index("-listen") + 1].lstrip(":"))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    class Server(socketserver.TCPServer):
        # socketserver.TCPServer leaves SO_REUSEADDR off; the stdlib's own
        # http.server.HTTPServer turns it on for exactly this reason. Without
        # it, binding a *fixed* port that is still in TIME_WAIT from a previous
        # run fails with EADDRINUSE, this process dies, and the caller sees
        # "exited immediately with code 1". The health check below establishes
        # and closes a connection, so TIME_WAIT is guaranteed once a test has
        # run -- which made the suite fail on any re-run within the ~15-60s
        # window rather than only under load.
        allow_reuse_address = True

    with Server(("127.0.0.1", port), Handler) as httpd:
        httpd.serve_forever()
    """
)


@pytest.fixture
def fake_zoekt_binary(tmp_path: Path) -> Path:
    script_path = tmp_path / "fake-zoekt-webserver"
    # Absolute shebang (vs. `#!/usr/bin/env python3`) avoids PATH-resolution
    # flakiness spawning this test double as a subprocess — the real
    # ZoektLifecycle always spawns the actual zoekt-webserver binary
    # directly, never through a shebang lookup.
    script_path.write_text(_FAKE_ZOEKT_SCRIPT.replace("PYTHON_SHEBANG_PLACEHOLDER", sys.executable), encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return script_path


def test_lifecycle_spawns_and_becomes_healthy(tmp_path: Path, fake_zoekt_binary: Path):
    lifecycle = ZoektLifecycle(
        index_dir=tmp_path / "zoekt-index",
        data_dir=tmp_path / "data",
        port=16070,
        binary=[sys.executable, str(fake_zoekt_binary)],
    )
    try:
        base_url = lifecycle.ensure_running()
        assert base_url == "http://127.0.0.1:16070"
        assert (tmp_path / "data" / "zoekt-webserver.pid").exists()
    finally:
        lifecycle.stop()
    assert not (tmp_path / "data" / "zoekt-webserver.pid").exists()


def test_lifecycle_reuses_already_running_instance(tmp_path: Path, fake_zoekt_binary: Path):
    lifecycle_a = ZoektLifecycle(
        index_dir=tmp_path / "zoekt-index", data_dir=tmp_path / "data", port=16071, binary=[sys.executable, str(fake_zoekt_binary)]
    )
    lifecycle_a.ensure_running()
    pid_a = int((tmp_path / "data" / "zoekt-webserver.pid").read_text())

    lifecycle_b = ZoektLifecycle(
        index_dir=tmp_path / "zoekt-index", data_dir=tmp_path / "data", port=16071, binary=[sys.executable, str(fake_zoekt_binary)]
    )
    try:
        lifecycle_b.ensure_running()
        pid_b = int((tmp_path / "data" / "zoekt-webserver.pid").read_text())
        assert pid_a == pid_b  # no duplicate process spawned
    finally:
        lifecycle_a.stop()


def test_lifecycle_raises_when_binary_exits_immediately(tmp_path: Path):
    bad_binary = tmp_path / "bad-zoekt"
    bad_binary.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    bad_binary.chmod(bad_binary.stat().st_mode | stat.S_IEXEC)

    lifecycle = ZoektLifecycle(
        index_dir=tmp_path / "zoekt-index",
        data_dir=tmp_path / "data",
        port=16072,
        binary=str(bad_binary),
        health_timeout_seconds=2.0,
    )
    with pytest.raises(ZoektUnavailableError):
        lifecycle.ensure_running()


def test_base_url_if_running_returns_none_without_a_pidfile(tmp_path: Path):
    """getIndexStatus must not spawn a webserver just to report coverage."""
    lifecycle = ZoektLifecycle(index_dir=tmp_path / ".zoekt", data_dir=tmp_path)

    assert lifecycle.base_url_if_running() is None


def test_base_url_if_running_returns_none_for_a_dead_pid(tmp_path: Path):
    (tmp_path / "zoekt-webserver.pid").write_text("999999999", encoding="utf-8")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / ".zoekt", data_dir=tmp_path)

    assert lifecycle.base_url_if_running() is None


def test_base_url_if_running_returns_url_when_healthy(tmp_path: Path, monkeypatch):
    (tmp_path / "zoekt-webserver.pid").write_text(str(os.getpid()), encoding="utf-8")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / ".zoekt", data_dir=tmp_path)
    monkeypatch.setattr(lifecycle, "_is_healthy", lambda: True)

    assert lifecycle.base_url_if_running() == lifecycle.base_url()


def test_zoekt_repo_documents_reads_the_list_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/list"
        return httpx.Response(
            200,
            json={"List": {"Repos": [{"Repository": {"Name": "myslug"}, "Stats": {"Documents": 133}}]}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert zoekt_repo_documents("http://localhost:6070", "myslug", client=client) == 133


def test_zoekt_repo_documents_returns_none_when_repo_absent():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"List": {"Repos": []}})))

    assert zoekt_repo_documents("http://localhost:6070", "myslug", client=client) is None


def test_zoekt_repo_documents_raises_on_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(ZoektUnavailableError):
        zoekt_repo_documents("http://localhost:6070", "myslug", client=client)
