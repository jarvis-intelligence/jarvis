# jarvis: Codebase Summary

## Directory Structure

```
jarvis/
├── src/jarvis/           # Core library (16 files)
├── tests/                   # Test suite (16 files)
├── docs/                    # Documentation
├── docs/assets/             # Architecture diagrams
├── plans/                   # Implementation plans
├── plugin/                  # Claude Code plugin (marketplace + MCP registration)
│   ├── .claude-plugin/
│   │   └── plugin.json      # Plugin manifest (name, version, description)
│   ├── .mcp.json            # MCP server registration for plugin installs
│   └── skills/              # Plugin skills (jarvis-setup, jarvis-use, jarvis-issues)
├── .claude-plugin/
│   └── marketplace.json     # Plugin marketplace declaration (root)
├── .github/workflows/       # CI: build-zoekt.yml, publish-pypi.yml, publish-mcp-registry.yml, setup-smoke.yml, test.yml
├── scripts/                 # Utilities
│   └── check_versions.py    # Version consistency guard (4 files kept in sync)
├── setup.sh                 # Dependency bootstrapper (scip, zoekt, indexers)
├── ZOEKT_COMMIT             # Pinned upstream sourcegraph/zoekt commit
├── pyproject.toml           # uv-managed project config
└── README.md                # User-facing getting started
```

## Core Modules (`src/jarvis/`)

### Initialization & Configuration

| File | Lines | Purpose | Key Exports |
|------|-------|---------|-------------|
| `__init__.py` | 2 | Stub entry; unused (`main()` prints "Hello from jarvis!") | — |
| `config.py` | 66 | Data-dir + repo-slug resolution; single-tenant layout; shared `IGNORED_DIRS` constant; `lancedb_dir()` for the semantic vector store | `data_dir()`, `repo_slug()`, `index_dir()`, `lancedb_dir()`, `IGNORED_DIRS`, `PROJECT`, `BRANCH`, `DEFAULT_DATA_DIR` |
| `models.py` | 64 | Frozen dataclasses for nav results (Position, Range, Location, SymbolInfo, etc.) + Freshness StrEnum | `Position`, `Range`, `Location`, `SymbolInfo`, `DocumentSymbolEntry`, `CallHierarchyEntry`, `TypeHierarchyEntry`, `Freshness` |

### Index & Search

| File | Lines | Purpose | Key Exports |
|------|-------|---------|-------------|
| `index_reader.py` | 160 | Vendored filestore reader from SCIP source; `IndexConnectionCache` — thread-safe, size-bounded cache of read-only immutable SQLite connections keyed by `(project, repo, branch, pointer_content)`, NFS-safe pointer invalidation | `IndexConnectionCache`, current pointer file handling |
| `scip_pb2.py` | 119 | Generated protobuf from scip.proto v0.9.0 — regenerated from v0.7.0 because v0.7.0 lacked the `typed_range` oneof that `scip-swift` requires (do not edit, vendored codegen) | scip.Document, scip.SymbolInformation, scip.Occurrence, scip.Relationship |
| `scip_decoder.py` | 326 | SCIP blob decoder (zstd+protobuf); isolation seam for protobuf dependency | `scip_range_to_positions()`, `kind_name()`, `parse_symbol_package()`, decode SCIP occurrences + relationships |
| `query.py` | 462 | QueryService: 5 SCIP nav ops + `getIndexStatus` via raw SQL against `scip expt-convert` schema | `QueryService`, `FreshnessSnapshot`, nav result builders |
| `search.py` | 183 | `searchCode` backend via real httpx client to zoekt-webserver; `ZoektLifecycle` lazy-spawns `zoekt-webserver -rpc`, pidfile-tracked | `searchCode()`, `ZoektLifecycle` |
| `chunker.py` | 394 | Tree-sitter AST chunking into function/class-sized chunks (256-512 token target), fixed-window fallback for unparseable languages, content-hash dedup; admission filters (generated-file banner/long-line, `.gitignore` via batched `git check-ignore`, 1 MB size cap, `--semantic-include` escape hatch); appends a `# file:`/`# in class:` context header to every chunk as a final pass | `chunk_file()`, `Chunk`, `hash_file()`, `language_for()`, `skip_reason()`, `iter_source_files()`, `gitignored()`, `oversized_file_reason()`, `CONTENT_FORMAT` |
| `embeddings.py` | 149 | Lazy-loaded self-hosted embedding model wrapper (`BAAI/bge-m3`, 1024-dim, pinned revision), L2-normalized vectors; model-aware query/doc instruction prefixes (`MODEL_PREFIXES`, env-overridable); `SemanticExtraMissingError` for clean skip when the `semantic` extra isn't installed | `EmbeddingModel`, `default_model()`, `SemanticExtraMissingError` |
| `semantic.py` | 340 | `SemanticStore` (one LanceDB table per repo), `index_semantic()` (chunk → dedup → embed → carry-over unchanged files → atomic overwrite), `reciprocal_rank_fusion()` (k=60), `semantic_search()`; `TableIdentity` (model + revision + prefixes + content format) gates carry-forward reuse | `SemanticStore`, `index_semantic()`, `semantic_search()`, `reciprocal_rank_fusion()`, `TableIdentity`, `TokenStats` |

