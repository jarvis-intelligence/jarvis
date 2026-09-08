---
focus: arch
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
---

# Codebase Structure

**Analysis Date:** 2026-09-08

## Directory Layout

```
jarvis/                              # repo root (distribution name: jarvis-mcp)
├── src/
│   ├── jarvis/                      # ALL product code — flat single package, 18 modules
│   │   ├── index_cli.py             # Writer CLI + indexing pipeline (1,738 lines)
│   │   ├── server.py                # Reader: FastMCP stdio, 9 tools (508 lines)
│   │   ├── query.py                 # QueryService nav SQL (521 lines)
│   │   ├── symbols.py               # SCIP symbol parsing + bare-name resolution
│   │   ├── symbol_search.py         # NL → symbol-definition hits (RRF 3rd signal)
│   │   ├── index_reader.py          # Pointer resolution + read-only conn cache
│   │   ├── search.py                # Zoekt HTTP client + ZoektLifecycle
│   │   ├── graph.py                 # Package graph store + blast_radius BFS
│   │   ├── registry.py              # repos/packages/edges store, status taxonomy
│   │   ├── semantic.py              # LanceDB SemanticStore + RRF fusion
│   │   ├── chunker.py               # tree-sitter chunking + admission policy
│   │   ├── embeddings.py            # Lazy sentence-transformers wrapper
│   │   ├── scip_decoder.py          # zstd+protobuf decode (sole scip_pb2 seam)
│   │   ├── scip_pb2.py              # Vendored protobuf gencode — never hand-edit
│   │   ├── config.py                # Data dir, slug, path layout
│   │   ├── models.py                # Frozen result dataclasses
│   │   ├── watch.py                 # Pure Debouncer + ignore set
│   │   └── __init__.py              # Empty
│   ├── codeintel/                   # EMPTY legacy leftover (pre-0.5.0 name) — untracked
│   └── jarvis_mcp.egg-info/         # Build metadata (generated)
├── tests/                           # pytest suite, mirrors src/jarvis 1:1
│   ├── fixtures/                    # mini repos + index builders (no binaries needed)
│   ├── conftest.py
│   ├── test_index_cli.py            # Largest suite (~190KB) — pipeline + degrade paths
│   ├── test_server_tools.py         # MCP tool wrappers
│   ├── test_registry.py             # Registry statuses/origins/migrations
│   ├── test_query.py / test_symbols.py / test_symbol_search.py
│   ├── test_search.py / test_graph.py / test_scip_decoder.py
│   ├── test_semantic.py / test_embeddings.py / test_chunker.py
│   ├── test_index_reader.py / test_index_status.py
│   ├── test_watch.py / test_config.py
│   ├── test_setup_sh.py             # Bootstrap-script tests (dash-guarded)
│   └── test_check_versions.py / test_check_wheel_contents.py
├── docs/                            # Published docs (GitHub Pages) + references
│   ├── system-architecture.md       # Canonical architecture narrative
│   ├── code-standards.md / project-roadmap.md / codebase-summary.md
│   ├── project-overview-pdr.md
│   ├── assets/                      # .dot/.svg/.png diagram sources
│   ├── journals/ · superpowers/ · index.html · .nojekyll
├── scripts/
│   ├── check_versions.py            # CI: toolchain version pins vs setup.sh
│   └── check_wheel_contents.py      # CI: wheel manifest audit
├── evals/                           # Skill eval workspaces (setup + use)
├── plans/                           # Archived implementation plan reports (history)
├── .github/workflows/               # test, publish-pypi, publish-mcp-registry,
│                                    # build-scip, build-zoekt, setup-smoke,
│                                    # sync-public-distribution
├── .planning/                       # GSD state (ROADMAP, STATE, milestones) — GSD-only
├── setup.sh                         # POSIX-sh bootstrapper for external binaries
├── pyproject.toml                   # Dist jarvis-mcp; import pkg jarvis; extras
├── setup.py                         # Cython compile shim for release wheels
├── server.json                      # MCP server registration manifest
├── SCIP_COMMIT / ZOEKT_COMMIT       # Pins for CI toolchain builds
├── uv.lock · .python-version (3.12)
├── README.md · CHANGELOG.md · LICENSE · AGENTS.md · CLAUDE.md
└── setup artifacts: .ruff_cache/ .pytest_cache/ .superpowers/ .worktrees/ .claude/
```

