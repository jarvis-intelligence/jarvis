---
name: codeintel-use
description: "Use codeintel MCP tools for code structure queries: finding references, go-to-definition, call/type hierarchy, who calls a function, where a symbol is defined, document symbols, natural-language semantic search. Prefer over grep."
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
| Natural-language / conceptual code search? | `semanticSearch(repo, query, limit?)` | `searchCode` (needs `semantic` extra + reindex) |

## Symbol format (critical)

`goToDefinition`, `findReferences`, `callHierarchy`, and `typeHierarchy` match **exactly** against the fully-qualified SCIP symbol string stored in the index — a bare name like `"index_repo"` returns **empty results**, not a match. The string has a language-specific shape:

- **Python (scip-python):** `` scip-python python <pkg> <ver> `<module>`/<name><suffix> ``
  e.g. `` scip-python python codeintel 0.1.0 `codeintel.index_cli`/index_repo() ``
  suffix: `()` = function/method, `#` = class, `.` = module variable, `:` = `__init__`.
- **TypeScript (scip-typescript):** npm-style, e.g. `@scope/pkg/src/file.ts/functionName`.
- **Swift (scip-swift):** swift-module-style, e.g. `MyModule/ClassName/functionName()`.

You do not need to guess the string. **Always discover it first** with `documentSymbols`:

1. Call `documentSymbols(repo, path)` on the file where the symbol lives (or is used).
2. Each returned entry's `symbol` field is the exact string to pass to `goToDefinition` / `findReferences` / `callHierarchy`.
3. Pass that string verbatim.

Example: to find callers of `index_repo`, first `documentSymbols(repo, "src/codeintel/index_cli.py")`, read the entry whose `symbol` ends in `/index_repo().`, then pass that full string to `callHierarchy`.

Full signatures and return shapes: `grep -nA20 "## Tool detail" references/tool-roster.md` (loaded on demand).

## The prefer-codeintel rule

Before any structural tool call, check freshness:

1. Call `getIndexStatus(repo, repo_path)` — pass `repo_path` = the repo's local git working dir to compare against `git rev-parse HEAD`.
2. Branch on the result:
   - **indexed + fresh** → call the structural tool now.
   - **indexed + stale** → run `codeintel reindex <slug>`, then call the tool.
   - **not indexed** → fall back to grep for this query; offer to index (`codeintel index <path>`).
3. For **text** search (not structure), use grep or `searchCode` — no preference between them.

## Gotchas

- **`typeHierarchy` errors on real indexes.** Upstream `scip expt-convert` never populates `relationships`, so the tool returns an explicit error (not a bug, not "no supertypes"). Do not file this as a bug; it's a known upstream gap.
- **Bare symbol names return empty results, not errors.** `goToDefinition(repo, "index_repo")` returns `{"definitions": []}` silently. The match is exact against the fully-qualified SCIP string — see "Symbol format" above. If a nav tool returns empty and the symbol definitely exists, you passed the wrong form: run `documentSymbols` first and use the returned `symbol` string verbatim.
- **`semanticSearch` needs the `semantic` extra.** If `repo` was indexed without the `semantic` extra installed (`uv tool install "codeintel-navigation-mcp[semantic]"`), it returns `{"error": "..."}` with an install hint — index/reindex after installing the extra.
- **`semanticSearch` will always error under this plugin's default registration — installing/reindexing with `[semantic]` does not fix it.** The `semantic` extra must be present in the specific server process answering the query, not just at index time. `plugin/.mcp.json` registers `codeintel` as plain `uvx --from codeintel-navigation-mcp codeintel-server` (no `[semantic]`) by design, to keep every plugin user's MCP server cold-start free of lancedb/torch. That decision is not being revisited here. If you genuinely need `semanticSearch`, register a second, differently-named MCP server pointed at the extra (the `codeintel` name is already taken by the plugin's registration):
  ```bash
  claude mcp add codeintel-semantic --scope user -- uvx --from "codeintel-navigation-mcp[semantic]" codeintel-server
  ```
  Then call `semanticSearch` through `codeintel-semantic` instead of `codeintel`.
- **`blastRadius` only sees already-indexed repos.** Index the dependency first, or re-run `codeintel index`/`reindex` after indexing it, for an edge to appear.
- **One language per repo.** No multi-language merge — a polyglot repo indexes only its plurality language.
- **Every tool returns `{"error": "..."}` on failure, never raises.** Check for an `error` key before reading results.
- **Queries never write.** Published indexes are opened read-only; never try to mutate an `index-<sha>.db`.

## Trigger examples (lightweight validation)

Should trigger: "find all callers of `index_repo`", "where is `QueryService` defined", "call hierarchy of `blast_radius`", "list symbols in server.py".
Should NOT trigger: "search for the string TODO" (text → grep/searchCode), "how do I install codeintel" (→ codeintel-setup).