### Graph & Registry

| File | Lines | Purpose | Key Exports |
|------|-------|---------|-------------|
| `graph.py` | 363 | Package dependency graph: sqlite3 CRUD on `packages`/`edges` tables in registry.db, `populate_graph_for_repo()` (rebuild-not-accumulate), `blast_radius()` 2-hop BFS | `GraphStore`, `extract_package_names()`, `populate_graph_for_repo()`, `blast_radius()` |
| `registry.py` | 256 | sqlite3 CRUD on `repos` table: slug/path/language/commit_sha/last_indexed/status (indexed/indexing/failed/partial/search-only), plus `scheme_override`, `semantic_include`, and `language_override` columns for persisting Xcode scheme, force-included semantic paths, and `--language` overrides across reindex runs, and nullable `semantic_indexed_at` column (survives failed semantic reindexes); `SEARCH_ONLY_STATUS` constant for the new search-only status value | `Registry`, repo table operations, `mark_semantic_indexed()`, `SEARCH_ONLY_STATUS`, idempotent migrations |

### Server & CLI

| File | Lines | Purpose | Key Exports |
|------|-------|---------|-------------|
| `server.py` | 267 | MCP stdio server entry (`FastMCP("jarvis")`), registers 9 tools with thin wrappers around QueryService/ZoektLifecycle/GraphStore/semantic, uniform `{"error": ...}` error payload; reports search-only status explicitly to clients | MCP tool handlers: `documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `getIndexStatus`, `searchCode`, `semanticSearch`, `blastRadius` |
| `index_cli.py` | 932 | The `jarvis` CLI: `index_repo()` pipeline (language detection from git-tracked files (`detect_language()`, `_git_tracked_files()`) or a persisted `--language` override (`_resolve_language()`) → language indexer → scip expt-convert → populate graph → zoekt-index → non-fatal semantic indexing stage (`_run_semantic_stage()`) → automatic search-only fallback on recognized indexer failures (`_SEARCH_ONLY_SIGNATURES`, `_search_only_reason()`, `_publish_search_only()`) → atomic pointer swap → registry update), with xcodebuild build-tool selection for Swift repos with checked-in Xcode projects (`_prefers_xcodebuild()`, `_swift_indexer_cmd()`) and Xcode scheme persistence via registry (`_resolve_scheme()`); `_git_head()`/`_git_tracked_files()` raise `NotAGitRepositoryError` for non-git paths; `--search-only` flag enables upfront search-only publish; `_cmd_watch` wires Debouncer to watchdog.Observer; `forget` also drops the repo's LanceDB table | CLI commands: `index`, `list`, `status`, `reindex`, `forget`, `watch` (with `--scheme`/`--language`/`--semantic-include` flag support on index/watch; `--search-only` on both) |
| `watch.py` | 55 | `Debouncer` (pure, thread-free, injectable clock) + `should_ignore_path` (.git/node_modules/.venv/__pycache__/dist/build) | `Debouncer`, `should_ignore_path()` |

### Root-Level Files

| File | Lines | Purpose | Key Exports |
|------|-------|---------|-------------|
| `setup.sh` | ~430 | POSIX-sh dependency bootstrapper: installs scip, zoekt, scip-swift, and the npm indexers into `~/.jarvis/bin`; detect-only for scip-java | `--only`, `--force` |
| `ZOEKT_COMMIT` | 1 | Pinned upstream `sourcegraph/zoekt` commit that CI cross-compiles | — |

## Test Suite (`tests/`)

### Test Files (1:1 map to src modules)

| Test File | Covers | Scope |
|-----------|--------|-------|
| `test_config.py` | config.py | Data-dir resolution, slug sanitization |
| `test_index_reader.py` | index_reader.py | Vendored cache behavior, connection pooling |
| `test_scip_decoder.py` | scip_decoder.py | Blob decoding, range/symbol parsing |
| `test_query.py` | query.py | SQL execution, nav result builders |
| `test_search.py` | search.py | Zoekt HTTP client, lifecycle management |
| `test_chunker.py` | chunker.py | AST chunking, fixed-window fallback, dedup hashing |
| `test_embeddings.py` | embeddings.py | Lazy model loading, normalization, missing-extra error |
| `test_semantic.py` | semantic.py | SemanticStore CRUD, index_semantic(), reciprocal_rank_fusion() |
| `test_graph.py` | graph.py | Dependency graph CRUD, blast_radius BFS |
| `test_registry.py` | registry.py | Registry table operations, status updates |
| `test_watch.py` | watch.py | Debouncer logic, path filtering |
| `test_server_tools.py` | server.py | MCP tool payloads, error handling |
| `test_index_cli.py` | index_cli.py | Full pipeline (e-2-e); marked `@pytest.mark.integration` — calls real scip-python/scip/zoekt binaries |
| `test_index_status.py` | index_cli.py + query.py | Freshness snapshot, staleness detection |
| `test_setup_sh.py` | setup.sh | Sources the script under `dash` (not `sh` — macOS `/bin/sh` accepts bashisms) and tests each function in isolation |
| `test_check_versions.py` | scripts/check_versions.py | Version consistency across `pyproject.toml`, `server.json`, `plugin/.claude-plugin/plugin.json`; MCP Registry floor version check |

### Fixtures (`tests/fixtures/`)

| Fixture | Purpose |
|---------|---------|
| `mini_py_repo/greeter.py` | Minimal Python file for integration tests |
| `mini_swift_repo/` | Minimal Swift repo (Package.swift + Sources/MiniSwiftRepo/Greeter.swift) for xcodebuild detection tests |
| `scip_encoder.py` | Real zstd+protobuf SCIP blob builders (synthetic index) |
| `synthetic_index.py` | Hand-copied real SQLite schema fixture (documents/chunks/global_symbols, etc.) |

## Key Patterns

### Dataclass-First Design
All result types (`Position`, `Range`, `Location`, `SymbolInfo`, etc.) are frozen dataclasses, not Pydantic. Server.py converts them to dicts via `dataclasses.asdict()` for MCP payloads. No validation layer because the runtime has no HTTP boundary.

### SCIP Decoder Isolation Seam
`scip_decoder.py` is the only module importing `scip_pb2` and `zstandard`. This allows future SCIP proto version bumps to be localized.

### Atomic Pointer-Swap Publish
Index publishing writes a new versioned database, waits for graph/Zoekt completion, then atomically swaps the `current` pointer file via `os.replace()`. Queries reading the old index are never interrupted.

### Rebuild-Not-Accumulate Graph
`populate_graph_for_repo()` clears that repo's outgoing edges before recomputing; removed dependencies are retracted. The graph is always current, never stale.

### Broad Exception Handling by Design
`server.py` catches all exceptions and returns `{"error": "..."}` payloads — not passing exceptions to the MCP transport. This keeps the server alive even on query bugs.

### ENV Var Overrides
- `JARVIS_DATA_DIR` — override default `~/.jarvis`
- `JARVIS_ZOEKT_BIN` — override zoekt-webserver binary path (default: `zoekt-webserver`)

### Single-Tenant Hardcoding
`config.py` pins `PROJECT = "_"` and `BRANCH = "_"` — the vendored `IndexConnectionCache` keys on 3-tuples, but jarvis has no project/branch concept. The disk path `scip/_/<slug>/_/` is an artifact of reusing the cache's path shape unchanged.

## Test Coverage

- **Unit tests** cover all modules except `__init__.py` (dead stub) and `models.py` (trivial frozen dataclasses)
- **Integration tests** (marked `@pytest.mark.integration`) run real SCIP indexers, `scip expt-convert`, and `zoekt-index` on a mini Python repo
- **Run tests:** `uv run pytest` (all), `uv run pytest -m "not integration"` (unit only), `uv run pytest -m integration` (real binaries only)

## CI Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `.github/workflows/build-zoekt.yml` | `ZOEKT_COMMIT` change or manual dispatch | Cross-compiles `zoekt-index`/`zoekt-webserver` for macOS+Linux (arm64/amd64) and publishes them to this repo's releases — upstream `sourcegraph/zoekt` ships no binaries at all |
| `.github/workflows/publish-pypi.yml` | `release` event | Publishes versioned release to PyPI (`jarvis-mcp` package); uses GitHub Actions OIDC for auth |
| `.github/workflows/publish-mcp-registry.yml` | `workflow_run` on publish-pypi completion | Publishes `server.json` to official MCP Registry (`io.github.phuongddx/jarvis-dist`); runs after PyPI publish succeeds; retries publish up to 6x for eventual consistency |
| `.github/workflows/setup-smoke.yml` | `setup.sh`/test changes, PRs, manual | Runs `setup.sh` on `ubuntu-latest` (where `/bin/sh` is dash) and `macos-latest`: parse check, install, idempotency, full unit suite; path-filtered |
| `.github/workflows/test.yml` | Every push/PR | Runs unit test suite (`pytest -m "not integration"`); installs `semantic` extra so `test_semantic.py` tests actually run; no path filter (runs on every change) |

## Dependencies & Imports

- **Runtime:** mcp[cli], protobuf, zstandard, httpx, watchdog (optional)
- **No ORM:** Direct sqlite3 usage throughout
- **No async framework:** Pure sync code, single-threaded query path
- **Minimal third-party:** ~150 lines of pure-dataclass models, ~200 lines of CLI glue

## Module Call Graph (Key Paths)

**Indexing pipeline (`index_cli.py`):**
1. Language detection (count extensions across git-tracked files, or use a persisted `--language` override)
2. Run language indexer (scip-python, scip-typescript, scip-java, scip-swift)
3. `scip expt-convert` → SQLite
4. `populate_graph_for_repo()` — extract package names, store edges
5. `zoekt-index` → shards in `.zoekt/`
6. `_run_semantic_stage()` — chunk/embed/store (non-fatal; skips or warns without blocking publish)
7. Atomic `os.replace()` on `current` pointer
8. `Registry.update_repo()` — mark indexed; `mark_semantic_indexed()` if the semantic stage succeeded

**Query path (`server.py` → `query.py`):**
1. MCP tool handler unpacks `repo`, `symbol`/`path` args
2. `QueryService._connection()` opens `index-<sha>.db` read-only
3. Execute SQL against `documents/chunks/global_symbols/mentions` tables
4. Build result dataclasses
5. MCP handler converts to dict, returns `{"result": ...}` or `{"error": ...}`

**Search path (`server.py` → `search.py`):**
1. First call to `searchCode` → `ZoektLifecycle.ensure_running()` spawns `zoekt-webserver -rpc` (pidfile-tracked)
2. HTTP POST to `http://localhost:PORT/api/search` with query
3. Parse JSON response, wrap in Zoekt result types
4. Return to MCP client
5. Server exit → `atexit` handler kills zoekt-webserver

