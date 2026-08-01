# codeintel tool roster

The 9 MCP tools registered by `codeintel-server`. All take `repo` (the slug from `codeintel index`). On failure every tool returns `{"error": "..."}` rather than raising.

## Tool detail

### documentSymbols(repo, path) → dict
Every top-level symbol defined in `path` within `repo`, each with its range. **The returned `symbol` field is the exact string to pass to the other nav tools** — always read it from here first rather than guessing.
Returns: `{"path": ..., "symbols": [{...}], "freshness": {...}}`.

### goToDefinition(repo, symbol) → dict
Resolve `symbol`'s definition location(s) within `repo`. `symbol` must be the **fully-qualified SCIP string** (e.g. `` scip-python python codeintel 0.1.0 `codeintel.index_cli`/index_repo() ``); a bare name returns empty. Get the string from `documentSymbols`.
Returns: `{"symbol": ..., "definitions": [{...}], "freshness": {...}}`.

### findReferences(repo, symbol) → dict
Every occurrence of `symbol` within `repo`, definition sites included. Same fully-qualified-`symbol` requirement as `goToDefinition`.
Returns: `{"symbol": ..., "references": [{...}], "freshness": {...}}`.

### callHierarchy(repo, symbol) → dict
Single-level incoming + outgoing call hierarchy for `symbol`. Same fully-qualified-`symbol` requirement.
Returns: `{"symbol": ..., "incomingCalls": [...], "outgoingCalls": [...], "freshness": {...}}`.

### typeHierarchy(repo, symbol) → dict
Single-level super/subtypes for `symbol`. Same fully-qualified-`symbol` requirement. **Returns an `error` on real indexes** — upstream `scip expt-convert` never populates `relationships`. Treat the error as "unavailable", not as "no supertypes".

### getIndexStatus(repo, repo_path=None) → dict
Whether `repo` has a published index, plus freshness. Pass `repo_path` (the repo's local git dir) to compare the published commit against `git rev-parse HEAD`.
Returns: `{"repo": ..., "indexed": bool, "freshness": {...}}`. Without `repo_path`, freshness is reported without a staleness check (never `stale: true` without evidence).

### searchCode(query, repo=None) → dict
Lexical search via an embedded Zoekt index (lazy-started on first call). `repo`, if given, is applied as a Zoekt `r:` filter scoping results to that one indexed repo.
Returns: `{"query": ..., "hits": [{"repo","path","lineNumber","lineText"}], "total": int}`.

### semanticSearch(repo, query, limit=10) → dict
Natural-language code search over `repo`: embeds `query`, retrieves top vector matches from the repo's semantic index, fuses them with Zoekt lexical hits via reciprocal rank fusion. Requires `repo` to have been indexed with the `semantic` extra installed (`uv sync --extra semantic`); otherwise returns `{"error": "..."}` with an install hint.
Returns: `{"query": ..., "results": [{"repo","filePath","startLine","endLine","symbolName","content","score","sources"}], "total": int}` (plus an optional `"warning"` if the configured embedding model differs from the index's).

### blastRadius(repo, symbol_or_package) → dict
2-hop bounded BFS over the package dependency graph: every other indexed repo whose package directly (1 hop) or transitively through one intermediary (2 hops) depends on `symbol_or_package` as registered for `repo` (e.g. `"npm:@scope/name"`). The graph has no per-node timestamp, so `freshness` is always `unknown` here.
Returns: `{"repo": ..., "symbolOrPackage": ..., "dependents": [{..., "hops": int}], "freshness": {...}}`.

## Freshness field

Every nav tool returns a `freshness` object describing the published index (`indexed`, `stale`, `published_commit`, etc.). Use it to decide whether to trust results or `codeintel reindex <slug>` first.
