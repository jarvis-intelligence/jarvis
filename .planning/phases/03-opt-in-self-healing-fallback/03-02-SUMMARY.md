---
phase: 03-opt-in-self-healing-fallback
plan: "02"
subsystem: api
tags: [mcp, cli, degraded-reporting, fallback, pytest]

# Dependency graph
requires:
  - phase: 03-opt-in-self-healing-fallback
    provides: DEGRADED_STATUS/ORIGIN_FALLBACK carriers, recovery_for fallback branch, degraded terminal row write (status/origin/reason/stderr)
provides:
  - getIndexStatus degraded branch — capabilities.navigation.reason = persisted status_reason (default "indexer failure — degraded to search-only"), recovery = fallback self-heal verb
  - Pinned phase-1 verbatim contracts for degraded rows — last_index_run outcome/origin/reason/recovery and _error_payload state/cause/recovery keys (zero reshaping, no code change needed)
  - jarvis list degraded rendering — ◐ glyph + 6th TSV reason field; search-only rows unchanged (5 fields)
  - README JARVIS_FALLBACK_SEARCH_ONLY env entry + --fallback-search-only usage line (docs rider, research A5)
affects: [03-03 watch sha-keyed skip + flag pass-through, phase-close UAT]

actuals:
  tokens: 3300  # 13262 diff chars / 4 (plan estimate: 50000)
  tasks: 2
  commits: 4

tech-stack:
  added: []  # read-only branch additions over existing code, zero packages
  patterns:
    - "Degraded joins the ◐ glyph family with a reason field — same reason-first TSV contract as failed rows, discriminated by status string only"
    - "Pin tests for additive phase-1 contracts that already carry new state verbatim — assert before extending, change no code"

key-files:
  created: []
  modified:
    - src/jarvis/server.py
    - src/jarvis/index_cli.py
    - README.md
    - tests/test_server_tools.py
    - tests/test_index_cli.py

key-decisions:
  - "The only server.py behavioral change is the _capability_fields degraded elif — last_index_run and _error_payload needed zero changes; the origin-key block already covers degraded rows (pinned, OQ-5 skip honored)"
  - "Degraded rows render ◐ like search-only (search still answers) but carry the failure cause as a 6th TSV field like failed — the glyph says what works, the field says why"
  - "6th-field condition expressed as repo.status in (\"failed\", DEGRADED_STATUS) — behavior of failed rows identical, one branch instead of two"

patterns-established:
  - "Report-surface contract: state carries verbatim from the registry; surfaces only format, never re-derive"

requirements-completed: [FALL-01]

coverage:
  - id: D1
    description: "getIndexStatus on a degraded repo: navigation.available false, reason = persisted status_reason (or the default degraded wording when NULL), recovery containing the jarvis reindex self-heal verb"
    requirement: FALL-01
    verification:
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_navigation_unavailable_for_degraded_reports_cause_and_recovery
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_degraded_reason_defaults_when_status_reason_missing
        status: pass
    human_judgment: false
  - id: D2
    description: "Verbatim payload pins: last_index_run reads outcome=degraded/origin=fallback/reason/recovery; _error_payload carries state=fallback, cause, recovery, and the error key"
    requirement: FALL-01
    verification:
      - kind: unit
        ref: tests/test_server_tools.py#test_get_index_status_reports_last_index_run_for_a_degraded_repo
        status: pass
      - kind: unit
        ref: tests/test_server_tools.py#test_error_payload_carries_state_cause_recovery_for_a_degraded_repo
        status: pass
    human_judgment: false
  - id: D3
    description: "CLI degraded rendering: list renders ◐ degraded with the reason as 6th TSV field, search-only rows keep exactly 5 fields; status prints origin: fallback, cause, and the jarvis reindex recovery line"
    requirement: FALL-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_list_renders_degraded_rows_with_glyph_and_reason
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_status_explains_a_degraded_repo
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_list_keeps_search_only_rows_five_field_beside_degraded
        status: pass
    human_judgment: false
  - id: D4
    description: "README docs rider: JARVIS_FALLBACK_SEARCH_ONLY entry naming accepted values (1/true/yes/on) with the treated-as-off warning, plus the --fallback-search-only usage line"
    verification:
      - kind: other
        ref: "grep README.md: JARVIS_FALLBACK_SEARCH_ONLY entry with `1`/`true`/`yes`/`on` + warning note; usage block line 168"
        status: pass
    human_judgment: false

# Metrics
duration: 5min
completed: 2026-08-22
status: complete
---

# Phase 3 Plan 02: Reporting Surfaces Summary

