# Repository Guidelines

Contributor guide for **jarvis** — a local-first code intelligence MCP server (SCIP symbol navigation + Zoekt lexical search + optional semantic vector search). PyPI distribution is `jarvis-mcp`; the import package, both CLIs, and the MCP server are all `jarvis`.

## Project Overview

jarvis gives AI coding assistants code-intelligence tools over a local data dir (`~/.jarvis`, overridable via `JARVIS_DATA_DIR`). Two processes share that dir:

- **Writer** — `jarvis` CLI (`src/jarvis/index_cli.py`): runs per-language SCIP indexers, converts to SQLite, builds Zoekt shards (+ optional LanceDB vectors), publishes atomically.
- **Reader** — `jarvis-server` MCP stdio server (`src/jarvis/server.py`): 9 tools (documentSymbols, goToDefinition, findReferences, callHierarchy, typeHierarchy, getIndexStatus, searchCode, semanticSearch, blastRadius).

Single-tenant by design: `config.py` pins `PROJECT = BRANCH = "_"`; no auth, no multi-tenancy. Languages: TypeScript/TSX, Python, Java, Kotlin, Swift are SCIP-navigable; ~10 additional languages are search-only via Zoekt. Before 0.5.0 this project was called codeintel and shipped as `codeintel-navigation-mcp` (still installable at 0.4.0, unmaintained). Most modules are simplified ports of an internal multi-tenant reference implementation with authorization, project/branch dimensions, and async SQLAlchemy stripped out in favor of stdlib `sqlite3` and the fixed `(project='_', branch='_')` pin — module docstrings name what was dropped and why.

## Architecture & Data Flow

**Indexing pipeline** (`index_repo()` in `index_cli.py`, ~line 966): git gates (`NotAGitRepositoryError`) → scip version check (≥0.9.0; older scip silently writes a schema-valid db with zero chunks/mentions — verified regression) → language resolve (explicit `--language` else `detect_language()` counting extensions over `git ls-files -z`, ties by fixed priority) → language SCIP indexer subprocess (scip-typescript/-python/-java/-swift) to a scratch `.scip` file → `scip expt-convert` to a scratch `index.db` (documents/chunks/global_symbols/mentions/defn_enclosing_ranges schema) → `graph.populate_graph_for_repo()` reads the scratch db *before* publish to extract package deps into `registry.db`'s packages/edges tables → `git config zoekt.name <slug>` pins the repo name, then `zoekt-git-index` runs against the working tree → optional semantic stage (`chunker.chunk_file` + `embeddings.EmbeddingModel` + `semantic.SemanticStore`, LanceDB) → `index_cli._publish_atomically()` write-temp-then-`os.replace()`s the `current` pointer file (the ONLY publish step) → `registry.Registry.upsert()` records the run outcome. On indexer failure, a degrade state machine (opt-in `FALL-0x` decisions) may publish search-only (Zoekt+semantic, no SCIP db, no `current` pointer) instead of leaving nothing — but only when `not published` yet; once the pointer has flipped, later failures are hard failures, never degrading an already-live good index.

**MCP query path**: `server.py`'s `@mcp.tool` functions are thin wrappers around a module-level singleton `QueryService`. Nav tools → `query.QueryService` → `index_reader.IndexConnectionCache.get_connection()` reads the `current` pointer file's **content** (never mtime — NFS-safe) as the cache key, opens the published `index-<sha>.db` `mode=ro&immutable=1`, decodes zstd+protobuf occurrence blobs via `scip_decoder.decode_occurrences()`. Bare-name → full-SCIP-symbol resolution goes through `symbols.resolve()` (rung 1 verbatim, rung 2 dotted-suffix), raising `AmbiguousSymbolError`/`SymbolNotFoundError` that `server.py` renders as `{error, candidates}`. `searchCode` lazily spawns `zoekt-webserver` (`search.ZoektLifecycle`, pidfile-based cross-process singleton, port default 6070) and POSTs to `/api/search`. `semanticSearch` fuses vector hits (`semantic.SemanticStore`/LanceDB), Zoekt lexical hits, and `symbol_search.search_symbols()` via `semantic.reciprocal_rank_fusion()` — Zoekt and symbol signals degrade gracefully (best-effort) if unavailable. `blastRadius` runs a 2-hop bounded BFS (`graph.blast_radius`) over `registry.db`'s packages/edges tables.

