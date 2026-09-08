---
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
---

# Testing Patterns

**Analysis Date:** 2026-09-08

## Test Framework

**Runner:**
- pytest `>=8.3` (the `dev` dependency group in `pyproject.toml`; the only dev dep)
- Config: `[tool.pytest.ini_options]` in `pyproject.toml` — `testpaths = ["tests"]`,
  `markers = ["integration: exercises real external binaries (scip-python, scip, zoekt-index)"]`
- Async MCP client tests use `@pytest.mark.anyio` (anyio's pytest plugin arrives
  transitively via `mcp`); no `[tool.anyio]` section — default asyncio backend.

**Assertion Library:**
- Plain `assert` + `pytest.raises` / `pytest.approx` / `pytest.importorskip`.
  No unittest-style classes; every suite is module-level functions.

**Run Commands:**
```bash
uv run pytest                        # everything (unit + integration if binaries present)
uv run pytest -m "not integration"   # unit only — the CI gate
uv run pytest -m integration         # real scip/scip-python/zoekt binaries only
uv run pytest tests/test_query.py    # one module
```

**CI gate** (`.github/workflows/test.yml`): `uv run pytest -m "not integration" -rs`
on a 3-leg matrix — ubuntu/3.12 (the `requires-python` floor), ubuntu/3.13,
macos/3.13 — installed with `uv sync --extra semantic` (without the extra,
`tests/test_semantic.py` skips silently and the suite "stays green" while
testing ~58 fewer tests). `-rs` surfaces skip reasons so a dependency dropping
tests out of the run is visible. The same workflow runs
`uv run python scripts/check_versions.py`. Integration tests are NOT run in CI
unit gate; `setup-smoke.yml` installs and verifies the real binaries on runners.

## Test File Organization

**Location:** all tests live in `tests/` (mirrored layout, not co-located with
source). `tests/__init__.py` and `tests/fixtures/__init__.py` exist so suites
import fixtures absolutely: `from tests.fixtures.synthetic_index import ...`.

**Naming:** `test_<module>.py` mirrors `src/jarvis/<module>.py` ~1:1:

| Source | Test file |
|---|---|
| `src/jarvis/query.py` | `tests/test_query.py` |
| `src/jarvis/index_cli.py` | `tests/test_index_cli.py` (4881 lines — unit + integration) |
| `src/jarvis/registry.py` | `tests/test_registry.py` |
| `src/jarvis/scip_decoder.py` | `tests/test_scip_decoder.py` |
| `src/jarvis/symbols.py` | `tests/test_symbols.py` |
| `src/jarvis/chunker.py` | `tests/test_chunker.py` |
| `src/jarvis/embeddings.py` | `tests/test_embeddings.py` |
| `src/jarvis/semantic.py` | `tests/test_semantic.py` |
| `src/jarvis/search.py` | `tests/test_search.py` |
| `src/jarvis/graph.py` | `tests/test_graph.py` |
| `src/jarvis/config.py` | `tests/test_config.py` |
| `src/jarvis/watch.py` | `tests/test_watch.py` |
| `src/jarvis/index_reader.py` | `tests/test_index_reader.py` |

**Deliberate deviations from the mirror rule:**
- `src/jarvis/server.py` → `tests/test_server_tools.py` (tests MCP *tools*, not the module per se)
- `query.py`'s `get_index_status` → `tests/test_index_status.py` (freshness transitions against real git)
- `setup.sh` → `tests/test_setup_sh.py` (POSIX-shell contract)
- `scripts/check_versions.py` → `tests/test_check_versions.py`; `scripts/check_wheel_contents.py` → `tests/test_check_wheel_contents.py`
- `src/jarvis/models.py` and `__init__.py` have no dedicated test file (pure dataclass/empty modules)

## Test Structure

**Suite organization (house shape):**

