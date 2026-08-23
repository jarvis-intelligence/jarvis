---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# Codebase Structure

**Analysis Date:** 2026-08-21

## Directory Layout

```
jarvis/
├── src/
│   └── jarvis/              # Main package — one module per concern
│       ├── __init__.py      # Empty
│       ├── index_cli.py     # CLI + indexing pipeline (largest file)
│       ├── server.py        # MCP stdio server (FastMCP, 9 tools)
│       ├── query.py         # SCIP navigation (SQL against scip schema)
│       ├── index_reader.py  # Read-only SQLite connection cache
│       ├── search.py        # Zoekt search client + ZoektLifecycle
│       ├── semantic.py      # LanceDB vector storage + hybrid RRF search
│       ├── chunker.py       # tree-sitter AST chunking for semantic index
│       ├── embeddings.py    # sentence-transformers model wrapper
│       ├── symbol_search.py # NL query → ranked SCIP symbol hits
│       ├── graph.py         # Package dependency graph (packages + edges)
│       ├── registry.py      # SQLite registry of indexed repos
│       ├── scip_decoder.py  # Isolation seam: zstd + protobuf decode
│       ├── scip_pb2.py      # Vendored gencode (scip.proto v0.9.0)
│       ├── symbols.py       # SCIP symbol-string parsing + resolution
│       ├── config.py        # Data-dir / slug / path resolution
│       ├── models.py        # Frozen dataclasses for tool result shapes
│       └── watch.py         # Debouncer + path-ignore for auto-reindex
├── tests/                   # Unit + integration tests (mirrors src/jarvis/)
│   ├── conftest.py          # Shared fixtures
│   ├── test_index_cli.py    # Indexing pipeline (largest test file)
│   ├── test_server_tools.py # MCP tool integration
│   ├── test_query.py        # SCIP navigation queries
│   ├── test_index_reader.py # Connection cache + pointer resolution
│   ├── test_search.py       # Zoekt search client
│   ├── test_semantic.py     # Semantic search + RRF fusion
│   ├── test_embeddings.py   # Embedding model wrapper
│   ├── test_chunker.py      # Tree-sitter chunking (has integration marks)
│   ├── test_symbol_search.py# NL symbol search
│   ├── test_graph.py        # Package graph + blast radius
│   ├── test_registry.py     # Registry CRUD
│   ├── test_scip_decoder.py # Blob decode + symbol package extraction
│   ├── test_symbols.py      # Symbol parsing + resolution
│   ├── test_config.py       # Config / slug resolution
│   ├── test_watch.py        # Debouncer
│   ├── test_setup_sh.py     # Version-pin consistency (SCIP_COMMIT ↔ setup.sh)
│   ├── test_check_versions.py # pyproject.toml/server.json version guard
│   ├── test_check_wheel_contents.py # Compiled wheel integrity
│   └── fixtures/            # Mini repos + synthetic index builder
│       ├── mini_py_repo/    # Minimal Python repo for integration tests
│       ├── mini_swift_repo/ # Minimal Swift repo for integration tests
│       ├── mini_java_repo/  # Minimal Java/Gradle repo for integration tests
│       ├── synthetic_index.py # Builds fake SCIP index.db for unit tests
│       └── scip_encoder.py  # Helper: encodes SCIP protobuf blobs
├── scripts/
│   ├── check_versions.py   # Version-consistency guard (pyproject/server.json)
│   └── check_wheel_contents.py # Asserts wheel ships .so, not .py/.pyx/.c
├── docs/                    # Architecture docs, roadmap, code standards
├── evals/                   # Eval harnesses (Claude Code workspace evals)
│   ├── codeintel-setup-workspace/
│   └── codeintel-use-workspace/
├── plans/                   # Design plans + execution reports
│   ├── reports/
│   └── <timestamp>-<name>/  # Per-plan directories with plan.md
├── .github/workflows/       # CI
├── setup.sh                 # POSIX dependency bootstrapper (pins SCIP/Zoekt)
├── setup.py                 # Cython build glue (exclude .py when .so exists)
├── pyproject.toml           # Distribution metadata, deps, build config
├── server.json              # MCP registry manifest
├── ZOEKT_COMMIT             # Zoekt commit pin (must match setup.sh)
├── SCIP_COMMIT              # SCIP fork commit pin (must match setup.sh)
├── uv.lock                  # Lockfile
├── CLAUDE.md                 # Claude Code context file
├── AGENTS.md                 # Repository guidelines (contributor guide)
└── CHANGELOG.md             # Release changelog
```

