# jarvis tool roster

The 9 MCP tools registered by `jarvis-server`. All take `repo` (the slug from `jarvis index`). On failure every tool returns `{"error": "..."}` rather than raising.

## Tool detail

### documentSymbols(repo, path) → dict
Every top-level symbol defined in `path` within `repo`, each with its range. `symbol` is the full SCIP string; `displayName`/`kind` are readable. Useful for browsing a file or picking a qualifier when a nav tool reports an ambiguous name — not a mandatory first step.
Returns: `{"path": ..., "symbols": [{...}], "freshness": {...}}`.

### goToDefinition(repo, symbol) → dict
Resolve `symbol`'s definition location(s) within `repo`. `symbol` accepts a bare name (`Greeter`), a qualified name (`Greeter.greet`, or `package-name.Greeter.greet` when a bare name collides across packages/modules), or the full SCIP symbol string. Resolution tries an exact match, then a dotted-suffix match; an ambiguous name returns an error payload with `candidates`/`candidateTotal` instead of a silent empty result. When resolution changed the input, the response includes `resolvedSymbol` (the canonical form); omitted when the caller already passed the exact full symbol.
Returns: `{"symbol": ..., "resolvedSymbol"?: ..., "definitions": [{...}], "freshness": {...}}`.

### findReferences(repo, symbol) → dict
Every occurrence of `symbol` within `repo`, definition sites included. Same `symbol`-resolution behavior as `goToDefinition`.
Returns: `{"symbol": ..., "resolvedSymbol"?: ..., "references": [{...}], "freshness": {...}}`.

### callHierarchy(repo, symbol) → dict
Single-level incoming + outgoing call hierarchy for `symbol`. Same `symbol`-resolution behavior as `goToDefinition`.
Returns: `{"symbol": ..., "resolvedSymbol"?: ..., "incomingCalls": [...], "outgoingCalls": [...], "freshness": {...}}`.

### typeHierarchy(repo, symbol) → dict
Single-level super/subtypes for `symbol`. Same `symbol`-resolution behavior as `goToDefinition`. **Returns an `error` on real indexes** — upstream `scip expt-convert` never populates `relationships`. Treat the error as "unavailable", not as "no supertypes".

### getIndexStatus(repo, repo_path=None) → dict
Whether `repo` has a published index, plus freshness and search coverage. Pass `repo_path` (the repo's local git dir) to compare the published commit against `git rev-parse HEAD`.
Returns: `{"repo": ..., "indexed": bool, "status": ..., "freshness": {...}, "searchCoverage": {...} | None, "searchCoverageReason": str}`. Without `repo_path`, freshness is reported without a staleness check (never `stale: true` without evidence). `searchCoverage` is `{"expected": int, "indexed": int, "complete": bool}` comparing git-tracked files at last index time against what Zoekt currently holds (catches shards lost after a successful index); when it can't be computed (e.g. zoekt-webserver not running), it's `None` and `searchCoverageReason` explains why.

### searchCode(query, repo=None) → dict
Lexical search via an embedded Zoekt index (lazy-started on first call). `repo`, if given, is applied as a Zoekt `r:` filter scoping results to that one indexed repo.
Returns: `{"query": ..., "hits": [{"repo","path","lineNumber","lineText"}], "total": int}`.

### semanticSearch(repo, query, limit=10) → dict
Natural-language code search over `repo`: embeds `query`, retrieves top vector matches from the repo's semantic index, and fuses them with Zoekt lexical hits and SCIP symbol-definition matches (when a SCIP index exists) via reciprocal rank fusion. Requires `repo` to have been indexed with the `semantic` extra installed (`uv tool install "jarvis-mcp[semantic]"`); otherwise returns `{"error": "..."}` with an install hint.
Returns: `{"query": ..., "results": [{"repo","filePath","startLine","endLine","symbolName","content","score","sources"}], "total": int}` (plus an optional `"warning"` if the configured embedding model differs from the index's). `sources` may include `"symbol"`; a symbol-only hit has `content=""` (SCIP stores no source text) and `symbolName` set to the definition's dotted path. Swift repos get no benefit from this signal — scip-swift emits clang USR strings as symbol names, which NL query tokens never match (same caveat as bare-name resolution in the nav tools).

### blastRadius(repo, symbol_or_package) → dict
2-hop bounded BFS over the package dependency graph: every other indexed repo whose package directly (1 hop) or transitively through one intermediary (2 hops) depends on `symbol_or_package` as registered for `repo` (e.g. `"npm:@scope/name"`). The graph has no per-node timestamp, so `freshness` is always `unknown` here.
Returns: `{"repo": ..., "symbolOrPackage": ..., "dependents": [{..., "hops": int}], "freshness": {...}}`.

## Freshness field

Every nav tool returns a `freshness` object describing the published index (`indexed`, `stale`, `published_commit`, etc.). Use it to decide whether to trust results or `jarvis reindex <slug>` first.
