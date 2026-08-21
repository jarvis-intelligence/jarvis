# Phase 1: Registry Foundation & Degradation Reporting - Context

**Gathered:** 2026-08-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Persist per-repo failure and degradation state in the registry (additive columns only) and surface it — cause, origin, recovery — on every surface a user or agent consults: `jarvis status`, `jarvis list`, `getIndexStatus`, and nav-tool error payloads. This phase is **reporting only**: it does not change indexing behavior, does not implement fallback (Phase 3), and does not touch the scip-swift toolchain (Phase 2). The origin taxonomy and capability payloads it defines must be shaped so Phase 3's opt-in `degraded` origin and Phase 5's `semantic_declined` column slot in without reshaping.

</domain>

<decisions>
## Implementation Decisions

### Persisted failure record shape
- **D-01:** Origin taxonomy is stored as string slugs in a TEXT column — `failed_hard`, `signature`, `manual` — with `degraded` reserved for Phase 3. Slugs are readable straight from the sqlite3 CLI and new origins are additive — **Reversibility:** costly — slugs get persisted in registry rows and MCP payloads; renaming one later requires a data migration plus payload-compat handling
- **D-02:** Persist the **complete, unbounded** indexer stderr on failure — no truncation. (User explicitly chose full fidelity over a capped tail; watch-loop overwrite churn was accepted.)
- **D-03:** Two cause columns: `status_reason` (one-line classified summary for status surfaces) + a second column (e.g. `status_stderr`) holding the full raw stderr verbatim
- **D-04:** Failure fields (`status_origin`/`status_reason`/stderr column) are NULLed on the next successful index — the registry always reflects the latest run

### Failed-run registry behavior
- **D-05:** A hard-failed **first** index creates a registry row (`status_origin='failed_hard'` + cause + path/language recorded) so `jarvis status`, `getIndexStatus`, and `jarvis list` can explain the failure and `jarvis reindex` works. Today nothing is persisted (`registry.upsert()` runs only post-publish)
- **D-06:** A failed reindex of an already-registered repo **fully overwrites** the row — `commit_sha`/`last_indexed`/`status` all reflect the failed attempt plus failure fields. (User chose full overwrite over keeping last-good facts in the row.)
- **D-07:** Nav tools keep serving the last published index when the latest run failed (atomic publish leaves it live). Status surfaces report both facts: "last index run failed" AND "navigation available but stale (indexed at <commit>)" — capability truth comes from the `current` pointer on disk, not from the row's status
- **D-08:** `jarvis list` gains status markers (✗ failed / ◐ search-only / ✓ ok) with the reason one-liner for failed rows

### Recovery command mapping
- **D-09:** Recovery commands are **derived from origin at read time** via a per-origin mapping in code — never persisted per-row. Phase 3 adds one mapping entry for `degraded`
- **D-10:** `manual` origin reports the real escape that exists today: `jarvis forget <slug>` then `jarvis index <path>` without `--search-only`. Un-setting `--search-only` in place remains out of scope
- **D-11:** `signature` origin reports one generic recovery — `jarvis reindex <slug>` after fixing the toolchain. The existing per-signature remedy texts (Kotlin 2.2.0, bash ≥4.4 shim) are NOT surfaced into status
- **D-12:** `failed_hard` origin reports the full original command: `jarvis index <path>`

### Capability field shape
- **D-13:** `getIndexStatus` exposes nested per-capability fields: `capabilities: {navigation: {available, reason, recovery}, search: {…}, semantic: {…}}` — an MCP client branches on `capabilities.navigation.available` without parsing prose — **Reversibility:** one-way — additive MCP payload keys, but once clients branch on them removal breaks the published tool contract
- **D-14:** Nav-tool error payloads gain additive structured keys — `state` (origin slug), `cause`, `recovery` — alongside the existing prose `error` string. Existing `error` key keeps its meaning — **Reversibility:** one-way — same published-contract reasoning as D-13
- **D-15:** Run outcome and capability are orthogonal layers: top-level `last_index_run: {outcome, origin, reason, recovery}` reports the latest run; `capabilities.*` reports on-disk truth. A repo can be `outcome=failed` with `navigation.available=true, reason="stale — indexed at <commit>"` (see D-07). Phase 3's degraded state slots into the same shape

