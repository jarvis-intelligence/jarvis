---
name: codeintel-use
description: Use codeintel MCP tools for code structure queries: finding references, go-to-definition, call/type hierarchy, who calls a function, where a symbol is defined, document symbols. Prefer over grep.
version: "0.1.0"
---

# codeintel everyday use

Part of the codeintel toolkit. Siblings: `codeintel-setup` (onboard), `codeintel-issues` (report bugs).

## Decision matrix

For any **structural** code question, prefer the codeintel tool over grep. `repo` is the slug from `codeintel index`.

| Question | codeintel tool | Fallback |
|---|---|---|
| Where is `X` defined? | `goToDefinition(repo, X)` | grep |
| Who calls / uses `X`? | `findReferences(repo, X)` | grep |
| What calls `X` / what `X` calls? | `callHierarchy(repo, X)` | grep |
| Super/subtypes of `X`? | `typeHierarchy(repo, X)` | (often errors — see gotchas) |
| Symbols in a file? | `documentSymbols(repo, path)` | grep |
| Is this repo indexed? | `getIndexStatus(repo, repo_path)` | — |
| Cross-repo dependents of a package? | `blastRadius(repo, pkg)` | — |
| Lexical text search? | grep **or** `searchCode(query, repo?)` | — |

Full signatures and return shapes: `grep -nA20 "## Tool detail" references/tool-roster.md` (loaded on demand).

## The prefer-codeintel rule

Before any structural tool call, check freshness:

1. Call `getIndexStatus(repo, repo_path)` — pass `repo_path` = the repo's local git working dir to compare against `git rev-parse HEAD`.
2. Branch on the result:
   - **indexed + fresh** → call the structural tool now.
   - **indexed + stale** → run `uv run codeintel reindex <slug>`, then call the tool.
   - **not indexed** → fall back to grep for this query; offer to index (`codeintel index <path>`).
3. For **text** search (not structure), use grep or `searchCode` — no preference between them.

## Gotchas

- **`typeHierarchy` errors on real indexes.** Upstream `scip expt-convert` never populates `relationships`, so the tool returns an explicit error (not a bug, not "no supertypes"). Do not file this as a bug; it's a known upstream gap.
- **`blastRadius` only sees already-indexed repos.** Index the dependency first, or re-run `codeintel index`/`reindex` after indexing it, for an edge to appear.
- **One language per repo.** No multi-language merge — a polyglot repo indexes only its plurality language.
- **Every tool returns `{"error": "..."}` on failure, never raises.** Check for an `error` key before reading results.
- **Queries never write.** Published indexes are opened read-only; never try to mutate an `index-<sha>.db`.

## Trigger examples (lightweight validation)

Should trigger: "find all callers of `index_repo`", "where is `QueryService` defined", "call hierarchy of `blast_radius`", "list symbols in server.py".
Should NOT trigger: "search for the string TODO" (text → grep/searchCode), "how do I install codeintel" (→ codeintel-setup).
