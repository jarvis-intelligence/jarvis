# Phase 3: Opt-In Self-Healing Fallback - Context

**Gathered:** 2026-08-22
**Status:** Ready for planning

<domain>
## Phase Boundary

With fallback enabled, a post-build-start indexer failure degrades the repo to a working search-only index instead of leaving it with nothing — and every later reindex automatically retries the full build. Delivers FALL-01..05: opt-in degraded publish (Zoekt + semantic queryable), tri-state CLI flag + env var with documented precedence, self-healing degraded state (distinct from permanent `search_only=1`), hard-failure boundary for pre-build failures, and watch sha-keyed retry skip.

</domain>

<decisions>
## Implementation Decisions

### Degraded run outcome & reporting (Area 1 — accepted as proposed)
- Degraded run exits **0** with one stderr warning line: something queryable was published; degradation is visible via `jarvis status` / `getIndexStatus` (scripts keep working).
- Degraded terminal row: `status='degraded'`, `origin='fallback'`, reason + full stderr persisted through the phase-1 additive taxonomy slot (`ORIGIN_FALLBACK` constant + `recovery_for` branch). Never reuses permanent `search_only=1` semantics.
- `jarvis list` renders degraded rows as **◐** with the reason in the 6th field — a partial-health variant of the existing glyph family (✗/◐/✓).
- MCP: `last_index_run.outcome='degraded'`; `capabilities.navigation.reason` names the actual failure cause; nav-tool `_error_payload` state='fallback'. Zero payload reshaping — slots into the phase-1 D-13/D-14 payload contracts.

### Opt-in surface & flag semantics (Area 2 — accepted as proposed)
- `jarvis watch` accepts the same tri-state `--fallback-search-only` / `--no-fallback-search-only` flag (persists per-repo like `--scheme`/`--language`); watch is just another reindex driver.
- `jarvis reindex <slug>` honors the persisted value silently — no new flag on reindex (mirrors `--scheme`/`--language` handling).
- `JARVIS_FALLBACK_SEARCH_ONLY` parses a strict truthy set `1/true/yes/on` (case-insensitive); any other value warns once to stderr and is treated as off — loud misconfiguration beats silent.
- `--no-fallback-search-only` on an already-degraded repo governs future runs immediately: next reindex with a still-broken indexer is a hard failure. The degraded state never traps; opt-out is immediate.
- Precedence (locked by FALL-02): CLI > persisted > env > off.

### Self-heal & retry mechanics (Area 3 — accepted as proposed)
- Watch retry-skip keying (FALL-05): persist the **last full-build attempt sha**; watch skips the full-build retry only when the source sha is unchanged AND the row is degraded. Explicit `jarvis index` always retries the full build (FALL-03).
- If the degraded publish itself fails (e.g. zoekt error during the fallback publish): hard failure with cause persisted — nothing was published, the fallback promise is void.
- Post-build-start boundary (FALL-04 inverse): degrade-eligible from the indexer subprocess invocation onward — indexer failure, `scip expt-convert`, graph populate, main-pipeline zoekt. Pre-pipeline gates (binary presence, version floors including the scip-swift floor, bash-shim path, language detection, duplicate-slug) stay hard failures even with fallback enabled.
- `recovery_for(ORIGIN_FALLBACK)`: "fix the indexer failure, then `jarvis reindex <slug>` (full build retries automatically)" — matches the signature/search-only recovery verb and names the self-heal.

### Claude's Discretion
None flagged during discussion — all areas resolved with explicit answers.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_publish_search_only(repo_path, slug, root, semantic_include)` — the degraded publish is this path verbatim (semantic non-fatal inside it; Zoekt is the search substrate).
- `_resolve_search_only` / `_resolve_scheme` / `_resolve_language` / `_resolve_semantic_include` in `index_repo` — the tri-state persisted-override resolution pattern to copy for `fallback_enabled`.
- `registry.py`: `_ensure_column` additive migration, `ORIGIN_*` constants (Phase 3 slot named in the `registry.py:25-33` comment), `recovery_for()` read-time mapping, `record_failure()` INSERT..ON CONFLICT, D-04 failure-field clearing on terminal upserts.
- `server.py`: `_error_payload` state/cause/recovery keys, `_capability_fields`, `last_index_run` spread — degraded origin slots in with zero reshaping (phase-1 D-13/D-14/D-15).

### Established Patterns
- Registry writes: transitional `indexing` upsert → terminal upsert; failure fields cleared only on terminal success (D-04).
- CLI flag persistence: nullable tri-state stored per-repo, resolved CLI > persisted > env-style default; `_cmd_watch` passes overrides through to `index_repo`.
- Watch: `Debouncer` + `should_ignore_path` in `index_cli._cmd_watch`; reindex goes through `index_repo` directly.

### Integration Points
- `index_repo` main pipeline failure handler (the `except` around indexer/convert/graph/zoekt — currently `record_failure` + raise) — becomes the degrade branch when fallback resolves on.
- `registry.upsert` needs the `fallback_enabled` column (ROADMAP's Phase 1 dependency note names it) and the watch skip needs the last full-build attempt sha persisted on degraded rows.
- `watch.py`/`_cmd_watch` — sha-keyed skip consults the registry row before invoking `index_repo`.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — answers captured above in the three grey-area tables.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
