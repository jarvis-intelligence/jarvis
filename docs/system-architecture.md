# codeintel: System Architecture

## High-Level Overview

codeintel is a **local-first, single-user code intelligence MCP server** that combines semantic navigation (SCIP-backed) with lexical search (Zoekt-backed) in a single stdio process. It bridges the SCIP indexing ecosystem with the MCP protocol, exposing 8 tools to Claude Code, Cursor, and other MCP clients.

The system is built around three core engines:

1. **Query Engine** — reads SCIP SQLite indexes, executes nav queries (go-to-definition, find-references, etc.)
2. **Search Engine** — manages embedded Zoekt instance, returns lexical search results
3. **Graph Engine** — builds and queries package dependency relationships across indexed repos

### Layered Architecture (primary view)

![codeintel layered architecture](assets/codeintel-full-architecture-with-swift.png)

*Editable source: [`assets/codeintel-full-architecture-with-swift.drawio`](assets/codeintel-full-architecture-with-swift.drawio) — also exported as `.svg` and as an XML-embedded `.drawio.png`*

Seven layers, top to bottom. **Storage (layer 4) is the seam**: the runtime only ever reads
down into it, the indexing pipeline only ever writes up into it, and the two halves share no
other contract.

| Layer | Contents |
|---|---|
| 1 · Clients | Claude Code, Cursor, any MCP host |
| 2 · MCP Server | `server.py` — FastMCP over stdio, 8 tools |
| 3 · Engines | Query (`query.py`), Search (`search.py`), Graph (`graph.py`) |
| 4 · Storage | `index-<sha>.db` + pointer, `.zoekt/` shards, `registry.db` |
| 5 · Indexing orchestration | `index_cli.py` — detect → run indexer → convert → graph/zoekt → atomic publish |
| 6 · Language indexers | `scip-typescript`, `scip-python`, `scip-java`, `scip-swift` |
| 7 · External toolchain | Node/npm, Python, JDK, and (Swift only) Xcode + iOS SDK |

Layer 6→7 is where Swift differs from every other language: the other three indexers need only
an ordinary runtime, while `scip-swift` needs Xcode and the iOS SDK, which Apple ships for macOS
only.

### Component Diagram (runtime detail)

![codeintel overview](assets/codeintel-architecture.png)

*Editable source: [`assets/codeintel-architecture.excalidraw`](assets/codeintel-architecture.excalidraw)*

Diagram shows:
- **Client**: Claude Code / Cursor / any MCP client → MCP stdio
- **Server** (`server.py`): FastMCP dispatcher → 8 tools
- **Query Engine** (`query.py`): Reads SCIP index SQLite (documents/chunks/global_symbols)
- **Search Engine** (`search.py`): HTTP client to embedded Zoekt webserver
- **Graph Engine** (`graph.py`): Queries package edges in registry.db
- **Storage**: SCIP indexes (index-<sha>.db), Zoekt shards (.zoekt/), registry (registry.db)

---

## Indexing Pipeline & Publishing

![codeintel index pipeline and package graph](assets/codeintel-system-architecture.png)

*Editable source: [`assets/codeintel-system-architecture.excalidraw`](assets/codeintel-system-architecture.excalidraw)*

Diagram shows the full indexing lifecycle from file changes → published index:

### The Pipeline (index_cli.py)

1. **Language Detection**
   - Count source files by extension
   - Select language with most files (tie-break by priority: .ts → .tsx → .py → .java → .kt → .swift)
   - Skip: .git, node_modules, .venv, __pycache__, dist, build, DerivedData, .build

2. **Run Language Indexer**
   - Execute `scip-typescript`, `scip-python`, `scip-java`, or `scip-swift` on repo root
   - Output: raw SCIP document (protobuf, optionally zstd-compressed)

3. **SCIP Conversion**
   - Run `scip expt-convert` to convert SCIP document → SQLite
   - Creates `documents`, `chunks`, `global_symbols`, `mentions`, `defn_enclosing_ranges` tables
   - Contains occurrence metadata and symbol information

