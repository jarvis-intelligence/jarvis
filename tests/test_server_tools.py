"""MCP in-memory client session: list_tools returns the 9 tools, and
roundtrips for documentSymbols, searchCode, semanticSearch, and blastRadius against the
synthetic fixture / a fake zoekt-webserver / an in-memory package graph."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from jarvis import config, query, server
from jarvis.graph import GraphStore
from jarvis.index_reader import IndexNotFoundError
from jarvis.search import ZoektLifecycle
from jarvis.symbols import AmbiguousSymbolError, Candidate, DescriptorKind
from tests.fixtures.synthetic_index import CLASS_SYMBOL, DOC_GREETER, METHOD_SYMBOL, build_published_index

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
    # ~/.jarvis/registry.db when a test's repo raises IndexNotFoundError.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
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

    # goToDefinition resolves the symbol before calling get_definitions, so
    # the raising call must be resolve_symbol to exercise the same
    # generic-exception path this test targets.
    monkeypatch.setattr(server.QueryService, "resolve_symbol", _boom)
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("goToDefinition", {"repo": REPO, "symbol": "x"})
        assert result.isError is not True
        payload = json.loads(result.content[0].text)
        assert payload == {"error": "boom"}


@pytest.mark.anyio
async def test_ambiguous_symbol_error_reaches_client_as_candidates_payload(monkeypatch):
    """Full-stack check that `AmbiguousSymbolError` -- raised during symbol
    resolution -- reaches a real MCP tool call as a structured
    `{"candidates": ..., "candidateTotal": ...}` payload. `resolve()` and
    `_error_payload()` are each covered directly elsewhere in this file /
    test_symbols.py, but neither exercises the actual tool-invocation path,
    which is what a caller of goToDefinition experiences."""
    candidates = (
        Candidate(symbol="sym-a", dotted_path="a.C.dup", kind=DescriptorKind.METHOD),
        Candidate(symbol="sym-b", dotted_path="b.D.dup", kind=DescriptorKind.METHOD),
    )

    def _ambiguous(*args, **kwargs):
        raise AmbiguousSymbolError("dup", candidates, 2)

    # goToDefinition resolves the symbol via resolve_symbol before calling
    # get_definitions -- see server.py's go_to_definition -- so that is the
    # call site to raise from to exercise the real wiring end to end.
    monkeypatch.setattr(server.QueryService, "resolve_symbol", _ambiguous)
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("goToDefinition", {"repo": REPO, "symbol": "dup"})
        assert result.isError is not True
        payload = json.loads(result.content[0].text)

    assert payload["candidateTotal"] == 2
    assert payload["candidates"] == [
        {"symbol": "sym-a", "dottedPath": "a.C.dup", "kind": "METHOD"},
        {"symbol": "sym-b", "dottedPath": "b.D.dup", "kind": "METHOD"},
    ]
    assert "definitions" not in payload


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
    def _fake_search(repo, query, limit, zoekt_base_url=None, scip_conn=None):
        return {"query": query, "results": [], "total": 0}

    monkeypatch.setattr(server, "_zoekt_base_url_or_none", lambda: "http://x")
    monkeypatch.setattr("jarvis.semantic.semantic_search", _fake_search)
    result = server.semantic_search_tool(repo="r", query="auth logic")
    assert result == {"query": "auth logic", "results": [], "total": 0}


def test_semantic_search_tool_wraps_errors(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("no semantic index for r — run jarvis reindex r")

    monkeypatch.setattr(server, "_zoekt_base_url_or_none", lambda: None)
    monkeypatch.setattr("jarvis.semantic.semantic_search", _boom)
    result = server.semantic_search_tool(repo="r", query="q")
    assert "no semantic index" in result["error"]


def test_scip_conn_or_none_returns_none_when_no_index(monkeypatch):
    """Search-only repos (IndexNotFoundError) and any other failure both
    degrade to None -- semanticSearch must not error over a missing SCIP index."""
    def _boom(*args, **kwargs):
        raise IndexNotFoundError("no index published")

    monkeypatch.setattr(server.QueryService, "connection", _boom)
    assert server._scip_conn_or_none(REPO) is None


def test_error_payload_explains_a_search_only_repo(tmp_path: Path, monkeypatch):
    from jarvis import config, server
    from jarvis.index_reader import IndexNotFoundError
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()

    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))
    assert "search-only" in payload["error"]
    assert "searchCode" in payload["error"]


def test_error_payload_passes_through_other_errors(tmp_path: Path, monkeypatch):
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    payload = server._error_payload("absent", RuntimeError("boom"))
    assert payload == {"error": "boom"}


def test_error_payload_carries_state_cause_recovery_for_a_signature_search_only_repo(tmp_path: Path, monkeypatch):
    """STAT-02 / D-14: on a search-only repo whose registry row explains the
    state, nav-tool errors gain additive `state`/`cause`/`recovery` keys
    alongside the unchanged prose `error` string."""
    from jarvis.index_reader import IndexNotFoundError
    from jarvis.registry import ORIGIN_SIGNATURE, Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only",
                        search_only=True, status_origin=ORIGIN_SIGNATURE,
                        status_reason="the build produced no SCIP shards")
    finally:
        registry.close()

    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))

    assert "search-only" in payload["error"]  # prose explanation retained
    assert "searchCode" in payload["error"]
    assert payload["state"] == "signature"
    assert payload["cause"] == "the build produced no SCIP shards"
    assert payload["recovery"] == "jarvis reindex gorepo"


def test_error_payload_reports_manual_state_for_a_legacy_search_only_row(tmp_path: Path, monkeypatch):
    """Pre-migration rows carry a NULL origin; `origin_of`'s read-time
    fallback reports them as 'manual' with the forget+index escape (SC5)."""
    from jarvis.index_reader import IndexNotFoundError
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()

    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))

    assert payload["state"] == "manual"
    assert payload["recovery"] == "jarvis forget gorepo && jarvis index /p"
    assert "cause" not in payload  # NULL reason on legacy rows — no fabricated key


def test_error_payload_without_a_registry_row_stays_bare(tmp_path: Path, monkeypatch):
    """D-14 keys appear only when the registry row actually explains the
    state — a repo that was never registered keeps the bare error dict."""
    from jarvis.index_reader import IndexNotFoundError

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    payload = server._error_payload("absent", IndexNotFoundError("no published index for absent"))
    assert payload == {"error": "no published index for absent"}


def test_error_payload_does_not_mask_other_faults_on_a_degraded_repo(tmp_path: Path, monkeypatch):
    """A query fault on a degraded repo is not a degradation explanation:
    structured keys apply to the IndexNotFoundError branch only, so a
    RuntimeError keeps its existing bare shape (no masking)."""
    from jarvis.registry import ORIGIN_SIGNATURE, Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only",
                        search_only=True, status_origin=ORIGIN_SIGNATURE,
                        status_reason="the build produced no SCIP shards")
    finally:
        registry.close()

    payload = server._error_payload("gorepo", RuntimeError("kaboom"))
    assert payload == {"error": "kaboom"}


def test_error_payload_degrades_to_bare_error_when_registry_is_unreadable(tmp_path: Path, monkeypatch):
    """A broken registry degrades the lookup, never replaces one error with
    another (the `_registry_status` best-effort convention)."""
    from jarvis import registry as registry_module
    from jarvis.index_reader import IndexNotFoundError
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()

    def _unusable(*args, **kwargs):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(registry_module, "Registry", _unusable)

    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))
    assert payload == {"error": "no pointer"}


def test_get_index_status_reports_search_only_status():
    """The MCP tool itself (not QueryService.get_index_status directly)
    must surface the registry's status so a caller can distinguish
    "search-only" from "never indexed" -- both report indexed=False."""
    from jarvis.registry import Registry

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


def test_error_payload_renders_ambiguous_candidates(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    candidates = (
        Candidate(symbol="sym-a", dotted_path="a.C.dup", kind=DescriptorKind.METHOD),
        Candidate(symbol="sym-b", dotted_path="b.D.dup", kind=DescriptorKind.METHOD),
    )
    payload = server._error_payload(REPO, AmbiguousSymbolError("dup", candidates, 7))
    assert payload["candidateTotal"] == 7
    assert payload["candidates"] == [
        {"symbol": "sym-a", "dottedPath": "a.C.dup", "kind": "METHOD"},
        {"symbol": "sym-b", "dottedPath": "b.D.dup", "kind": "METHOD"},
    ]
    assert "ambiguous" in payload["error"]
    assert "a.C.dup" in payload["error"]  # leads with the qualifier hint


def test_go_to_definition_reports_resolved_symbol_for_a_bare_name():
    result = server.go_to_definition(REPO, "Greeter")
    assert result["symbol"] == "Greeter"
    assert result["resolvedSymbol"] == CLASS_SYMBOL
    assert result["definitions"]


def test_go_to_definition_omits_resolved_symbol_when_input_was_already_full():
    result = server.go_to_definition(REPO, CLASS_SYMBOL)
    assert "resolvedSymbol" not in result
    assert result["definitions"]


def test_find_references_reports_resolved_symbol_for_a_bare_name():
    result = server.find_references(REPO, "greet")
    assert result["resolvedSymbol"] == METHOD_SYMBOL


def test_unknown_symbol_returns_error_not_empty_references():
    result = server.find_references(REPO, "NoSuchSymbol")
    assert "no symbol named" in result["error"]
    assert "references" not in result


def test_search_coverage_fields_reports_complete_when_counts_match(monkeypatch, tmp_path):
    from jarvis import config, server
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: "http://x")
    monkeypatch.setattr(server, "zoekt_repo_documents", lambda url, repo: 133)

    assert server._search_coverage_fields("myslug") == {
        "searchCoverage": {"expected": 133, "indexed": 133, "complete": True}
    }


def test_search_coverage_fields_reports_incomplete_after_shard_loss(monkeypatch, tmp_path):
    """The incident: shards deleted after a successful index. Search kept
    answering with partial results and nothing reported a problem."""
    from jarvis import config, server
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 1307)
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: "http://x")
    monkeypatch.setattr(server, "zoekt_repo_documents", lambda url, repo: 615)

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] == {"expected": 1307, "indexed": 615, "complete": False}


def test_search_coverage_fields_is_null_when_webserver_is_down(monkeypatch, tmp_path):
    from jarvis import config, server
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: None)

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] is None
    assert "not running" in fields["searchCoverageReason"]


def test_search_coverage_fields_is_null_when_never_recorded(monkeypatch, tmp_path):
    """Repos indexed before tracked_files existed have no expectation to
    compare against; report unknown rather than guessing."""
    from jarvis import config, server
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: "http://x")

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] is None
    assert "reindex" in fields["searchCoverageReason"]


def test_search_coverage_fields_never_raises(monkeypatch, tmp_path):
    """A coverage probe failure must not replace a working status response
    with an error.

    `tracked_files` must be recorded first -- otherwise the function
    short-circuits at the "no tracked-file count recorded" branch before it
    ever reaches `_zoekt_base_url_if_running()`, and the test would pass
    vacuously without exercising the try/except at all. `call_count` makes
    that structurally impossible: the assertion below fails if the raiser
    was never invoked.
    """
    from jarvis import config, server
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
    finally:
        registry.close()

    call_count = 0

    def boom() -> str:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", boom)

    fields = server._search_coverage_fields("myslug")

    assert call_count == 1, "the raiser was never reached -- test is vacuous"
    assert fields["searchCoverage"] is None


def test_mcp_server_advertises_the_jarvis_name():
    """The FastMCP instance name is user-visible in `/mcp` output and in a
    client's server list, so it is part of the public surface, not an
    implementation detail."""
    from jarvis.server import mcp

    assert mcp.name == "jarvis"
