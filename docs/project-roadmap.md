# codeintel: Project Roadmap

## Current Status: All Phases Complete

**All 4 planned implementation phases are shipped and verified.**

See [`plans/0724-2316-codeintel-mcp-implementation/`](../plans/0724-2316-codeintel-mcp-implementation/) for the detailed implementation plan and phase documentation.

### Phase Completion Summary

| Phase | Feature | Delivered | Verification |
|-------|---------|-----------|--------------|
| **1** | Scaffold + vendored SCIP core (scip_pb2, scip_decoder, index_reader) | ✓ July 24 | Unit tests green, vendored code functional |
| **2** | MCP stdio server + 5 SCIP nav tools + getIndexStatus | ✓ July 24 | 9 tools registered, error handling tested |
| **3** | Indexer CLI, registry, embedded Zoekt + searchCode | ✓ July 25 | End-to-end index pipeline, search working |
| **4** | blastRadius (package graph) + codeintel watch (auto-reindex) | ✓ July 25 | Graph BFS tested, watch debounce functional |

### Post-Phase-4: Swift Detection (Landed, July 25)

`detect_language()` now recognizes `.swift` and routes majority-Swift repos to `scip-swift`,
and `DerivedData`/`.build` are excluded from the extension-majority scan.

**Update (July 26) — Swift navigation works; the earlier diagnosis was wrong.** An earlier note
claimed `scip-swift` emitted no occurrence ranges; incorrect. `scip-swift` correctly sets
`single_line_range` (the `typed_range` oneof, scip.proto's current spec), but two consumers were
dropping it: `scip expt-convert` v0.7.0 predates the code that reads `typed_range` and silently
produced `chunks=0, mentions=0` (fixed by pinning v0.9.0, `index_repo()` now refuses older); and
codeintel's vendored `scip_pb2.py` was generated from scip.proto v0.7.0 with no `typed_range` field
(regenerated from v0.9.0; `scip_decoder.py` reads `typed_range` first with a deprecated-`range`
fallback). Verified end-to-end on a real 21-file Swift repo: all of `documentSymbols`,
`goToDefinition`, `findReferences`, `callHierarchy` return real results.

**Invocation compatibility (July 26):** `scip-swift` is invoked in its *bare* form
(`scip-swift --output <path>`, no `index` subcommand) since its `index` subcommand landed after
`v0.1.0` and the old binary parses `index` as the repo path otherwise. Works on every version —
old binaries default the repo path to cwd, newer ones dispatch to `index` as their default. Floor
is now `v0.1.2` — see "Swift Index-Safe Code-Signing Defaults" below.

**`typeHierarchy` is unavailable, and now says so.** `scip expt-convert` declares
`global_symbols.relationships` in its schema but never writes it, so the tool returns an explicit
`{"error": ...}` rather than empty arrays (an empty result would wrongly assert "no supertypes").
Reported upstream: [scip-code/scip#464](https://github.com/scip-code/scip/issues/464), fixed by
[scip-code/scip#465](https://github.com/scip-code/scip/pull/465) (open). `query.py`'s logic
self-heals once a future converter populates the column.

**Acceptance criteria met:**
- ✓ All 9 MCP tools return correct results on real TypeScript/Python repos
- ✓ `codeintel index` end-to-end: language detection → indexer → scip expt-convert → zoekt-index → atomic publish → registry update
- ✓ `getIndexStatus` correctly flags stale after new commits; reindex has zero query downtime
- ✓ Test suite green: `uv run pytest` passes, 12 test modules (17 files total under `tests/` with fixtures), integration tests use real binaries

### Post-Phase-4: Swift xcodebuild Build-Tool Override (Landed, July 27, PR #1)

Swift repos with a checked-in `.xcodeproj` or `.xcworkspace` (but no macOS-compatible Package.swift) are now indexed via `scip-swift --build-tool xcodebuild` instead of the default SwiftPM backend. Rationale: `scip-swift`'s `BuildBackendDetector` picks SwiftPM whenever `Package.swift` exists, even for UIKit-only iOS packages with no macOS platform support, where plain `swift build` fails with "no such module 'UIKit'".

New features:
- `_prefers_xcodebuild(repo_path)` — detects presence of `.xcodeproj`/`.xcworkspace`
- `_swift_indexer_cmd(base_cmd, repo_path, scheme)` — appends `--build-tool xcodebuild` and optional `--scheme`
- `--scheme` CLI flag on both `codeintel index` and `codeintel watch` — specify Xcode scheme for repos with multiple schemes
- `scheme_override` column in registry.db — persists the chosen scheme across `reindex` and `watch` runs; `_resolve_scheme()` manages the None-preserves / explicit-overwrites semantics
- `_ensure_scheme_override_column()` idempotent migration — adds the new column to existing databases

Verified: End-to-end indexing works on real Swift repos with Xcode projects; all 8 MCP nav tools return correct results.

---

### Post-Phase-4: Swift Index-Safe Code-Signing Defaults (Landed, July 31)

Swift repos with signed app-extension targets could not be indexed: `scip-swift`'s xcodebuild
backend passed no `-destination`, so xcodebuild auto-selected `My Mac`, then failed provisioning
for every signed target during `GatherProvisioningInputs` before compiling anything. Reproduced
against a 5-target real repo (`luz_epost_ios`).

Fixed upstream in `scip-swift` v0.1.2, not in codeintel: `XcodebuildBuildRunner`'s `xcodebuild`
invocation now always passes `CODE_SIGNING_ALLOWED=NO`, `CODE_SIGNING_REQUIRED=NO`,
`CODE_SIGN_IDENTITY=`, and `CODE_SIGN_ENTITLEMENTS=` — an index build never ships the product, so
signing is dead weight on that path. codeintel's only change is the `SCIP_SWIFT_VERSION` pin in
`setup.sh`; no new CLI flag, registry column, or `_swift_indexer_cmd()` change. `-destination` was
deliberately not added: disabling signing alone was sufficient, and forcing an iOS destination
would break the macOS-only repos that also route through xcodebuild.

**Verified:** re-indexing the reproduction repo with v0.1.2 produces zero provisioning errors
(previously five) and reaches Swift compilation. That repo's build still fails afterward for an
unrelated pre-existing reason (a source file referenced by the Xcode project is missing from the
checkout) — `codeintel status` correctly reports `failed`, no index published, atomic publish
behaving as designed. The `xcodebuild: WARNING: … name:My Mac` line still appears as an expected
warning, not a failure, since `-destination` is deliberately not passed.

**Known related gap:** `scip-swift` hardcodes `-configuration Debug`; schemes with custom
configuration names (e.g. `Debug Development`) get `Debug` forced regardless. Not yet addressed.

See `docs/superpowers/specs/2026-07-31-scip-swift-signing-defaults-design.md`.

---

### Semantic/Vector Search (Landed, July 30)

Added natural-language code search (`semanticSearch`) alongside the existing structural nav
(SCIP) and lexical search (Zoekt) — a third, complementary way to find code, for queries that
don't map to an exact symbol name or keyword.

**New modules:**
- `chunker.py` — tree-sitter AST chunking into function/class-sized chunks (256-512 token
  target), with a fixed-window fallback for unparseable languages
- `embeddings.py` — lazy-loaded self-hosted embedding model wrapper (`BAAI/bge-m3`, 1024-dim,
  pinned revision, via `sentence-transformers`), L2-normalized vectors; `SemanticExtraMissingError`
  for a clean skip when the optional extra isn't installed
- `semantic.py` — `SemanticStore` (one LanceDB table per repo under `~/.codeintel/lancedb/`,
  cosine-metric search), `index_semantic()` (chunk → dedup by content hash → embed → carry over
  unchanged files by file hash → atomic table overwrite), `reciprocal_rank_fusion()` (merges
  vector hits with Zoekt lexical hits, k=60), `semantic_search()`

**Modified:** `config.py` (`IGNORED_DIRS`, `lancedb_dir()`); `index_cli.py` (`index_repo()` now
runs a non-fatal semantic-indexing stage after Zoekt and before atomic publish — a missing extra
or any semantic-stage failure is caught and logged, never blocks the SCIP/Zoekt publish; `forget`
also drops the repo's LanceDB table); `registry.py` (nullable `semantic_indexed_at` column,
survives failed semantic reindexes, `mark_semantic_indexed()`); `server.py` (new 9th tool
`semanticSearch(repo, query, limit=10)`, same `{"error": ...}` pattern as every other tool,
degrades to vector-only if Zoekt is unavailable).

New optional extra in `pyproject.toml`, mirroring the existing `watch` extra:
`semantic = ["lancedb>=0.20", "sentence-transformers>=3.0", "tree-sitter>=0.25", "tree-sitter-language-pack>=0.1"]`,
installed via `uv sync --extra semantic`. Every heavy import (lancedb, sentence-transformers,
tree-sitter) is deferred inside functions, never at module top-level — a base install's behavior
is unchanged without the extra.

**Key architectural decision — model-identity rule:** a LanceDB table only ever holds vectors from
one embedding model + revision at a time. If the configured model changes, the old table's vectors
are never reused — every chunk is fully re-embedded. At query time, `semanticSearch` always embeds
the query using the model identity recorded in the table (not whatever's currently configured),
and includes a `"warning"` field in the result if the two differ, nudging a reindex. This is what
prevents silently mixing incompatible embedding spaces.

New unit tests: `test_chunker.py`, `test_embeddings.py`, `test_semantic.py`. One new integration
test in `test_index_cli.py` runs the full real pipeline with a small real model
(`sentence-transformers/all-MiniLM-L6-v2`) to keep integration-test runtime reasonable — the
production default is still `BAAI/bge-m3`.

### Post-Phase-Semantic: Pre-Indexing Admission Filter (Landed, July 30)

`chunker.py`'s `skip_reason()` now filters generated/minified files out of the semantic index
before embedding — a generated-banner scan (`GENERATED_BANNERS`, built via string concatenation
so `chunker.py` itself doesn't match its own filter) plus an overlong-line check catches files
like `scip_pb2.py` that are pure noise once embedded, while staying lexically searchable through
Zoekt.

New features:
- `--semantic-include` CLI flag (repeatable) on `codeintel index` — force-include a path prefix the generated-file filter would otherwise skip
- `semantic_include` column in registry.db — persists the force-include prefixes across `reindex` and `watch` runs; `_resolve_semantic_include()` manages the None-preserves / explicit-overwrites semantics (same contract as `_resolve_scheme`)
- `_ensure_semantic_include_column()` idempotent migration — adds the new column to existing databases

### Post-Phase-Semantic: Chunk Context Enrichment & Model-Aware Prefixes (Landed, July 31, PR #3)

Bundled two changes that both invalidate every stored vector, so they cost one full re-embed
instead of two: static per-chunk context headers, and model-aware embedding instruction prefixes.
Also closed two gaps found along the way — no `.gitignore` support, and no backstop for large
non-generated files — and added chunk-size visibility.

**Context headers (`chunker.py`):** every chunk now gets a `# file: <path>` header (plus
`# in class: <Parent>` when the chunk is a method split out of an oversized class), applied as a
final pass after splitting/merging so `_merge_small`'s concatenation never duplicates a header.
Replaces the old per-class import-line prefix (`_collect_imports`/`MAX_IMPORT_LINES`), which is
now removed. `MAX_TOKENS` sizing reserves `HEADER_RESERVE_TOKENS` so a chunk sized to the cap
doesn't overflow once its header lands.

**Model-aware prefixes (`embeddings.py`):** `EmbeddingModel` now applies a query/document
instruction prefix before encoding, auto-detected by substring match against `MODEL_PREFIXES`
(bge-m3, e5, nomic-embed — longest-pattern-first for determinism), overridable via
`CODEINTEL_EMBEDDING_QUERY_PREFIX`/`CODEINTEL_EMBEDDING_DOC_PREFIX`. An unlisted model with no
override produces a `prefix_warning()` instead of silently shipping no prefix; `index_cli.py`
surfaces it as `warning: ...` on stderr, `semantic_search()` folds it into the result's
`"warning"` field.

**Table identity extended (`semantic.py`):** `table_identity()` now returns a `TableIdentity`
(model name, model revision, query prefix, doc prefix, `CONTENT_FORMAT` — chunker.py's version of
the stored chunk-text shape) instead of a bare `(model, revision)` tuple. A table is only reused
as a carry-forward source when every field matches, so a content-format bump (or a prefix change)
can't leave old-format rows on a file whose bytes never changed (carry-forward keys on
`file_hash`, not `content_hash`). Query-time reconstruction of the embedding model restores the
table's *stored* prefixes explicitly, never the currently configured ones.

**Admission gaps closed (`chunker.py`):** `iter_source_files()` now drops `.gitignore`-matched
paths via one batched `git check-ignore --stdin` call (`gitignored()`; a subprocess failure
degrades to "nothing ignored", never to over-filtering); `oversized_file_reason()` adds a 1 MB
size backstop (`MAX_FILE_BYTES`), checked from `stat()` before the file is read. `--semantic-include`
was extended to override both new checks, the same way it already overrode the generated-file
filter — a single escape hatch across all three, not three separate ones.

**Visibility:** `chunk_file()` reports chunk-size percentiles (`TokenStats`: p50/p90/max) over
each indexing run; `index_cli.py` prints them alongside the existing skip/truncation report.

Also fixed along the way: a methods-less oversized class (e.g. constants-only) now windows like
an oversized top-level def instead of shipping as one unbounded chunk.

New/updated tests: `test_chunker.py` (headers, gitignore, size cap, no-methods windowing),
`test_embeddings.py` (prefix resolution and precedence), `test_index_cli.py` (percentile/warning
output). Design docs: [`docs/superpowers/specs/2026-07-30-chunk-context-enrichment-design.md`](superpowers/specs/2026-07-30-chunk-context-enrichment-design.md),
[`docs/superpowers/plans/2026-07-30-chunk-context-enrichment.md`](superpowers/plans/2026-07-30-chunk-context-enrichment.md).

### Post-Phase-4: Git-Aware Language Detection & --language Override (Landed, July 31, PR #4)

`detect_language()` previously walked the filesystem (`repo_path.rglob("*")`), which also counts
gitignored scratch directories — vendored checkouts, sibling clones, `.worktrees/` — that can
outnumber a repo's own tracked code and flip detection to a language the repo doesn't use (real
case: a repo with 81 tracked `.py` files and a gitignored `.local-checkouts/` of 4782 `.ts`/`.tsx`
files was detected as TypeScript). Detection now counts extensions across `git ls-files` instead
(`_git_tracked_files()`, `-z`/NUL-delimited to avoid git quoting non-ASCII names and corrupting
suffix parsing); `_IGNORED_DIRS` still applies on top, since git does not exclude build output a
repo happens to commit.

New features:
- `_git_tracked_files(repo_path)` — repo-relative tracked paths via `git ls-files -z`; raises
  `NotAGitRepositoryError` (new exception) if `repo_path` isn't a git working tree
- `_git_head()` now distinguishes "not a git repository" from "git repository with no commits" —
  both fail identically on a bare `git rev-parse HEAD`, so it checks
  `git rev-parse --is-inside-work-tree` first and raises `NotAGitRepositoryError` for the former,
  leaving `IndexingError("has no commits yet")` for the latter
- `--language <name>` CLI flag on both `codeintel index` and `codeintel watch` — bypasses
  `detect_language()` entirely instead of guessing then correcting; choices are validated against
  `_INDEXER_BY_LANGUAGE`, a reverse map derived from `_LANGUAGE_INDEXERS` so the two cannot drift
- `language_override` column in registry.db — persists the override across `reindex` and `watch`
  runs; `_resolve_language()` manages the None-preserves / explicit-overwrites semantics (same
  contract as `_resolve_scheme`/`_resolve_semantic_include`); the registry's plain `language`
  column still records the effective language actually indexed, so `list`/`status` stay accurate
- `_ensure_language_override_column()` idempotent migration — adds the new column to existing
  databases

This is a heuristic with known edge cases, documented rather than solved: git shows duplicate
entries for unmerged paths, sparse-checkout entries absent from disk still count, and repos with
code entirely in git submodules won't be counted — `--language` is the escape hatch for all of
these.

New/updated tests: `test_index_cli.py` (git-tracked detection, override precedence, non-git-repo
and no-commits error paths), `test_registry.py` (`language_override` column persistence and
migration). Design docs:
[`docs/superpowers/specs/2026-07-31-git-aware-language-detection-design.md`](superpowers/specs/2026-07-31-git-aware-language-detection-design.md),
[`docs/superpowers/plans/2026-07-31-git-aware-language-detection.md`](superpowers/plans/2026-07-31-git-aware-language-detection.md).

### Post-Phase-4: Java/Kotlin Indexing & Automatic Search-Only Fallback (Landed, August 1)

Added SCIP navigation support for Java/Kotlin repos via `scip-java`. Recognized two cases where SCIP indexing cannot succeed and implemented automatic `--search-only` fallback:

- **Android/AGP:** scip-java's Gradle plugin relies on standard source sets that AGP replaces with variants, producing zero SCIP shards (upstream scip-java#177). Detected by matching stderr: "No SCIP shards found".
- **Kotlin version mismatch:** scip-kotlinc is compiled against exactly one pinned Kotlin release. Other versions fail with AbstractMethodError or NoSuchMethodError. Detected by matching stderr for "fir" + (AbstractMethodError | NoSuchMethodError).

**New features:**
- `_SEARCH_ONLY_SIGNATURES` tuple in `index_cli.py`: pairs of (required-substrings, human-reason) matched against indexer output; unrecognized failures still hard-fail
- `_search_only_reason()` and `_publish_search_only()`: detect recognized failures and publish Zoekt + semantic search only
- `--search-only` CLI flag on `codeintel index` and `codeintel watch`: explicit upfront choice to skip SCIP indexing
- `SEARCH_ONLY_STATUS = "search-only"` constant in `registry.py`; status column values now include "search-only"
- Navigation tools report search-only repos explicitly (server.py change)

**Verified:** Full pipeline runs; Zoekt and semantic search work on Java/Kotlin repos; navigation tools gracefully report search-only status.

### Post-Phase-4: Claude Code Plugin & MCP Registry Distribution (Landed, August 1)

Published codeintel as a Claude Code plugin (marketplace discovery) and to the official MCP Registry, enabling installation via three channels: PyPI, plugin marketplace, and MCP Registry direct listing.

**New structure:**
- `plugin/` directory: plugin manifest (`.claude-plugin/plugin.json`), MCP server registration (`.mcp.json`), and plugin skills (under `plugin/skills/`)
- `.claude-plugin/marketplace.json` (root): declares this repo as a plugin marketplace
- `server.json` (root): MCP Registry server descriptor (`io.github.phuongddx/codeintel`)
- `.github/workflows/publish-mcp-registry.yml`: publishes `server.json` to official MCP Registry after PyPI publish succeeds; retries up to 6x for eventual consistency
- `scripts/check_versions.py`: asserts `pyproject.toml`, `server.json`, plugin manifest, and MCP registration floor all declare the same version (run via test + CI to prevent drift)

**Install flows:**
- PyPI: `pip install codeintel-navigation-mcp` or `uv sync`
- Plugin: `/plugin marketplace add phuongddx/codeintel` → `/plugin install codeintel@codeintel`
- MCP Registry: Claude Code directly discovers `io.github.phuongddx/codeintel`

**Version consistency:** Four version fields (pyproject.toml [project].version, server.json.version, server.json.packages[0].version, plugin/.claude-plugin/plugin.json.version) are asserted identical. `.claude-plugin/marketplace.json` deliberately omits a version field to avoid a fifth place drift could occur.

---

## Explicitly Out of Scope (Not Planned)

The following items were considered during planning but are **not** planned for implementation:

### Cloud Deployment (Phase 5 in Original Brainstorm, Deprioritized)

**Why not:** codeintel is a single-user, personal tool. The MCP stdio interface is inherently local-first. Hosting would require:
- Multi-tenant schema (separate indexes per user/project/branch)
- Authentication & authorization layer
- Shared database (registry, package graph)
- Query caching / performance optimization
- Operational overhead (monitoring, disaster recovery)

**Verdict:** Not justified by the single-user use case. Revisit only if user demand exceeds 1.

---

### Stats & Dashboard Endpoints

**Why not:** codeintel is a **query engine**, not an analytics backend. A dashboard would require:
- HTTP API (replaces stdio)
- Metrics collection (per-query latency, cache hit rates, etc.)
- Web UI (additional complexity)

**Verdict:** Not needed for personal tool. Queries themselves don't need instrumentation.

---

### Monorepo Language-Merge Polish

**Why not:** codeintel detects language by file-extension plurality and indexes one language per repo. Multi-language monorepos would require:
- Schema redesign (separate tables per language, cross-language symbol links)
- Multiple indexers running per repo (complexity, time)
- Language-aware symbol resolution (SCIP doesn't provide this across languages)

**Verdict:** Out of scope for single-user tool. Users can index the dominant language or split the repo logically.

---

### GUI / TUI Interface

**Why not:** codeintel is designed for integration with Claude Code and Cursor, where the IDE handles the UI. A standalone GUI would require:
- Frontend framework (React, Vue, etc.)
- Backend HTTP API (not just MCP stdio)
- Maintenance burden for a personal tool

**Verdict:** IDE integration is superior UX for the intended workflow.

---

### Incremental Indexing

**Why not:** Tracking file-level changes and doing delta indexing would require:
- Change tracking schema (file hashes, modification times)
- Language indexer delta support (most don't offer this)
- Risk of incomplete / stale deltas

**Verdict:** Full rebuild on `codeintel reindex` is simple, reliable, and fast enough (5-30s for most repos). Not worth the complexity.

---

### Custom Symbol Filtering / Search Ranking

**Why not:** codeintel delegates to SCIP (for navigation) and Zoekt (for search) as-is. Custom filtering/ranking would require:
- Configuration format (YAML, .codeintel file, env vars)
- Per-repo filtering logic (database schema for rules)
- Tuning burden on users

**Verdict:** SCIP and Zoekt defaults are good enough. Users who need custom ranking should operate on raw query results.

---

## Possible Future Enhancements (Not Planned, No Timeline)

These are ideas that *could* be valuable but are not currently prioritized:

### Environment Variable Parity with Binaries

Currently supported:
- `CODEINTEL_DATA_DIR` — override default `~/.codeintel`

**Future candidates:**
- `CODEINTEL_ZOEKT_BIN` — override zoekt-webserver path
- `CODEINTEL_SCIP_BIN` — override scip binary path
- `CODEINTEL_INDEXER_TIMEOUT` — timeout for language indexers (default: no timeout)

**Status:** Not planned; env var currently cover the main need. Add if users request custom binary paths.

---

### Garbage Collection for Old Indexes

Currently: All old `index-<sha>.db` files are kept on disk indefinitely.

**Improvement:** A `codeintel gc` command could:
- Delete indexes older than N days or beyond M versions per repo
- Free up disk space

**Status:** Not planned; disk is cheap for single-user tool. Add if users report bloat.

---

### Package Graph Timestamps

Currently: The graph has no per-node freshness (only repo-level). `blastRadius` always reports `freshness: "unknown"`.

**Improvement:** Store last-indexed timestamp per dependency, allow stale-detection per node.

**Status:** Not planned; repo-level staleness is sufficient. Would require schema change and UI updates.

---

### Cross-Repo Symbol Resolution

Currently: `blastRadius` resolves only by exact package name. Cross-repo symbol lookups (e.g., "find all implementations of interface X") are not supported.

**Improvement:** Build a global symbol table across repos, enable cross-repo type hierarchy.

**Status:** Not planned; adds significant complexity (symbol collision handling, multi-language symbol matching). Single-repo nav is the primary use case.

---

### Language Server Protocol (LSP) Interface

Currently: MCP stdio only. IDE integration is via Claude Code / Cursor MCP support.

**Improvement:** Support LSP protocol directly for broader IDE compatibility (VS Code, Vim, Emacs, etc.).

**Status:** Not planned; MCP is a cleaner protocol for this use case. LSP would duplicate features (symbol search, references, etc.).

---

## Non-Roadmap Items (Out of Scope Permanently)

The following are **not** planned and not being reconsidered:

- **Cloud hosting / SaaS** — conflicts with single-user / personal-tool premise
- **GUI** — IDE integration via MCP is the UX
- **Real-time collaboration** — single-user only
- **AI code generation** — out of scope (codeintel is navigation, not generation)
- **Plugin system** — unnecessary for personal tool
- **Database migration framework** — schema is small and rarely changes

---

## How to Extend codeintel

If you want to add features or integrate deeper:

### Adding a Language Indexer

1. Ensure the language has a SCIP indexer (check sourcegraph/scip)
2. Add file extension detection to `index_cli.py` (extend `LANGUAGE_PRIORITIES`)
3. Test end-to-end on a sample repo
4. Document the language in README

### Adding a New MCP Tool

1. Implement query logic in `query.py` or `graph.py` — or, for an engine backed by its own
   storage layer (a second real example of this pattern, alongside Graph/`registry.db`), follow
   `semantic.py`'s shape: own storage (LanceDB table), an indexing entry point called from
   `index_cli.py`'s pipeline, and a query entry point called from `server.py`
2. Add tool function in `server.py` with uniform error handling
3. Write tests in `test_server_tools.py`
4. Document the tool signature in README

### Upgrading SCIP or Zoekt Version

1. Regenerate `scip_pb2.py` from new scip.proto (or update binary pins)
2. Verify integration tests pass with new binary
3. Update version pin and compatibility notes in README
4. Test with real repos that use the new version

### Forking for Multi-Tenant Use

If you need multi-tenant / cloud support:
1. Fork the repo
2. Redesign registry.db schema (add user/project columns)
3. Add auth layer (HTTP API instead of MCP stdio)
4. Add caching / performance optimization
5. Consider renaming to avoid confusion with personal codeintel

---

## Long-Term Vision (Hypothetical)

If codeintel were to grow significantly (large number of users, cross-organization use), a natural evolution might be:

1. **Personal cloud option:** Cloud-hosted personal instance (like Figma personal org)
2. **Team edition:** Multi-user support, shared indexes, auth
3. **Enterprise integration:** SSO, audit logs, deployment automation
4. **Advanced search:** Custom ranking, result caching, incremental indexing

**However:** This is speculative and not planned. Current scope is single-user, local-first, zero operational overhead.

---

## Contributing & Feedback

The roadmap is not set in stone. If you have feature requests or use cases that aren't covered by the current scope:

1. Open an issue describing the use case
2. Explain why it matters for your workflow
3. Provide examples (real repos, queries, expected results)

High-priority items will be reconsidered if user demand emerges.