**On-disk layout** (`~/.jarvis`): `scip/_/<slug>/_/{current, index-<sha>.db, index-<sha>.metadata.json}` · `.zoekt/<slug>_v*.zoekt*` shards + pid · `lancedb/<slug>.lance/` · `registry.db` (repos + packages + edges) · `cache/scip-swift/<slug>/` · `shims/`.

**Load-bearing invariants** — violating these breaks correctness:

- **Atomic publish**: write `index-<sha>.db` first; only after graph + Zoekt + semantic all succeed, flip the `current` pointer via `os.replace()`. Old versioned files deleted only after the new pointer is live. Never mutate a published `index-<sha>.db` — readers open them `mode=ro&immutable=1`; connection-cache invalidation is by pointer *content*, never mtime.
- **Rebuild-not-accumulate graph**: `populate_graph_for_repo()` clears a repo's outgoing edges (for every package it ever registered) before recomputing; `_retire_scip_artifacts` clears edges *before* rmtree.
- **Isolation seam**: `scip_decoder.py` is the ONLY module importing `scip_pb2`/`zstandard` (enforced by test). `scip_pb2.py` is vendored protoc gencode from scip.proto v0.9.0 — never hand-edit.
- **Role bitmask**: `mentions.role` is a raw SymbolRoles bitmask — always filter `(m.role & ?) != 0`, never equality.
- **LanceDB model identity**: one table holds vectors from exactly one `TableIdentity` (model + revision + prefixes + `CONTENT_FORMAT`); mismatch at index time → full rebuild, never carry-forward vectors.
- **Language detection reads git, not the filesystem** (`git ls-files`, `IGNORED_DIRS` on top). Zoekt indexes git blobs (reflects HEAD) while SCIP reflects the working tree — known asymmetry.
- **Zoekt repo name pinning**: `git config zoekt.name <slug>` must be set or `r:<slug>` silently matches nothing; `jarvis forget` unpins it, leaving no footprint.
- **MCP boundary never raises**: every tool body wraps in broad `except Exception` → `{"error": ...}` dict (deliberate; keep the stdio server alive); status/coverage helpers are documented "never raises" so a derivation bug degrades to null fields.
- **SCIP lines are 0-based**; jarvis surfaces 1-based — converted only in `symbol_search.search_symbols` (the one seam where SCIP coordinates cross into jarvis's 1-based space, matching `chunker.py` and Zoekt).
- **scip-swift invocation stays bare** (no `index` subcommand token) — load-bearing for cross-version compat.

## Key Directories

- `src/jarvis/` — one module per concern:
  - `index_cli.py` (1738 lines, largest) — CLI (`index`/`list`/`status`/`reindex`/`forget`/`watch`) + full pipeline, carries most `D-xx`/`FALL-xx` decision-ID comments.
  - `server.py` — FastMCP stdio server, 9 tools, lazy singletons (`_service`/`_zoekt`/`_graph`).
  - `query.py` — 5 SCIP nav ops (documentSymbols/definitions/references/callHierarchy/typeHierarchy) + getIndexStatus; `index_reader.py` — pointer resolution + `IndexConnectionCache` (thread-safe OrderedDict, max 64, LRU).
  - `symbols.py` — the only module parsing SCIP symbol strings, bare-name resolution ladder; `symbol_search.py` — NL-query → ranked symbol hits (third RRF signal), tokenize + score by matched-token count + kind priority.
  - `search.py` — Zoekt client + `ZoektLifecycle` (never spawn zoekt-webserver elsewhere).
  - `graph.py` — package graph (`extract_package_names`, `GraphStore`, `populate_graph_for_repo`, `blast_radius`) + `registry.py` — repo registry, both SQLite over `registry.db` (`busy_timeout=5000`; must be `close()`d via try/finally).
  - `semantic.py` + `chunker.py` + `embeddings.py` — optional semantic path (LanceDB, tree-sitter AST chunking at function/class boundaries, sentence-transformers `BAAI/bge-m3` default; all imports deferred).
  - `scip_decoder.py` — isolation seam; `scip_pb2.py` — vendored gencode; `config.py` — paths/slugs/pinning, owns `JARVIS_DATA_DIR`/`JARVIS_FALLBACK_SEARCH_ONLY`; `models.py` — frozen result dataclasses + `Freshness` StrEnum; `watch.py` — pure thread-free `Debouncer` (injectable clock) + `should_ignore_path`.
  - `dashboard.py` + `dashboard_assets/` — stdlib localhost console; imports server singletons; never spawns the pipeline in-process.
- `tests/` — mirrors src ~1:1 (`test_<module>.py` ↔ `<module>.py`); `tests/fixtures/` holds a synthetic real-schema index builder, a zstd/protobuf blob encoder, and mini repos (`mini_py_repo`, `mini_swift_repo`, `mini_java_repo`, `mini_xcode_repo`).
- `scripts/` — `check_versions.py` (version lockstep guard), `check_wheel_contents.py` (compiled-wheel guard).
- `docs/` — `code-standards.md` (canonical style contract, 482 lines, 12 documented patterns), `system-architecture.md` (7-layer architecture), `project-overview-pdr.md` (product definition record, scope/non-goals), `codebase-summary.md` (module index + call-graph walkthroughs; note: one internal count inconsistency, "12 test modules" text vs. its own 16-file table — trust the actual `tests/` listing), `project-roadmap.md` (dated changelog of landed phases, includes the Aug-5 version reset to 0.0.1), `journals/` (per-phase decision journals), rendered HTML doc (`index.html` — presentation artifact, not source of truth), plus historical `superpowers/{specs,plans}/` design docs.
- `plans/` — dated design-history dirs (`MMDD-HHMM-<slug>/plan.md` + optional `phase-NN-*.md`) + `reports/` (standalone brainstorm/review/distribution-strategy artifacts). Planning scaffolding, not runtime code.
- `.claude/skills/jarvis-release/` — maintainer-only release runbook (see Runtime/Tooling Preferences).
- `.github/workflows/` — `test.yml` (gate), `setup-smoke.yml`, `build-zoekt.yml`, `build-scip.yml`, `publish-pypi.yml`, `publish-mcp-registry.yml`, `sync-public-distribution.yml`.
- Claude Code / Codex plugin + marketplace: **NOT in this repo** — source of truth is `jarvis-intelligence/jarvis-index`, versioned independently. Any doc/plan directing edits to `plugin/**` here describes pre-2026-08-06 state.

## Development Commands

Uses `uv`; Python ≥3.12, <3.15 (cap matches the cibuildwheel matrix cp312/cp313/cp314; `.python-version` pins 3.12 as the local floor — no sdist exists, so an uncapped resolve would strand `uvx` installs).

```sh
uv sync                                  # base deps
uv sync --extra semantic --extra watch   # optional features (semantic needed for test_semantic.py to run, not to pass)
uv run pytest -m "not integration" -rs   # unit tests — the CI gate
uv run pytest -m integration             # e2e vs real scip/zoekt binaries (skips cleanly if absent)
uv run pytest tests/test_query.py::test_name   # single test
uv run python scripts/check_versions.py  # must print "versions consistent"
python scripts/check_wheel_contents.py dist/*.whl   # release-only: verify a built wheel ships only compiled .so modules
```

CLI surface:

```sh
uv run jarvis index /path/to/repo [--slug name] [--language java|python|swift|typescript] \
                                   [--scheme name] [--semantic-include path] [--search-only]
uv run jarvis list | status <slug> | reindex <slug> | forget <slug>
uv run jarvis watch /path/to/repo [--debounce 5.0] [--scheme name] [--language name]
uv run jarvis dashboard [--port N] [--no-open]  # localhost web console (blocks; default port 6080)
uv run jarvis-server                     # MCP stdio entry point
```

External binaries (not pip deps) come from `setup.sh`: `sh setup.sh` or `sh setup.sh --only scip|zoekt|scip-swift|scip-typescript|scip-python|scip-java|bash-shim|jarvis-mcp` (`--force` to reinstall). No lint/type-check config committed — match surrounding style; don't add ruff to CI without configuring it first.

## Code Conventions & Common Patterns

- **Typing**: Python 3.12+ syntax throughout — `X | None` (never `Optional[T]`), `list[T]`, `dict[K, V]`; `from __future__ import annotations` in every module.
- **Result shapes**: `@dataclass(frozen=True)` value objects across `models.py`, `query.py`, `graph.py`, `chunker.py`, `symbols.py`, `symbol_search.py`, `semantic.py`, `scip_decoder.py` — no mutation after construction. `StrEnum` for closed string vocab (`Freshness`, `DescriptorKind`). Deliberately not Pydantic; `server.py` converts via `dataclasses.asdict()`.
- **Persistence**: raw stdlib `sqlite3`, parameterized queries only (`?` positional in query/registry, `:named` in graph), no ORM. Fully synchronous codebase — do not add async to the query path.
- **Error handling**: two-tier — internal/library code raises typed exceptions (`IndexNotFoundError`, `AmbiguousSymbolError`, `SymbolNotFoundError`, `OccurrenceDecodeError`, `ZoektUnavailableError`, `SemanticExtraMissingError`, `NoSemanticIndexError`, `IndexingError`/`MissingBinaryError`/`SearchPublishedButIncomplete`); the MCP boundary (`server.py`) and best-effort status helpers catch broad `Exception` intentionally and say so in comments ("Broad on purpose"). Index failures persist cause/origin/recovery into the registry. Never silent — every broad catch logs to stderr, returns a structured `{error}` dict, or documents intentional degradation.
- **Naming**: modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`, constants `UPPER_CASE`, private `_`-prefixed. MCP tool functions are named as verb phrases matching their `@mcp.tool(name="camelCase")` decorator (e.g. `def go_to_definition` → tool name `goToDefinition`) — the one deliberate place camelCase leaks in, to match MCP's JS-facing convention.
- **Optional extras pattern**: extras deps (`watch`, `semantic`) are imported lazily inside the functions that need them, so a base install imports every module. Follow this for any new optional dependency.
- **Env overrides are `JARVIS_`-prefixed**, resolved centrally in the owning module (never at call sites): `JARVIS_DATA_DIR`, `JARVIS_FALLBACK_SEARCH_ONLY` (`config.py`); `JARVIS_ZOEKT_PORT`, `JARVIS_ZOEKT_BIN` (`search.py`); `JARVIS_EMBEDDING_MODEL`, `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`, `JARVIS_EMBEDDING_DOC_PREFIX` (`embeddings.py`). Invalid values degrade to defaults with a stderr warning rather than raising.
- **Decision-ID comments**: pervasive in `registry.py`/`index_cli.py` — `D-01`..`D-15` (origin taxonomy, failure recording), `FALL-01`..`FALL-05` (opt-in self-healing fallback), `SEMA-01` (semantic-install decline memory), `WR-xx` (referenced from `docs/superpowers/specs/`). Each points back to a named phase/spec section rather than free-floating rationale — preserve and extend this style when touching that code.
- **Docstrings**: every module states what it is a simplified/ported version of and explicitly names what was dropped (multi-tenancy, async SQLAlchemy, authorization) — document provenance, not just behavior.
- **stdout stays machine-parseable** (`indexed <slug>`); warnings/notes to stderr.

## Important Files

- `pyproject.toml` — dist name `jarvis-mcp`, version currently `0.7.2`, src-layout, extras (`watch`, `semantic`), entry points (`jarvis = jarvis.index_cli:main`, `jarvis-server = jarvis.server:main`), pytest config (`testpaths=["tests"]`, markers `integration` + `interactive_input`), cibuildwheel matrix (cp312/cp313/cp314 × linux x86_64/aarch64 + macOS arm64/x86_64, `JARVIS_COMPILE=1`).
- `setup.py` — Cython gate: compiles every `src/jarvis/*.py` except `__init__.py`/`scip_pb2.py` only under `JARVIS_COMPILE=1` (release CI only); strips `.py` sources from compiled wheels. Dev installs stay pure Python.
- `setup.sh` — POSIX-`sh` (dash-compatible — no bashisms) binary bootstrapper (~1064 lines); sha256-verified pinned downloads from the public `jarvis-intelligence/jarvis-index` repo (never the private repo). `JARVIS_SETUP_SOURCED=1` lets tests source functions without running `main()`.
- `SCIP_COMMIT` / `ZOEKT_COMMIT` (repo root) — must match `SCIP_COMMIT_PIN` / `ZOEKT_COMMIT_PIN` in `setup.sh` (`tests/test_setup_sh.py` enforces; never edit one without the other). `scip` is the `phuongddx/scip` fork (upstream v0.9.0 + the scip#465 relationships fix — without it typeHierarchy is unanswerable). `build-scip.yml`/`build-zoekt.yml` cross-compile 4 platform/arch pairs and publish releases to `jarvis-intelligence/jarvis-index` via `secrets.JARVIS_DIST_TOKEN`, triggered only by changes to their pin files.
- `server.json` — MCP registry manifest; version declared twice (top-level + `packages[0]`), both must match `pyproject.toml`.
- `README.md` — must keep the literal HTML-comment marker `<!-- mcp-name: io.github.jarvis-intelligence/jarvis -->` on line 3 (hard-enforced by `publish-pypi.yml`'s preflight job). Covers quick start, 9-tool reference table, requirements/limits, configuration env vars, and links to all `docs/*.md`.
- `docs/code-standards.md` — canonical conventions doc; trust `scripts/check_versions.py` over any stale prose about the version guard. `docs/codebase-summary.md`'s sync-workflow row is similarly stale (sync ships `setup.sh` only).
- `.claude/skills/jarvis-release/SKILL.md` — the maintainer release runbook: bump 3 files (`pyproject.toml`, `server.json` ×2, `uv.lock` via `uv lock`) → CHANGELOG entry → verify locally → PR → GPG-signed annotated tag → `gh release create` (triggers `publish-pypi.yml`) → confirm `publish-mcp-registry.yml` follows.

## Runtime/Tooling Preferences

- **Package manager**: `uv` exclusively. `.python-version` pins 3.12 for local dev; only the `UV_PYTHON` env var overrides it in CI (`--path` flag loses to the pin; test.yml sets `UV_PYTHON` per matrix leg).
- **Runtime**: Python ≥3.12,<3.15; deps capped below next major (`mcp<2.0.0` — FastMCP import; `protobuf` must stay ≥ the vendored gencode revision).
- **External binaries on PATH** (installed to `~/.jarvis/bin` by `setup.sh`): `scip` + `zoekt-git-index` + `zoekt-webserver` (core; zoekt uses `zoekt-git-index` only — never `zoekt-index`, gitignored content must not enter the index); `scip-python`/`scip-typescript` need npm/Node; `scip-java` needs a JDK (Kotlin repos require Kotlin **2.2.0 exactly**, fixed launcher version `v0.13.1`); `scip-swift` is macOS arm64-only (auto-rolls to the latest release ≥ 0.3.0, needs Xcode); a bash ≥4.4 shim is installed on darwin for scip-java.
- **`tests/test_setup_sh.py` requires `dash`** (`brew install dash`) — its guard test hard-fails if only macOS `/bin/sh` is available.
- **Version lockstep triangle**: `pyproject.toml [project].version` ↔ `server.json.version` ↔ `server.json.packages[0].version` — enforced by `scripts/check_versions.py` (in `test.yml` on every push/PR) and again inline in `publish-mcp-registry.yml`. Plugin versions in `jarvis-intelligence/jarvis-index` are versioned independently and intentionally NOT checked here.

## Testing & QA

- Framework: pytest; config in `pyproject.toml` (`testpaths = ["tests"]`, markers `integration` and `interactive_input`). No coverage gate.
- **Mirroring convention**: `tests/test_<module>.py` ↔ `src/jarvis/<module>.py`. Deviations: `server.py` → `test_server_tools.py`; `query.py`'s status fn → `test_index_status.py`; `setup.sh` → `test_setup_sh.py`; `scripts/*.py` → `test_check_*.py` (loaded via `importlib.util.spec_from_file_location` — scripts/ is not a package). `models.py` and `scip_pb2.py` have no dedicated file.
- **Unit tests mock every external boundary**: subprocess via monkeypatched `jarvis.index_cli._run`/`subprocess.run` (dispatch on `step.endswith(" index")`); HTTP via `httpx.MockTransport` mirroring Zoekt's `/api/search` shape; embeddings via a fake `sentence_transformers` module (`_install_fake`) or `FakeEmbedder`; `Debouncer` tested thread-free with an injectable clock cell (`clock=lambda: clock[0]`); optional-extra absence simulated via `tests/conftest.py`'s `BlockImportFinder` (meta-path finder — `sys.modules[name] = None` doesn't work under Cython-compiled imports). Git is NOT mocked where real git behavior is the contract (throwaway repos in `tmp_path`).
- **`JARVIS_DATA_DIR` isolation is mandatory** in any new test touching config/registry/index state: `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` before constructing anything that might otherwise fall back to the real `~/.jarvis/registry.db`.
- **Integration tests** (`@pytest.mark.integration` + module-level `shutil.which` skip checks) copy a mini-repo fixture (`tests/fixtures/mini_py_repo`, `mini_swift_repo`, `mini_java_repo`, `mini_xcode_repo`) into `tmp_path` and run real binaries end-to-end; they self-skip gracefully even without `-m` filtering.
- Fixture gems: `tests/fixtures/synthetic_index.py` builds an index with the verbatim `scip expt-convert` DDL against a deterministic toy TypeScript repo (covers chunk boundaries, local-symbol exclusion, combined role bitmasks, call-hierarchy pairs); `tests/fixtures/scip_encoder.py` encodes real zstd+protobuf blobs via the vendored `scip_pb2` (encoder/decoder share one source of truth with `scip_decoder.py`).
- CI (`.github/workflows/test.yml`, every push/PR, no path filter): 3-leg matrix (ubuntu py3.12/py3.13, macos py3.13) via `UV_PYTHON`, `uv sync --extra semantic` (so LanceDB-gated `test_semantic.py` actually runs), `pytest -m "not integration" -rs`, then `check_versions.py`. `setup-smoke.yml` (path-filtered to `setup.sh`/`src/jarvis/**`) separately runs `test_setup_sh.py` plus a live binary install/idempotency check, and a macOS-only full Swift-fixture indexing smoke test. Release chain: `publish-pypi.yml` (preflight test run + README marker check → 12 compiled wheels via cibuildwheel, no sdist → wheel-content + live MCP JSON-RPC handshake smoke → trusted publishing) → `publish-mcp-registry.yml` (workflow_run-triggered, retries PyPI 404s) → `sync-public-distribution.yml` (ships `setup.sh` only to the public repo).
- **Version bumps are lockstep**: `pyproject.toml` + both `server.json` fields + `uv.lock` (via `uv lock`, never hand-edited); plugins bump separately in jarvis-index. Full runbook: `.claude/skills/jarvis-release/SKILL.md`.
- Commits follow Conventional Commits — `feat(scope):`, `fix(scope):`, `docs:`, `chore:`, `test:`; lowercase imperative subjects. PRs against `main`; `uv run pytest -m "not integration"` must be green.
