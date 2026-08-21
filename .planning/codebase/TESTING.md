---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# Testing Patterns

**Analysis Date:** 2026-08-21

## Test Framework

**Runner:**
- pytest >= 8.3
- Config: `[tool.pytest.ini_options]` in `pyproject.toml`
- `testpaths = ["tests"]`
- Custom marker: `integration: exercises real external binaries (scip-python, scip, zoekt-index)`

**Assertion Library:**
- pytest's built-in `assert` statement (no `assertpy` or `hamcrest`)
- `pytest.raises(ExceptionType)` for expected exceptions

**Run Commands:**
```bash
uv run pytest                                      # Run all tests (unit + integration)
uv run pytest -m "not integration" -rs            # Unit tests only (CI gate; -rs shows skip reasons)
uv run pytest -m integration                      # Integration tests only
uv run pytest tests/test_query.py::test_name       # Single test
uv run python scripts/check_versions.py           # Version consistency guard
```

## Test File Organization

**Location:**
- Co-located in `tests/` at repo root, mirroring `src/jarvis/` 1:1

**Naming:**
- `test_<module>.py` ↔ `<module>.py`
- `test_server_tools.py` covers `server.py` (no `test_server.py`)
- `test_check_versions.py` covers `scripts/check_versions.py`
- `test_setup_sh.py` covers `setup.sh`
- `test_check_wheel_contents.py` covers `scripts/check_wheel_contents.py`
- No dedicated test files for: `models.py` (pure data, no logic), `scip_pb2.py` (vendored gencode)

**Structure:**
```
tests/
├── conftest.py              # Shared helpers (BlockImportFinder)
├── __init__.py
├── test_config.py           # Config & env var tests
├── test_query.py            # SCIP navigation against synthetic index
├── test_index_reader.py     # Pointer resolution & connection cache
├── test_index_cli.py        # CLI unit + integration tests (largest file)
├── test_search.py           # Zoekt HTTP client & lifecycle
├── test_server_tools.py     # MCP tool roundtrips & error shapes
├── test_semantic.py         # Semantic store, RRF fusion, LanceDB
├── test_embeddings.py       # Embedding model wrapper
├── test_chunker.py          # Tree-sitter chunking
├── test_graph.py            # Package dependency graph & blast radius
├── test_registry.py         # SQLite registry CRUD & migration
├── test_scip_decoder.py     # SCIP protobuf decoding
├── test_symbols.py          # Symbol resolution
├── test_symbol_search.py    # Symbol-based search signal
├── test_watch.py            # Debouncer (pure, no threads)
├── test_index_status.py     # Freshness staleness detection
├── test_check_versions.py   # Version consistency guard
├── test_setup_sh.py         # Shell script tests (dash)
├── test_check_wheel_contents.py  # Wheel hygiene assertions
└── fixtures/
    ├── __init__.py
    ├── synthetic_index.py    # Real-schema SQLite fixture builder
    ├── scip_encoder.py       # Protobuf fixture helper
    ├── mini_py_repo/         # Python fixture repo for integration
    ├── mini_swift_repo/      # Swift fixture repo for integration
    └── mini_java_repo/       # Java/Gradle fixture repo for integration
```

## Test Structure

**Suite Organization:**
Tests are flat `def test_*()` functions within each module file. No test classes are used — even `tests/test_server_tools.py` (the largest at ~520 lines) uses flat functions. Grouping is done with comment-section headers:

```python
# ---------------------------------------------------------------------------
# Bare-name resolution wiring (Task 3)
# ---------------------------------------------------------------------------

def test_get_definitions_accepts_a_bare_name(query_service: QueryService):
    ...
```

**Patterns:**
- **Setup via fixtures:** `@pytest.fixture` for reusable state (e.g. `query_service` in `tests/test_query.py` builds a synthetic index and returns a `QueryService`)
- **`autouse=True` fixtures:** Used in `tests/test_server_tools.py` to isolate `JARVIS_DATA_DIR` per test
- **`tmp_path`:** Standard pytest fixture for filesystem isolation — used extensively in registry, graph, config, and index tests
- **`monkeypatch`:** Standard for env var and attribute injection
- **No teardown:** `tmp_path` auto-cleans; SQLite connections are closed in `finally` blocks or via context managers
- **No class-based tests:** All tests are plain functions

**Async Tests:**
- MCP tool roundtrips use `@pytest.mark.anyio` with `async def test_*`
- `create_connected_server_and_client_session(server.mcp)` as async context manager
- Pattern in `tests/test_server_tools.py`:

```python
@pytest.mark.anyio
async def test_document_symbols_roundtrip():
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("documentSymbols", {"repo": REPO, "path": DOC_GREETER})
        payload = json.loads(result[0].text)
        assert [s["displayName"] for s in payload["symbols"]] == ["Greeter", "greet", "DEFAULT_NAME", "sayHi"]
```

## Mocking

**Framework:**
- `pytest.monkeypatch` for attribute/env mocking
- `httpx.MockTransport` for HTTP mocking
- Direct function/lambda injection for clock, subprocess, and dependency replacement

**Patterns:**

HTTP mocking (Zoekt client):
```python
# tests/test_search.py
def _zoekt_response(request: httpx.Request) -> httpx.Response:
    encoded_line = base64.b64encode(b"def greet(name):").decode()
    return httpx.Response(200, json={...})

client = httpx.Client(transport=httpx.MockTransport(_zoekt_response))
hits = search_zoekt("http://localhost:6070", "greet", client=client)
```

Fake clock injection:
```python
# tests/test_watch.py
clock = [0.0]
d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])
d.notify()
clock[0] = 5.0
assert d.poll() is True
```

Fake embedding model (in-process, no download):
```python
# tests/test_semantic.py
class FakeEmbedder:
    """Deterministic 3-dim embedder; counts embed calls for reuse assertions."""
    def __init__(self):
        self.embedded: list[list[str]] = []
        self.identity = ("fake-model", "rev1")
    def embed_texts(self, texts): ...
    def embed_query(self, query): ...
```

Subprocess mocking:
```python
# tests/test_server_tools.py — fake zoekt-webserver binary
_FAKE_ZOEKT_SEARCH_SCRIPT = """\
import http.server, json, sys
...
"""
script_path = tmp_path / "fake-zoekt-webserver"
script_path.write_text(_FAKE_ZOEKT_SEARCH_SCRIPT)
script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
lifecycle = ZoektLifecycle(..., binary=[sys.executable, str(script_path)])
```

Import blocking for optional extras:
```python
# tests/conftest.py — BlockImportFinder
class BlockImportFinder:
    """Meta-path finder that fails one import as if the module were absent.
    Forces a real import-machinery failure that both CPython and Cython consult
    identically."""
    def find_spec(self, fullname, path, target=None):
        if fullname == self._blocked_name:
            raise ModuleNotFoundError(f"No module named {fullname!r}")
        return None
```

**What to Mock:**
- HTTP calls to zoekt-webserver (`httpx.MockTransport`)
- Subprocess calls to external binaries (`monkeypatch` on `_run`, or inject `binary=` into `ZoektLifecycle`)
- Time/clock for debounce tests (inject `clock=lambda: ...`)
- Environment variables (`monkeypatch.setenv("JARVIS_DATA_DIR", ...)`)
- Module attributes for lazy singletons (`monkeypatch.setattr(server, "_query_service", None)` to reset)
- Filesystem paths via `root=` parameter override pattern

**What NOT to Mock:**
- SQLite operations — use real in-memory or `tmp_path` databases
- `scip_decoder` logic — use real protobuf encoding/decoding against the synthetic fixture
- `dataclasses.asdict()` — it's stdlib, used directly

## Fixtures and Factories

**Test Data:**
- `tests/fixtures/synthetic_index.py` builds a real-schema SQLite index.db with known symbols, documents, and occurrence blobs — used by `test_query.py`, `test_index_reader.py`, `test_server_tools.py`, `test_graph.py`
- `build_published_index(tmp_path, project, repo, branch)` lays out the full `{root}/scip/{project}/{repo}/{branch}/` directory tree with `current` pointer and `*.metadata.json`
- Mini repos in `tests/fixtures/mini_py_repo/`, `mini_swift_repo/`, `mini_java_repo/` for integration tests
- `tests/fixtures/scip_encoder.py` provides `encode_occurrences()` and `encode_relationships()` helpers for building protobuf blobs in tests

**Location:**
- `tests/fixtures/` for shared test infrastructure
- Per-file constants (e.g. `REPO = "toy-repo"`, `CLASS_SYMBOL`, `DOC_GREETER`) defined at module level in test files that use the synthetic index

**Parameterized Tests:**
- `@pytest.mark.parametrize` for data-driven input sets, e.g. `test_detect_os_maps_darwin` variants, `test_recognized_failures_map_to_a_search_only_reason` in `tests/test_index_cli.py`

## Coverage

**Requirements:** No enforced coverage threshold

**CI:** Runs `pytest -m "not integration" -rs` — the `-rs` flag surfaces skip reasons so silently-skipped tests are visible in logs

## Test Types

