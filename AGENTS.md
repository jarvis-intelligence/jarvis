# Repository Guidelines

Contributor guide for **jarvis** — a local-first code intelligence MCP server
(SCIP navigation + Zoekt search). The distribution is published as
`jarvis-mcp`; the import package, both CLIs, and the MCP server
are all `jarvis`.

## Project Structure & Module Organization

`src/jarvis/` — one module per concern:

- `index_cli.py` — the `jarvis` CLI (subcommands `index`/`list`/`status`/`reindex`/`forget`/`watch`) and the indexing pipeline: detect language → run the matching SCIP indexer → `scip expt-convert` → populate graph → `zoekt-index` → atomic pointer-swap publish.
- `server.py` — MCP stdio server (FastMCP) wrapping `QueryService` / `ZoektLifecycle` / `GraphStore`; registers 9 tools.
- `query.py` + `index_reader.py` — SCIP navigation (go-to-definition, find-references, call/type hierarchy, document symbols) as raw SQL against the `scip expt-convert` schema. `index_reader.py` is the read-only SQLite connection cache keyed on the vendored 3-tuple `(project, repo, branch)`.
- `search.py` — `searchCode` via an `httpx` client to a lazily-spawned `zoekt-webserver`; `ZoektLifecycle` owns the pidfile/health-check/exit-kill — never spawn the webserver elsewhere.
- `graph.py` + `registry.py` — two SQLite stores over `~/.jarvis/registry.db`. `graph.py` is the package dependency graph (`packages`/`edges`) driving `blastRadius` (2-hop BFS); `registry.py` is the repo registry (`repos` table).
- `semantic.py` + `chunker.py` + `embeddings.py` — optional semantic path: tree-sitter chunking → sentence-transformers embeddings → per-repo LanceDB table; `semanticSearch` fuses vector hits with Zoekt hits via reciprocal rank fusion.
- `scip_decoder.py` — **isolation seam**, the only module that imports `scip_pb2` or `zstandard`.
- `scip_pb2.py` — vendored gencode (regenerated from `scip.proto` at tag v0.9.0); **do not hand-edit**.
- `config.py` — data-dir / repo-slug / index-path resolution; single-tenant pinning (`PROJECT = "_"`, `BRANCH = "_"`).
- `models.py` — frozen dataclasses for nav-tool result shapes.
- `watch.py` — `Debouncer` (pure, thread-free, fake-clock testable) + `should_ignore_path`.

`tests/` — 17 files mirroring source modules ~1:1 (`test_<module>.py` ↔ `<module>.py`). Only `models.py`, `scip_pb2.py`, and `server.py` have no dedicated test file (`server` is covered by `tests/test_server_tools.py`). `tests/fixtures/` holds real SCIP/Zoekt blobs and mini per-language repos (`mini_py_repo`, `mini_swift_repo`, `mini_java_repo`) used by integration tests.

Top-level:

