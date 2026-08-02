"""MCP in-memory client session: list_tools returns the 9 tools, and
roundtrips for documentSymbols, searchCode, semanticSearch, and blastRadius against the
synthetic fixture / a fake zoekt-webserver / an in-memory package graph."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from codeintel import config, query, server
from codeintel.graph import GraphStore
from codeintel.search import ZoektLifecycle
from tests.fixtures.synthetic_index import DOC_GREETER, build_published_index

REPO = "toy-repo"

EXPECTED_TOOLS = {
    "documentSymbols",
    "goToDefinition",
    "findReferences",
    "callHierarchy",
    "typeHierarchy",
    "getIndexStatus",
    "searchCode",
    "semanticSearch",
    "blastRadius",
}


@pytest.fixture(autouse=True)
def _wired_query_service(tmp_path: Path, monkeypatch):
    # Isolates config.data_dir() to tmp_path so _error_payload's registry
    # lookup (added for search-only explanations) never touches the real
    # ~/.codeintel/registry.db when a test's repo raises IndexNotFoundError.
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    build_published_index(tmp_path, config.PROJECT, REPO, config.BRANCH)
    service = query.QueryService(config.new_connection_cache(tmp_path))
    monkeypatch.setattr(server, "_query_service", service)
    yield
    monkeypatch.setattr(server, "_query_service", None)


@pytest.mark.anyio
async def test_list_tools_returns_nine_tools():
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.list_tools()
        names = {tool.name for tool in result.tools}
        assert names == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_document_symbols_roundtrip():
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("documentSymbols", {"repo": REPO, "path": DOC_GREETER})
        assert result.isError is not True
        payload = json.loads(result.content[0].text)
        assert [s["displayName"] for s in payload["symbols"]] == ["Greeter", "greet", "DEFAULT_NAME", "sayHi"]


@pytest.mark.anyio
async def test_go_to_definition_missing_repo_returns_error_payload():
    """A repo that was never registered at all is a plain "index not found"
    -- distinct from the search-only explanation, which only applies when
    the registry records status == SEARCH_ONLY_STATUS for that repo."""
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("goToDefinition", {"repo": "never-published", "symbol": "x"})
        payload = json.loads(result.content[0].text)
        assert "error" in payload
        assert "search-only" not in payload["error"]


@pytest.mark.anyio
async def test_unexpected_exception_still_returns_structured_error_payload(monkeypatch):
    """Every tool must fail the same way — a `{"error": ...}` dict, not an
    MCP-level `isError` text result — regardless of which exception type
    the underlying service raises."""

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(server.QueryService, "get_definitions", _boom)
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("goToDefinition", {"repo": REPO, "symbol": "x"})
        assert result.isError is not True
        payload = json.loads(result.content[0].text)
        assert payload == {"error": "boom"}


_FAKE_ZOEKT_SEARCH_SCRIPT = """\
#!PYTHON_SHEBANG_PLACEHOLDER
import base64
import http.server
import json
import socketserver
import sys

