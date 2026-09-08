---
focus: arch
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
---

# Architecture

**Analysis Date:** 2026-09-08

## System Overview

jarvis is a **local-first, single-user code-intelligence MCP server** built on a strict
**two-process model**: a *writer* (the `jarvis` CLI) that runs the indexing pipeline and publishes
artifacts, and a *reader* (`jarvis-server`, FastMCP over stdio) that answers 9 MCP tools
read-only. The two processes share **no code path at runtime** — their only contract is the
on-disk storage layer under `~/.jarvis/` (overridable via `JARVIS_DATA_DIR`, see
`src/jarvis/config.py`). Storage is the seam: the reader only ever reads down into it, the writer
only ever writes up into it.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│  1 · MCP Clients (Claude Code, Cursor, any MCP host)                          │
├──────────────────────────────────────────────────────────────────────────────┤
│  2 · Reader process: `jarvis-server`                                         │
│     `src/jarvis/server.py` — FastMCP stdio, 9 tools, lazy singletons,        │
│     never raises across the MCP boundary                                      │
├──────────────────────────────┬────────────────────────┬──────────────────────┤
│  3 · Engines (read-only)     │                        │                      │
│  Query  `src/jarvis/query.py`│ Search `search.py`     │ Graph `graph.py`     │
│  + `symbols.py` (resolve)    │ + ZoektLifecycle       │ + `registry.py`      │
│  + `index_reader.py` (cache) │                        │                      │
│  + `symbol_search.py`        │ Semantic `semantic.py` + `chunker.py`          │
│  + `scip_decoder.py` (seam)  │ + `embeddings.py`     │                      │
├──────────────────────────────┴────────────────────────┴──────────────────────┤
│  4 · STORAGE SEAM: `~/.jarvis/`                                              │
│     `scip/_/<slug>/_/index-<sha>.db` + `current` pointer + `.metadata.json`  │
│     `.zoekt/` shards · `registry.db` · `lancedb/<slug>.lance/` ·             │
│     `cache/scip-swift/<slug>/` · `shims/`                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│  5 · Writer process: `jarvis` CLI — `src/jarvis/index_cli.py`                 │
│     detect → gate → indexer → convert → graph → zoekt → semantic →           │
│     atomic publish (`index_repo()`); degrade/fallback state machine          │
├──────────────────────────────────────────────────────────────────────────────┤
│  6 · Language indexers (external binaries): scip-typescript, scip-python,    │
│     scip-java, scip-swift   ·   7 · Toolchain: Node, Python, JDK, Xcode      │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Writer CLI | `jarvis index/list/status/reindex/forget/watch`; full indexing pipeline; degrade state machine | `src/jarvis/index_cli.py` |
| MCP server | 9 stdio tools; lazy singletons; degradation reporting at the MCP surface | `src/jarvis/server.py` |
| Query engine | Nav SQL + occurrence decoding + freshness snapshots (`QueryService`) | `src/jarvis/query.py` |
| Symbol resolution | SCIP symbol-string parsing; bare-name resolution rungs; bounded name-map cache | `src/jarvis/symbols.py` |
| Symbol search signal | NL query → ranked SCIP symbol-definition hits; the 0→1-based line seam | `src/jarvis/symbol_search.py` |
| Index reader | `current` pointer resolution; read-only SQLite connection cache | `src/jarvis/index_reader.py` |
| Search engine | Zoekt HTTP client; lazy `zoekt-webserver` lifecycle (pidfile, atexit) | `src/jarvis/search.py` |
| Graph engine | Package extraction from index.db; `packages`/`edges` store; 2-hop BFS | `src/jarvis/graph.py` |
| Registry | `repos` table CRUD; status/origin taxonomy; idempotent column migrations | `src/jarvis/registry.py` |
| Semantic engine | LanceDB `SemanticStore`; RRF fusion; `TableIdentity` reuse rule | `src/jarvis/semantic.py` |
| Chunker | tree-sitter chunking; admission policy (gitignore/banner/size); `CONTENT_FORMAT` | `src/jarvis/chunker.py` |
| Embeddings | Lazy sentence-transformers wrapper; model/revision identity; prefixes | `src/jarvis/embeddings.py` |
| SCIP decoder | zstd + protobuf blob decode; **sole import seam** for `scip_pb2`/`zstandard` | `src/jarvis/scip_decoder.py` |
| Vendored gencode | `scip_pb2.py` — generated protobuf code, never hand-edited | `src/jarvis/scip_pb2.py` |
| Config | Data dir, slug normalization, path layout (`index_dir`, `lancedb_dir`, …) | `src/jarvis/config.py` |
| Result models | Frozen dataclasses for nav-tool shapes | `src/jarvis/models.py` |
| Watch | Pure `Debouncer` + watcher ignore set (thread-free, fake-clock testable) | `src/jarvis/watch.py` |

