# jarvis: System Architecture

## High-Level Overview

jarvis is a **local-first, single-user code intelligence MCP server** that combines structural navigation (SCIP-backed) with lexical search (Zoekt-backed) and natural-language semantic/vector search in a single stdio process. It bridges the SCIP indexing ecosystem with the MCP protocol, exposing 9 tools to Claude Code, Cursor, and other MCP clients: `documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `getIndexStatus`, `searchCode`, `semanticSearch`, `blastRadius`.

The system is built around five core engines:

1. **Query Engine** — reads navigation snapshots (SCIP tables + Tree-sitter syntax
   declarations in one SQLite db), executes nav queries (go-to-definition, find-references, etc.)
2. **Search Engine** — manages embedded Zoekt instance, returns lexical search results
3. **Graph Engine** — builds and queries package dependency relationships across indexed repos
4. **Semantic Engine** — tree-sitter chunking + self-hosted embeddings into a per-repo LanceDB table, fused with Zoekt hits for natural-language `semanticSearch`
5. **Syntax Engine** — the curated Tree-sitter grammar provider (`syntax.py`) and the immutable
   syntax snapshot builder (`syntax_index.py`): declaration-level extraction for 17 languages on
   every indexing run, no compiler or build system required

### Layered Architecture (primary view)

Seven layers, top to bottom. **Storage (layer 4) is the seam**: the runtime only ever reads
down into it, the indexing pipeline only ever writes up into it, and the two halves share no
other contract.

| Layer | Contents |
|---|---|
| 1 · Clients | Claude Code, Cursor, any MCP host |
| 2 · MCP Server | `server.py` — FastMCP over stdio, 9 tools |
| 3 · Engines | Query (`query.py`), Search (`search.py`), Graph (`graph.py`), Semantic (`semantic.py`, `chunker.py`, `embeddings.py`, `symbol_search.py`), Syntax (`syntax.py`, `syntax_index.py`) |
| 4 · Storage | `index-<sha>-<generation>.db` + `current` pointer, `.zoekt/` shards, `registry.db`, `~/.jarvis/lancedb/` |
| 5 · Indexing orchestration | `index_cli.py` — capture → syntax baseline → optional SCIP → Zoekt → optional semantic → graph → atomic publish |
| 6 · Language indexers & grammars | pip-installed Tree-sitter grammar wheels (base deps, always present) + optional `scip-typescript`, `scip-python`, `scip-java`, `scip-swift` |
| 7 · External toolchain | Node/npm, Python, JDK, and (Swift only) Xcode + iOS SDK — SCIP enrichment only; the syntax baseline needs none of these |

Layer 6→7 is where Swift differs from every other language: the other three indexers need only
an ordinary runtime, while `scip-swift` needs Xcode and the iOS SDK, which Apple ships for macOS
only.

### Runtime Detail

Expanding layers 1–4 of the table above, the runtime path is:

- **Client**: Claude Code / Cursor / any MCP client → MCP stdio
- **Server** (`server.py`): FastMCP dispatcher → 9 tools
- **Query Engine** (`query.py`): reads the navigation snapshot SQLite (SCIP documents/chunks/global_symbols tables + namespaced `syntax_*` tables) and routes per file
- **Search Engine** (`search.py`): HTTP client to embedded Zoekt webserver
- **Graph Engine** (`graph.py`): Queries package edges in registry.db
- **Semantic Engine** (`chunker.py` + `embeddings.py` + `semantic.py`): tree-sitter chunking → embeddings → per-repo LanceDB table, fused with Zoekt hits
- **Syntax Engine** (`syntax.py` + `syntax_index.py`): curated grammar provider + per-file provider facts read from the same snapshot connection
- **Storage**: navigation snapshots (index-<sha>-<generation>.db), Zoekt shards (.zoekt/), registry (registry.db), LanceDB tables (~/.jarvis/lancedb/)

---

## Indexing Pipeline & Publishing

The full indexing lifecycle, from file changes → published index (layers 5–7 of the table above,
writing up into the storage seam):

### The Staged Pipeline (index_cli.py)

`index_repo()` runs seven stages in a fixed order (spec TSI-04 §5). Every stage
has an explicit failure boundary; only stage-4 (Zoekt) and stage-6/7
(storage/publication) failures fail the run.

1. **Validation** — git input, slug/path ownership, persisted configuration.
   SCIP tooling is deliberately NOT validated here: it is optional enrichment,
   so a machine without any indexer can still publish a baseline.

2. **Syntax baseline (always runs)** — capture git-tracked source bytes
   (`syntax_index.capture_sources()`), extract or reuse declaration rows
   (`build_syntax_index()` keyed on file hash + grammar identity), build a
   scratch snapshot. This stage replaces the old step-1 language detection as
   the thing every run does: the baseline classifies every tracked file by its
   own extension across 17 languages, independent of the repo's primary
   language. A `--language` override (persisted, reused by `reindex`/`watch`)
   now selects only the SCIP enrichment language — it never restricts syntax
   coverage.

3. **Optional SCIP enrichment** — run the language indexer (`scip-typescript`,
   `scip-python`, `scip-java`, or `scip-swift` — Swift with
   `--build-tool xcodebuild` for Xcode-project repos) and `scip expt-convert`
   (produces `documents`, `chunks`, `global_symbols`, `mentions`,
   `defn_enclosing_ranges`). Gated on the reversible persisted
   `--scip`/`--no-scip` choice and the watch suppression predicate (same-commit
   retry of a known failure skips only this stage). Any expected failure —
   missing binary, old `scip`, indexer crash — records the cause and degrades
   the run to exit-0 `degraded`; the baseline still publishes. The old
   signature-matching search-only fallback is gone (superseded, spec §12).

4. **Lexical indexing** — `zoekt-git-index` into `.zoekt/`. Its failure fails
   the run (nothing publishes).

5. **Optional semantic stage** (`prepare_semantic` + `finish_semantic`,
   splitting the old `index_semantic()` so the syntax stage can share one parse
   per file) — chunks, embeds, writes the per-repo LanceDB table; non-fatal on
   failure or missing `semantic` extra.

6. **Revalidate + graph** — `validate_sources()` re-scans for mid-run file
   changes; graph edges update (a generation without usable SCIP data clears
   the repo's outgoing edges and keeps package identities; no edges are
   synthesized from Tree-sitter). Storage failures here are hard failures,
   never degradation.

7. **Publish, record, retire — strictly in that order** (see the diagram
   below): one immutable snapshot becomes visible via a single pointer flip,
   then the registry records the terminal status, then superseded snapshots
   are deleted. A crash between any of the three leaves either the old or the
   new snapshot live, never a half-published one.

### Single-snapshot publication

Every run publishes exactly one immutable SQLite navigation snapshot — syntax
tables plus, when the SCIP stage succeeded, genuine SCIP tables in the same
database — and readers select it through one `current` pointer:

```
 scratch build                     publish (atomic)                read path