On-disk runtime layout (created by the writer, read by the reader; default root
`~/.jarvis`, override `JARVIS_DATA_DIR` — see `src/jarvis/config.py`):

```
~/.jarvis/
├── registry.db                     # repos + packages + edges tables
├── scip/_/<slug>/_/
│   ├── current                     # Pointer file → "index-<sha>.db" (atomic os.replace)
│   ├── index-<sha>.db              # Versioned, immutable SCIP SQLite
│   └── index-<sha>.metadata.json   # commit_sha + published_at sibling
├── .zoekt/                         # <slug>_v*.zoekt shards + zoekt-webserver.pid
├── lancedb/<slug>.lance/           # One semantic table per repo
├── cache/scip-swift/<slug>/        # Per-repo scip-swift incremental cache (D-05)
└── shims/                          # bash shim for scip-java (PATH-prepended)
```

## Directory Purposes

**`src/jarvis/`:**
- Purpose: the entire product — writer CLI, reader server, engines, storage access
- Contains: 18 flat Python modules; deliberately **no subpackages** — the package is one
  concern-list, and module count is the whole map
- Key files: `index_cli.py` (writer), `server.py` (reader), `query.py` (nav),
  `scip_decoder.py` (protobuf/zstd seam)

**`tests/`:**
- Purpose: pytest suite; one `test_<module>.py` per source module, plus bootstrap/wheel audits
- Contains: unit tests (fake indexes via fixtures — no real scip/zoekt binaries needed),
  `integration`-marked tests that do exercise real binaries (marker declared in
  `pyproject.toml [tool.pytest.ini_options]`)
- Key files: `fixtures/synthetic_index.py` (builds a real-schema `index.db` without `scip
  expt-convert`), `fixtures/scip_encoder.py` (encodes occurrence blobs)

**`tests/fixtures/`:**
- Purpose: deterministic mini repos and index builders
- Contains: `mini_py_repo/`, `mini_java_repo/`, `mini_swift_repo/`, `mini_xcode_repo/`,
  `synthetic_index.py`, `scip_encoder.py`
- Convention: a new language support PR adds a `mini_<lang>_repo/` here

**`docs/`:**
- Purpose: GitHub-Pages-published documentation plus the architecture narrative consumed by
  agents (`system-architecture.md` is the canonical reference; keep it in sync with pipeline
  changes)
- Contains: markdown pages, `assets/` Graphviz sources + rendered images, `journals/`

**`scripts/`:**
- Purpose: repo maintenance executed by CI, not shipped
- Contains: `check_versions.py` (toolchain pins vs `setup.sh`), `check_wheel_contents.py`

**`evals/`:**
- Purpose: skill-evaluation workspaces (`codeintel-setup-workspace/`,
  `codeintel-use-workspace/`) with with/without-skill grading runs

**`plans/`:**
- Purpose: archived, dated implementation-plan reports (`MMDD-HHMM-<slug>/`) — read-only
  history, not living docs

**`.planning/`:**
- Purpose: GSD workflow state (`ROADMAP.md`, `STATE.md`, `milestones/`, `codebase/`)
- Generated: partially (this document); Committed: yes
- Rule: only GSD workflows write here

**`.github/workflows/`:**
- Purpose: CI/CD — `test.yml` (unit suite), `publish-pypi.yml` (cibuildwheel matrix cp312–314),
  `publish-mcp-registry.yml`, `build-scip.yml` / `build-zoekt.yml` (Go cross-compiles pinned by
  `SCIP_COMMIT` / `ZOEKT_COMMIT`), `setup-smoke.yml` (`sh -n setup.sh` POSIX guard),
  `sync-public-distribution.yml`