## Pattern Overview

**Overall:** Layered pipes-and-filters around an immutable published-artifact store, with a
command/CLI writer and a thin MCP reader facade.

**Key Characteristics:**
- **Two-process split**: writer (`jarvis`) and reader (`jarvis-server`) are separate entry points
  (`[project.scripts]` in `pyproject.toml`); they coordinate exclusively through published files.
- **Thin MCP boundary**: every `@mcp.tool` wrapper in `src/jarvis/server.py` is a dumb
  unpack-call-catch-serialize shim; all logic lives in engines.
- **Lazy everything on the read path**: singletons, Zoekt webserver, embedding model, and the
  `semantic`/`registry` imports are all deferred until first use, so a base install starts and
  answers tools without optional extras.
- **Degrade, don't die**: recognized indexer failures publish search-only instead of hard-failing;
  status/coverage helpers never raise; every degradation is persisted with an origin taxonomy.
- **Immutable versioned artifacts + atomic pointer flip**: indexes are `index-<sha>.db` files that
  are never mutated; liveness is a one-line `current` pointer swapped by `os.replace()`.

## Layers

**MCP server layer (reader):**
- Purpose: protocol dispatch, arg unpacking, error shaping, degradation reporting
- Location: `src/jarvis/server.py`
- Contains: 9 `@mcp.tool` functions, lazy singleton accessors (`_service()`, `_zoekt()`,
  `_graph()`), status-derivation helpers (`_capability_fields`, `_search_coverage_fields`,
  `_error_payload`)
- Depends on: `query.py`, `search.py`, `graph.py`, `registry.py` (deferred), `semantic.py` (deferred)
- Used by: MCP clients over stdio

**Engine layer (read path):**
- Purpose: answer nav/search/graph/semantic questions from published artifacts
- Location: `src/jarvis/query.py`, `src/jarvis/symbols.py`, `src/jarvis/symbol_search.py`,
  `src/jarvis/index_reader.py`, `src/jarvis/search.py`, `src/jarvis/graph.py`,
  `src/jarvis/semantic.py`, `src/jarvis/embeddings.py`, `src/jarvis/scip_decoder.py`
- Contains: SQL against `index-<sha>.db` tables (`documents`, `chunks`, `global_symbols`,
  `mentions`, `defn_enclosing_ranges`), HTTP to `zoekt-webserver`, SQL against `registry.db`,
  vector search against LanceDB
- Depends on: storage layer only (never on `index_cli.py`)
- Used by: `server.py`

**Indexing orchestration layer (writer):**
- Purpose: run the pipeline, gate on toolchain versions, publish, record outcome
- Location: `src/jarvis/index_cli.py`
- Contains: `index_repo()`, `_publish_search_only()`, `_publish_atomically()`,
  `_retire_scip_artifacts()`, the `_resolve_*` persistence trio, CLI subcommands
- Depends on: `graph.py`, `registry.py`, `config.py`, `watch.py`, `semantic.py` (TYPE_CHECKING +
  runtime), external binaries via `_run()`
- Used by: humans/CI via the `jarvis` CLI

