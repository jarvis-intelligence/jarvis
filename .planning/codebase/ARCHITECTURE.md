---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# Architecture

**Analysis Date:** 2026-08-21

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        MCP Server Layer                            │
│              `src/jarvis/server.py` (FastMCP, 9 tools)              │
├──────────────┬──────────────┬──────────────┬────────────────────────┤
│  SCIP Nav    │  Zoekt Search│ Semantic      │  Package Graph         │
│  `query.py`  │  `search.py` │ `semantic.py` │  `graph.py`            │
│  `index_     │  Zoekt       │ `chunker.py`  │  `registry.py`         │
│  reader.py`  │  Lifecycle   │ `embeddings.  │                        │
│              │              │  py`          │                        │
├──────────────┴──────────────┴──────────────┴────────────────────────┤
│                     Isolation Seam                                  │
│              `src/jarvis/scip_decoder.py`                           │
│        (only module importing `scip_pb2` / `zstandard`)             │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 On-Disk Data (single-tenant)                        │
│               `~/.jarvis/` (configurable)                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │registry. │ │  scip/_/ │ │ .zoekt/  │ │ lancedb/ │ │  shims/   │ │
│  │   db     │ │ <slug>/_ │ │ (shards) │ │ (<slug>  │ │ (bash     │ │
│  │          │ │ /current │ │          │ │  .lance/)│ │  symlink) │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────────┘ │
└─────────────────────────────────────────────────────────────────────┘
         ▲
         │
┌─────────────────────────────────────────────────────────────────────┐
│                   Indexing Pipeline (CLI)                          │
│                `src/jarvis/index_cli.py`                            │
│  detect_language → SCIP indexer → scip expt-convert → graph →       │
│  zoekt-git-index → semantic index → atomic publish → registry       │
└─────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| `index_cli` | CLI entry point (`jarvis index/list/status/reindex/forget/watch`) and the full indexing pipeline | `src/jarvis/index_cli.py` |
| `server` | MCP stdio server (FastMCP), registers 9 tools, wires services together | `src/jarvis/server.py` |
| `query` | SCIP navigation: go-to-definition, find-references, call/type hierarchy, document symbols, index status | `src/jarvis/query.py` |
| `index_reader` | Read-only SQLite connection cache keyed on `(project, repo, branch, pointer)`; path resolution for `current` pointer | `src/jarvis/index_reader.py` |
| `search` | Zoekt lexical search via `httpx` to a lazily-spawned `zoekt-webserver`; `ZoektLifecycle` owns pidfile/health/kill | `src/jarvis/search.py` |
| `semantic` | LanceDB vector storage, tree-sitter chunking, embeddings, hybrid RRF search (vector + Zoekt + SCIP symbols) | `src/jarvis/semantic.py` |
| `chunker` | tree-sitter AST chunking (256-512 token target), context headers, admission filtering for generated files | `src/jarvis/chunker.py` |
| `embeddings` | Self-hosted embedding model wrapper (sentence-transformers, lazy-loaded); model-identity `(name, revision)` | `src/jarvis/embeddings.py` |
| `symbol_search` | NL query → ranked SCIP symbol-definition hits via lowercased name matching | `src/jarvis/symbol_search.py` |
| `graph` | Package dependency graph extraction from SCIP index; 2-hop BFS `blastRadius`; `GraphStore` (packages + edges) | `src/jarvis/graph.py` |
| `registry` | SQLite-backed registry of indexed repos (slug, path, language, commit, status) | `src/jarvis/registry.py` |
| `scip_decoder` | Isolation seam: zstd decompression + protobuf parsing of SCIP blobs; only module importing `scip_pb2`/`zstandard` | `src/jarvis/scip_decoder.py` |
| `scip_pb2` | Vendored gencode from `scip.proto` (SCIP v0.9.0); never hand-edit | `src/jarvis/scip_pb2.py` |
| `symbols` | SCIP symbol-string parsing and bare-name resolution; only module knowing the SCIP symbol grammar | `src/jarvis/symbols.py` |
| `config` | Data-dir / repo-slug / index-path resolution; single-tenant pinning (`PROJECT="_"`, `BRANCH="_"`) | `src/jarvis/config.py` |
| `models` | Frozen dataclasses for nav-tool result shapes (`Location`, `Range`, `Position`, `CallHierarchyEntry`, etc.) | `src/jarvis/models.py` |
| `watch` | `Debouncer` (pure, thread-free, fake-clock testable) + `should_ignore_path` for auto-reindex | `src/jarvis/watch.py` |