- `setup.sh` — POSIX-`sh` dependency bootstrapper; pins `scip` (fork build, `SCIP_COMMIT_PIN`), `zoekt` (`ZOEKT_COMMIT_PIN`), `scip-typescript`, `scip-python`, `scip-java` (incl. `SCIP_JAVA_KOTLIN`), `scip-swift`.
- `ZOEKT_COMMIT` — the zoekt commit CI builds from; must match `ZOEKT_COMMIT_PIN` in `setup.sh`.
- `SCIP_COMMIT` — the phuongddx/scip fork commit CI builds from (upstream v0.9.0 + the scip#465 relationships fix); must match `SCIP_COMMIT_PIN` in `setup.sh`.
- `scripts/check_versions.py` — version-consistency guard across `pyproject.toml`, `server.json`, and the plugin manifest.
- `server.json` — MCP registry manifest (two version fields).
- `pyproject.toml` — distribution `jarvis-mcp`, `setuptools` + Cython backend (`[build-system]`), compiled only under `JARVIS_COMPILE=1`; local dev and editable installs stay pure Python.
- `setup.py` — Cython build glue: overrides `build_py.find_package_modules` to exclude `.py` sources whose `.so` counterpart was just built, so a compiled release wheel ships no readable source alongside its extensions.
- `scripts/check_wheel_contents.py` — asserts a built wheel ships compiled `jarvis/*.so` modules and no leaked `.py`/`.pyx`/`.c` source; run in CI before every PyPI upload.
- `docs/`, `evals/`, `plans/` — architecture, code standards, roadmap, eval harness, and design plans.
- Claude Code plugin + marketplace definition: NOT in this repo — source of truth is `jarvis-intelligence/jarvis-index` (`plugin/` + `.claude-plugin/` there, edited directly, versioned independently).
- `.claude/skills/jarvis-release` — maintainer-only release skill (never ships to end users).
- `.github/workflows/test.yml` — unit-test CI gate.

## Build, Test, and Development Commands

The project uses `uv` (Python ≥3.12, `setuptools` + Cython build backend; compilation only under `JARVIS_COMPILE=1`, which release CI sets and dev never does):

- `uv sync` — install base deps. `uv sync --extra semantic --extra watch` for optional features.
- `uv run pytest` — run all tests.
- `uv run pytest -m "not integration" -rs` — unit tests only (what CI gates; `-rs` surfaces skip reasons).
- `uv run pytest -m integration` — end-to-end tests calling real `scip`/`scip-python`/`scip-java`/`scip-swift`/`zoekt` binaries; skip cleanly if absent.
- `uv run pytest tests/test_query.py::test_name` — single test.
- `uv run jarvis index /path/to/repo [--slug name] [--language name] [--scheme name] [--semantic-include path] [--search-only]` — index a repo.
- `uv run jarvis list` / `status <slug>` / `reindex <slug>` / `forget <slug>` — registry operations.
- `uv run jarvis watch /path/to/repo [--debounce N] [--scheme name] [--language name] [--semantic-include path]` — foreground auto-reindex on file changes.
- `uv run jarvis-server` — MCP stdio entry point.
- `uv run python scripts/check_versions.py` — verify version sources agree.

Optional extras (`watch`, `semantic`) must be installed via `--extra <name>`; their imports are always deferred inside the functions that need them, so a base install can still import every module in `src/jarvis/`. Required on `PATH` for anything beyond unit tests: one language indexer per repo (`scip-typescript` / `scip-python` / `scip-java` / `scip-swift`), `scip` (for `scip expt-convert`), and `zoekt-index` / `zoekt-webserver`.

## MCP Tools

`server.py` registers nine tools. Each nav tool takes `repo` (the slug from `jarvis index`) plus a tool-specific `symbol` or `path`; all tools catch exceptions broadly and return `{"error": "..."}` rather than raising, to keep the stdio server alive:

- `documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy` — SCIP navigation.
- `getIndexStatus` — index presence + freshness (compares the published commit to `git rev-parse HEAD` when `repo_path` is given).
- `searchCode` — Zoekt lexical search (lazy-started webserver, `repo` applied as an `r:` filter).
- `semanticSearch` — vector + Zoekt hybrid via reciprocal rank fusion (requires the `semantic` extra).
- `blastRadius` — 2-hop package-dependency BFS; freshness always reported `unknown`.

## Coding Style & Naming Conventions

- Python 3.12+, modern type-hint syntax throughout: `T | None` (never `Optional[T]`), `list[T]`, `dict[K, V]`.
- Result types are **frozen dataclasses** (no Pydantic). MCP tools return dicts via `dataclasses.asdict()`.
- Raw `sqlite3` only (no ORM). Always use parameterized queries.
- `scip_pb2` and `zstandard` imports are confined to `scip_decoder.py` — never import them elsewhere.
- `scip_pb2.py` is vendored gencode — never hand-edit; regenerate from `scip.proto`.
- Naming: modules `snake_case`; classes `PascalCase`; functions/vars `snake_case`; constants `UPPER_CASE`; private members `_`-prefixed.
- Comments document *why*, not *what*; docstrings carry rationale (see `config.py`, `models.py` for the pattern).
- Environment variable overrides are `JARVIS_`-prefixed: `JARVIS_DATA_DIR`, `JARVIS_ZOEKT_BIN`, `JARVIS_EMBEDDING_MODEL`, `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`, `JARVIS_EMBEDDING_DOC_PREFIX`.
- No lint/type-check config is currently committed; keep style consistent with surrounding code.

## Architecture Invariants (Load-Bearing)

- **Atomic publish:** write the new `index-<sha>.db`, and only once graph + Zoekt both succeed, flip the `current` pointer via `os.replace()`. Never mutate a published `index-<sha>.db` in place — queries open them `mode=ro&immutable=1`.
- **Rebuild-not-accumulate graph:** `populate_graph_for_repo()` deletes a repo's outgoing edges before recomputing, so retracted dependencies don't linger.
- **Single-tenant hardcoding:** `config.py` pins `PROJECT = "_"` and `BRANCH = "_"`; the on-disk `scip/_/<slug>/_/` shape is an artifact of reusing the vendored `IndexConnectionCache` unchanged, not a real multi-tenancy feature.
- **Language detection reads git, not the filesystem:** `detect_language()` counts extensions across `git ls-files` (not an `rglob` walk, which counts gitignored scratch dirs); ties broken by fixed priority `.ts→.tsx→.py→.java→.kt→.swift`; `IGNORED_DIRS` still applied on top. A non-git path raises `NotAGitRepositoryError`. Override with `--language <name>` on the first `jarvis index`.
- **Model-identity rule:** a LanceDB table only ever holds vectors from one model+revision.

## Testing Guidelines

- Framework: `pytest` (`testpaths = ["tests"]`).
- Test files mirror source modules (`test_<module>.py` ↔ `<module>.py`); only `models.py`, `scip_pb2.py`, and `server.py` lack dedicated files.
- Unit tests mock subprocess/file I/O/HTTP; integration tests (`@pytest.mark.integration`, concentrated in `test_index_cli.py` and `test_chunker.py`) call real binaries against `tests/fixtures/`. Semantic tests use a fake in-process model — nothing is downloaded at run time.
- Before adding any file that declares a version, register it in the version-consistency guard (`scripts/check_versions.py`, asserted by `tests/test_check_versions.py`).

## CI

`.github/workflows/test.yml` runs on every push/PR with **no path filter** (it's the gate that must run on every change). Matrix: ubuntu-latest py3.12, ubuntu-latest py3.13, macos-latest py3.13. It installs `--extra semantic` (without it, `test_semantic.py` silently skips and the suite still looks green), sets `UV_PYTHON` per leg (`.python-version` would otherwise override), runs `pytest -m "not integration" -rs`, and runs `scripts/check_versions.py`. Unit-only by design — `integration` tests need real binaries handled by `setup-smoke.yml` and `build-zoekt.yml`.

## Commit & Pull Request Guidelines

Follow **Conventional Commits** as seen in history — `feat(scope):`, `fix(scope):`, `docs:`, `chore:`, `test:`. Keep the subject line lowercase, imperative.

- Open PRs against `main`. Ensure `uv run pytest -m "not integration"` is green.
- `ZOEKT_COMMIT`/`ZOEKT_COMMIT_PIN` and `SCIP_COMMIT`/`SCIP_COMMIT_PIN` (file ↔ `setup.sh`) must each stay in sync (`tests/test_setup_sh.py` enforces both) — never edit one without the other.
- Bumping the release version touches three files in lockstep: `pyproject.toml`, `server.json` (two fields), plus `uv.lock` via `uv lock`. The Claude Code and Codex plugins version separately in jarvis-index. See `.claude/skills/jarvis-release/SKILL.md` for the full release runbook.
