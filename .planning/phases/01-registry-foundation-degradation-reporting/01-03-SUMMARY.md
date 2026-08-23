---
phase: 01-registry-foundation-degradation-reporting
plan: "03"
subsystem: api
tags: [mcp, fastmcp, degradation-reporting, additive-payloads, tdd]

# Dependency graph
requires:
  - phase: 01-01
    provides: status_origin/status_reason/status_stderr columns, origin_of()/recovery_for() read-time derivation, ORIGIN_* taxonomy
  - phase: 01-02
    provides: origin-stamped search-only writes (manual/signature slugs + matched reason verbatim), failed_hard rows on every failure path
provides:
  - Nav-tool error payloads carry additive state/cause/recovery keys when the registry row explains the state (D-14, STAT-02, SC3)
  - getIndexStatus exposes last_index_run {outcome, origin, reason, recovery} + capabilities.{navigation,search,semantic} (D-13/D-15, STAT-03/STAT-01-MCP, SC4)
  - server.py _registry_entry() best-effort full-row lookup (_registry_status delegates to it)
  - The payload shape Phase 3's degraded origin slots into without reshaping (outcome mirrors status verbatim; one new recovery_for branch)
affects: [phase-3 degraded origin reporting, phase-5 semantic_declined capability reason]

actuals:
  tokens: 5784  # 23138 diff chars / 4 (plan estimate: 32000)
  tasks: 2
  commits: 4

tech-stack:
  added: []  # FastMCP + stdlib pathlib glob only — no new deps
  patterns:
    - "Two orthogonal payload layers in one helper: run truth (registry row) vs on-disk truth (pointer/shards) — never cross-derived (D-07/D-15)"
    - "Double-layer never-raise: helper-internal broad except (searchCoverage gold standard) + call-site belt in the published tool"
    - "Capability payload built key-by-key, never asdict(entry) — status_stderr cannot leak (Pitfall 7)"

key-files:
  created: []
  modified:
    - src/jarvis/server.py
    - tests/test_server_tools.py

key-decisions:
  - "last_index_run.origin uses origin_of() (read-time fallback) rather than raw entry.status_origin — the plan's feature behavior says \"<origin or 'manual'>\" and recovery_for() derives from origin_of, so raw status_origin would desync origin from recovery on legacy search-only rows"
  - "Structured error keys apply to the IndexNotFoundError branch for ANY origin-carrying row (a failed_hard first-index row also explains itself), while the prose search-only explanation stays gated on status == 'search-only' — matches D-14's 'keys appear when the row explains the state'"
  - "capabilities.* sub-dicts carry always-present keys with None values (stable shape for MCP clients branching without KeyError), while _error_payload keeps conditional keys (plan-specified, resolvedSymbol precedent)"
  - "Non-spawning search derivation: zoekt_dir.is_dir() + {slug}_v*.zoekt glob (the _v guard from _sweep_zoekt_tmp_orphans), asserted by monkeypatched subprocess.run/Popen + ZoektLifecycle.ensure_running raisers"

patterns-established:
  - "Status/capability code never spawns: filesystem glob + row read only, test-enforced (A5)"
  - "TDD RED/GREEN per task: every task has test(01-03) commit preceding its feat(01-03) commit"

requirements-completed: [STAT-01, STAT-02, STAT-03]

coverage:
  - id: D1
    description: "Nav-tool errors on degraded/search-only repos carry additive state (origin slug), cause (status_reason), recovery keys alongside the intact prose error; legacy NULL-origin rows read 'manual'; row-less/unreadable/other-fault paths keep their prior bare shapes"
    requirement: STAT-02
    verification:
      - kind: unit
        ref: tests/test_server_tools.py#test_error_payload_carries_state_cause_recovery_for_a_signature_search_only_repo
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_error_payload_reports_manual_state_for_a_legacy_search_only_row
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_error_payload_without_a_registry_row_stays_bare
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_error_payload_does_not_mask_other_faults_on_a_degraded_repo
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_error_payload_degrades_to_bare_error_when_registry_is_unreadable
        status: pass
    human_judgment: false
  - id: D2
    description: "getIndexStatus returns additive last_index_run (outcome mirrors the registry status string verbatim; origin/reason/recovery populated exactly when the row carries them) and capabilities.{navigation,search,semantic} an MCP client branches on without parsing prose; existing keys (repo/indexed/status/freshness/searchCoverage) unchanged"
    requirement: STAT-03
    verification:
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_reports_last_index_run_for_a_search_only_repo
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_navigation_unavailable_for_search_only_explains_and_recovers
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_search_capability_follows_zoekt_shards_on_disk
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_semantic_capability_follows_the_row
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_adds_capability_fields_without_reshaping_existing_keys
        status: pass
    human_judgment: false
  - id: D3
    description: "D-07's orthogonal combination verified: a repo whose last run failed but whose current pointer is live reports outcome='failed' AND capabilities.navigation.available=true with the stale commit named in the reason"
    requirement: STAT-03
    verification:
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_failed_run_with_live_pointer_reports_stale_navigation
        status: pass
    human_judgment: false
  - id: D4
    description: "Safety envelope: capability-derivation failure degrades fields to nulls while the status response stays alive, and status/capability derivation never spawns zoekt-webserver or any subprocess"
    requirement: STAT-03
    verification:
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_capability_failure_degrades_to_nulls
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_capability_derivation_never_spawns
        status: pass
    human_judgment: false

# Metrics
duration: 9min
completed: 2026-08-21
status: complete
---