## Key File Locations

**Entry Points:**
- `src/jarvis/index_cli.py`: `main()` → `build_parser()` — the `jarvis` writer CLI
  (`index`, `list`, `status`, `reindex`, `forget`, `watch` subcommands)
- `src/jarvis/server.py`: `main()` → `mcp.run()` — the `jarvis-server` MCP stdio reader
- `pyproject.toml` `[project.scripts]`: `jarvis = "jarvis.index_cli:main"`,
  `jarvis-server = "jarvis.server:main"`
- `server.json`: MCP client registration manifest
- `setup.sh`: bootstrap for external binaries (scip, indexers, zoekt, shims)

**Configuration:**
- `pyproject.toml`: dependencies (`mcp[cli]`, `protobuf`, `zstandard`, `httpx`), extras
  (`watch`, `semantic`), pytest config, cibuildwheel matrix
- `setup.py`: Cython compilation for release wheels (`JARVIS_COMPILE=1`)
- `src/jarvis/config.py`: data-dir/slug/path resolution; env tiers
  (`JARVIS_DATA_DIR`, `JARVIS_FALLBACK_SEARCH_ONLY`)
- `SCIP_COMMIT`, `ZOEKT_COMMIT`: toolchain build pins consumed by CI workflows

**Core Logic:**
- `src/jarvis/index_cli.py`: `index_repo()` (pipeline), `_publish_search_only()`,
  `_publish_atomically()`, `_retire_scip_artifacts()`, degrade gate
- `src/jarvis/query.py`: `QueryService` (nav tools + freshness)
- `src/jarvis/symbols.py`: `parse_symbol()`, `resolve()` (rungs), name-map cache
- `src/jarvis/registry.py`: `Registry`, status/origin constants, `recovery_for()`
- `src/jarvis/graph.py`: `GraphStore`, `populate_graph_for_repo()`, `blast_radius()`
- `src/jarvis/semantic.py`: `SemanticStore`, `table_identity()`, `reciprocal_rank_fusion()`

**Testing:**
- `tests/test_index_cli.py`: pipeline stages, degrade/fallback state machine, watch driver
- `tests/test_server_tools.py`: all 9 MCP tools, error shapes, capability/coverage fields
- `tests/test_registry.py`: statuses, origins, migrations, recovery derivation
- `tests/conftest.py`: shared fixtures

## Naming Conventions

**Files:**
- Source modules: singular snake_case nouns naming the concern — `query.py`, `search.py`,
  `graph.py`, `registry.py`, `chunker.py`. One module per concern; no `utils.py` dumping ground.
- Tests: `test_<module>.py`, exactly 1:1 with `src/jarvis/` modules (plus a few cross-cutting
  suites like `test_setup_sh.py`).
- Fixtures: `mini_<lang>_repo/` directories; `synthetic_index.py` / `scip_encoder.py` builders.

**Directories:**
- Lowercase, no nesting inside `src/jarvis/` (flat by design). Repo-root dirs are singular
  (`docs/`, `scripts/`, `evals/`, `plans/`).

**Symbols:**
- Frozen dataclasses for all result shapes (`src/jarvis/models.py`; engine dataclasses like
  `ScipOccurrence`, `Candidate`, `FusedHit`, `RegisteredRepo`). MCP JSON keys are camelCase
  (`displayName`, `resolvedSymbol`); status sub-objects use snake_case
  (`last_index_run`) — follow the existing tool's shape.
- Private helpers: leading underscore. Test seams are module-level underscore functions so
  tests can monkeypatch them (`_scip_version_output`, `_scip_swift_version_output`,
  `_semantic_extra_missing`, `_at_interactive_tty`, `_install_semantic_extra`).