**Storage layer (the seam):**
- `~/.jarvis/registry.db` — `repos`, `packages`, `edges` tables (writer RW; reader issues SELECTs)
- `~/.jarvis/scip/_/<slug>/_/` — `current` pointer, `index-<sha>.db`, `index-<sha>.metadata.json`
- `~/.jarvis/.zoekt/` — zoekt shards (`<slug>_v*.zoekt`) + `zoekt-webserver.pid`
- `~/.jarvis/lancedb/<slug>.lance/` — one table per repo
- `~/.jarvis/cache/scip-swift/<slug>/` — per-repo scip-swift incremental cache
- `~/.jarvis/shims/` — tool shims (currently `bash` for scip-java's wrapper; prepended to PATH by
  `_java_indexer_env()` in `src/jarvis/index_cli.py`)

## Data Flow

### Indexing pipeline (`index_repo()` — `src/jarvis/index_cli.py:966`)

1. Resolve `repo_path`, derive slug (`config.repo_slug`), read `_git_head()`; open `Registry`;
   `_reject_duplicate_slug_for_path()` enforces one slug per repo path.
2. **Pre-pipeline gate** (any failure here persists a `failed` row with origin `failed_hard`
   before the first upsert — D-05/D-06): `check_scip_version()` (floor `MIN_SCIP_VERSION` 0.9.0 —
   older converters silently write zero chunks/mentions), then resolve persisted-or-CLI intent:
   `_resolve_search_only`, `_resolve_fallback` (FALL-02 precedence: CLI > persisted row >
   `JARVIS_FALLBACK_SEARCH_ONLY` env), `_resolve_language` (override bypasses detection
   entirely), `detect_language()`, `_resolve_scheme`, `_resolve_semantic_include`. Swift adds
   `check_scip_swift_version()` (floor 0.3.0, D-04) and `_swift_indexer_cmd()` (forces
   `--build-tool xcodebuild` when a `.xcodeproj`/`.xcworkspace` is checked in; appends
   `--scheme`; points `--cache-dir` outside the working tree).
3. **`--search-only` branch**: upsert `indexing` → `_publish_search_only()` → upsert `search-only`
   status with origin `manual` → `mark_tracked_files` / `mark_semantic_indexed`.
4. **Full build** inside `tempfile.TemporaryDirectory(prefix="jarvis-index-")`:
   1. `_run([*indexer_cmd, "--output", index.scip])` — java runs under `_java_indexer_env()`
      (Gradle parallel off; PATH gains `~/.jarvis/shims` when a bash shim exists).
   2. On `IndexingError`: bash-shim failure → hard fail with remedy text; `_search_only_reason()`
      signature match (Kotlin ABI mismatch, Android/AGP no-shards — every substring required) →
      `_publish_search_only()` + `search-only` status, origin `signature` (sticky); anything else
      re-raises.
   3. `scip expt-convert --output index.db index.scip`.
   4. `populate_graph_for_repo()` against a `GraphStore` on `registry.db`, reading the scratch db
      **before anything is published** (a graph failure must precede the pointer flip);
      `index_has_navigation_data()` records whether chunks+mentions exist.
   5. Zoekt: `_pin_zoekt_repo_name()` (`git config zoekt.name <slug>`), `_tracked_blob_count()`,
      `_run(_zoekt_index_cmd(...))` (`zoekt-git-index` into `~/.jarvis/.zoekt`),
      `_warn_on_coverage_shortfall()`, `_sweep_zoekt_tmp_orphans()`.
   6. `_run_semantic_stage()` — chunk (`chunker.py`) → embed (`embeddings.py`) → LanceDB table
      (`semantic.index_semantic()`); non-fatal: `SemanticExtraMissingError` skips cleanly, other
      failures are logged.
   7. Publish: copy scratch db → `config.index_dir(slug)/index-<sha>.db` + sibling
      `index-<sha>.metadata.json`; `_publish_atomically()` (`src/jarvis/index_cli.py:457`) writes
      the `current` pointer via write-temp-then-`os.replace()`; sets `published = True`.
   8. Registry bookkeeping: final status `indexed`, or `PARTIAL_STATUS` (`"partial"`) when symbols
      published but no navigable positions; `mark_tracked_files`, `mark_semantic_indexed`.