┌─────────────────────────┐   ┌───────────────────────────────┐   ┌──────────────────────┐
│ syntax_index.build_     │   │ final name:                   │   │ IndexConnectionCache │
│ syntax_index(...)       │   │   index-<sha>-<generation>.db │   │ opens mode=ro&       │
│  + syntax tables        ├──►│   index-<sha>-<generation>.   ├──►│ immutable=1, keyed   │
│  + copied SCIP tables   │   │     metadata.json (same stem) │   │ on pointer CONTENT;  │
│  + jarvis_snapshot row  │   │ _publish_atomically():        │   │ a pointer change     │
│    (generation, commit, │   │   write .current.tmp-<pid>    │   │ invalidates by key,  │
│     scip_state, counts) │   │   os.replace → current        │   │ never by mtime       │
└─────────────────────────┘   └───────────────────────────────┘   └──────────────────────┘
```

`<generation>` is a fresh `uuid4().hex` per publish, so a same-commit reindex
never mutates or reuses a live filename. Retiring old snapshots happens only
after the registry records the new terminal state.

### Registry status vocabulary (spec TSI-06)

| Status | Meaning | Exit |
|---|---|---|
| `indexing` | Transient marker written when a run starts; every terminal decision replaces it | — |
| `indexed` | Baseline published; SCIP usable, disabled (`--no-scip`), or unsupported (no SCIP-indexable language — the baseline still covers the repo) | 0 |
| `partial` | Published with documented syntax/SCIP extraction gaps (e.g. positions published but no navigable chunks) | 0 |
| `degraded` | Published, but the enabled SCIP stage failed, is unavailable, or remains watch-suppressed at the same commit; cause + recovery recorded on the row | 0 |
| `failed` | A required stage (Zoekt), storage, publication, or registry failure; nothing new published, previous snapshot stays live | nonzero |

Each run also persists a SCIP stage state on the row: `available`, `partial`,
`failed`, `unavailable`, `unsupported`, `disabled` — read-time normalized to
`unknown` for historical rows with insufficient evidence (`SCIP_STATES` in
`registry.py` is the single source of the vocabulary; the CLI writer and the
MCP reader spell it identically).

### The Watch Loop (jarvis watch)

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
   - Once debounce window closes, run the full staged pipeline above
   - The syntax baseline, Zoekt, semantic stage, and publication all run every
     time; the watch suppression predicate may skip ONLY the SCIP retry when
     the persisted stage state is failed/unavailable at the same commit and the
     caller passed no explicit `--scip`/`--language`/`--scheme` override
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

**The guarantee:** Publishing is atomic at the pointer boundary. A reindex builds a new immutable `index-<sha>-<generation>.db` (fresh `<generation>` per publish, so a same-commit reindex never touches a live filename), populates the graph, and runs `zoekt-index` — only once *all* succeed does the pointer flip via `os.replace()`. A query already reading the old file keeps working; no partial-state window. The flip is pointer-content-only: superseded snapshots are retired strictly after the registry records the new terminal state, so cleanup can never destroy a live snapshot.

**Implementation:**
```python
# index_cli.py: publish flow (stage 7 of the staged pipeline)
generation = uuid.uuid4().hex
versioned_name = f"index-{sha}-{generation}.db"