```python
"""One-line scope statement — what is tested and against what double."""
from __future__ import annotations

import pytest

from jarvis import config
from tests.fixtures.synthetic_index import build_published_index

REPO = "toy-repo"                       # module-level constants


@pytest.fixture
def query_service(tmp_path: Path) -> QueryService:
    build_published_index(tmp_path, config.PROJECT, REPO, config.BRANCH)
    return QueryService(IndexConnectionCache(str(tmp_path)))


def test_get_definitions_returns_class_definition(query_service: QueryService):
    """Docstring pins the regression/rationale when non-obvious."""
    locations, freshness = query_service.get_definitions(REPO, CLASS_SYMBOL)
    assert locations[0].path == DOC_GREETER
```
(excerpted from `tests/test_query.py`)

**Patterns:**
- Test names are `<function>_<observed_behavior>`: `test_debouncer_coalesces_a_burst_into_a_single_fire`, `test_index_status_stale_after_new_commit`.
- Fixtures build all state under pytest's `tmp_path`; nothing touches the real `~/.jarvis` (see Isolation below).
- Setup/teardown is fixture-based or `try/finally + monkeypatch.setattr(..., None)` (e.g. `tests/test_server_tools.py` lifecycle tests).
- Assertions target observable values (ordered lists, payload keys, exact structs), not implementation details.
- Float math asserts with `pytest.approx` against hand-computed expected values (`tests/test_semantic.py` RRF: `2/61`, `1/62`).
- Regex-escape brackets in `pytest.raises(..., match=r"jarvis-mcp\[semantic\]")` — noted inline because `[semantic]` reads as a character class.

## Mocking

Unit tests mock **external boundaries only**; SQL, the decoder, and pure logic always run for real.

### 1. Subprocess boundary: `_run` dispatch on `step`

`src/jarvis/index_cli.py` funnels every external binary through
`_run(cmd, *, cwd, step, env=None)`. Tests monkeypatch it and dispatch on the
human-readable `step` label, failing exactly one pipeline stage while the rest
(publish, registry writes, zoekt) runs for real:

```python
def _fake_run(cmd, *, cwd, step, env=None):
    if step.endswith(" index"):          # the language-indexer stage
        raise IndexingError(carrier)
    return _fake_completed_process(cmd)  # helper: CompletedProcess(cmd, 0, stdout="", stderr="")

monkeypatch.setattr(index_cli, "_run", _fake_run)
```
(see `tests/test_index_cli.py` — a dozen `_fake_run` variants; `_fake_completed_process`
exists because callers read `.stderr` off the return). Real step labels:
`f"{indexer_cmd[0]} index"`, `"scip expt-convert"`, `"zoekt-git-index"`,
`"git config zoekt.name"`.

### 2. HTTP boundary: `httpx.MockTransport` via the injectable `client=` param

`search_zoekt()` / `zoekt_repo_documents()` accept `client: httpx.Client | None`;
tests inject `httpx.Client(transport=httpx.MockTransport(handler))` with handlers
returning zoekt's real response shape (`tests/test_search.py`) — including
error-shape tests that assert `ZoektUnavailableError` on non-2xx.

### 3. Heavy ML dep: `sys.modules` SimpleNamespace fake

`tests/test_embeddings.py` fakes sentence-transformers without the extra installed:

```python
def _install_fake(monkeypatch):
    holder = {}
    def ctor(*args, **kwargs):
        holder["model"] = _FakeST(*args, **kwargs)
        return holder["model"]
    fake_module = types.SimpleNamespace(SentenceTransformer=ctor)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    return holder
```
The holder dict lets tests assert on the constructed instance (batches,
normalize flags). `tests/test_semantic.py` similarly defines a `FakeEmbedder`
class (deterministic 3-dim vectors, records every embedded text, exposes
`identity()`/`prefixes()`) passed via `index_semantic(..., model=embedder)`.

### 4. Missing-extra simulation: `BlockImportFinder`

`tests/conftest.py` exports `BlockImportFinder` — a meta-path finder whose
`find_spec` raises `ModuleNotFoundError`. It exists because
`monkeypatch.setitem(sys.modules, name, None)` does NOT trip Cython-compiled
imports (fast path reads sys.modules directly and returns the cached None), and
the wheel is Cython-compiled (cibuildwheel `JARVIS_COMPILE=1`). Usage
(`tests/test_embeddings.py`, `tests/test_semantic.py`):

