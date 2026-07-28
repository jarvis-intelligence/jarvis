# codeintel tool roster

The 8 MCP tools registered by `codeintel-server`. All take `repo` (the slug from `codeintel index`). On failure every tool returns `{"error": "..."}` rather than raising.

## Tool detail

### documentSymbols(repo, path) → dict
Every top-level symbol defined in `path` within `repo`, each with its range.
Returns: `{"path": ..., "symbols": [{...}], "freshness": {...}}`.

### goToDefinition(repo, symbol) → dict
Resolve `symbol`'s definition location(s) within `repo`.
Returns: `{"symbol": ..., "definitions": [{...}], "freshness": {...}}`.

### findReferences(repo, symbol) → dict
Every occurrence of `symbol` within `repo`, definition sites included.
Returns: `{"symbol": ..., "references": [{...}], "freshness": {...}}`.

### callHierarchy(repo, symbol) → dict
Single-level incoming + outgoing call hierarchy for `symbol`.
Returns: `{"symbol": ..., "incomingCalls": [...], "outgoingCalls": [...], "freshness": {...}}`.

### typeHierarchy(repo, symbol) → dict
Single-level super/subtypes for `symbol`. **Returns an `error` on real indexes** — upstream `scip expt-convert` never populates `relationships`. Treat the error as "unavailable", not as "no supertypes".

### getIndexStatus(repo, repo_path=None) → dict
Whether `repo` has a published index, plus freshness. Pass `repo_path` (the repo's local git dir) to compare the published commit against `git rev-parse HEAD`.
Returns: `{"repo": ..., "indexed": bool, "freshness": {...}}`. Without `repo_path`, freshness is reported without a staleness check (never `stale: true` without evidence).

### searchCode(query, repo=None) → dict
Lexical search via an embedded Zoekt index (lazy-started on first call). `repo`, if given, is applied as a Zoekt `r:` filter scoping results to that one indexed repo.
Returns: `{"query": ..., "hits": [{"repo","path","lineNumber","lineText"}], "total": int}`.

### blastRadius(repo, symbol_or_package) → dict
2-hop bounded BFS over the package dependency graph: every other indexed repo whose package directly (1 hop) or transitively through one intermediary (2 hops) depends on `symbol_or_package` as registered for `repo` (e.g. `"npm:@scope/name"`). The graph has no per-node timestamp, so `freshness` is always `unknown` here.
Returns: `{"repo": ..., "symbolOrPackage": ..., "dependents": [{..., "hops": int}], "freshness": {...}}`.

## Freshness field

Every nav tool returns a `freshness` object describing the published index (`indexed`, `stale`, `published_commit`, etc.). Use it to decide whether to trust results or `codeintel reindex <slug>` first.
