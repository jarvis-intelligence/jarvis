# Phase 5: Semantic Install Onboarding - Context

**Gathered:** 2026-08-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Interactive `jarvis index` makes the semantic extra discoverable — offered once per repo at a TTY, installed on consent, remembered when declined — while watch and MCP paths stay silent and non-blocking. Delivers SEMA-01/02: TTY y/N install offer with same-invocation enablement, per-repo decline memory (additive `semantic_declined` column), and preserved non-TTY silence.

</domain>

<decisions>
## Implementation Decisions

### Install mechanics (Area 1 — accepted as proposed)
- Install command on consent: `uv pip install --python <sys.executable> "jarvis-mcp[semantic]"` — targets the running interpreter's environment regardless of how the venv was created.
- Missing-extra detection: `importlib.util.find_spec` on the same module(s) the semantic stage imports (e.g. `sentence_transformers`/`lancedb`) — the honest "would semantic run" test.
- Install failure (offline, no uv, resolver error): warn + continue — index completes without semantic, one stderr line naming the failed command; the decline is NOT remembered (next TTY index offers again).
- After a successful install: the semantic stage runs in the SAME invocation (fresh import after install) — SC1's "index completes with semantic search enabled".

### Prompt UX & TTY gating (Area 2 — accepted as proposed)
- TTY gate: `sys.stdin.isatty() and sys.stdout.isatty()` — pip's convention; any redirected stream means automation, never prompt.
- Placement: at the semantic stage, AFTER SCIP/Zoekt publish succeeded — the offer never delays or risks the index itself; prompt names what installs.
- Prompt: `Install semantic search support for this repo? [y/N] ` — Enter = No (safe default); y/Y/yes accepts; everything else declines.
- EOFError/KeyboardInterrupt at the prompt: treated as decline (remembered), no traceback, index completes.

### Memory semantics (Area 3 — accepted as proposed)
- Storage: additive `semantic_declined` (0/1) column via `_ensure_column` on the existing repos row — migration-safe, mirrors `fallback_enabled`.
- Clearing: `jarvis forget` only (row death); a successful later install makes the bit moot.
- Explicit `--semantic-include` on a declined repo: runs semantic as given — the flag is direct user intent, never blocked, does not clear the decline.
- Extra already installed: no prompt ever; decline bit never written.

### Claude's Discretion
None flagged — all areas resolved with explicit answers.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_run_semantic_stage` in `src/jarvis/index_cli.py` (~583-612) — the silent-skip + stderr hint path SEMA-02 preserves; the offer slots ahead of it.
- `_ensure_column` additive migration + nullable-bool column pattern (`fallback_enabled`, phase 3) — `semantic_declined` mirrors it.
- `_resolve_*` tri-state read pattern and `mark_*` setter precedent (mark_tracked_files, set_fallback_enabled) for the decline bit.
- `_publish_search_only`'s stage ordering (publish first, then optional stages) — prompt placement follows the same safety logic.
- Non-TTY guards: watch path and MCP server path already never touch stdin — SEMA-02 is a regression pin, not new wiring.

### Established Patterns
- Deferred imports inside functions (semantic extra optional) — detection code must follow the same import style.
- One stderr line per non-fatal condition (degraded warnings, semantic skip hint).

### Integration Points
- `index_repo` semantic stage call site + `_cmd_index` (CLI entry owns stdin/TTY UX — verify where input() belongs).
- `registry.py` `_SCHEMA` + `RegisteredRepo` fields + upsert column lists.
- `jarvis forget` already deletes the row (memory dies with it — free).

</code_context>

<specifics>
## Specific Ideas

No specific requirements — answers captured above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