# Expensive operations (can fail, no risk to the old snapshot)
#   ... SCIP conversion, graph edges, Zoekt shards ...

# Only once all succeed: pointer flip, then registry record, then retire
_publish_atomically(index_dir, versioned_name)
#   inside: write ".current.tmp-<pid>", then os.replace -> "current"
```

**Why it matters:**
- Zero query downtime across reindex
- If anything fails (graph population, Zoekt indexing), old snapshot stays live
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

1. **MCP Client** sends: `{"repo": "myrepo", "symbol": "Greeter.greet"}` (or
   `{"repo": ..., "path": ...}` for `documentSymbols`)

2. **server.py (MCP dispatcher)**
   - Unpacks args
   - Calls the matching `QueryService` method (`resolve_definition`,
     `get_document_symbols`, ...)
   - Catches all exceptions → `{"error": "..."}`; a SCIP-only tool asked for
     more than this snapshot can do renders `requiredCapability`/`reason`/
     `recovery` alongside the error string (never an empty array)

3. **query.py (Query Engine) — the per-file routing seam (spec TSI-05)**
   - Looks up repo in registry.db (validate it exists, get commit SHA)
   - Opens `index-<sha>-<generation>.db` read-only via `IndexConnectionCache`
   - Reads the snapshot's provider facts (`read_snapshot_facts()`:
     per-file SCIP outline/definition coverage + syntax parse states)
   - Routes per file: usable SCIP coverage → SCIP tables; otherwise the
     namespaced `syntax_symbols` rows extracted by the Tree-sitter baseline
   - A bare/qualified name resolves through both providers in parallel and
     merges candidates; full SCIP symbols and opaque `syntax:` identifiers
     resolve only through their own provider
   - Builds result dataclasses (Location carries `source` and
     `positionEncoding`; syntax outline entries add `selectionRange`)

4. **server.py (Response)**
   - Converts result dataclass to dict via `dataclasses.asdict()`
   - Returns `{"symbol": ..., "definitions": [...], "resolvedSymbol"?,
     "coverage"?, freshness...}` to MCP client

5. **MCP Client** (Claude Code)
   - Receives JSON response
   - Displays nav result to user (file, line, column, symbol, provenance)

---

## Search Path (Runtime)

When a user calls `searchCode`:

1. **First call only:** `ZoektLifecycle.ensure_running()` spawns `zoekt-webserver -rpc` in background
   - Pidfile: `~/.jarvis/.zoekt/zoekt.pid`
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
- On `jarvis-server` exit, `atexit` handler kills `zoekt-webserver`

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

## Semantic Path (Runtime)

When a user calls `semanticSearch(repo, query, limit=10)`:

1. **Admission (indexing time, `chunker.py`)**
   - `iter_source_files()` walks the repo, then drops anything `.gitignore` matches (via one
     batched `git check-ignore --stdin` call, `gitignored()`) — a subprocess failure degrades to
     "nothing ignored" rather than disabling filtering repo-wide
   - `oversized_file_reason()` rejects any file over 1 MB (`MAX_FILE_BYTES`), checked from `stat()`
     before the file is ever read into memory — a backstop for large-but-not-generated content that
     the banner/long-line generated-file filter (see roadmap) wouldn't otherwise catch
   - `--semantic-include <prefix>` is a single escape hatch across all three admission checks
     (generated-file banner, gitignore, size cap) — a force-included path is always admitted

2. **Chunking (indexing time, `chunker.py`)**
   - Parse each source file with tree-sitter, splitting at function/class boundaries
     (256-512 token target, reserving `HEADER_RESERVE_TOKENS` headroom for the header added below);
     an unparseable language falls back to fixed-window chunks
   - As a final pass, every chunk gets a `# file: <path>` context header (plus `# in class: <Name>`
     when the chunk is a method split out of an oversized class) — this re-adds context that the
     chunk's own text lost when it was split from its surrounding file
   - Each chunk carries a content hash (over the header + code, so a header change invalidates the
     hash) for dedup and the file hash for change detection
   - `chunk_file()` reports p50/p90/max token-size percentiles (`TokenStats`) over the batch, for
     CLI visibility into how close chunks are running to `MAX_TOKENS`