5. **Degrade gate** (except handler, FALL-01/FALL-04): fires only when `not published and
   fallback_enabled and not isinstance(exc, MissingBinaryError) and not _bash_shim_failure(text)` —
   environment misconfiguration must stay a loud failure, and post-flip bookkeeping failures must
   not rmtree a just-published good index. On the gate: `_publish_search_only()` →
   - `SearchPublishedButIncomplete` (Zoekt shards live, retiring the old SCIP index failed) →
     combined text, fall through to the hard-failure record;
   - any earlier publish failure → nothing published, fall through to hard failure;
   - success → upsert `DEGRADED_STATUS` with origin `fallback` + one-line reason (D-03) + full
     stderr (D-02); `search_only` stays **False** so the next reindex retries the full build
     (FALL-03 self-heal).
6. **Hard failure**: `_record_failure_best_effort()` writes origin `failed_hard` (the write itself
   demoted to a stderr warning — WR-03), then `raise IndexingError(...)`.

### Degrade/fallback state machine

Registry statuses (`src/jarvis/registry.py`): `indexing`, `indexed`, `partial`, `failed`,
`search-only` (`SEARCH_ONLY_STATUS`), `degraded` (`DEGRADED_STATUS`). Origins (D-01):
`failed_hard`, `signature`, `manual`, `fallback`.

- Explicit `--search-only` or signature match → `search-only`, `search_only=1` **sticky**
  (reindex/watch reuse it; the only escape is `jarvis forget` + reindex).
- Opt-in fallback on a post-build-start failure → `degraded`, `search_only=0` → full-build retry
  on next reindex.
- `_retire_scip_artifacts()` (`src/jarvis/index_cli.py:817`) tears down a previously published
  SCIP index when a repo degrades, so nav tools fail honestly instead of serving stale data;
  graph edges are cleared **before** the rmtree (WR-02 — the reversible half first).
- Read-time helpers: `origin_of()` / `recovery_for()` in `src/jarvis/registry.py` derive the
  origin slug and the recovery command from the row.
- MCP surface: `getIndexStatus` → `_capability_fields()` (D-15: `last_index_run` = registry-row
  truth vs `capabilities` = on-disk truth; navigation availability comes from the pointer read,
  never the row status — D-07) and `_search_coverage_fields()` (Zoekt document count vs
  git-tracked count at last index — a floor check, greater-than is legitimate);
  `_error_payload()` (D-14) adds structured `state`/`cause`/`recovery` keys to
  `IndexNotFoundError` responses and a qualifier hint for `AmbiguousSymbolError`.

### MCP query path (e.g. `goToDefinition`)

1. MCP client JSON → FastMCP dispatch → `go_to_definition` in `src/jarvis/server.py`.
2. `_service()` returns the lazy `QueryService(config.new_connection_cache())` singleton.
3. `QueryService.get_definitions()` → `get_connection()` → `IndexConnectionCache`
   (`src/jarvis/index_reader.py`) reads `~/.jarvis/scip/_/<slug>/_/current` and opens the
   versioned db read-only: `sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)`.
4. `symbols.resolve(conn, symbol)` (`src/jarvis/symbols.py`):
   - **Rung 1** — verbatim passthrough: one indexed exact-match lookup; makes `resolve()` idempotent
     for full SCIP symbol strings.
   - **Rung 2** — dotted-suffix match over the cached leaf-name map (`_name_map`, bounded
     64-entry `OrderedDict` keyed by db path — safe because published dbs are immutable and a
     reindex publishes under a new sha/path).
   - Miss → `SymbolNotFoundError` with a differentiated message (bare name vs parameter-only vs
     stale full symbol that embeds a package version); multiple → `AmbiguousSymbolError`.