port = int(sys.argv[sys.argv.index("-listen") + 1].lstrip(":"))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        line = base64.b64encode(b"def greet(name):").decode()
        body = json.dumps({
            "Result": {"Files": [{"Repository": "toy-repo", "FileName": "toy/greeter.py",
                                   "LineMatches": [{"LineNumber": 5, "Line": line}]}]}
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

class Server(socketserver.TCPServer):
    # See the matching note in tests/test_search.py: without SO_REUSEADDR a
    # fixed port still in TIME_WAIT from a previous run cannot be rebound, so
    # this process exits with EADDRINUSE and the test reports the server as
    # having "exited immediately".
    allow_reuse_address = True

with Server(("127.0.0.1", port), Handler) as httpd:
    httpd.serve_forever()
"""


@pytest.mark.anyio
async def test_search_code_roundtrip(tmp_path: Path, monkeypatch):
    script_path = tmp_path / "fake-zoekt-webserver"
    # An absolute shebang (vs. `#!/usr/bin/env python3`) avoids PATH-resolution
    # flakiness spawning this fake server as a subprocess — this only affects
    # the test double; the real ZoektLifecycle always spawns the actual
    # zoekt-webserver binary directly, never through a shebang lookup.
    script_path.write_text(
        _FAKE_ZOEKT_SEARCH_SCRIPT.replace("PYTHON_SHEBANG_PLACEHOLDER", sys.executable), encoding="utf-8"
    )
    script_path.chmod(0o755)

    lifecycle = ZoektLifecycle(
        index_dir=tmp_path / "zoekt-index",
        data_dir=tmp_path / "zoekt-data",
        port=16090,
        binary=[sys.executable, str(script_path)],
    )
    monkeypatch.setattr(server, "_zoekt_lifecycle", lifecycle)
    try:
        async with create_connected_server_and_client_session(server.mcp) as client:
            result = await client.call_tool("searchCode", {"query": "greet"})
            payload = json.loads(result.content[0].text)
            assert payload["total"] == 1
            assert payload["hits"][0]["repo"] == "toy-repo"
            assert payload["hits"][0]["lineText"] == "def greet(name):"
    finally:
        lifecycle.stop()
        monkeypatch.setattr(server, "_zoekt_lifecycle", None)


@pytest.mark.anyio
async def test_blast_radius_roundtrip(tmp_path: Path, monkeypatch):
    store = GraphStore(tmp_path / "registry.db")
    seed_id = store.upsert_package(repo=REPO, name="npm:seed")
    dep_id = store.upsert_package(repo="dep-repo", name="npm:dep")
    store.add_edge(from_package_id=dep_id, to_package_id=seed_id)
    monkeypatch.setattr(server, "_graph_store", store)
    try:
        async with create_connected_server_and_client_session(server.mcp) as client:
            result = await client.call_tool("blastRadius", {"repo": REPO, "symbol_or_package": "npm:seed"})
            payload = json.loads(result.content[0].text)
            assert payload["dependents"] == [{"repo": "dep-repo", "name": "npm:dep", "hops": 1}]
            assert payload["freshness"] == "unknown"
            assert payload["commit"] is None
    finally:
        store.close()
        monkeypatch.setattr(server, "_graph_store", None)


@pytest.mark.anyio
async def test_blast_radius_unknown_package_returns_error_payload(tmp_path: Path, monkeypatch):
    store = GraphStore(tmp_path / "registry.db")
    monkeypatch.setattr(server, "_graph_store", store)
    try:
        async with create_connected_server_and_client_session(server.mcp) as client:
            result = await client.call_tool("blastRadius", {"repo": REPO, "symbol_or_package": "npm:no-such"})
            payload = json.loads(result.content[0].text)
            assert "error" in payload
    finally:
        store.close()
        monkeypatch.setattr(server, "_graph_store", None)


@pytest.mark.anyio
async def test_type_hierarchy_reports_unavailable_instead_of_empty(monkeypatch):
    """Empty arrays read as "no supertypes exist" -- a false answer.

    scip expt-convert never populates global_symbols.relationships on a real
    index, so the tool cannot answer and must say so rather than imply one.
    """

    def _unavailable(self, repo, symbol):
        _, metadata = query.get_connection(self._cache, repo)
        return [], [], query._freshness_snapshot(metadata), False

    monkeypatch.setattr(server.QueryService, "type_hierarchy", _unavailable)
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("typeHierarchy", {"repo": REPO, "symbol": "x"})
        payload = json.loads(result.content[0].text)

    assert "error" in payload, payload
    assert "relationships" in payload["error"].lower()
    assert "supertypes" not in payload


@pytest.mark.anyio
async def test_type_hierarchy_returns_results_when_relationships_present():
    """The synthetic fixture DOES carry a contrived non-NULL relationships
    blob for `Greeter#`, so the available path is what this index exercises —
    no monkeypatching needed. Guards against the unavailable branch
    swallowing genuine results.
    """
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("typeHierarchy", {"repo": REPO, "symbol": "Greeter"})
        payload = json.loads(result.content[0].text)

    assert "error" not in payload, payload
    assert "supertypes" in payload
    assert "subtypes" in payload


def test_semantic_search_tool_returns_results(monkeypatch):
    def _fake_search(repo, query, limit, zoekt_base_url=None):
        return {"query": query, "results": [], "total": 0}

    monkeypatch.setattr(server, "_zoekt_base_url_or_none", lambda: "http://x")
    monkeypatch.setattr("codeintel.semantic.semantic_search", _fake_search)
    result = server.semantic_search_tool(repo="r", query="auth logic")
    assert result == {"query": "auth logic", "results": [], "total": 0}


def test_semantic_search_tool_wraps_errors(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("no semantic index for r — run codeintel reindex r")

    monkeypatch.setattr(server, "_zoekt_base_url_or_none", lambda: None)
    monkeypatch.setattr("codeintel.semantic.semantic_search", _boom)
    result = server.semantic_search_tool(repo="r", query="q")
    assert "no semantic index" in result["error"]


def test_error_payload_explains_a_search_only_repo(tmp_path: Path, monkeypatch):
    from codeintel import config, server
    from codeintel.index_reader import IndexNotFoundError
    from codeintel.registry import Registry

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()

    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))
    assert "search-only" in payload["error"]
    assert "searchCode" in payload["error"]


def test_error_payload_passes_through_other_errors(tmp_path: Path, monkeypatch):
    from codeintel import server

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    payload = server._error_payload("absent", RuntimeError("boom"))
    assert payload == {"error": "boom"}


def test_get_index_status_reports_search_only_status():
    """The MCP tool itself (not QueryService.get_index_status directly)
    must surface the registry's status so a caller can distinguish
    "search-only" from "never indexed" -- both report indexed=False."""
    from codeintel.registry import Registry

    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()

    result = server.get_index_status(repo="gorepo")
    assert result["status"] == "search-only"
    assert result["indexed"] is False


def test_get_index_status_reports_none_status_when_never_registered():
    result = server.get_index_status(repo="never-registered-anywhere")
    assert result["status"] is None
    assert result["indexed"] is False
