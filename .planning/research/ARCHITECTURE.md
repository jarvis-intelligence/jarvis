# Architecture Patterns

**Domain:** Local-first code-intelligence MCP server — indexing-robustness milestone (brownfield integration)
**Researched:** 2026-08-16
**Confidence:** HIGH — all findings verified by direct source reading of `src/jarvis/registry.py`, `src/jarvis/index_cli.py`, `src/jarvis/server.py`, `.planning/codebase/ARCHITECTURE.md`

## Recommended Architecture

All five features slot into the existing three-layer split without new modules:

```text
┌────────────────────────────────────────────────────────────────────┐
│ CLI entry (_cmd_index / _cmd_status)          index_cli.py         │
│  • TTY-only semantic install prompt (feature 5) lives HERE         │
│  • status printing gains reason/remedy lines (feature 4)           │
├────────────────────────────────────────────────────────────────────┤
│ Pipeline (index_repo)                         index_cli.py         │
│  • classification ladder in the indexer except-branch:             │
│    bash-shim → signatures (feature 3) → opt-in fallback (1+2)      │
│    → hard failure with persisted reason (feature 4)                │
│  • _resolve_fallback() mirrors _resolve_scheme/_resolve_search_only│
├────────────────────────────────────────────────────────────────────┤
│ State (Registry)                              registry.py          │
│  • additive columns via _ensure_column:                            │
│    status_reason TEXT, fallback_enabled INTEGER (tri-state),       │
│    semantic_declined INTEGER NOT NULL DEFAULT 0                    │
│  • new status constant DEGRADED_STATUS = "degraded"                │
├────────────────────────────────────────────────────────────────────┤
│ Reporting (read-only)                                              │
│  • server.py getIndexStatus adds statusReason/remedy fields        │
│  • server.py IndexNotFoundError translation extends its status     │
│    check from == SEARCH_ONLY_STATUS to a {search-only, degraded}   │
│    set                                                             │
│  • config.py gains the JARVIS_FALLBACK_SEARCH_ONLY env accessor    │
└────────────────────────────────────────────────────────────────────┘
```

**The load-bearing design decision:** the generic fallback (features 1–2) does NOT
reuse `search_only=1`. Self-healing falls out of *not* setting that column:
`_resolve_search_only()` returns `False` on the next reindex, so the full SCIP
build is retried automatically. The degraded state is instead expressed as a
new registry **status** (`degraded`) plus a persisted **reason** — status is
already re-derived on every index run, so it self-heals by construction the
moment a build succeeds. No new state machine is needed.

### Component Boundaries

| Component | Responsibility (new) | Communicates With |
|-----------|---------------------|-------------------|
| `registry.py` | Owns all persisted per-repo state: `status_reason`, `fallback_enabled` (tri-state NULL/0/1), `semantic_declined`. New `DEGRADED_STATUS` constant next to `SEARCH_ONLY_STATUS`. Zero classification logic. | Read by pipeline, CLI status, server.py (read-only) |
| `index_cli.py` — `index_repo()` | Failure classification ladder + publish decision. Extends `_SEARCH_ONLY_SIGNATURES` with scip-swift entries. New `_resolve_fallback()` helper. Never prompts, never reads stdin. | Writes registry; runs `_publish_search_only` |
| `index_cli.py` — `_cmd_index()` | The only interactive surface: TTY detection + semantic install y/N prompt + `uv` subprocess install, *before* `index_repo()` runs. | Reads/writes `semantic_declined` in registry |
| `index_cli.py` — `_cmd_status()` | Prints `reason:`/`remedy:` lines when `status_reason` is set | Reads registry |
| `config.py` | `JARVIS_FALLBACK_SEARCH_ONLY` env accessor (matches existing `JARVIS_DATA_DIR` pattern — env reads live in config, not scattered) | Read by `_resolve_fallback` |
| `server.py` | Reporting only: `getIndexStatus` returns `statusReason`; nav-tool error translation covers both `search-only` and `degraded`. Never writes registry. | Reads registry via existing `_registry_status` pattern |
| `watch.py` | **Unchanged.** Calls `index_repo()` directly, so it inherits non-interactive behavior and fallback resolution (persisted flag / env var) with zero edits. | — |