# Phase 1 Plan 03: Degradation-Aware MCP Payloads Summary

**Machine-readable degradation reporting at the MCP boundary: nav-tool errors carry additive state/cause/recovery structured keys (D-14), and getIndexStatus exposes last_index_run + pointer-truth capabilities.{navigation,search,semantic} (D-13/D-15) — additive only, never spawning, never leaking stderr.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-08-21T17:13:49Z
- **Completed:** 2026-08-21T17:22:38Z
- **Tasks:** 2 (both tdd; 4 RED/GREEN commits)
- **Files modified:** 2

## Accomplishments
- All 5 nav-tool choke points (via the single `_error_payload`) now return `state` (origin slug via `origin_of`, legacy rows read 'manual'), `cause` (one-line status_reason), and `recovery` (read-time `recovery_for` command) alongside the byte-identical prose `error` — STAT-02, SC3, D-14
- `getIndexStatus` returns `last_index_run: {outcome, origin, reason, recovery}` (outcome = registry status string verbatim, resolution #3) and `capabilities: {navigation: {available, reason, recovery}, search: {available, reason}, semantic: {available, reason}}` — STAT-03/STAT-01 MCP side, SC4, D-13/D-15
- D-07 verified in-payload: failed run + live pointer → outcome='failed' AND navigation.available=true with "stale — indexed at <commit>" reason (Pitfall 4's "correct, not a bug" combination)
- Never-raise and no-spawn enforced by tests: capability derivation degrades to nulls on any failure; search truth is a pure `{slug}_v*.zoekt` glob, semantic truth is the row's `semantic_indexed_at`; `status_stderr` never enters any payload (Pitfall 7)

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: _registry_entry helper + structured nav-error keys (D-14)** — `88d3186` (test) + `6b82432` (feat)
2. **Task 2: getIndexStatus capabilities + last_index_run (D-13/D-15)** — `8c89224` (test) + `6d44503` (feat)

## Files Created/Modified
- `src/jarvis/server.py` — `_registry_entry()` best-effort full-row lookup with `_registry_status` delegating; `_error_payload` IndexNotFoundError branch extended with conditional state/cause/recovery keys; `_capability_fields(repo, indexed, freshness)` never-raise helper; `get_index_status` spreads capability fields additively (service-failure path unchanged)
- `tests/test_server_tools.py` — 13 new tests: 5 structured-error (incl. legacy-manual, no-row, no-masking, unreadable-registry guards) + 8 capability (last_index_run mirroring, search-only navigation reason/recovery, failed+live-pointer staleness, shard glob with `_v` guard, semantic row derivation, never-raise degradation, additive-key preservation, no-spawn)

## Decisions Made
- `last_index_run.origin` derives from `origin_of()` (legacy search-only rows read 'manual'), not raw `entry.status_origin` — keeps origin consistent with `recovery_for()` and the plan's `<origin or "manual">` behavior line
- Structured error keys apply to any origin-carrying row on the IndexNotFoundError branch (failed_hard first-index rows explain themselves too); the prose search-only explanation remains gated on `status == "search-only"`
- `capabilities.*` uses always-present keys with None values (client-stable shape); `_error_payload` keeps conditional keys per the plan's explicit instruction
- Double-layer never-raise: broad except inside `_capability_fields` (mirroring `_search_coverage_fields`) plus a call-site belt in `get_index_status` so even a buggy/monkeypatched helper cannot kill the published status response

## Deviations from Plan

None - plan executed exactly as written (all four RED/GREEN commits present, acceptance criteria all pass).

## Issues Encountered
- Two transient edit-hunk mistakes during GREEN phases (an import line briefly replaced by the extended registry import; a one-space indentation typo in a RED test) — both caught immediately by the following test run/edit echo and repaired before any commit. No shipped impact.
- Bare `uv run pytest` on this host resolves to a Homebrew pytest outside the project venv (`ModuleNotFoundError: jarvis`, known from 01-01); all runs used `uv run python -m pytest`. No code impact.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 1 complete: every degradation surface (registry columns, CLI status/list, MCP nav errors, getIndexStatus) now reports origin, cause, and recovery from one shared read-time derivation (`origin_of`/`recovery_for`)
- Phase 3's `degraded` origin slots in with zero payload reshaping: one `ORIGIN_DEGRADED` constant, one `recovery_for` branch, one `upsert(status_origin=...)` call site — `last_index_run.outcome` mirrors whatever status string the row carries
- Phase 5's `semantic_declined` can extend `capabilities.semantic.reason` without touching the branch logic
- Flagged assumptions carried (not resolved here, per plan): EDGE-STAT-02/EDGE-STAT-03 boundary coverage; A1 (MCP clients tolerate additive keys — precedent-based)

## Self-Check: PASSED

- Both modified files exist on disk
- All 4 task commits present in git log (88d3186, 6b82432, 8c89224, 6d44503); no accidental file deletions in any task commit
- TDD gate: each `test(01-03):` commit precedes its `feat(01-03):` commit
- `uv run python -m pytest tests/test_server_tools.py -q`: 41 passed
- `uv run python -m pytest -m "not integration" -q -rs`: 478 passed, 25 skipped (semantic extra absent), 12 deselected (integration) — CI gate green
- `grep status_stderr src/jarvis/server.py` matches only the two docstrings documenting the prohibition — no payload usage

---
*Phase: 01-registry-foundation-degradation-reporting*
*Completed: 2026-08-21*