## Directory Purposes

**`src/jarvis/`:**
- Purpose: The entire application — one Python module per concern, flat (no sub-packages)
- Contains: 18 `.py` files (16 source + `__init__.py` + vendored `scip_pb2.py`)
- Key files: `index_cli.py` (1189 LOC, the indexing pipeline + CLI), `query.py` (521 LOC, SCIP navigation), `semantic.py` (376 LOC, hybrid search)

**`tests/`:**
- Purpose: Unit and integration tests mirroring source modules ~1:1
- Contains: 17 test files + `conftest.py` + `fixtures/` directory
- Key files: `test_index_cli.py` (largest), `test_server_tools.py` (MCP tool coverage), `fixtures/synthetic_index.py` (test index builder)

**`tests/fixtures/`:**
- Purpose: Mini per-language repos for integration tests + synthetic SCIP index builder
- Contains: `mini_py_repo/`, `mini_swift_repo/`, `mini_java_repo/` (real repo structures), `synthetic_index.py` (builds fake `index.db` in-memory for unit tests), `scip_encoder.py` (protobuf helper)
- Key files: `synthetic_index.py` — the workhorse that lets unit tests run without real SCIP binaries

**`scripts/`:**
- Purpose: Build-time integrity checks
- Contains: Version-consistency guard, compiled-wheel assertion
- Key files: `check_versions.py` (asserts pyproject.toml, server.json, plugin manifest agree)

**`docs/`:**
- Purpose: Architecture documentation and project planning materials
- Contains: HTML architecture diagrams, markdown docs (roadmap, code standards, system architecture)

**`evals/`:**
- Purpose: Claude Code workspace evaluation harnesses
- Contains: `codeintel-setup-workspace/`, `codeintel-use-workspace/` — each with iteration directories and eval definitions

**`plans/`:**
- Purpose: Historical design plans and execution reports
- Contains: Timestamped plan directories (`<timestamp>-<name>/plan.md`) and `reports/` subdirectory
- Generated: No (authored during development)
- Committed: Yes

## Key File Locations

**Entry Points:**
- `src/jarvis/index_cli.py`: CLI entry point — `jarvis index|list|status|reindex|forget|watch`
- `src/jarvis/server.py`: MCP server entry point — `jarvis-server` stdio

**Configuration:**
- `pyproject.toml`: Distribution metadata, dependencies, build system, entry points
- `server.json`: MCP registry manifest (two version fields)
- `src/jarvis/config.py`: Runtime path resolution, single-tenant constants
- `setup.sh`: Dependency bootstrapper (SCIP, Zoekt, language indexers)

**Core Logic:**
- `src/jarvis/index_cli.py`: Full indexing pipeline (detect → SCIP → convert → graph → zoekt → semantic → publish)
- `src/jarvis/query.py`: SCIP navigation queries (raw SQL against scip schema)
- `src/jarvis/search.py`: Zoekt search client and process lifecycle
- `src/jarvis/semantic.py`: Hybrid RRF search (vector + Zoekt + SCIP symbols)
- `src/jarvis/graph.py`: Package dependency graph and blast radius

**Testing:**
- `tests/test_index_cli.py`: Indexing pipeline tests
- `tests/test_query.py`: Navigation query tests
- `tests/test_search.py`: Zoekt search tests
- `tests/test_semantic.py`: Semantic search + RRF tests
- `tests/fixtures/synthetic_index.py`: Shared test index builder

**Version Pins:**
- `ZOEKT_COMMIT`: Zoekt commit SHA (top-level file)
- `SCIP_COMMIT`: SCIP fork commit SHA (top-level file)
- `setup.sh`: Contains `ZOEKT_COMMIT_PIN` and `SCIP_COMMIT_PIN` (must match top-level files)

## Naming Conventions