**Graph path (`server.py` → `graph.py`):**
1. `blastRadius(repo, symbol_or_package)` → lookup package name
2. BFS up to 2 hops in `edges` table (outgoing edges from that package)
3. For each dependent, fetch repo info from registry
4. Return list with hop distances

**Semantic path (`server.py` → `semantic.py`):**
1. `semanticSearch(repo, query, limit=10)` → embed query with the table's recorded `TableIdentity` (model, revision, and prefixes)
2. Vector search the repo's LanceDB table (cosine metric)
3. `reciprocal_rank_fusion()` merges vector hits with `searchCode`'s Zoekt lexical hits (k=60)
4. Return ranked hits; include a `"warning"` if the table's identity (model/revision/prefixes/content format) differs from the currently configured one

## Size Profile

- **Total LOC (src):** ~4,050 LOC (excluding generated scip_pb2.py, which adds ~119 LOC) — up from 3,762, mostly `index_cli.py` growth (search-only fallback signatures and publish path, explicit `--search-only` flag) and `registry.py` growth (`SEARCH_ONLY_STATUS` constant and search-only status value)
- **Total LOC (tests):** ~5,220 LOC (across 16 test files; excluding fixtures) — up from 4,534 with addition of `test_check_versions.py`
- **Largest module:** `index_cli.py` (932 LOC)
- **Smallest module:** `__init__.py` (2 LOC)
