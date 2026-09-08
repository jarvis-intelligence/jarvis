# Repository Guidelines

Contributor guide for **jarvis** — a local-first code intelligence MCP server (SCIP symbol navigation + Zoekt lexical search + optional semantic vector search). PyPI distribution is `jarvis-mcp`; the import package, both CLIs, and the MCP server are all `jarvis`.

## Project Overview

jarvis gives AI coding assistants code-intelligence tools over a local data dir (`~/.jarvis`, overridable via `JARVIS_DATA_DIR`). Two processes share that dir:

- **Writer** — `jarvis` CLI (`src/jarvis/index_cli.py`): runs per-language SCIP indexers, converts to SQLite, builds Zoekt shards (+ optional LanceDB vectors), publishes atomically.
- **Reader** — `jarvis-server` MCP stdio server (`src/jarvis/server.py`): 9 tools (documentSymbols, goToDefinition, findReferences, callHierarchy, typeHierarchy, getIndexStatus, searchCode, semanticSearch, blastRadius).

Single-tenant by design: `config.py` pins `PROJECT = BRANCH = "_"`; no auth, no multi-tenancy. Languages: TypeScript/TSX, Python, Java, Kotlin, Swift.

## Architecture & Data Flow

**Indexing pipeline** (`index_repo()` in `index_cli.py`): git gates (`NotAGitRepositoryError`) → scip version check (≥0.9.0; older scip silently drops `typed_range`) → language resolve (explicit `--language` else `detect_language()` counting extensions over `git ls-files -z`, ties by fixed priority) → SCIP indexer subprocess → `scip expt-convert` → `populate_graph_for_repo()` → `zoekt-git-index` → optional semantic stage → `_publish_atomically()`. On indexer failure, a degrade state machine may publish search-only (`--search-only`, signature-detected fallbacks, or `JARVIS_FALLBACK_SEARCH_ONLY`).

**MCP query path**: `server.py` lazy singletons → nav tools resolve symbols via `symbols.resolve()` (rung 1 verbatim, rung 2 dotted-suffix), query the published index via `QueryService` (raw sqlite3 over the expt-convert schema) + `scip_decoder` blob decode; `searchCode` lazily spawns `zoekt-webserver` (`ZoektLifecycle`, port 6070, pidfile + atexit kill); `semanticSearch` fuses vector + Zoekt + symbol hits via reciprocal rank fusion.

**On-disk layout** (`~/.jarvis`): `scip/_/<slug>/_/{current, index-<sha>.db, index-<sha>.metadata.json}` · `.zoekt/<slug>_v*.zoekt*` shards + pid · `lancedb/<slug>.lance/` · `registry.db` (repos + packages + edges) · `cache/scip-swift/<slug>/` · `shims/`.

**Load-bearing invariants** — violating these breaks correctness:

- **Atomic publish**: write `index-<sha>.db` first; only after graph + Zoekt + semantic all succeed, flip the `current` pointer via `os.replace()`. Old versioned files deleted only after the new pointer is live. Never mutate a published `index-<sha>.db` — readers open them `mode=ro&immutable=1`; connection-cache invalidation is by pointer *content*, never mtime.
- **Rebuild-not-accumulate graph**: `populate_graph_for_repo()` clears a repo's outgoing edges (for every package it ever registered) before recomputing; `_retire_scip_artifacts` clears edges *before* rmtree.
- **Isolation seam**: `scip_decoder.py` is the ONLY module importing `scip_pb2`/`zstandard` (enforced by test). `scip_pb2.py` is vendored protoc gencode from scip.proto v0.9.0 — never hand-edit. `OccurrenceDecodeError` is fail-closed; never map it to empty results.
- **Role bitmask**: `mentions.role` is a raw SymbolRoles bitmask — always filter `(m.role & ?) != 0`, never equality.
- **LanceDB model identity**: one table holds vectors from exactly one `TableIdentity` (model + revision + prefixes + `CONTENT_FORMAT`); mismatch at index time → full rebuild, never carry-forward vectors.
- **Language detection reads git, not the filesystem** (`git ls-files`, `IGNORED_DIRS` on top). Zoekt indexes git blobs (reflects HEAD) while SCIP reflects the working tree — known asymmetry.
- **Zoekt repo name pinning**: `git config zoekt.name <slug>` must be set or `r:<slug>` silently matches nothing.
- **MCP boundary never raises**: every tool body wraps in broad `except Exception` → `{"error": ...}` dict (deliberate; keep the stdio server alive).
- **SCIP lines are 0-based**; jarvis surfaces 1-based — converted only in `symbol_search.search_symbols`.
- **scip-swift invocation stays bare** (no `index` subcommand token) — load-bearing for cross-version compat.