### Claude's Discretion
- Exact column names (`status_origin`/`status_reason`/`status_stderr` are suggestions, shapes are locked)
- Marker glyphs and layout of the `jarvis list` status column
- Exact key naming/casing within the locked payload shapes of D-13/D-14/D-15
- How `capabilities.semantic` reports when the `semantic` extra isn't installed

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Planning documents
- `.planning/ROADMAP.md` §Phase 1 — goal, requirements STAT-01..03, 5 success criteria (incl. additive `_ensure_column` migration criterion)
- `.planning/REQUIREMENTS.md` §Status & Reporting — STAT-01, STAT-02, STAT-03 definitions; §Out of Scope (one-way `--search-only` trap stays)
- `.planning/PROJECT.md` §Context / §Constraints — key implementation surfaces, additive-migration constraint, no-new-deps rule

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md` — layering, error-handling strategy (error dicts at tool boundaries), registry/index_reader responsibilities
- `.planning/codebase/INTEGRATIONS.md` §Data Storage — current `repos` table column list (the additive-migration baseline)

### Source surfaces this phase modifies
- `src/jarvis/registry.py` — `Registry` class; where new columns + failure-row writes land
- `src/jarvis/index_cli.py` — `index_repo()` pipeline (fallback branch ~817–833, `_publish_search_only`, `registry.upsert()` call ~830); where failed-run persistence hooks in
- `src/jarvis/server.py` — `getIndexStatus` tool + nav-tool error wrapping; where capability payloads land
- `src/jarvis/index_reader.py` — `IndexNotFoundError` / `current` pointer reads backing D-07's on-disk capability truth

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Registry` (`src/jarvis/registry.py`) — existing CRUD over the `repos` table; extend with failure-row upsert + column migration
- `search_only_reason()` / `_SEARCH_ONLY_SIGNATURES` (`src/jarvis/index_cli.py:87`) — existing signature matching that produces search-only states; the `signature` origin rows describe what this already does
- `IndexConnectionCache.read_pointer()` (`src/jarvis/index_reader.py`) — reads the `current` pointer; the on-disk truth source for D-07 capability reporting
- Frozen-dataclass → `asdict()` pattern (`src/jarvis/models.py`) — result shapes for the status/capability payloads

### Established Patterns
- Raw `sqlite3`, parameterized queries, no ORM; additive columns must not break pre-v1.0 registries (ROADMAP success criterion 5)
- MCP tools catch broadly and return `{"error": "..."}` dicts, never raise — D-14 extends this dict additively
- `JARVIS_`-prefixed env vars; print-to-stdout CLI output, no logging framework
- Optional extras use deferred imports — status reporting must work on a base install

### Integration Points
- `registry.upsert()` call site after publish (`src/jarvis/index_cli.py:830`) — today the only registry write in the pipeline; failure writes need new call sites in the failure branches
- `getIndexStatus` (`src/jarvis/server.py`) — currently reports presence + freshness; gains `capabilities` + `last_index_run`
- Nav-tool exception path in `server.py` — `IndexNotFoundError` currently becomes a bare error dict; gains structured keys when the repo row explains the state
- `jarvis list` / `jarvis status` subcommands in `index_cli.py` — output formatting for D-08 markers

</code_context>

<specifics>
## Specific Ideas

No specific external references were cited. Notable explicit user calls (against the recommended options — treat as locked intent):
- Full unbounded stderr persistence, not a truncated tail (D-02)
- Full row overwrite on reindex failure, not last-good facts (D-06)
- `failed_hard` recovery reports the full `jarvis index <path>` command, not `jarvis reindex` (D-12)
- Generic recovery for signature origins; per-signature remedy texts stay out of status (D-11)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 1-Registry Foundation & Degradation Reporting*
*Context gathered: 2026-08-21*