- Exceptions: domain-specific classes defined next to their concern (`IndexingError` family in
  `index_cli.py`, `SymbolNotFoundError`/`AmbiguousSymbolError` in `symbols.py`,
  `OccurrenceDecodeError` in `scip_decoder.py`, `ZoektUnavailableError` in `search.py`).
- Constants: SCREAMING_SNAKE at module top with a rationale comment
  (`MIN_SCIP_VERSION`, `PARTIAL_STATUS`, `_EXT_PRIORITY`, `CONTENT_FORMAT`).
- Registry status/origin strings are module constants in `src/jarvis/registry.py`
  (`SEARCH_ONLY_STATUS`, `DEGRADED_STATUS`, `ORIGIN_*`) — import them, never inline literals.

**Design-rationale comments:** module docstrings and block comments carry the *why* (invariant
reasoning, upstream issue links, decision IDs like FALL-02/D-15/WR-02). Preserve and extend
them; they are the documentation of record for invariants.

## Where to Add New Code

**New language indexer:**
- Add one row to `_LANGUAGE_INDEXERS` and, if a new extension, `_EXT_PRIORITY` in
  `src/jarvis/index_cli.py` — `_INDEXER_BY_LANGUAGE` (the `--language` validator) is derived
  and updates itself.
- Fixture: `tests/fixtures/mini_<lang>_repo/`; tests in `tests/test_index_cli.py`.

**New MCP tool:**
- Engine method first (e.g. `QueryService.<method>` in `src/jarvis/query.py`); result shape as a
  frozen dataclass in `src/jarvis/models.py` if new.
- Thin `@mcp.tool` wrapper in `src/jarvis/server.py`: unpack → call → broad `except Exception` →
  `{"error": ...}`; serialize with `_json_safe(asdict(...))`; update the tool-count docstring
  ("Registers 9 tools") and `docs/system-architecture.md`.
- Tests: `tests/test_server_tools.py` + `tests/test_query.py`.

**New registry column / status:**
- `_SCHEMA` + an `_ensure_column()` migration + `RegisteredRepo`/`_row_to_repo()` in
  `src/jarvis/registry.py`; additive only (old rows must keep reading). Tests in
  `tests/test_registry.py`.

**New persisted CLI flag:**
- Follow the `_resolve_<flag>()` pattern in `src/jarvis/index_cli.py`: `None` means "leave the
  persisted value alone"; persist only the explicit CLI value; add the argparse argument in
  `build_parser()`.

**New engine module:**
- Flat file in `src/jarvis/` (no subpackage); wire a lazy singleton accessor in
  `src/jarvis/server.py` if the reader needs it; defer optional-extra imports inside functions;
  add `test_<module>.py`.

**New decode capability over SCIP blobs:**
- Inside `src/jarvis/scip_decoder.py` only — it is the sole `scip_pb2`/`zstandard` import seam.

**Utilities / shared helpers:**
- There is no utils module; put a helper in the module that owns the concern. Cross-engine
  shapes (e.g. `FreshnessSnapshot`) live in `src/jarvis/query.py` and are imported by peers
  (`graph.py` does this).

## Special Directories

**`src/codeintel/`:**
- Purpose: empty leftover from the pre-0.5.0 package name
- Generated: no · Committed: no (untracked; safe to ignore)

**`src/jarvis_mcp.egg-info/`:**
- Purpose: setuptools build metadata
- Generated: yes · Committed: no

**`.planning/`:**
- Purpose: GSD workflow state including this map
- Generated: by GSD commands · Committed: yes · Rule: edit only via GSD workflows

**`plans/`:**
- Purpose: archived dated plan reports — append-only history
- Generated: by past planning sessions · Committed: yes

**`tests/fixtures/`:**
- Purpose: deterministic mini repos and synthetic index builders
- Generated: no · Committed: yes

**Caches (`.ruff_cache/`, `.pytest_cache/`, `__pycache__/`):**
- Generated tooling artifacts, gitignored — never edit

---

*Structure analysis: 2026-09-08*