### Data Flow

**Failure → classification → publish decision → reporting** (the core flow, all inside `index_repo`'s indexer `except IndexingError` branch, currently lines 814–833):

1. `_run(indexer_cmd)` fails → raises `IndexingError` carrying the subprocess's stdout+stderr in its message. This string is the *only* classification input — the existing rule "detect from indexer output, never from parsing build files" holds for everything new.
2. Classification ladder, in order (order is a contract — earlier rungs are more specific):
   - **Rung a (existing):** Java bash-shim tokens → hard failure with remedy. Stays first.
   - **Rung b (feature 3):** `_search_only_reason()` signature match → *permanent* search-only publish, `search_only=1`, status `search-only`. scip-swift entries are pure data additions to `_SEARCH_ONLY_SIGNATURES` — same `(required substrings, human reason)` shape, ALL substrings must match.
   - **Rung c (features 1+2, new):** `_resolve_fallback()` is truthy → *degraded* publish: call `_publish_search_only()` (reused verbatim — it already retires stale SCIP artifacts and clears graph edges, exactly what a degraded repo needs), then upsert with `DEGRADED_STATUS`, `status_reason=<reason + truncated indexer error>`, and critically `search_only=False`.
   - **Rung d (feature 4, changed):** no match, no opt-in → hard failure, but `mark_status` grows into `mark_failed(slug, reason)` so the exception message is persisted instead of lost. Today `registry.mark_status(slug, "failed")` discards the why.
3. **Self-heal loop:** next `reindex`/`watch` run → `_resolve_search_only()` sees `search_only=0` → full pipeline runs → success upserts `indexed` with `status_reason=NULL` (healed) — or fails again and re-enters the ladder.
4. **Reporting reads, never re-derives:** `jarvis status` prints the persisted `status_reason`; `getIndexStatus` adds it to its payload; the `IndexNotFoundError` translation in `server.py` (currently `_registry_status(repo) == SEARCH_ONLY_STATUS`) extends to `in {SEARCH_ONLY_STATUS, DEGRADED_STATUS}` and can include the persisted reason in the explanation.

**Fallback opt-in resolution (feature 2)** — three-level precedence, resolved at classification time:

```
explicit CLI flag this run  >  persisted per-repo fallback_enabled (non-NULL)  >  JARVIS_FALLBACK_SEARCH_ONLY env  >  off
```

- `fallback_enabled` is **tri-state** (`NULL`/`0`/`1`): NULL = "never set, defer to env", 0 = explicitly disabled per-repo (overrides a global env default), 1 = enabled. A plain boolean column can't express "this repo opts out of the global default".
- Only an explicit CLI value is persisted (same contract as `--scheme`/`--language`: `None` means "leave the persisted value alone"). The env var is read live each run, never persisted — a changed global default takes effect immediately.
- Use `argparse.BooleanOptionalAction` (`--fallback-search-only` / `--no-fallback-search-only`, `default=None`) rather than `store_true`. This deliberately avoids repeating the `--search-only` set-but-never-clearable trap the PROJECT.md calls out.

**Semantic install prompt (feature 5)** — flows entirely through `_cmd_index`, *before* `index_repo()`:

1. `_cmd_index` checks: semantic extra importable? → skip. `sys.stdin.isatty() and sys.stderr.isatty()`? no → skip (watch and MCP-triggered paths call `index_repo()` directly and never even reach this code — non-blocking by construction, not by conditional). `semantic_declined` set for this slug? → skip.
2. y/N prompt → yes: subprocess `uv` install, then proceed; no: `registry.mark_semantic_declined(slug)` and proceed.
3. `_run_semantic_stage` itself is untouched — its existing skip-with-hint behavior remains the non-TTY path's behavior, satisfying the "silent skip + stderr hint" requirement with zero changes.
4. Install-command detection is a real design point: a `uv tool install jarvis-mcp[semantic]` install and a source checkout (`uv sync --extra semantic`) need different commands — the existing skip-hint already names both, so the prompt path must pick one (detect via `__file__` location or importlib metadata) or ask.

## Patterns to Follow

### Pattern 1: Additive columns via `_ensure_column`
**What:** every new persisted field is `ALTER TABLE ADD COLUMN` through the existing idempotent `_ensure_column` helper, added to `_SCHEMA`, `RegisteredRepo`, and `_row_to_repo` together.
**When:** all three new columns (`status_reason`, `fallback_enabled`, `semantic_declined`).
**Why it satisfies the compatibility constraint:** existing registries migrate lazily on first open; old rows read as NULL/0 and keep exact current semantics (`search_only=1` rows stay permanently search-only).

### Pattern 2: `_resolve_*` for persisted-flag plumbing
**What:** `_resolve_fallback(registry, slug, cli_value)` copies `_resolve_scheme`'s contract — `None` means "leave persisted value alone", then falls through to the env var.
**Why:** `_cmd_reindex` deliberately does *not* pass `search_only` today; the resolve-inside-`index_repo` pattern is what makes `reindex`/`watch` inherit persisted state without repeating flags. New state must use the same mechanism or reindex silently drops it.

### Pattern 3: reason column clears on success (upsert), NOT preserve-on-upsert
**What:** `status_reason` goes into `upsert`'s column list (passed as `None` on success paths) so a successful index naturally erases the stale reason.
**Why the distinction matters:** `tracked_files` uses the *opposite* pattern (separate `UPDATE`, excluded from upsert's ON CONFLICT) precisely so upserts don't reset it. Copying that pattern for `status_reason` would leave a healed repo reporting last month's failure forever. Two fields, two lifecycles, two patterns — both already exist in `registry.py`.

### Pattern 4: substring-signature classification only
**What:** scip-swift signatures are `(required substrings, reason)` tuples on `_SEARCH_ONLY_SIGNATURES`; ALL substrings must co-occur (that's what keeps a generic error string from an unrelated library from matching).
**When:** only failures verified as *permanently unfixable from jarvis's side* join this list (the AGP/Kotlin precedent). A fixable Swift failure (wrong scheme name, missing Xcode) must stay a hard failure — the bash-shim comment block (index_cli.py:116–131) documents exactly this reasoning.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Reusing `search_only=1` for the degraded state
**What:** persisting `search_only=1` on a generic-fallback publish.
**Why bad:** it's a one-way trap — `--search-only` is settable but never clearable, so the only escape is `jarvis forget`. The whole point of the generic fallback is self-healing retry. PROJECT.md lists this explicitly as out of scope.
**Instead:** degraded = `DEGRADED_STATUS` + `status_reason`, with `search_only` left 0.

### Anti-Pattern 2: Prompting inside `index_repo()`
**What:** TTY detection or `input()` anywhere in the pipeline function.
**Why bad:** `index_repo` is called by `watch.py` and is the natural entry for any future MCP-triggered reindex; a prompt there blocks a non-interactive process on stdin. The constraint is structural, not conditional.
**Instead:** all interaction in `_cmd_index`, before the pipeline runs.

### Anti-Pattern 3: A distinct "failed" reason table or log file
**What:** inventing a side-channel (JSON file, separate table) for failure details.
**Why bad:** the registry row is already the single source of truth `status`/`list`/`getIndexStatus` read; a second store can drift and needs its own lifecycle on `forget`.
**Instead:** one nullable `status_reason TEXT` column serves both `failed` (rung d) and `degraded` (rung c) — the status string disambiguates.

### Anti-Pattern 4: New status string without updating server.py's translation
**What:** adding `DEGRADED_STATUS` but leaving `_translate` checking `== SEARCH_ONLY_STATUS`.
**Why bad:** a degraded repo's nav call would return the raw `IndexNotFoundError` instead of the friendly "search works, nav doesn't, here's why" explanation — the exact UX this milestone exists to fix. The status-string check in server.py:140 is the one consumer that must move in lockstep with the new constant.

## Suggested Build Order

Dependencies point one way: registry schema → reason reporting → fallback → the two independents.

1. **Registry foundation** — three columns, `DEGRADED_STATUS`, `mark_failed(slug, reason)`, `mark_semantic_declined(slug)`, `status_reason` in upsert. Pure additive; every later feature depends on it; testable in isolation against `test_registry.py`.
2. **Failure-reason persistence + reporting (feature 4)** — swap `mark_status(slug, "failed")` for `mark_failed`, extend `_cmd_status` and `getIndexStatus`. Immediately valuable for today's hard failures, and gives features 1–3 their reporting surface before they exist. No behavior change to the publish decision.
3. **Generic opt-in fallback (features 1+2)** — classification rung c, `_resolve_fallback`, CLI flag + `JARVIS_FALLBACK_SEARCH_ONLY` in config.py, server.py translation-set extension. The largest change; lands on a registry and reporting surface that already work.
4. **scip-swift signatures (feature 3)** — data-only change to `_SEARCH_ONLY_SIGNATURES`, but *sequenced after the scip-swift pin update* (a separate milestone item): the signatures must be captured from the pinned binary's real failure output, and a version bump can change those strings. Independent of steps 1–3 in code, dependent on them for good reporting.
5. **Semantic install prompt (feature 5)** — needs only step 1's `semantic_declined` column; otherwise fully independent and parallelizable with steps 3–4. Touches `_cmd_index` only.

**Phase-structure implication:** steps 1+2 make a natural first phase (state + reporting, no behavior change to publishing); step 3 a second phase (the behavioral core); steps 4+5 can share a third phase or ride along wherever convenient.

## Compatibility & Risk Notes

| Concern | Assessment |
|---------|------------|
| Existing registries | Safe: `_ensure_column` migrates on open; NULL defaults preserve current semantics for every existing row |
| Existing `search_only=1` repos | Untouched: rung b and `_resolve_search_only` behavior are unchanged |
| `watch` re-running doomed builds on every change for a degraded repo | Accepted cost of self-healing (explicit PROJECT.md decision); debounce bounds the frequency. Worth a stderr note per retry so the cost is visible |
| Concurrent watch + manual reindex racing on new columns | Covered by the existing `busy_timeout=5000` and `_ensure_column`'s lock-error re-raise |
| `_publish_search_only` reuse for degraded path | Correct as-is: it already retires stale SCIP artifacts and graph edges, preventing the self-contradictory `{"indexed": true, "status": "degraded"}` state |

## Gaps / Open Questions

- **Exact scip-swift failure signature strings** — cannot be determined from this codebase; requires running the newly pinned scip-swift against known-failing repos (scheme errors, xcodebuild vs swiftpm mismatches) and capturing stderr. Flag the scip-swift-signatures phase for empirical capture, not web research.
- **Semantic install command detection** (uv tool vs source checkout) — small design decision deferred to the feature-5 phase.
- **Env var name** — `JARVIS_FALLBACK_SEARCH_ONLY` recommended (boolean, `JARVIS_` prefix per convention); confirm naming at plan time.

## Sources

- `src/jarvis/registry.py` — schema, `_ensure_column`, upsert ON CONFLICT semantics, `tracked_files` preserve pattern (HIGH, direct read)
- `src/jarvis/index_cli.py` — `_SEARCH_ONLY_SIGNATURES` (:87), bash-shim rationale (:116–131), fallback branch (:814–833), `_publish_search_only` (:648), `_run_semantic_stage` (:583), `_resolve_*` helpers, `_cmd_reindex` flag plumbing (HIGH, direct read)
- `src/jarvis/server.py` — `getIndexStatus` (:283–296), `_registry_status` (:66), `IndexNotFoundError` translation (:140) (HIGH, direct read)
- `.planning/PROJECT.md`, `.planning/codebase/ARCHITECTURE.md` — milestone decisions and system map (HIGH)