**Unit Tests:**
- Majority of the suite (~270 tests without semantic extra)
- Mock subprocess/file I/O/HTTP; use synthetic index.db fixture for SQLite-dependent tests
- Run in CI on every push/PR with no path filter
- Examples: `test_query.py`, `test_search.py`, `test_config.py`, `test_watch.py`, `test_embeddings.py`

**Integration Tests:**
- Marked with `@pytest.mark.integration`
- Concentrated in `tests/test_index_cli.py` and `tests/test_chunker.py`
- Call real `scip-python`, `scip`, `scip-java`, `scip-swift`, `zoekt-index`/`zoekt-webserver` binaries against `tests/fixtures/mini_*_repo/`
- Skip cleanly when binaries are absent:

```python
_REQUIRED_BINARIES = ["scip-python", "scip", "zoekt-git-index"]
_missing = [b for b in _REQUIRED_BINARIES if shutil.which(b) is None]

@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_end_to_end_atomic_swap_under_open_reader(tmp_path: Path):
    ...
```

- Semantic integration tests use `pytest.importorskip("lancedb")` via a fixture:

```python
@pytest.fixture()
def lancedb_available():
    pytest.importorskip("lancedb")
```

- Full-pipeline semantic integration test: `test_index_repo_builds_semantic_index_and_searches` in `tests/test_index_cli.py`

**E2E Tests:** Not used (no browser/driver testing)

## Common Patterns

**Error Testing:**
```python
# Specific exception type
with pytest.raises(IndexNotFoundError):
    query_service.get_definitions("does-not-exist", CLASS_SYMBOL)

# Error message content
with pytest.raises(ValueError, match="does not produce a usable"):
    config.repo_slug("   ")

# MCP error payload shape
result = server.find_references(REPO, "NoSuchSymbol")
assert "no symbol named" in result["error"]
assert "references" not in result
```

**Regression Tests:**
- Named by the bug they prevent, with a comment explaining the regression
- Example: `test_unknown_name_raises_instead_of_returning_empty` — "the regression this whole feature exists for"
- Example: `test_detect_language_ignores_gitignored_checkout_directory` — "Direct regression test for the sample-python-repo failure"

**MCP Server Test Pattern:**
- In-process MCP client session via `create_connected_server_and_client_session(server.mcp)`
- No network or subprocess — full stack exercised in a single process
- Isolate `JARVIS_DATA_DIR` via `monkeypatch.setenv` in `autouse=True` fixture
- Reset module-level singletons via `monkeypatch.setattr(server, "_query_service", None)` etc.

## Version Guard Tests

**`scripts/check_versions.py` + `tests/test_check_versions.py`:**
- Asserts `pyproject.toml` version, `server.json` version, and `server.json` packages[0].version are identical
- Tests build miniature repos under `tmp_path` — never read the real working tree
- CI runs `scripts/check_versions.py` on every push/PR to catch drift at introduction time
- Pattern for adding new version-bearing files: register in `check_versions.py`'s `read_declared_versions()` and add a corresponding test in `test_check_versions.py`

## Shell Script Tests

**`tests/test_setup_sh.py`:**
- Tests `setup.sh` by sourcing it into `dash` (not `sh`) and running shell functions
- `dash` is required because macOS `/bin/sh` accepts bashisms, giving false confidence
- Tests shell functions individually via a `run_func(snippet)` helper that sources setup.sh and executes the snippet
- Guards pin synchronization: `test_scip_pin_matches_committed_file` and `test_zoekt_pin_matches_committed_file` assert `SCIP_COMMIT_PIN`/`ZOEKT_COMMIT_PIN` in the script match the committed `SCIP_COMMIT`/`ZOEKT_COMMIT` files
- Uses `tmp_path` for filesystem isolation, fake binaries for download/install tests
- Excluded from compiled-wheel CI (`test-command` in `pyproject.toml` ignores it) because `dash` may not exist in manylinux containers

## CI Matrix

**Workflow:** `.github/workflows/test.yml`

**Matrix:**
| OS | Python | Purpose |
|---|---|---|
| ubuntu-latest | 3.12 | Floor version declared by `requires-python` |
| ubuntu-latest | 3.13 | Forward compatibility |
| macos-latest | 3.13 | Primary user platform; Swift indexing guard |

**Key Details:**
- `UV_PYTHON` set per leg at job level — `.python-version` would otherwise override
- Installs `--extra semantic` so `test_semantic.py` tests actually run (23 tests would silently skip without it)
- Runs `pytest -m "not integration" -rs` (unit-only gate)
- Runs `scripts/check_versions.py` as a separate step
- Confirms the actual Python interpreter matches the matrix leg (guards against silent resolution failures)
- No path filter — this is the gate that must run on every change

---

*Testing analysis: 2026-08-21*