4. **Package Graph Population**
   - Extract package names from all symbols (e.g., "npm:@scope/name", "python:requests")
   - Build edges in registry.db: source_repo → dependent_package
   - Uses rebuild-not-accumulate (delete old edges for this repo, insert new ones)

5. **Lexical Indexing**
   - Run `zoekt-index` against the same repo
   - Creates Zoekt shards in `.zoekt/` directory
   - Files are searchable by keyword, filename, content

6. **Atomic Publishing**
   - Copy SCIP index to final location: `~/.codeintel/scip/_/<slug>/_/index-<sha>.db`
   - Update `current` pointer file (small text file, atomic `os.replace()`)
   - Update registry.db: mark repo as indexed, record commit SHA, timestamp

### The Watch Loop (codeintel watch)

Runs in foreground using `watchdog` library:

1. **File Monitor**
   - Observe filesystem changes in repo directory
   - Skip ignored paths (.git, node_modules, .venv, __pycache__, dist, build)

2. **Debounce**
   - Buffer file change events
   - Wait `--debounce` seconds (default 5s) since *last* change
   - Coalesce burst of file edits into single reindex trigger

   *Note: `watch.py`'s `should_ignore_path` uses its own ignore set, which does not
   include the `DerivedData` / `.build` entries added to `detect_language()`'s
   `_IGNORED_DIRS`. On an Xcode-project repo, build-artifact churn can therefore
   still trigger a debounce cycle.*

3. **Reindex**
   - Once debounce window closes, run the full indexing pipeline above
   - Atomically publish, zero query downtime

---

## Three Architectural Guarantees

### 1. Read-Only Runtime (Zero Writes During Queries)

**The guarantee:** Query operations open published indexes read-only with immutability flags. The runtime never mutates index files.

**Implementation:**
```python
# index_reader.py (vendored from SCIP source)
conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
```

**Why it matters:**
- Queries are safe concurrent operations; multiple readers can coexist
- No lock contention during index publish
- Safe on NFS and shared filesystems (immutable flag ensures WAL safety)

---

### 2. Atomic Publishing (Zero Downtime Reindex)

**The guarantee:** Publishing is atomic at the pointer boundary. A reindex writes a new versioned `.db`, populates the graph, and runs `zoekt-index` — only once *all* succeed does the pointer flip via `os.replace()`. A query already reading the old file keeps working; no partial-state window.

**Implementation:**
```python
# index_cli.py: Publish flow
new_db_path = index_dir / f"index-{commit_sha}.db"

# Expensive operations (can fail, no risk to old index)
populate_graph_for_repo(repo_slug, symbols)
zoekt_index(repo_path, output=new_db_path)

# Only once all succeed, atomically swap
current_pointer = index_dir / "current"
os.replace(new_db_path, current_pointer)  # POSIX atomic rename(2)
```

**Why it matters:**
- Zero query downtime across reindex
- If anything fails (graph population, Zoekt indexing), old index stays live
- No "half-indexed" state visible to queries
- Clients never see "index not found" mid-query

---

### 3. Rebuild-Not-Accumulate Graph (Always Current)

**The guarantee:** Each reindex clears that repo's own outgoing edges before recomputing them. Removed dependencies are retracted. The graph always reflects each repo's *last* index run, not an accumulation.

**Implementation:**
```python
# graph.py: Populate for repo
def populate_graph_for_repo(repo_slug: str, symbols: list[str]):
    # DELETE old edges for this repo first
    db.execute("DELETE FROM edges WHERE source_repo = ?", (repo_slug,))
    
    # Then INSERT new edges
    for pkg in extract_package_names(symbols):
        db.execute("INSERT INTO edges ...", (repo_slug, pkg, ...))
```

