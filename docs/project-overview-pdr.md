# codeintel: Project Overview & PDR

## What Is codeintel?

**codeintel** is a personal, local-first code intelligence MCP server that brings structural code navigation (SCIP-backed), lexical search (Zoekt-backed), and natural-language semantic/vector search to Claude Code, Cursor, and any MCP client. It runs as a single stdio process — no server, no auth, no network.

**Core value proposition:**
- **Structural navigation** (SCIP) at your fingertips: go-to-definition, find-references, call/type hierarchy, document symbols
- **Lexical search** with Zoekt: index your repos once, search instantly across all indexed code
- **Semantic/vector search:** ask in plain English (`semanticSearch`) and get tree-sitter-chunked code ranked by a self-hosted embedding model, fused with lexical hits — distinct from SCIP's structural navigation above
- **Single-user, local:** read-only runtime, atomic publish guarantees, privacy-by-default
- **Minimal dependencies:** stdlib sqlite3, plain dataclasses, no ORMs or async framework bloat

## Who Is It For?

- **Personal developers** working with multiple repositories and wanting MCP-integrated code intelligence
- **Users of Claude Code / Cursor** who want semantic results without cloud services
- **Teams / enterprises** (future, Phase 5+) — roadmap planned; not in current scope

## Scope

### In Scope (Shipped, All 4 Phases Complete)

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Scaffold + vendored SCIP core (`scip_pb2`, `scip_decoder`, `index_reader`) | ✓ Done |
| 2 | MCP stdio server + 5 SCIP nav tools + `getIndexStatus` | ✓ Done |
| 3 | Indexer CLI (`codeintel index`), registry, embedded Zoekt + `searchCode` | ✓ Done |
| 4 | `blastRadius` (package dependency graph) + `codeintel watch` (auto-reindex) | ✓ Done |
| Post-Phase-4 | Semantic/vector search (`semanticSearch`): tree-sitter chunking, self-hosted embeddings, per-repo LanceDB store, fused with Zoekt via reciprocal rank fusion. Gated behind the optional `semantic` extra | ✓ Done |