**Files:**
- Source modules: `snake_case.py` — one word or compound with underscores (e.g., `index_cli.py`, `scip_decoder.py`, `symbol_search.py`)
- Test files: `test_<module>.py` — mirrors the source module name (e.g., `test_query.py` ↔ `query.py`)
- Vendored gencode: `scip_pb2.py` — follows protobuf naming convention
- Top-level pins: `UPPER_CASE` with underscore separator (e.g., `ZOEKT_COMMIT`, `SCIP_COMMIT`)

**Directories:**
- Source: flat under `src/jarvis/` — no sub-packages
- Tests: flat under `tests/` — no sub-packages; `tests/fixtures/` for shared test data
- Mini repos: `mini_<language>_repo/` (e.g., `mini_py_repo/`, `mini_swift_repo/`)
- Plans: `<timestamp>-<short-name>/` (e.g., `0805-2346-scip-boosted-retrieval/`)

**Symbols (within code):**
- Modules: `snake_case`
- Classes: `PascalCase` (e.g., `QueryService`, `ZoektLifecycle`, `GraphStore`)
- Functions/variables: `snake_case` (e.g., `detect_language`, `populate_graph_for_repo`)
- Constants: `UPPER_CASE` (e.g., `PROJECT`, `BRANCH`, `MAX_TOKENS`, `RRF_K`)
- Private members: `_`-prefixed (e.g., `_query_service`, `_publish_atomically`, `_IGNORED_DIRS`)
- Environment overrides: `JARVIS_`-prefixed (e.g., `JARVIS_DATA_DIR`, `JARVIS_EMBEDDING_MODEL`)

## Where to Add New Code

**New MCP tool:**
- Tool registration: add `@mcp.tool(name="...")` function in `src/jarvis/server.py`
- Query logic: add method to `QueryService` in `src/jarvis/query.py` (if SCIP-based) or a new module in `src/jarvis/`
- Result shapes: add frozen dataclass to `src/jarvis/models.py`
- Tests: add test function in `tests/test_server_tools.py`

**New navigation query type:**
- Implementation: add method to `QueryService` in `src/jarvis/query.py` with raw SQL against the SCIP schema
- If new blob decoding is needed: add to `src/jarvis/scip_decoder.py` (the isolation seam)
- Tests: add to `tests/test_query.py`

**New language indexer support:**
- Language map: add entry to `_LANGUAGE_INDEXERS` in `src/jarvis/index_cli.py`
- Extension priority: add to `_EXT_PRIORITY` in `src/jarvis/index_cli.py`
- Mini repo: add `tests/fixtures/mini_<lang>_repo/` for integration tests
- Bootstrapper: add download/build block to `setup.sh`

**New search signal (for semantic RRF):**
- Retrieval function: add to `src/jarvis/semantic.py` or a new module
- Integration into RRF: add signal to `reciprocal_rank_fusion()` in `src/jarvis/semantic.py`
- Tests: add to `tests/test_semantic.py`

**New component/module:**
- Implementation: add `<module>.py` to `src/jarvis/` (flat, no sub-package)
- Tests: add `tests/test_<module>.py`

**Utilities:**
- Shared helpers: add to the most relevant existing module in `src/jarvis/` (no separate `utils.py` — the codebase avoids it)
- Test helpers: add to `tests/conftest.py` or `tests/fixtures/`

## Special Directories

**`src/jarvis/scip_pb2.py`:**
- Purpose: Vendored gencode from `scip.proto` at SCIP v0.9.0
- Generated: Yes (regenerated from proto, never hand-edited)
- Committed: Yes

**`tests/fixtures/`:**
- Purpose: Mini repos and synthetic index builder for tests
- Generated: No (authored)
- Committed: Yes

**`plans/`:**
- Purpose: Historical design plans and execution reports
- Generated: No (authored during development)
- Committed: Yes

**`evals/`:**
- Purpose: Claude Code workspace evaluation harnesses
- Generated: Partially (iteration results generated during eval runs)
- Committed: Yes

**`docs/`:**
- Purpose: Architecture documentation and planning materials
- Generated: Partially (HTML diagrams generated, markdown authored)
- Committed: Yes

**`.planning/`:**
- Purpose: GSD planning artifacts (PROJECT.md, ROADMAP.md, REQUIREMENTS.md, codebase maps)
- Generated: Yes (by GSD workflow commands)
- Committed: Sometimes (varies by workflow)

---
*Structure analysis: 2026-08-21*