**Why it matters:**
- `blastRadius` results are never stale (don't reference deleted dependencies)
- No accumulation bugs from partial re-runs or tool restarts
- Graph is always consistent with the current index

---

## Query Path (Runtime)

When a Claude Code user calls a tool like `goToDefinition`:

1. **MCP Client** sends: `{"repo": "myrepo", "path": "src/main.ts", "line": 10, "character": 5}`

2. **server.py (MCP dispatcher)**
   - Unpacks args
   - Calls `QueryService.go_to_definition(...)`
   - Catches all exceptions → `{"error": "..."}`

3. **query.py (Query Engine)**
   - Looks up repo in registry.db (validate it exists, get commit SHA)
   - Opens `index-<sha>.db` read-only via `IndexConnectionCache`
   - Executes SQL query against documents/chunks/global_symbols tables
   - Builds result dataclass (Location, SymbolInfo, etc.)

4. **server.py (Response)**
   - Converts result dataclass to dict via `dataclasses.asdict()`
   - Returns `{"location": {...}, "symbol": {...}}` to MCP client

5. **MCP Client** (Claude Code)
   - Receives JSON response
   - Displays nav result to user (file, line, column, symbol)

---

## Search Path (Runtime)

When a user calls `searchCode`:

1. **First call only:** `ZoektLifecycle.ensure_running()` spawns `zoekt-webserver -rpc` in background
   - Pidfile: `~/.codeintel/.zoekt/zoekt.pid`
   - Listens on port (discovered from Zoekt binary)
   - Indexes already on disk in `.zoekt/` are auto-loaded

2. **HTTP Request** (search.py)
   - POST to `http://localhost:PORT/api/search`
   - JSON body: `{"q": "query string", "repos": ["repo1", "repo2"] (optional)}`

3. **HTTP Response**
   - Zoekt returns matched files + line fragments
   - Parse JSON, wrap in result types

4. **Response to MCP client**
   - Return list of matches with file path, line number, content snippet

**Lifecycle:**
- Server keeps running for subsequent `searchCode` calls (fast)
- On `codeintel-server` exit, `atexit` handler kills `zoekt-webserver`

---

## Graph Path (Runtime)

When a user calls `blastRadius(repo, symbol_or_package)`:

1. **Input Validation**
   - Validate repo is indexed in registry.db

2. **Package Name Resolution**
   - If input is a symbol (e.g., "python:requests"), extract package name
   - If already a package name, use as-is

3. **BFS (2-hop bounded)**
   - Query registry.db edges table
   - Find all repos whose packages depend on this package
   - Limit to 2 hops (direct + transitive)
   - Track hop distance for each dependent

4. **Result Building**
   - For each dependent repo, fetch metadata from registry.db
   - Build result with repo slug, hop distance, package name

5. **Response to MCP client**
   - Return `[{repo: "...", symbol_or_package: "...", distance: 1}, ...]`

---

## Storage Layout

```
~/.codeintel/                          # Default data dir (override via CODEINTEL_DATA_DIR)
├── registry.db                        # Master registry: repos table + packages + edges
├── scip/
│   └── _/
│       └── <slug>/
│           └── _/
│               ├── current            # Pointer file (text, content: commit SHA or db name)
│               ├── index-<sha1>.db    # SCIP SQLite (documents, chunks, global_symbols, ...)
│               └── index-<sha2>.db    # (old versions, kept for GC later)
└── .zoekt/
    ├── zoekt.pid                      # Pidfile (zoekt-webserver PID)
    └── <shards>                       # Zoekt index shards (repo-specific)
```

**registry.db schema:**
- `repos(slug TEXT PRIMARY KEY, path TEXT, language TEXT, commit_sha TEXT, last_indexed TIMESTAMP, status TEXT)`
- `packages(id, repo_slug, package_name)`
- `edges(source_repo TEXT, target_package TEXT, ...)`

**index-<sha>.db schema** (from `scip expt-convert`):
- `documents(document_id, path, text, relative_url, language)`
- `chunks(chunk_id, document_id, range_start_line, range_start_char, range_end_line, range_end_char)`
- `global_symbols(symbol_id, symbol, kind, display_name, ...)`
- `mentions(symbol_id, range_id, is_definition, is_type_definition, is_reference, ...)`
- `defn_enclosing_ranges(definition_id, enclosing_range_id, ...)`

---

## Concurrency & Thread Safety

### IndexConnectionCache (Thread-Safe)

The vendored `IndexConnectionCache` maintains a bounded pool of SQLite connections per unique (project, repo, branch, pointer_content) tuple.

- **Thread-safe:** Uses locks internally
- **Size-bounded:** Garbage collects stale connections
- **NFS-safe:** Detects pointer file changes and invalidates caches

### MCP Server (Single-Threaded)

The FastMCP server is single-threaded:
- No concurrent query handling
- No locking needed for static tables (registry.db)
- All database access is read-only (except during indexing)

### Indexing (Exclusive)

Indexing is exclusive — only one reindex can run at a time per slug (enforced by watching process or user discipline).

---

## Dependencies & External Tools

### Runtime Dependencies (Python)

- **mcp[cli]** — FastMCP framework
- **protobuf** — SCIP document decoding
- **zstandard** — SCIP blob decompression
- **httpx** — Zoekt webserver HTTP client
- **watchdog** (optional) — filesystem monitor for `codeintel watch`

### External Binaries (Must be on PATH)

- **Language indexers** (pick one or more):
  - `scip-typescript` — TypeScript/JavaScript indexing
  - `scip-python` — Python indexing
  - `scip-java` — Java/Kotlin indexing
  - `scip-swift` — Swift indexing. **Does not exist yet** — no such indexer is published
    upstream; `.swift` repos raise `IndexingError` until one is built and installed.
    Requires a macOS host (Xcode + iOS SDK) for any repo importing Apple-platform
    frameworks.
- **SCIP converter:**
  - `scip` (uses `scip expt-convert` subcommand)
- **Search indexer & server:**
  - `zoekt-index` — Zoekt indexing
  - `zoekt-webserver` — Embedded search server

---

## Failure Modes & Recovery

### Index Publish Fails (New Index Incomplete)

**What happens:** Indexing pipeline fails partway through (language indexer crashes, zoekt-index fails, etc.)

**Recovery:** Old index stays live. User can retry `codeintel reindex` once the issue is fixed.

### Query Against Missing Index

**What happens:** `repo` argument refers to an unindexed repo.

**Recovery:** `{"error": "Repo not indexed"}` returned to MCP client.

### Zoekt Webserver Dies

**What happens:** Embedded zoekt-webserver crashes mid-session.

**Recovery:** Next `searchCode` call triggers `ZoektLifecycle.ensure_running()` and respawns it. First call may be slow; subsequent calls are fast.

### Registry.db Corruption

**What happens:** Manual corruption or concurrent writes to registry.db.

**Recovery:** Manual backup restore or re-run `codeintel index` for all repos to rebuild registry.

---

## Known Limitations (Upstream)

These are real behaviors of SCIP/Zoekt, not codeintel bugs:

- **typeHierarchy returns empty:** SCIP v0.7.0 converter never populates `global_symbols.relationships`. Upstream issue in scip.proto or expt-convert tooling.
- **displayName / kind often null:** Same cause as above.
- **Zoekt repo filter matches directory name:** The basename of the directory you indexed, which can diverge from codeintel's `--slug` if passed. Unscoped searches are more reliable.
- **Package graph freshness always "unknown":** Graph has no per-node timestamp; only repo-level freshness (commit SHA) is tracked.

---

## Future Extensibility

### Adding a New Language Indexer

1. Create a new SCIP indexer (e.g., `scip-go` for Go)
2. Add to language detection in `index_cli.py` (`_LANGUAGE_INDEXERS` + `_EXT_PRIORITY`)
3. Test end-to-end (index repo → query nav tools)

Swift is the worked example of this path — the codeintel-side entry landed in one table,
but the indexer itself was the real work.

### Adding a New Query Tool

1. Implement query logic in `query.py`
2. Add MCP tool in `server.py` with error handling
3. Add tests in `test_server_tools.py` and `test_query.py`

### Upgrading SCIP Version

1. Regenerate `scip_pb2.py` from new `scip.proto`
2. Update version pin in README
3. Test with real indexes from new version
4. Adapt query logic if schema changes (comments in code help)

### Distributed / Multi-User (Phase 5+)

Would require:
- Multi-tenant schema (separate indexes per user/project)
- Auth layer (not MCP stdio, likely HTTP API)
- Centralized registry (shared database)
- Query caching / performance optimization
- Currently out of scope.