## Pattern Overview

**Overall:** Module-per-concern with a strict isolation seam.

**Key Characteristics:**
- One Python module per concern under `src/jarvis/` — no sub-packages, no grouping beyond the flat module
- SCIP protobuf/zstandard knowledge confined to a single isolation seam (`scip_decoder.py`) — no other module imports `scip_pb2` or `zstandard`
- Raw `sqlite3` throughout — no ORM, no async database driver
- Frozen dataclasses for all result shapes — no Pydantic
- Lazy initialization: Zoekt webserver spawns on first search; embedding model loads on first embed
- Optional extras (`semantic`, `watch`) use deferred imports inside the functions that need them

## Layers

**CLI / Entry Point Layer:**
- Purpose: User-facing commands and the indexing pipeline
- Location: `src/jarvis/index_cli.py`
- Contains: Argument parsing, subcommand dispatch, the full index → convert → graph → zoekt → publish pipeline, language detection, atomic publish
- Depends on: `config`, `graph`, `registry`, `semantic`, `watch`, `search`
- Used by: End users via `uv run jarvis <subcommand>`

**MCP Server Layer:**
- Purpose: Tool registration and request routing
- Location: `src/jarvis/server.py`
- Contains: 9 FastMCP tool functions, lazy service initialization (`_service()`, `_zoekt()`, `_graph()`), error-to-dict conversion
- Depends on: `query`, `search`, `semantic`, `graph`, `config`
- Used by: MCP clients (Claude Code, Codex, etc.) via `uv run jarvis-server` stdio

**Query / Navigation Layer:**
- Purpose: SCIP-based code navigation (definition, references, hierarchy, document symbols)
- Location: `src/jarvis/query.py`, `src/jarvis/index_reader.py`, `src/jarvis/scip_decoder.py`, `src/jarvis/symbols.py`, `src/jarvis/symbol_search.py`
- Contains: Raw SQL against `scip expt-convert` schema, occurrence-blob decoding, symbol resolution, connection caching
- Depends on: `scip_decoder` (isolation seam), `config`, `models`, `symbols`, `symbol_search`
- Used by: `server.py` (nav tools), `semantic.py` (symbol signal for RRF)

**Search Layer:**
- Purpose: Lexical code search via Zoekt
- Location: `src/jarvis/search.py`
- Contains: `ZoektLifecycle` (lazy-spawn pidfile/health/kill), `search_zoekt()` HTTP client, `zoekt_repo_documents()` coverage check
- Depends on: `httpx` (external), `config`
- Used by: `server.py` (searchCode), `semantic.py` (Zoekt signal for RRF)

**Semantic Search Layer:**
- Purpose: Vector embeddings + hybrid RRF search
- Location: `src/jarvis/semantic.py`, `src/jarvis/chunker.py`, `src/jarvis/embeddings.py`, `src/jarvis/symbol_search.py`
- Contains: LanceDB storage, tree-sitter chunking, sentence-transformers embeddings, reciprocal rank fusion (vector + Zoekt + SCIP symbols)
- Depends on: `search` (Zoekt signal), `query` (SCIP symbol signal), `config`, `embeddings`, `chunker`
- Used by: `server.py` (semanticSearch), `index_cli.py` (indexing pipeline semantic stage)