5. `_occurrences_for_symbol(conn, symbol, role_bit=SymbolRoles.DEFINITION)` — SQL joins
   `chunks`×`documents`, decodes occurrence blobs via `scip_decoder.decode_occurrences()`
   (zstd decompress + protobuf parse), filters by the **role bitmask** (`occ.is_definition()`;
   `findReferences` deliberately passes `role_bit=None` for all occurrences).
6. `scip_range_to_positions()` unpacks the packed SCIP range; `_occurrence_to_location()` builds
   the frozen dataclasses from `src/jarvis/models.py`.
7. `server.py` serializes via `dataclasses.asdict` + `_json_safe`, appends freshness fields
   (`_freshness_fields`), returns the dict. **Any exception becomes `{"error": ...}`** — the MCP
   boundary never raises.

### Search path (`searchCode`)

1. `_zoekt()` lazy `ZoektLifecycle` (`src/jarvis/search.py`): `ensure_running()` reuses a healthy
   webserver recorded in the pidfile (`~/.jarvis/zoekt-webserver.pid`, port 6070) or spawns
   `zoekt-webserver -index <dir> -rpc -listen :<port>`; `atexit` kills only a self-spawned process.
   `base_url_if_running()` **never spawns** — status calls must not start a webserver.
2. `search_zoekt()` POSTs `{"q": ...}` to `/api/search`; the optional `repo` arg becomes an
   `r:<repo>` query filter; base64 line content is decoded (`_decode_line`).
3. `zoekt_repo_documents()` POSTs to `/api/list` for the coverage check.

### Graph path (`blastRadius`)

1. `_graph()` lazy `GraphStore` on `registry.db` (shared with the registry's own connection).
2. `blast_radius()` (`src/jarvis/graph.py`): seed resolution → 2-hop bounded BFS over
   `edges` (`_MAX_HOPS = 2`) → `BlastRadiusResult` with per-dependent hop distance; freshness is
   always `unknown` (no per-node timestamps — honest limitation).

### Semantic path (`semanticSearch`)

1. `server.py` gathers optional signals without requiring any: `_zoekt_base_url_or_none()`
   (spawn failure → vector+symbol only), `_scip_conn_or_none(repo)` (search-only repo →
   vector+zoekt only). `jarvis.semantic` is imported inside the tool so it exists without the extra.
2. `semantic.semantic_search()` embeds the query **with the table's recorded identity** (model,
   revision, and prefixes — never the currently configured ones), runs a cosine vector search.
3. `symbol_search.search_symbols()` tokenizes the NL query (stopword-filtered), matches tokens
   against a lowercased name map, ranks by matched-token count then kind priority, resolves top
   candidates to definition locations — converting **SCIP 0-based lines to 1-based here, the one
   seam** (`src/jarvis/symbol_search.py:174-179`).
4. `reciprocal_rank_fusion()` (k=60, unweighted) fuses vector + Zoekt + symbol hits; each hit's
   `sources` names the contributing signals; a mismatched table identity adds a `warning` field.

### Watch loop (`jarvis watch`)

`_cmd_watch()` (`src/jarvis/index_cli.py:1562`) wraps the pure `Debouncer` (`src/jarvis/watch.py`)
with a `watchdog` `Observer`; events filtered by `should_ignore_path`; the poll loop catches its
own errors so watching never silently dies. `_watch_should_retry_full_build()` / `_watch_skip_check()`
implement the FALL-05 anti-treadmill: a persisted `degraded`/`search-only` repo skips doomed full-build
retries unless the commit moved.

**State Management:**
- No in-process mutable state on the query path beyond lazy singletons and bounded immutable-key
  caches. Cross-process state lives entirely in files: `current` pointer (version identity),
  `registry.db` (repo rows + graph), pidfile (webserver liveness).

## Key Abstractions

