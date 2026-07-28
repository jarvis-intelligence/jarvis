# codeintel: Project Roadmap

## Current Status: All Phases Complete

**All 4 planned implementation phases are shipped and verified.**

See [`plans/0724-2316-codeintel-mcp-implementation/`](../plans/0724-2316-codeintel-mcp-implementation/) for the detailed implementation plan and phase documentation.

### Phase Completion Summary

| Phase | Feature | Delivered | Verification |
|-------|---------|-----------|--------------|
| **1** | Scaffold + vendored SCIP core (scip_pb2, scip_decoder, index_reader) | ✓ July 24 | Unit tests green, vendored code functional |
| **2** | MCP stdio server + 5 SCIP nav tools + getIndexStatus | ✓ July 24 | 8 tools registered, error handling tested |
| **3** | Indexer CLI, registry, embedded Zoekt + searchCode | ✓ July 25 | End-to-end index pipeline, search working |
| **4** | blastRadius (package graph) + codeintel watch (auto-reindex) | ✓ July 25 | Graph BFS tested, watch debounce functional |

### Post-Phase-4: Swift Detection (Landed, July 25)

`detect_language()` now recognizes `.swift` and routes majority-Swift repos to `scip-swift`,
and `DerivedData`/`.build` are excluded from the extension-majority scan.

**Update (July 26) — Swift navigation works; the earlier diagnosis was wrong.**
An earlier note here claimed `scip-swift` emitted no occurrence ranges. That was
incorrect. `scip-swift` sets `single_line_range`, the `typed_range` oneof
introduced in scip.proto (`SingleLineRange single_line_range = 8`), and does not
set the deprecated repeated-int32 `range` field — which is exactly what the
current spec tells producers to do.

The ranges were being dropped by two consumers:

1. **`scip expt-convert` v0.7.0** predates `bindings/go/scip/occurrence_range.go`
   and cannot read `typed_range`, so it silently produced a schema-valid database
   with `chunks=0, mentions=0`. Verified: the same `.scip` file yields
   `chunks=0/mentions=0` under v0.7.0 and `chunks=1/mentions=14` under v0.9.0.
   `setup.sh` pins v0.9.0, and `index_repo()` now refuses anything older.
2. **codeintel's vendored `scip_pb2.py`** was generated from scip.proto v0.7.0 and
   had no `typed_range` field, so even a v0.9.0-produced index decoded 0/16
   occurrence ranges. Regenerated from v0.9.0; `scip_decoder.py` now reads
   `typed_range` first with a deprecated-`range` fallback, mirroring upstream
   Go's `Occurrence.SourceRange()`.

Verified end-to-end against a real 21-file Swift repo: `codeintel index` completes
without error, `getIndexStatus` reports `language: swift` / `indexed`, and both
`global_symbols` and `chunks`/`mentions` populate — `documentSymbols`,
`goToDefinition`, `findReferences`, and `callHierarchy` all return real results
on Swift repos.

**Invocation compatibility (July 26):** `scip-swift` is invoked in its *bare* form
(`scip-swift --output <path>`, no `index` subcommand token) — unlike the other indexers, which all
take `index`. Reason: `scip-swift`'s `index` subcommand landed *after* its `v0.1.0` release, so the
v0.1.0 binary parses `index` as the repo path and fails with "Could not detect a build system". The
bare form works on every version — old binaries default the repo path to the working directory,
newer ones dispatch to `index` as their default subcommand. Verified against both v0.1.0 and
v0.1.1. `scip-swift v0.1.1` was cut to make the released binary match committed behavior (both
earlier builds reported `0.1.0` despite differing), and `setup.sh` pins `v0.1.1` as the floor.

**`typeHierarchy` is unavailable, and now says so.** `scip expt-convert` declares
`global_symbols.relationships` in its schema but never writes it —
`insertGlobalSymbols()` in `cmd/scip/convert.go` (v0.9.0) binds only symbol,
display_name, kind, documentation and enclosing_symbol. The tool therefore
returns an explicit `{"error": ...}` rather than empty arrays, because an empty
result would assert "this type has no supertypes" when the truth is "cannot
tell". `query.py`'s logic is complete and self-heals if a future converter
populates the column.

Reported upstream: [scip-code/scip#464](https://github.com/scip-code/scip/issues/464),
fixed by [scip-code/scip#465](https://github.com/scip-code/scip/pull/465) (open, CI
green). `global_symbols.signature` is left unpopulated there deliberately — the
column name and the proto field (`signature_documentation`) diverge. Once #465
lands, `typeHierarchy` starts working with no change here beyond installing the
newer `scip`.

**Acceptance criteria met:**
- ✓ All 8 MCP tools return correct results on real TypeScript/Python repos
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

1. Implement query logic in `query.py` or `graph.py`
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