3. **Embedding (indexing time, `embeddings.py`)**
   - Lazy-loaded `EmbeddingModel` (default `BAAI/bge-m3`, 1024-dim, pinned revision) embeds
     changed chunks; unchanged files carry over their existing vectors by file hash
   - A model-aware query/document instruction prefix is applied before encoding — auto-detected by
     substring match against `MODEL_PREFIXES` (bge-m3, e5, nomic-embed), overridable via
     `JARVIS_EMBEDDING_QUERY_PREFIX`/`JARVIS_EMBEDDING_DOC_PREFIX`. An unlisted model with no
     override surfaces a `prefix_warning()` instead of silently applying no prefix
   - Vectors are L2-normalized for cosine search

4. **Storage (`semantic.py`: `SemanticStore`)**
   - One LanceDB table per repo under `~/.jarvis/lancedb/`, atomically overwritten per index run
   - The table records a full `TableIdentity` used to build it (`table_identity()`): model name,
     model revision, query prefix, doc prefix, and `CONTENT_FORMAT` (chunker.py's version of the
     stored chunk-text shape) — a table is only reused as a carry-forward source when every field
     matches

5. **Query (`semantic.py`: `semantic_search()`)**
   - Embeds the query using the full identity stored in the table (model, revision, *and*
     prefixes — never whatever's currently configured) and runs a cosine-metric vector search
   - `symbol_search.search_symbols()` matches query tokens against the SCIP name map (when a
     SCIP index exists) and resolves ranked candidates to definition locations — the third signal
   - Fuses vector hits, `searchCode`'s Zoekt lexical hits, and the symbol-definition hits via
     `reciprocal_rank_fusion()` (k=60, all signals unweighted); each signal degrades silently
     when unavailable — no SCIP index or a Zoekt spawn failure never errors the search

6. **Response to MCP client**
   - Returns ranked hits; includes a `"warning"` field if the table's recorded identity differs
     from the currently configured one, nudging a reindex

**Key invariant:** a LanceDB table only ever holds vectors from one `TableIdentity` at a time —
model, revision, prefixes, and content format together. If any part changes, old vectors are never
reused — every chunk is fully re-embedded on the next `jarvis index`/`reindex`. This is what
prevents silently mixing incompatible embedding spaces or wrong-prefix/wrong-header text.

---

## Storage Layout

```
~/.jarvis/                          # Default data dir (override via JARVIS_DATA_DIR)
├── registry.db                        # Master registry: repos table + packages + edges
├── scip/
│   └── _/
│       └── <slug>/
│           └── _/
│               ├── current                          # Pointer file (text, content: a versioned db filename)
│               ├── index-<sha>-<generation>.db      # Navigation snapshot (SCIP + syntax tables); NEVER mutated
│               ├── index-<sha>-<generation>.metadata.json  # Sibling metadata (same filename stem)
│               └── index-<sha>-<generation2>.db     # (older generations, retired after the registry records the new state)
├── .zoekt/
│   ├── zoekt.pid                      # Pidfile (zoekt-webserver PID)
│   └── <shards>                       # Zoekt index shards (repo-specific)
└── lancedb/
    └── <slug>                         # One LanceDB table per repo (semantic search vectors)
```

**registry.db schema:**
- `repos(slug TEXT PRIMARY KEY, path TEXT, language TEXT, commit_sha TEXT, last_indexed TIMESTAMP, status TEXT, scheme_override TEXT, semantic_indexed_at TEXT, semantic_include TEXT, language_override TEXT, ...)` plus the stage/outcome columns (`tracked_files`, `status_origin`, `status_reason`, `status_stderr`, `scip_enabled`, `scip_state`, `scip_failure_reason`, `scip_failure_stderr`, `scip_failed_at_sha`, `semantic_declined`) written transactionally per terminal run decision; a one-time idempotent migration carries legacy search-only rows forward
- `packages(id, repo_slug, package_name)`
- `edges(source_repo TEXT, target_package TEXT, ...)`

**index-<sha>-<generation>.db schema** — two namespaces in one immutable snapshot:
- SCIP tables (from `scip expt-convert`): `documents(document_id, path, text, relative_url, language)`, `chunks(chunk_id, document_id, ...)`, `global_symbols(symbol_id, symbol, kind, display_name, ...)`, `mentions(symbol_id, range_id, ...)`, `defn_enclosing_ranges(definition_id, enclosing_range_id, ...)`
- Jarvis-owned syntax tables (spec TSI-04 §5, prefixed `syntax_`): `syntax_files` (per-file parse state + provider coverage flags), `syntax_symbols` (declaration name/kind/span + opaque `syntax:` id), and the `jarvis_snapshot` singleton (format version, generation, commit, published time, source manifest hash, `scip_state`, per-file SCIP capability facts, extraction counts)

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
- **tree-sitter** + 16 curated grammar packages (`tree-sitter-python`,
  `tree-sitter-typescript`, ...) — **base dependencies** (spec TSI-02): the
  syntax baseline parses offline from prebuilt abi3 wheels; nothing is
  vendored into jarvis's own wheel and nothing is downloaded at index time.
  `syntax.py`'s `FACTORIES` is the one curated map from internal language name
  to (distribution, module, factory); repository-supplied grammar code is
  never instantiated
- **watchdog** (optional, `--extra watch`) — filesystem monitor for `jarvis watch`
- **lancedb**, **sentence-transformers** (optional, `--extra semantic`) —
  embedding and vector storage for `semanticSearch`. Every import of the
  extra's packages is deferred inside functions, never at module top-level,
  so a base install is completely unaffected.

### External Binaries (Must be on PATH)

- **Language indexers** (pick one or more):
  - `scip-typescript` — TypeScript/JavaScript indexing
  - `scip-python` — Python indexing
  - `scip-java` — Java/Kotlin indexing. **Known limitations:** Android/AGP projects produce zero SCIP shards because scip-java's Gradle plugin relies on standard source sets that AGP replaces with variants (upstream scip-java#177); Kotlin versions other than the pinned release fail with AbstractMethodError or NoSuchMethodError because scip-kotlinc is compiled against exactly one Kotlin version and the compiler-plugin API is internal/unstable. Both are detected from the indexer's own failure output and degrade the run to exit-0 `degraded` (SCIP skipped; the syntax baseline still publishes).
  - `scip-swift` — Swift indexing ([phuongddx/scip-swift](https://github.com/phuongddx/scip-swift)).
    Exists, builds, and runs end-to-end via `jarvis index` without error, populating the
    symbol table. Requires a macOS host (Xcode + iOS SDK) for any repo importing Apple-platform frameworks.
- **SCIP converter:**
  - `scip` (uses `scip expt-convert` subcommand)
- **Search indexer & server:**
  - `zoekt-index` — Zoekt indexing
  - `zoekt-webserver` — Embedded search server

---

## Failure Modes & Recovery

### Index Publish Fails (New Index Incomplete)

**What happens:** Indexing pipeline fails partway through (language indexer crashes, zoekt-index fails, etc.)

**Recovery:** Old index stays live. User can retry `jarvis reindex` once the issue is fixed.

### Query Against Missing Index

**What happens:** `repo` argument refers to an unindexed repo.

**Recovery:** `{"error": "Repo not indexed"}` returned to MCP client.

### Zoekt Webserver Dies

**What happens:** Embedded zoekt-webserver crashes mid-session.

**Recovery:** Next `searchCode` call triggers `ZoektLifecycle.ensure_running()` and respawns it. First call may be slow; subsequent calls are fast.

### Registry.db Corruption

**What happens:** Manual corruption or concurrent writes to registry.db.

**Recovery:** Manual backup restore or re-run `jarvis index` for all repos to rebuild registry.

---

## Known Limitations (Upstream)

These are real behaviors of SCIP/Zoekt, not jarvis bugs:

- **typeHierarchy returns empty:** SCIP v0.9.0 converter never populates `global_symbols.relationships`. Upstream issue: [scip-code/scip#464](https://github.com/scip-code/scip/issues/464), fixed by [PR #465](https://github.com/scip-code/scip/pull/465).
- **displayName / kind often null:** Same cause as above.
- **Zoekt repo filter matches directory name:** The basename of the directory you indexed, which can diverge from jarvis's `--slug` if passed. Unscoped searches are more reliable.
- **Package graph freshness always "unknown":** Graph has no per-node timestamp; only repo-level freshness (commit SHA) is tracked.

---

## Future Extensibility

### Adding a New Language Indexer

1. Create a new SCIP indexer (e.g., `scip-go` for Go)
2. Add to language detection in `index_cli.py` (`_LANGUAGE_INDEXERS` + `_EXT_PRIORITY`) — this also
   extends `_INDEXER_BY_LANGUAGE`, the derived reverse map that validates `--language` values, so
   the two cannot drift
3. Test end-to-end (index repo → query nav tools)

Swift is the worked example of this path — the jarvis-side entry landed in one table,
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