**`QueryService`** (`src/jarvis/query.py`):
- Purpose: the 5 nav tools + `getIndexStatus` against one repo's published index
- Pattern: constructor-injected `IndexConnectionCache`; `_resolved()` calls `symbols.resolve`
  explicitly at each call site (deliberately not a decorator — traceability)

**`IndexConnectionCache`** (`src/jarvis/index_reader.py`):
- Purpose: pooled read-only connections keyed by `(project, repo, branch, pointer_content)` —
  the pointer's **content** is the version identifier, never mtime (NFS-safe)
- Pattern: bounded `OrderedDict` LRU/FIFO hybrid with internal locks; stale-pointer entries are
  simply never looked up again

**`Registry`** (`src/jarvis/registry.py`):
- Purpose: `repos` table as the single source of run-outcome truth (status, origin, reason,
  stderr, overrides, fallback flag, tracked-file count, semantic timestamps)
- Pattern: `_ensure_column()` idempotent additive migrations — "duplicate column name" swallowed,
  "database is locked" re-raised

**`SemanticStore` / `TableIdentity`** (`src/jarvis/semantic.py`):
- Purpose: one LanceDB table per repo; identity gates carry-forward vector reuse
- Pattern: `table_identity()` = model + revision + query prefix + doc prefix + `CONTENT_FORMAT`
  (`src/jarvis/chunker.py`); any mismatch → full re-embed, never mixed embedding spaces

**`ZoektLifecycle`** (`src/jarvis/search.py`):
- Purpose: exactly-one-webserver guarantee across jarvis processes
- Pattern: pidfile + health probe + `atexit` scoped to self-spawned processes; `base_url()`,
  `base_url_if_running()`, `ensure_running()` form the spawn/spy tiers

**`Debouncer`** (`src/jarvis/watch.py`):
- Purpose: coalesce filesystem event bursts into one reindex
- Pattern: pure, thread-free, injectable clock — unit-testable without watchdog

## Entry Points

**`jarvis` (writer CLI):**
- Location: `src/jarvis/index_cli.py` (`main` → `build_parser` → subcommand dispatch)
- Triggers: user/CI shell; `pyproject.toml` `[project.scripts] jarvis = "jarvis.index_cli:main"`
- Subcommands: `index` (with `--slug/--scheme/--semantic-include/--language/--search-only/
  --fallback-search-only`), `list`, `status`, `reindex`, `forget`, `watch`
  (`--debounce`, default 5s)

**`jarvis-server` (reader):**
- Location: `src/jarvis/server.py` (`main` → `mcp.run()`); registered for MCP clients via
  `server.json`
- Triggers: MCP client spawning a stdio server
- Responsibilities: expose the 9 tools — `documentSymbols`, `goToDefinition`, `findReferences`,
  `callHierarchy`, `typeHierarchy`, `getIndexStatus`, `searchCode`, `semanticSearch`,
  `blastRadius`

**`scripts/check_versions.py`** (CI), **`setup.sh`** (toolchain bootstrap) — ancillary entry
points outside the runtime.

## Architectural Constraints

- **Threading:** FastMCP server is single-threaded; `IndexConnectionCache` is internally
  thread-safe anyway; `Debouncer` is thread-free by design. Indexing is exclusive per slug by
  discipline (no lock file); `registry.db` contention surfaces as "database is locked" and is
  deliberately not swallowed during migrations.
- **Global state:** module-level lazy singletons in `src/jarvis/server.py`
  (`_query_service`, `_zoekt_lifecycle`, `_graph_store`), the default embedding model in
  `src/jarvis/embeddings.py` (`default_model()`), and bounded caches `symbols._name_maps` /
  `symbol_search._lower_maps`. All are append-once and immutable-key safe.
- **Import discipline:** `lancedb`/`sentence-transformers`/`tree-sitter` are never imported at
  module top level (the `semantic` extra must be optional); `watchdog` is imported only inside
  `_cmd_watch`; `server.py` defers `jarvis.registry.Registry` and `jarvis.semantic`.
- **Dependency direction:** `graph.py` imports `query.py` (`FreshnessSnapshot`); engines never
  import `index_cli.py`. No known circular imports.