**Graph Layer:**
- Purpose: Package dependency graph and blast-radius traversal
- Location: `src/jarvis/graph.py`
- Contains: `GraphStore` (packages + edges tables in `registry.db`), `populate_graph_for_repo()`, `blast_radius()` (2-hop BFS)
- Depends on: `scip_decoder` (package extraction), `config`
- Used by: `server.py` (blastRadius), `index_cli.py` (graph population during indexing)

**Data Layer:**
- Purpose: Path resolution, connection caching, registry, config
- Location: `src/jarvis/config.py`, `src/jarvis/index_reader.py`, `src/jarvis/registry.py`, `src/jarvis/models.py`
- Contains: Data-dir resolution, single-tenant pinning, read-only SQLite connection cache, repo metadata CRUD
- Depends on: stdlib only
- Used by: Every other layer

## Data Flow

### Primary Indexing Path

1. **Language detection** — `detect_language()` counts git-tracked file extensions (not filesystem walk) and picks the majority language (`src/jarvis/index_cli.py:137`) 
2. **SCIP indexing** — the matching language indexer runs (`scip-typescript`, `scip-python`, `scip-java`, `scip-swift`) producing a `.scip` blob (`src/jarvis/index_cli.py:790`) 
3. **Protocol buffer conversion** — `scip expt-convert` converts the `.scip` blob to a SQLite database (`index.db`) with the SCIP relational schema (`src/jarvis/index_cli.py:798`) 
4. **Graph population** — `populate_graph_for_repo()` extracts package names from the fresh index.db and upserts edges into `registry.db` (`src/jarvis/graph.py:284`) 
5. **Zoekt indexing** — `zoekt-git-index` walks the git tree (not filesystem) and produces a searchable shard (`src/jarvis/index_cli.py:812`) 
6. **Semantic indexing** (optional) — tree-sitter chunking → sentence-transformers embeddings → LanceDB table write (`src/jarvis/index_cli.py:819`) 
7. **Atomic publish** — versioned `index-<sha>.db` copied to the index directory, then `os.replace()` flips the `current` pointer (`src/jarvis/index_cli.py:354`) 
8. **Registry update** — `registry.upsert()` records slug, path, language, commit SHA, and status (`src/jarvis/index_cli.py:830`) 

### Primary Query Path (SCIP Navigation)

1. **Tool invocation** — MCP client calls a nav tool (e.g. `goToDefinition`) with `repo` (slug) and `symbol` (`src/jarvis/server.py:105`) 
2. **Connection resolution** — `IndexConnectionCache.get_connection()` reads the `current` pointer, opens (or retrieves cached) read-only SQLite connection (`src/jarvis/index_reader.py:81`) 
3. **Symbol resolution** — bare name resolved to full SCIP symbol via `symbols.resolve()` against the index's `global_symbols` table (`src/jarvis/query.py:363`) 
4. **SQL query** — raw SQL joins `mentions` → `global_symbols` → `chunks` → `documents`, filtered by role bitmask (`src/jarvis/query.py:126`) 
5. **Blob decoding** — zstd-decompressed + protobuf-parsed via `scip_decoder.decode_occurrences()` (`src/jarvis/scip_decoder.py:73`) 
6. **Response** — `dataclasses.asdict()` converts frozen dataclass to dict; FastMCP serializes to JSON (`src/jarvis/server.py:60`) 

### Search Path (Zoekt)

1. **Lazy spawn** — first `searchCode` call triggers `ZoektLifecycle.ensure_running()`, which checks/reuses a pidfile or spawns `zoekt-webserver` (`src/jarvis/search.py:101`) 
2. **HTTP request** — `POST /api/search` with `{"Q": "r:<repo> <query>"}` via `httpx` (`src/jarvis/search.py:54`) 
3. **Response** — base64-decoded line text, `ZoektHit` frozen dataclass → dict (`src/jarvis/search.py:65`) 