**Language support:** TypeScript, Python, Java/Kotlin, and Swift — `.swift` repos are recognized by `detect_language()` and routed to [`scip-swift`](https://github.com/phuongddx/scip-swift), which builds and indexes end-to-end. All 9 nav tools return correct results on real Swift repos. Requires a macOS host (Xcode + iOS SDK) for repos importing Apple-platform frameworks. One language per index; language detection by file-extension plurality across git-tracked files. Pass `--language <name>` to override detection.

**Java/Kotlin caveat:** SCIP navigation is supported for plain JVM Gradle/Maven repos with `scip-java`, but two cases fall back to `--search-only` (lexical search + semantic search only, no navigation): Android/AGP projects (scip-java's Gradle plugin relies on standard source sets that AGP replaces with variants, producing zero SCIP shards upstream scip-java#177); and Kotlin versions other than the pinned release (scip-kotlinc is compiled against exactly one Kotlin version — others fail with AbstractMethodError/NoSuchMethodError). The `--search-only` fallback is applied automatically on these detected failures, or can be requested explicitly on `codeintel index` to skip SCIP indexing entirely, publishing Zoekt + semantic search for 10 additional languages (Go, Ruby, Rust, C, C++, C#, PHP, Scala, shell, SQL).

**9 MCP tools:** `documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `getIndexStatus`, `searchCode`, `semanticSearch`, `blastRadius`

### Out of Scope (Explicitly Not Planned)

- **Cloud deployment** (Phase 5 in original brainstorm; deprioritized indefinitely)
- **Stats/dashboard endpoints** — codeintel is a query engine, not an analytics backend
- **Monorepo language-merge polish** — multi-language indexing would require schema refactoring not justified by single-user use case
- **GUI** — CLI + MCP tools only
- **Incremental indexing** — full rebuild on each `index` / `watch` reindex
- **Custom symbol filtering / search rankinge tuning** — delegates to SCIP and Zoekt as-is

## Architectural Guarantees

**Read-only runtime:** Query operations open published indexes read-only (`mode=ro&immutable=1`). The runtime never mutates index files.

**Atomic publishing:** Reindex writes a new versioned `.db`, populates the package graph, and runs `zoekt-index`. Only once all succeed does `os.replace` flip the small `current` pointer. A query already reading the old file keeps working; zero downtime, no partial-state windows.

**Rebuild-not-accumulate graph:** Each reindex clears that repo's outgoing package dependencies before recomputing them. Removed dependencies are retracted. The graph always reflects each repo's *last* index run, not an accumulation.

## Dependencies

Core runtime:
- Python 3.12+
- `mcp[cli]` — FastMCP for stdio server
- `protobuf` — scip_pb2 message decoding
- `zstandard` — SCIP blob decompression
- `httpx` — Zoekt webserver client
- `watchdog` (optional, `--extra watch`) — file monitor for `codeintel watch`

Optional, gated behind `--extra semantic` (not required for the base install):
- `lancedb` — per-repo vector table storage
- `sentence-transformers` — self-hosted embedding model (`BAAI/bge-m3` by default)
- `tree-sitter`, `tree-sitter-language-pack` — AST-aware source chunking

External binaries (must be on `PATH`):
- Language indexers: `scip-typescript`, `scip-python`, `scip-java` (pick per language); `scip-swift` ([phuongddx/scip-swift](https://github.com/phuongddx/scip-swift))
- SCIP converter: `scip` (uses `scip expt-convert`)
- Search indexers: `zoekt-index`, `zoekt-webserver`

## Entry Points

| Command | Module | Purpose |
|---------|--------|---------|
| `codeintel` | `index_cli.py` | Indexing, registry, watch |
| `codeintel-server` | `server.py` | MCP stdio server |

## Distribution

codeintel is available through three discovery and installation channels:

**PyPI package:** `codeintel-navigation-mcp` — Install via `pip install codeintel-navigation-mcp` or `uv sync` from source. Published on every release via GitHub Actions OIDC auth.

**Claude Code plugin:** Available via `/plugin marketplace add phuongddx/codeintel` (plugin marketplace discovery) then `/plugin install codeintel@codeintel` (auto-registers the MCP server, alternative to manual `claude mcp add`). Plugin manifest and MCP registration live under `plugin/.claude-plugin/` and `plugin/.mcp.json`; skills are under `plugin/skills/`.

**MCP Registry:** Official listing at [`io.github.phuongddx/codeintel`](https://modelcontextprotocol.io/registry) — published via `server.json` (MCP Registry server descriptor) on every release, gated on PyPI publish success to ensure availability. Allows MCP clients (beyond Claude Code) to discover and install codeintel.

**Version consistency:** All four version fields (pyproject.toml, server.json, plugin manifest, MCP registration floor) are asserted identical by `scripts/check_versions.py`, run automatically in CI and as a test (`tests/test_check_versions.py`), preventing version drift across distribution channels.

## Database Schema

**Indexing:**
- `registry.db` — repos table (slug/path/language/commit_sha/last_indexed/status/scheme_override/semantic_indexed_at/semantic_include/language_override); packages/edges tables (dependency graph)
- Per-repo: `index-<sha>.db` (from `scip expt-convert`) — documents/chunks/global_symbols/mentions/defn_enclosing_ranges
- Zoekt shards: `.zoekt/` directory (spawned lazily)
- `~/.codeintel/lancedb/` — one LanceDB vector table per repo (semantic search), gated behind the `semantic` extra

**Current pointer:** Small `current` file per repo, atomically updated on successful publish.

## Success Criteria (All Met)

- From Claude Code (user-scope MCP), on real TypeScript and Python repos: all 9 tools return correct results; nav results hand-verified on known symbols ✓
- `codeintel index <repo>` end-to-end: detect language → run language indexer → `scip expt-convert` → zoekt-index → atomic pointer swap → registry update ✓
- `getIndexStatus` correctly flags stale after new commits; reindex has zero query downtime ✓
- Test suite: all phases gate on `uv run pytest` green (12 test modules, 1-1 map to src modules except `__init__.py`/`models.py`, plus fixtures with real SCIP/Zoekt blobs — 17 files total under `tests/`) ✓

## Non-Goals / Known Limitations

**Upstream (not bugs):**
- `typeHierarchy` returns empty (`scip expt-convert` v0.9.0 never populates `global_symbols.relationships` — upstream issue [scip-code/scip#464](https://github.com/scip-code/scip/issues/464), fixed by [PR #465](https://github.com/scip-code/scip/pull/465))
- `displayName` / `kind` often null for the same reason
- Zoekt `repo` filter matches directory basename, not codeintel slug — may diverge if `--slug` was passed
- **Java/Kotlin gaps** (see "Language support" above): Android/AGP projects produce no SCIP shards (upstream scip-java#177), and Kotlin versions other than the pinned release fail to compile with scip-kotlinc (compiler-plugin API is internal/unstable) — both trigger automatic fallback to `--search-only` with lexical+semantic search only

**By design:**
- No per-node timestamp on the package graph → `blastRadius` always reports `freshness: "unknown"`
- Cross-repo dependency edges resolve by exact package name — index dependencies first, or re-run `codeintel index` after indexing them, for edges to appear
- Single-user only; no auth, no multi-tenant schema
- No config file; env vars + CLI flags only

## Standards & Compliance

- **SCIP protocol:** `scip_pb2.py` is generated from `scip.proto` at sourcegraph/scip **v0.9.0** (regenerated from v0.7.0 because v0.7.0 lacked the `typed_range` oneof that `scip-swift` requires)
- **SQLite schema:** Output of `scip expt-convert` (not a published spec, treated as a moving target across releases)
- **Code standards:** Dataclasses over Pydantic, stdlib sqlite3 (no ORMs), broad exception-handling in MCP server (uniform error payload), atomic pointer-swap for publish safety