- **External binaries on PATH are load-bearing:** `scip`, language indexers, `zoekt-index`/
  `zoekt-webserver`; version floors enforced at runtime (`check_scip_version`,
  `check_scip_swift_version`) because setup.sh auto-rolls but PATH shadowing can leave stale
  binaries.
- **Platform:** macOS or Linux; Swift indexing effectively macOS-only (Xcode + iOS SDK).

## Load-Bearing Invariants (do not break)

1. **Atomic publish via pointer `os.replace`** — `_publish_atomically()`
   (`src/jarvis/index_cli.py:457`): every expensive/failable step (graph, zoekt, semantic, copy)
   precedes the flip; the `published` flag keeps the degrade gate away from post-flip registry
   bookkeeping failures. A concurrent reader either sees the old or the new index, never partial.
2. **Rebuild-not-accumulate graph edges** — `populate_graph_for_repo()` (`src/jarvis/graph.py`)
   deletes a repo's outgoing edges before inserting the new set; `_retire_scip_artifacts()`
   clears them on degrade (edges first, rmtree second — WR-02).
3. **`scip_decoder` is the sole `scip_pb2`/`zstandard` import seam** — verified: only
   `src/jarvis/scip_decoder.py` imports them; `query.py` and `graph.py` consume
   `ScipOccurrence`/`SymbolRoles`/`parse_symbol_package`/`decode_*` from it. Vendored
   `src/jarvis/scip_pb2.py` is gencode; regenerating it is the only sanctioned change.
4. **Role bitmask filtering** — `SymbolRoles` re-exports `scip_pb2.SymbolRole` values;
   `occ.is_definition()` gates definitions; `findReferences` passes `role_bit=None` on purpose.
5. **LanceDB `TableIdentity` single-model rule** — a table only ever holds vectors from one
   (model, revision, query prefix, doc prefix, `CONTENT_FORMAT`) tuple; queries embed with the
   table's recorded identity, never the current configuration.