**Degraded state is now visible everywhere agents and users look: getIndexStatus's navigation.reason names the actual failure cause (the one real gap), list renders ◐ degraded with the cause as a 6th TSV field, and the phase-1 last_index_run/_error_payload contracts are pinned as carrying degraded/fallback verbatim with zero reshaping.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-08-22T17:33:19Z
- **Completed:** 2026-08-22T17:37:36Z
- **Tasks:** 2 (both tdd; 4 RED/GREEN commits)
- **Files modified:** 5

## Accomplishments
- server.py: the `_capability_fields` degraded elif — a degraded repo's `capabilities.navigation.reason` is the persisted `status_reason` (default "indexer failure — degraded to search-only"), and `recovery_for`'s fallback branch supplies the self-heal verb automatically
- Pinned the phase-1 additive contracts for degraded rows: `last_index_run` = outcome "degraded"/origin "fallback"/reason/recovery; `_error_payload` = state "fallback"/cause/recovery alongside `error` — both needed no code change (OQ-5 resolved as skip, honored)
- index_cli.py: `jarvis list` renders degraded rows ◐ with the cause as a 6th TSV field; search-only rows keep exactly 5 fields; `jarvis status` origin/cause/recovery lines pinned as already correct for fallback
- README: `JARVIS_FALLBACK_SEARCH_ONLY` env-var entry (strict 1/true/yes/on, garbage→off with one warning) + `--fallback-search-only` usage example

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: MCP degraded reporting — navigation.reason branch + payload pins** — `5ea8bbe` (test) + `ccc7159` (feat)
2. **Task 2: CLI degraded rendering — list glyph, 6th field, status pin** — `dcb07bb` (test) + `c19aae6` (feat)

## Files Created/Modified
- `src/jarvis/server.py` — DEGRADED_STATUS import + the one `_capability_fields` degraded elif (6 insertions total; nothing else touched)
- `src/jarvis/index_cli.py` — `_cmd_list` degraded glyph branch + 6th-field condition + D-08 comment update
- `README.md` — fallback usage line + env-var entry
- `tests/test_server_tools.py` — 4 tests: degraded nav reason (RED), default wording (RED), last_index_run pin, _error_payload pin
- `tests/test_index_cli.py` — 3 tests: list glyph+6th field (RED), status pin, search-only 5-field regression guard

## Decisions Made
- The 6th-field branch is `repo.status in ("failed", DEGRADED_STATUS)` — failed behavior identical, single condition instead of duplicated branches
- Kept `_error_payload` and the `last_index_run` spread untouched; the plan's key_links predicted no change was required and the pin tests proved it before any edit

## Deviations from Plan

None - plan executed exactly as written.

## TDD Gate Compliance

Both tasks (tdd="true") produced a `test(03-02):` RED commit before their `feat(03-02):` GREEN commit. Task 1 RED failed exactly on the predicted gap (`assert None == 'indexer crashed'` / the default wording); Task 2 RED failed on `assert '✓ degraded' == '◐ degraded'` and the missing 6th field. Pin tests (Task 1 Tests 3-4, Task 2 Test 2) passed in RED as predicted — phase-1 contracts already carried degraded verbatim.

## Issues Encountered
- One edit-tool boundary mistake while inserting the server.py elif (line numbers had shifted after the import edit) briefly clobbered the search-only branch body; caught by immediate re-read and repaired before any test run or commit
- Plan's read_first line ranges for index_cli.py (1017-1075) pointed at an older snapshot — the actual `_cmd_list`/`_cmd_status` sit at 1106-1164 post-03-01; located by grep, no impact

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All FALL-01 reporting surfaces ship in this plan; Plan 03-03 (watch sha-keyed skip + flag pass-through) has its carriers intact — no interface changes here affect it
- The `jarvis reindex` recovery verb and ◐ glyph contract are now observable end-to-end for the phase-close UAT

## Self-Check: PASSED

- All 5 modified files exist on disk
- All 4 task commits present in git log (5ea8bbe, ccc7159, dcb07bb, c19aae6)
- `uv run python -m pytest tests/test_server_tools.py -q`: 45 passed
- `uv run python -m pytest tests/test_server_tools.py tests/test_index_cli.py -k "degraded or fallback" -q`: 41 passed (plan verification)
- `uv run python -m pytest -m "not integration" -rs -q`: 616 passed, 17 deselected — CI gate green
- README acceptance: `JARVIS_FALLBACK_SEARCH_ONLY` entry names `1`/`true`/`yes`/`on` and the treated-as-off warning (grep-verified)

---
*Phase: 03-opt-in-self-healing-fallback*
*Completed: 2026-08-22*
