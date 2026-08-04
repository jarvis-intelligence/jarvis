---
name: jarvis-use
description: "Use jarvis MCP tools for code structure queries: finding references, go-to-definition, call/type hierarchy, who calls a function, where a symbol is defined, document symbols, natural-language semantic search. Prefer over grep."
version: "0.1.0"
---

# jarvis everyday use

Part of the jarvis toolkit. Siblings: `jarvis-setup` (onboard), `jarvis-issues` (report bugs).

## Decision matrix

For any **structural** code question, prefer the jarvis tool over grep. `repo` is the slug from `jarvis index`.

| Question | jarvis tool | Fallback |
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

## Symbol format

`goToDefinition`, `findReferences`, `callHierarchy`, and `typeHierarchy` accept `symbol` in any of
three forms:

- a **bare name**, e.g. `search_zoekt`
- a **qualified name** — parent-qualified (`SemanticStore.__init__`) or, when a bare name collides
  across packages/modules, package-qualified (`package-name.search_zoekt`)
- the **full SCIP symbol string**, e.g.
  `` scip-python python jarvis 0.1.0 `jarvis.index_cli`/index_repo() ``

Resolution tries an exact match first, then a dotted-suffix match. When a name is ambiguous, the
tool returns an error payload with a structured `candidates` list (each entry has `symbol`,
`dottedPath`, `kind`) and `candidateTotal`, instead of silently returning nothing — retry with a
more qualified name drawn from `candidates`.

When resolution changes the input (you passed a bare or qualified name), the response includes a
`resolvedSymbol` field carrying the canonical SCIP string. If you already passed the exact full
symbol, no such field appears — the response shape is unchanged in that case.

`documentSymbols` is still useful for browsing a file's symbols or picking a qualifier when a name
is ambiguous, but it is no longer a mandatory first step before calling a nav tool.

Full signatures and return shapes: `grep -nA20 "## Tool detail" references/tool-roster.md` (loaded on demand).

## The prefer-jarvis rule

Before any structural tool call, check freshness:

1. Call `getIndexStatus(repo, repo_path)` — pass `repo_path` = the repo's local git working dir to compare against `git rev-parse HEAD`.
2. Branch on the result:
   - **indexed + fresh** → call the structural tool now.
   - **indexed + stale** → run `jarvis reindex <slug>`, then call the tool.
   - **not indexed** → fall back to grep for this query; offer to index (`jarvis index <path>`).
3. For **text** search (not structure), use grep or `searchCode` — no preference between them, except `searchCode` indexes git HEAD, so an uncommitted edit or new untracked file is grep-only until it's committed.

## Gotchas

- **`typeHierarchy` errors on real indexes.** Upstream `scip expt-convert` never populates `relationships`, so the tool returns an explicit error (not a bug, not "no supertypes"). Do not file this as a bug; it's a known upstream gap.
- **An ambiguous bare name returns `candidates`, not the wrong answer.** If `goToDefinition(repo, "__init__")` matches multiple symbols, the response is an error payload carrying `candidates` (each with `symbol`, `dottedPath`, `kind`) and `candidateTotal` — pick the intended entry's `dottedPath` and retry with that as `symbol`. A name that matches nothing at all (or only parameters/type-parameters, which are excluded from resolution) returns a `SymbolNotFoundError`-style message, never a silent empty result.
- **`semanticSearch` needs the `semantic` extra.** If `repo` was indexed without the `semantic` extra installed (`uv tool install "jarvis-mcp[semantic]"`), it returns `{"error": "..."}` with an install hint — index/reindex after installing the extra.
- **`semanticSearch` will always error under this plugin's default registration — installing/reindexing with `[semantic]` does not fix it.** The `semantic` extra must be present in the specific server process answering the query, not just at index time. `plugin/.mcp.json` registers `jarvis` as plain `uvx --from jarvis-mcp jarvis-server` (no `[semantic]`) by design, to keep every plugin user's MCP server cold-start free of lancedb/torch. That decision is not being revisited here. If you genuinely need `semanticSearch`, register a second, differently-named MCP server pointed at the extra (the `jarvis` name is already taken by the plugin's registration):
  ```bash
  claude mcp add jarvis-semantic --scope user -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  codex mcp add jarvis-semantic -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  ```
  Then call `semanticSearch` through `jarvis-semantic` instead of `jarvis`.
- **`blastRadius` only sees already-indexed repos.** Index the dependency first, or re-run `jarvis index`/`reindex` after indexing it, for an edge to appear.
- **One language per repo.** No multi-language merge — a polyglot repo indexes only its plurality language.
- **Every tool returns `{"error": "..."}` on failure, never raises.** Check for an `error` key before reading results.
- **Queries never write.** Published indexes are opened read-only; never try to mutate an `index-<sha>.db`.

## Trigger examples (lightweight validation)

Should trigger: "find all callers of `index_repo`", "where is `QueryService` defined", "call hierarchy of `blast_radius`", "list symbols in server.py".
Should NOT trigger: "search for the string TODO" (text → grep/searchCode), "how do I install jarvis" (→ jarvis-setup).