### Semantic Search Path (Hybrid RRF)

1. **Vector retrieval** — query embedded via `EmbeddingModel.embed_query()`, LanceDB cosine search returns top-30 vector hits (`src/jarvis/semantic.py:315`) 
2. **Zoekt retrieval** — same query sent to Zoekt (best-effort, degrades to vector-only on failure) (`src/jarvis/semantic.py:321`) 
3. **SCIP symbol retrieval** — NL tokens matched against lowercased symbol names in the SCIP index (best-effort) (`src/jarvis/semantic.py:327`) 
4. **Reciprocal Rank Fusion** — three signal streams merged by RRF score (`1/(k+rank)`) with containment-based deduplication (`src/jarvis/semantic.py:80`) 
5. **Response** — fused results sorted by score, each annotated with `sources` tuple indicating which signals contributed (`src/jarvis/semantic.py:345`) 

**State Management:**
- No global mutable state in the query path; `IndexConnectionCache` is the closest thing to shared state — a bounded `OrderedDict` LRU cache guarded by a threading lock
- `GraphStore` and `Registry` each hold a single `sqlite3.Connection` — opened on construction, closed explicitly
- Module-level lazy singletons: `_query_service`, `_zoekt_lifecycle`, `_graph_store` in `server.py`; `_default` embedding model in `embeddings.py`

## Key Abstractions

**Isolation Seam (`scip_decoder.py`):**
- Purpose: Confine all protobuf and zstandard knowledge to one module
- Examples: `src/jarvis/scip_decoder.py` — `decode_occurrences()`, `decode_relationships()`, `ScipOccurrence`, `ScipRelationship`, `SymbolRoles`, `parse_symbol_package()`
- Pattern: Every other module calls these pure functions / dataclasses; no other module imports `scip_pb2` or `zstandard`

**Connection Cache (`IndexConnectionCache`):**
- Purpose: Cache read-only SQLite connections keyed on `(project, repo, branch, pointer_content)`
- Examples: `src/jarvis/index_reader.py` — `get_connection()`, `read_pointer()`, `read_metadata()`
- Pattern: Bounded `OrderedDict` with FIFO/LRU eviction, thread-safe via `threading.Lock`; connections opened `mode=ro&immutable=1`

**Zoekt Lifecycle (`ZoektLifecycle`):**
- Purpose: Lazy-spawn, pidfile-based process management for `zoekt-webserver`
- Examples: `src/jarvis/search.py` — `ensure_running()`, `base_url_if_running()`, `atexit`-registered kill
- Pattern: Check pidfile → health-check → spawn if needed; pidfile survives across jarvis processes

**Symbol Grammar (`symbols.py`):**
- Purpose: SCIP symbol-string parsing and bare-name resolution
- Examples: `src/jarvis/symbols.py` — `parse_symbol()`, `resolve()`, `Candidate`, `ParsedSymbol`
- Pattern: The only module knowing the SCIP symbol grammar — a grammar change lands here and nowhere else

## Entry Points

**`jarvis` CLI:**
- Location: `src/jarvis/index_cli.py` (module-level `main()` via argparse)
- Triggers: `uv run jarvis index|list|status|reindex|forget|watch <args>`
- Responsibilities: Full indexing pipeline, registry CRUD, auto-reindex via file watcher

**`jarvis-server` MCP stdio:**
- Location: `src/jarvis/server.py` (`main()` calls `mcp.run()`)
- Triggers: `uv run jarvis-server`
- Responsibilities: 9 MCP tool registrations, lazy service initialization, error-to-dict conversion

## Architectural Constraints