```python
monkeypatch.delitem(sys.modules, "lancedb", raising=False)
monkeypatch.setattr(sys, "meta_path", [BlockImportFinder("lancedb"), *sys.meta_path])
```

### 5. Time: injectable clock instead of sleeps

`src/jarvis/watch.py`'s `Debouncer` is pure and takes a clock; `tests/test_watch.py`
drives `now()`/`notify()`/`poll()` with list-append callbacks — zero real waiting.

### 6. Whole server doubles: fake zoekt-webserver process

`tests/test_server_tools.py` writes `_FAKE_ZOEKT_SEARCH_SCRIPT` (a stdlib
`http.server` JSON responder) into `tmp_path`, chmods it, and runs it through
the real `ZoektLifecycle(binary=[sys.executable, script_path])` — exercising
spawn/health-check/stop for real while controlling responses. Comments pin two
traps: absolute-shebang (PATH flakiness) and `allow_reuse_address = True`
(EADDRINUSE on TIME_WAIT).

### 7. In-memory MCP client

Tool tests use `create_connected_server_and_client_session(server.mcp)` from
`mcp.shared.memory` under `@pytest.mark.anyio`, then assert on
`json.loads(result.content[0].text)` — full wire roundtrip without stdio.

**What to mock:** binaries, HTTP, missing packages, clocks, the embedder model.
**What NOT to mock:** git (see below), SQLite, the scip blob decoder, SQL query
logic, `server.py`'s payload assembly.

## Real Dependencies On Purpose

**Real git is not mocked where git is the contract.** Throwaway repos are built
in `tmp_path` via a shared helper:

```python
def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
```
(`tests/test_index_cli.py` and `tests/test_index_status.py`). Freshness tests
add `git commit --allow-empty` to manufacture SHAs; one test monkeypatches
`jarvis.query._git_head` only when HEAD content (not git behavior) is the
variable under test.

## Fixtures and Factories

**Shared fixture code lives in `tests/fixtures/` (imported, not pytest plugins):**

- `tests/fixtures/synthetic_index.py` — builds a complete *published* index
  (`build_published_index(root, project, repo, branch)`) plus
  `build_synthetic_index_db`. Its DDL is **copied verbatim** from a real
  `scip expt-convert` index's `.schema` output (verification date recorded in
  the module docstring), with blob columns populated by the encoder below. It
  models a deterministic TypeScript-like repo (`toy/greeter.ts`,
  `toy/constants.ts`, `toy/animal.ts`) whose every row exists to pin a specific
  converter behavior (combined role bitmasks, NULL relationships, locals
  skipped from mentions, enclosing-range gaps) — each documented inline.
  Exports stable constants (`CLASS_SYMBOL`, `METHOD_SYMBOL`, `DOC_GREETER`,
  `COMMIT_SHA`, …) reused across suites.
- `tests/fixtures/scip_encoder.py` — `encode_occurrences` /
  `encode_relationships`: build real zstd+protobuf blobs via the vendored
  `scip_pb2`, so encoder and decoder share one source of truth. Test-only;
  never shipped.
- `tests/fixtures/mini_py_repo/greeter.py` (+ `mini_swift_repo/`,
  `mini_java_repo/`) — tiny real repos for end-to-end integration runs.

**Per-suite factories** build rows/dicts inline (`_row(...)` in
`tests/test_semantic.py`, `_wheel(...)` in `tests/test_check_wheel_contents.py`,
`_build(...)` writing miniature version-file repos in
`tests/test_check_versions.py` — scripts are exercised against `tmp_path`
copies, never the working tree).

## Integration Tests

**Marker + availability guard, computed at module import:**

```python
_REQUIRED_BINARIES = ["scip-python", "scip", "zoekt-git-index"]
_missing = [b for b in _REQUIRED_BINARIES if shutil.which(b) is None]

@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_end_to_end_atomic_swap_under_open_reader(tmp_path: Path):
    ...
```
(`tests/test_index_cli.py`; separate `_missing_swift` for `scip-swift`,
`_missing_java` for `scip-java`). Integration tests copy a mini repo into
`tmp_path`, init git, and run the true pipeline — atomic swap under an open
reader, scheme/language override persistence, zoekt shard naming, semantic
index + hybrid search, bare-name resolution against a genuinely converted index.