6. **Git-based language detection** — `detect_language()` counts extensions over
   `git ls-files` (`_git_tracked_files`), never a filesystem walk (vendored checkouts would
   outvote the repo's own code); tie-break by `_EXT_PRIORITY`; non-git paths raise
   `NotAGitRepositoryError`.
7. **Zoekt repo name pinning** — `_pin_zoekt_repo_name()` sets `git config zoekt.name <slug>`
   before every `zoekt-git-index` run (shard names follow the slug, not the directory basename);
   `forget` unpins; the `_v*.zoekt` glob guard in `server.py` must keep its `_v` (bare `api`
   would glob-match `api-gateway`).
8. **MCP boundary never raises** — every tool wrapper catches `Exception` broadly and returns
   `{"error": ...}`; status helpers degrade to nulls-with-reason. Note the linked invariant:
   `status_stderr` (potentially megabytes) never enters an MCP payload.
9. **SCIP 0-based → 1-based conversion happens only in
   `symbol_search.search_symbols`** (`src/jarvis/symbol_search.py:174-179`) — nav tools return
   raw SCIP coordinates; `SymbolHit`/`FusedHit` are 1-based to match `chunker.py` and Zoekt.
10. **Bare `scip-swift` invocation** — `_LANGUAGE_INDEXERS[".swift"] = ("swift", ["scip-swift"])`
    with no `index` subcommand token: `scip-swift index` fails against v0.1.0-era binaries; the
    bare form works on every version. Do not add the token back.

## Anti-Patterns

### Importing `scip_pb2` or `zstandard` outside `scip_decoder`

**What happens:** Callers couple directly to gencode/codec shapes.
**Why it's wrong:** Regenerating `scip_pb2.py` or swapping the codec then fans out across the
codebase; the decoder also owns the `typed_range` oneof → packed wire conversion.
**Do this instead:** Import `ScipOccurrence`, `SymbolRoles`, `decode_occurrences`,
`decode_relationships`, `parse_symbol_package`, `scip_range_to_positions` from
`src/jarvis/scip_decoder.py` (see `src/jarvis/query.py`, `src/jarvis/graph.py`).

### Publishing anything after the pointer flip that can fail

**What happens:** Post-flip failures meet a `published=True` index that the degrade gate must
refuse to destroy.
**Why it's wrong:** Degrading then would rmtree a good live index; not degrading records a
bookkeeping error as an indexer failure.
**Do this instead:** Keep registry bookkeeping last and let it fail through to the hard-failure
record, exactly as `index_repo()` does (`src/jarvis/index_cli.py:1127-1131`).

### Letting an MCP tool raise

**What happens:** A raised exception kills the tool response contract for the client.
**Why it's wrong:** Clients see protocol errors instead of actionable messages.
**Do this instead:** Catch broadly and shape `{"error": ...}`; for missing indexes route through
`_error_payload()` (`src/jarvis/server.py:225`) so search-only state is explained.

### Top-level imports of optional-extra dependencies

**What happens:** Base installs (no `semantic` extra) fail at server startup.
**Why it's wrong:** The 8 non-semantic tools must work everywhere.
**Do this instead:** Defer imports inside functions/tools, as `semantic_search_tool` and
`_run_semantic_stage` do.

### Mutating a published `index-<sha>.db` or caching by mtime

**What happens:** Breaks the `immutable=1` read contract and NFS-safety.
**Why it's wrong:** Readers assume versioned files never change; mtime lies on network filesystems.
**Do this instead:** Publish a new sha and flip the pointer; key caches by pointer content
(`src/jarvis/index_reader.py`).

## Error Handling

**Strategy:** Classify, persist, and degrade — never swallow silently, never crash the reader.

**Patterns:**
- Writer: typed exception family in `src/jarvis/index_cli.py`
  (`IndexingError`, `MissingBinaryError`, `UnsupportedLanguageError`,
  `NotAGitRepositoryError`, `SearchPublishedButIncomplete`); every terminal path persists a
  registry row (status + origin + one-line reason + full text) before raising.
- Recognized-but-unfixable indexer failures degrade via `_SEARCH_ONLY_SIGNATURES`
  (all-substrings-required matching keeps generic errors out); unrecognized failures stay loud.
- Reader: broad `except Exception` per tool → `{"error": ...}`; best-effort helpers return
  `None`-with-reason instead of raising (`_registry_entry`, `_search_coverage_fields`,
  `_capability_fields`).
- Index decode failures raise `OccurrenceDecodeError` (`src/jarvis/scip_decoder.py`); symbol
  resolution raises `SymbolNotFoundError`/`AmbiguousSymbolError` with actionable messages
  (`src/jarvis/symbols.py`).

## Cross-Cutting Concerns

**Logging:** No framework — `print(..., file=sys.stderr)` for writer-side notes/warnings; the MCP
server returns diagnostics in payloads instead of logging. Index-time semantic summaries go to
stderr (`_print_semantic_report`).

**Validation:** Slug normalization (`config.repo_slug`, `_SLUG_UNSAFE` regex); duplicate-slug
rejection; language-override validation against `_INDEXER_BY_LANGUAGE`; toolchain version floors;
coverage shortfall warnings (`_warn_on_coverage_shortfall`).

**Authentication:** None — single-user, localhost-only (stdio MCP; Zoekt webserver bound to
127.0.0.1:6070).

**Configuration:** `pyproject.toml` (deps, extras `watch`/`semantic`, entry points, pytest,
cibuildwheel); env vars `JARVIS_DATA_DIR`, `JARVIS_FALLBACK_SEARCH_ONLY`,
`JARVIS_ZOEKT_BIN`, `JARVIS_EMBEDDING_QUERY_PREFIX`/`JARVIS_EMBEDDING_DOC_PREFIX`;
per-repo persisted overrides in `registry.db` (scheme, language, semantic include, search-only,
fallback flag).

---

*Architecture analysis: 2026-09-08*