## Key Directories

- `src/jarvis/` — one module per concern:
  - `index_cli.py` — CLI (`index`/`list`/`status`/`reindex`/`forget`/`watch`) + full pipeline, degrade/fallback state machine.
  - `server.py` — FastMCP stdio server, 9 tools, lazy singletons.
  - `query.py` + `index_reader.py` — nav SQL over expt-convert schema; pointer resolution + `IndexConnectionCache` (thread-safe OrderedDict, max 64).
  - `symbols.py` — the only module parsing SCIP symbol strings; `symbol_search.py` — NL-query → ranked symbol hits (third RRF signal).
  - `search.py` — Zoekt client + `ZoektLifecycle` (never spawn zoekt-webserver elsewhere).
  - `graph.py` + `registry.py` — package graph + repo registry, both SQLite over `registry.db` (`busy_timeout=5000`; must be `close()`d via try/finally).
  - `semantic.py` + `chunker.py` + `embeddings.py` — optional semantic path (LanceDB, tree-sitter chunking, sentence-transformers; all imports deferred).
  - `scip_decoder.py` — isolation seam; `scip_pb2.py` — vendored gencode; `config.py` — paths/slugs/pinning; `models.py` — frozen result dataclasses; `watch.py` — pure thread-free `Debouncer` + `should_ignore_path`.
- `tests/` — mirrors src ~1:1 (`test_<module>.py` ↔ `<module>.py`); `tests/fixtures/` holds a synthetic real-schema index builder, a zstd/protobuf blob encoder, and mini repos (`mini_py_repo`, `mini_swift_repo`, `mini_java_repo`, `mini_xcode_repo`).
- `scripts/` — `check_versions.py` (version lockstep guard), `check_wheel_contents.py` (compiled-wheel guard).
- `docs/` — `code-standards.md` (canonical style contract), `system-architecture.md`, `project-overview-pdr.md`, `codebase-summary.md` (module index), `project-roadmap.md`, + historical `superpowers/` specs/plans and journals.
- `plans/` — dated design-history dirs + `reports/`. `evals/` — gitignored local skill-benchmark output (not committed test infra).
- `.claude/skills/jarvis-release/` — maintainer-only release runbook.
- `.github/workflows/` — `test.yml` (gate), `setup-smoke.yml`, `build-zoekt.yml`, `build-scip.yml`, `publish-pypi.yml`, `publish-mcp-registry.yml`, `sync-public-distribution.yml`.
- Claude Code / Codex plugin + marketplace: **NOT in this repo** — source of truth is `jarvis-intelligence/jarvis-index`, versioned independently. Any doc/plan directing edits to `plugin/**` here describes pre-2026-08-06 state.

## Development Commands

Uses `uv`; Python ≥3.12, <3.15 (cap is load-bearing: no sdist exists, so an uncapped resolve would strand `uvx` installs).

```sh
uv sync                                  # base deps
uv sync --extra semantic --extra watch   # optional features (semantic needed for test_semantic.py to run, not to pass)
uv run pytest -m "not integration" -rs   # unit tests — the CI gate
uv run pytest -m integration             # e2e vs real scip/zoekt binaries (skips cleanly if absent)
uv run pytest tests/test_query.py::test_name   # single test
uv run python scripts/check_versions.py  # must print "versions consistent"
```

CLI surface:

```sh
uv run jarvis index /path/to/repo [--slug name] [--language java|python|swift|typescript] \
                                   [--scheme name] [--semantic-include path] [--search-only]
uv run jarvis list | status <slug> | reindex <slug> | forget <slug>
uv run jarvis watch /path/to/repo [--debounce 5.0] [--scheme name] [--language name]
uv run jarvis-server                     # MCP stdio entry point
```

External binaries (not pip deps) come from `setup.sh`: `sh setup.sh` or `sh setup.sh --only scip|zoekt|scip-swift|scip-typescript|scip-python|scip-java|bash-shim|jarvis-mcp` (`--force` to reinstall). No lint/type-check config committed — match surrounding style; don't add ruff to CI without configuring it first.

## Code Conventions & Common Patterns

- **Typing**: Python 3.12+ syntax throughout — `X | None` (never `Optional[T]`), `list[T]`, `dict[K, V]`; `from __future__ import annotations` in every module.
- **Result shapes**: frozen dataclasses, deliberately not Pydantic; `server.py` converts via `dataclasses.asdict()`. StrEnums for status enums.
- **Persistence**: raw stdlib `sqlite3`, parameterized queries only (`?` positional in query/registry, `:named` in graph), no ORM. Fully synchronous codebase — do not add async to the query path.
- **Error handling**: broad `except Exception` → `{"error": ...}` at the MCP boundary only (by design); typed exceptions elsewhere (`IndexingError`, `MissingBinaryError`, `SymbolNotFoundError`, `AmbiguousSymbolError`, `ZoektUnavailableError`, `SemanticExtraMissingError`). Index failures persist cause/origin/recovery into the registry.
- **Optional extras pattern**: extras deps (`watch`, `semantic`) are imported lazily inside the functions that need them, so a base install imports every module. Follow this for any new optional dependency.
- **Comments/docstrings carry rationale** — decision IDs (D-xx, FALL-xx, SEMA-xx, WR-xx) and regression provenance. Read docstrings before changing behavior; many tests are regression pins named after real incidents. Preserve and extend this style.
- **Naming**: modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`, constants `UPPER_CASE`, private `_`-prefixed.
- **Env overrides are `JARVIS_`-prefixed**: `JARVIS_DATA_DIR`, `JARVIS_FALLBACK_SEARCH_ONLY`, `JARVIS_ZOEKT_BIN`, `JARVIS_EMBEDDING_MODEL`, `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`, `JARVIS_EMBEDDING_DOC_PREFIX`.
- **stdout stays machine-parseable** (`indexed <slug>`); warnings/notes to stderr.

## Important Files

- `pyproject.toml` — dist name `jarvis-mcp`, src-layout, extras, entry points (`jarvis = jarvis.index_cli:main`, `jarvis-server = jarvis.server:main`), pytest config, cibuildwheel matrix.
- `setup.py` — Cython gate: compiles only under `JARVIS_COMPILE=1` (release CI only); strips `.py` sources from compiled wheels except `__init__.py` and `scip_pb2.py`. Dev installs stay pure Python.
- `setup.sh` — POSIX-`sh` (dash-compatible — no bashisms) binary bootstrapper; sha256-verified pinned downloads.
- `SCIP_COMMIT` / `ZOEKT_COMMIT` (repo root) — must match `SCIP_COMMIT_PIN` / `ZOEKT_COMMIT_PIN` in `setup.sh` (`tests/test_setup_sh.py` enforces; never edit one without the other). `scip` is the `phuongddx/scip` fork (upstream v0.9.0 + the scip#465 relationships fix — without it typeHierarchy is unanswerable).
- `server.json` — MCP registry manifest; version declared twice, both must match `pyproject.toml`.
- `docs/code-standards.md` — canonical conventions doc (482 lines); note its §9 version-guard description is stale — trust `scripts/check_versions.py`. `docs/codebase-summary.md`'s sync-workflow row is similarly stale (sync ships `setup.sh` only).

## Runtime/Tooling Preferences

- **Package manager**: `uv` exclusively. `.python-version` pins 3.12 for local dev; only the `UV_PYTHON` env var overrides it in CI (`--python` flag loses to the pin).
- **Runtime**: Python ≥3.12,<3.15; deps capped below next major (`mcp<2.0.0` — FastMCP import; `protobuf` must stay ≥ the vendored gencode revision).
- **External binaries on PATH** (installed to `~/.jarvis/bin` by `setup.sh`): `scip` + `zoekt-git-index` + `zoekt-webserver` (core; zoekt uses `zoekt-git-index` only — never `zoekt-index`, gitignored content must not enter the index); `scip-python`/`scip-typescript` need npm/Node; `scip-java` needs a JDK (Kotlin repos require Kotlin **2.2.0 exactly**); `scip-swift` is macOS arm64-only (≥0.3.0, needs Xcode); a bash ≥4.4 shim is installed on darwin for scip-java.
- **`tests/test_setup_sh.py` requires `dash`** (`brew install dash`) — its guard test hard-fails if only macOS `/bin/sh` is available.

## Testing & QA

- Framework: pytest; config in `pyproject.toml` (`testpaths = ["tests"]`, marker `integration`). No coverage gate.
- **Mirroring convention**: `tests/test_<module>.py` ↔ `src/jarvis/<module>.py`. Deviations: `server.py` → `test_server_tools.py`; `query.py`'s status fn → `test_index_status.py`; `setup.sh` → `test_setup_sh.py`; `scripts/*.py` → `test_check_*.py` (loaded via `importlib.util.spec_from_file_location` — scripts/ is not a package). `models.py` and `scip_pb2.py` have no dedicated file.
- **Unit tests mock every external boundary**: subprocess via monkeypatched `jarvis.index_cli._run` (dispatch on `step.endswith(" index")`); HTTP via `httpx.MockTransport`; embeddings via `FakeEmbedder` / a `sys.modules` SimpleNamespace fake; missing-extra simulation via `tests/conftest.py`'s `BlockImportFinder` (meta-path blocker — `sys.modules[name] = None` doesn't work under Cython); `Debouncer` tested thread-free with an injectable clock cell. Git is NOT mocked where real git behavior is the contract (throwaway repos in `tmp_path`).
- **Integration tests** (`@pytest.mark.integration` + `skipif` on module-level `shutil.which` checks) copy a mini-repo fixture into `tmp_path` and run real binaries end-to-end.
- **Always isolate the data dir** in new tests: `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` — otherwise you touch the real `~/.jarvis/registry.db`.
- Fixture gems: `tests/fixtures/synthetic_index.py` builds an index with the verbatim expt-convert DDL; `tests/fixtures/scip_encoder.py` encodes real zstd+protobuf blobs via the vendored `scip_pb2` (encoder/decoder share one source of truth).
- CI (`.github/workflows/test.yml`, every push/PR, no path filter): 3-leg matrix (ubuntu py3.12/py3.13, macos py3.13) via `UV_PYTHON`, `uv sync --extra semantic`, `pytest -m "not integration" -rs`, then `check_versions.py`. Release chain: `publish-pypi.yml` (12 compiled wheels via cibuildwheel, no sdist; wheel-content + MCP-handshake smoke; trusted publishing — the workflow filename and empty environment are part of the publisher identity) → `publish-mcp-registry.yml` (workflow_run-triggered, retries PyPI 404s) → `sync-public-distribution.yml` (setup.sh only). `README.md` must keep the literal marker `mcp-name: io.github.jarvis-intelligence/jarvis` (publish gate).
- **Version bumps are lockstep**: `pyproject.toml` + both `server.json` fields + `uv.lock` (via `uv lock`, never hand-edited); plugins bump separately in jarvis-index. Full runbook: `.claude/skills/jarvis-release/SKILL.md`.
- Commits follow Conventional Commits — `feat(scope):`, `fix(scope):`, `docs:`, `chore:`, `test:`; lowercase imperative subjects. PRs against `main`; `uv run pytest -m "not integration"` must be green.