`tests/test_chunker.py` marks gitignore-behavior tests `@pytest.mark.integration`
**without** skipif — they only need git (assumed present; `git check-ignore`
semantics are the contract).

## Isolation & Environment

- `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` — the autouse fixture
  in `tests/test_server_tools.py` states the reason: registry lookups in error
  paths must never touch the developer's real `~/.jarvis/registry.db`.
- Path-scoped state (indexes, LanceDB dirs, zoekt shards, fake servers) is
  always rooted under `tmp_path` / `tmp_path / "data"`.
- Shell tests run with a scrubbed `PATH=/usr/bin:/bin:/usr/sbin:/sbin` env.

## Special Suites

**`tests/test_setup_sh.py` (POSIX-shell contract):** sources `setup.sh` with
`JARVIS_SETUP_SOURCED=1` (suppresses `main()`) and invokes one function per
test via `POSIX_SH -c ". setup.sh\n<snippet>"`, faking commands by defining
shell functions (`uname() { echo Darwin; }`). Hard-requires **dash**: macOS
`/bin/sh` is bash-in-POSIX-mode and accepts bashisms, giving false confidence;
the suite's first test (`test_dash_is_available_for_honest_bashism_detection`)
fails with `brew install dash` guidance if dash is missing — "guard the guard".

**Script tests load by path** (`scripts/` is not a package):

```python
spec = importlib.util.spec_from_file_location("check_versions", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

**cibuildwheel** runs the unit suite against the *installed compiled wheel*
(`pytest {project}/tests -m 'not integration' -q --ignore={project}/tests/test_setup_sh.py`)
as the empirical gate for Cython semantic fidelity — the reason
`BlockImportFinder` exists. It does not install the `semantic` extra, so
`pytest.importorskip("lancedb")`-gated tests skip there.

## Coverage

**Requirements:** none enforced (no coverage tooling configured).

**Effective gate:** the CI unit matrix + `-rs` skip reporting; semantic tests
gated by the `lancedb_available` fixture:

```python
@pytest.fixture()
def lancedb_available():
    pytest.importorskip("lancedb")
```

**Known gaps:** integration tests only run where binaries exist;
`tests/test_setup_sh.py` is excluded from wheel builds; full-extras
compiled-wheel coverage is not automated (documented in `pyproject.toml`
`[tool.cibuildwheel]` comments).

## Test Types

**Unit (default, no marker):** everything under `tests/` except marked tests;
external boundaries doubled as described above.

**Integration (`@pytest.mark.integration`):** real binaries — concentrated in
`tests/test_index_cli.py` (full pipeline) plus gitignore-behavior tests in
`tests/test_chunker.py`.

**E2E:** no separate framework; the MCP-session roundtrip tests in
`tests/test_server_tools.py` and the end-to-end integration tests fill this role.

## Common Patterns

**Async testing** (MCP client only):

```python
@pytest.mark.anyio
async def test_document_symbols_roundtrip():
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("documentSymbols", {"repo": REPO, "path": DOC_GREETER})
        payload = json.loads(result.content[0].text)
        assert [s["displayName"] for s in payload["symbols"]] == ["Greeter", "greet", "DEFAULT_NAME", "sayHi"]
```

**Error testing:** `pytest.raises(TypedError, match=...)` for internal APIs;
for the MCP surface, assert the *payload* carries `{"error": ...}` (or
`candidates`/`candidateTotal` for `AmbiguousSymbolError`) and `result.isError
is not True` — the server must convert, not propagate. Failure-mode doubles
raise from the exact production call site, with a comment explaining why that
site (e.g. `resolve_symbol`, because `goToDefinition` resolves first).

**Regression-pin discipline:** a test docstring names the bug it prevents and
the real-world behavior it was verified against (dates, upstream file paths),
e.g. `tests/test_query.py`'s role-bitmask and NULL-relationships tests,
`tests/test_index_cli.py`'s `-z` quoting test (octal-escaped non-ASCII names).

---

*Testing analysis: 2026-09-08*