- **Threading:** Single-threaded event loop for the MCP stdio server; `ZoektLifecycle` spawns a subprocess; `IndexConnectionCache` uses a `threading.Lock` for thread-safety (defensive, for potential future async hosts)
- **Global state:** Module-level lazy singletons in `server.py` (`_query_service`, `_zoekt_lifecycle`, `_graph_store`) and `embeddings.py` (`_default`); a bounded `_lower_maps` cache in `symbol_search.py`; no other shared mutable state
- **Circular imports:** None — the flat module-per-concern layout with explicit imports avoids cycles; `scip_decoder.py` imports `scip_pb2` (gencode, leaf dependency); `index_reader.py` imported by `config.py` for `new_connection_cache()` return type annotation only
- **Single-tenant:** `config.py` pins `PROJECT = "_"` and `BRANCH = "_"`; on-disk path shape `scip/_/<slug>/_/` is an artifact of reusing the vendored `IndexConnectionCache` unchanged, not a real multi-tenancy feature
- **SCIP schema dependency:** All navigation SQL is written against the `scip expt-convert` output schema (`documents`, `global_symbols`, `chunks`, `mentions`, `defn_enclosing_ranges` tables) — schema changes in upstream SCIP require SQL updates here

## Anti-Patterns

### Importing `scip_pb2` or `zstandard` outside the isolation seam

**What happens:** Direct protobuf/zstandard imports spread across multiple modules
**Why it's wrong:** Violates the isolation seam — protobuf schema knowledge becomes scattered, making schema migrations error-prone
**Do this instead:** All protobuf/zstandard usage goes through `src/jarvis/scip_decoder.py` functions (`decode_occurrences`, `decode_relationships`, `ScipOccurrence`, `SymbolRoles`)

### Spawning `zoekt-webserver` outside `ZoektLifecycle`

**What happens:** A new subprocess is spawned directly via `subprocess.Popen` for Zoekt
**Why it's wrong:** Bypasses pidfile-based deduplication, health-checking, and `atexit` cleanup — leads to orphan processes and port conflicts
**Do this instead:** Use `ZoektLifecycle.ensure_running()` and `ZoektLifecycle.base_url()` from `src/jarvis/search.py`

### Walking the filesystem instead of git for language detection

**What happens:** Using `pathlib.rglob()` or `os.walk()` to count source extensions
**Why it's wrong:** Counts gitignored vendored checkouts and sibling clones, which can outnumber the repo's own code
**Do this instead:** `detect_language()` in `src/jarvis/index_cli.py` uses `git ls-files` output, with `_IGNORED_DIRS` applied on top

## Error Handling

**Strategy:** Broad exception catching at tool boundaries; specific exception types internally

**Patterns:**
- MCP tools in `server.py` catch all exceptions and return `{"error": "..."}` dicts — keeps the stdio server alive
- `IndexNotFoundError` (from `index_reader.py`) is caught by `server.py` and turned into an explanatory error dict or `indexed: false` status
- `ZoektUnavailableError` is caught by `semantic_search()` and degrades to vector-only (never propagates)
- Indexing pipeline raises `IndexingError` / `UnsupportedLanguageError` / `NotAGitRepositoryError` — caught by the CLI subcommand dispatcher, never by the MCP server
- `search_only_reason()` matches known indexer-failure signatures (Kotlin ABI mismatch, AGP no-shard) and degrades gracefully rather than failing hard

## Cross-Cutting Concerns

**Logging:** No logging framework — `print()` to stdout/stderr in the CLI; MCP server is silent except for tool response payloads

**Validation:** Input validation at the CLI layer (`argparse` types, `repo_slug()` normalization rejecting `.`/`..`/empty); MCP tools accept string inputs and let SQLite/Zoekt handle invalid queries

**Authentication:** None — local-first, single-tenant, no auth layer

**Configuration:** `JARVIS_`-prefixed environment variables: `JARVIS_DATA_DIR`, `JARVIS_ZOEKT_BIN`, `JARVIS_EMBEDDING_MODEL`, `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`, `JARVIS_EMBEDDING_DOC_PREFIX`

---

*Architecture analysis: 2026-08-21*
