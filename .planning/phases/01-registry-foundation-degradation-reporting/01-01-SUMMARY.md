---
phase: 01-registry-foundation-degradation-reporting
plan: "01"
subsystem: database
tags: [sqlite, registry, cli, degradation-reporting, additive-migration]

# Dependency graph
requires:
  - phase: none (first plan of the milestone)
    provides: n/a
provides:
  - status_origin/status_reason/status_stderr columns on the repos table (additive, SC5-safe)
  - Registry.record_failure() INSERT..ON CONFLICT write (creates row when absent, D-05/D-06)
  - ORIGIN_FAILED_HARD/ORIGIN_SIGNATURE/ORIGIN_MANUAL constants + read-time origin_of()/recovery_for() (D-01, D-09)
  - upsert() NULL-clearing of failure fields on success (D-04)
  - jarvis status origin/cause/recovery lines + stderr tail; jarvis list ✗/◐/✓ markers (D-08, D-12)
affects: [01-02 origin stamping for search-only paths, 01-03 getIndexStatus/nav payloads, phase-3 degraded origin, phase-4 signatures]

actuals:
  tokens: 6800  # 27314 diff chars / 4 (plan estimate: 38000)
  tasks: 3
  commits: 6

tech-stack:
  added: []  # stdlib sqlite3 + pytest only — no new deps (PROJECT.md constraint honored)
  patterns:
    - "Additive nullable-TEXT columns declared in BOTH _SCHEMA and _ensure_column (5th..7th use of the proven idiom)"
    - "Failure write as INSERT..ON CONFLICT (never mark_status — bare UPDATE no-ops on missing rows)"
    - "Recovery command derived at read time from origin slug — never persisted per-row (D-09)"

key-files:
  created: []
  modified:
    - src/jarvis/registry.py
    - src/jarvis/index_cli.py
    - tests/test_registry.py
    - tests/test_index_cli.py

key-decisions:
  - "reason one-liner = first NON-EMPTY line of str(exc) (hardened one notch past the plan's splitlines()[0] so an empty-message exception still yields a non-empty reason per D-03)"
  - "record_failure conflict list deliberately excludes search_only/overrides/semantic_indexed_at/tracked_files (losing search_only would re-run doomed builds)"
  - "One migration test asserting all three columns together (plan's executor's-choice option) instead of three parameterized copies"
  - "List markers keyed on the status string (✗ failed / ◐ search-only / ✓ otherwise) so partial stays in the ✓ family (Pitfall 9)"
  - "Stderr display tail = last 20 lines + pointer 'full log: persisted in the registry (status_stderr column)'"

patterns-established:
  - "Origin taxonomy as module-level slugs beside SEARCH_ONLY_STATUS; Phase 3's `degraded` slots in as one constant + one recovery_for branch (assumption_delta: add-alongside)"
  - "TDD RED/GREEN per task: every task has test(01-01) commit preceding its feat(01-01) commit"

requirements-completed: [STAT-01]

coverage:
  - id: D1
    description: "Hard-failed index runs persist origin ('failed_hard'), one-line reason, and complete untruncated stderr in the registry row"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_hard_failed_index_persists_cause_and_full_stderr
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_record_failure_creates_row_when_absent
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_record_failure_overwrites_existing_row_and_preserves_search_only
        status: pass
    human_judgment: false
  - id: D2
    description: "jarvis status explains a failed repo: origin/cause lines plus a working recovery command, and shows the stderr tail with a pointer to the persisted full log"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_status_explains_a_failed_repo
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_status_prints_stderr_tail_and_pointer
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_status_omits_stderr_block_when_absent
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_recovery_for_derives_per_origin_commands
        status: pass
    human_judgment: false
  - id: D3
    description: "jarvis list marks repo health at a glance (✗/◐/✓) with the reason one-liner on failed rows, TSV columns 1-5 stable"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_list_marks_repo_health_at_a_glance
        status: pass
    human_judgment: false
  - id: D4
    description: "Pre-v1.0 registries migrate additively: gain the three columns on open, rows survive, legacy search_only=1 semantics untouched (SC5); success upserts clear stale failure fields (D-04)"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_registry.py#test_failure_columns_migrate_onto_an_existing_database
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_upsert_clears_failure_fields_on_success
        status: pass
      - kind: other
        ref: manual spot-check: copy of live ~/.jarvis/registry.db (14 rows) opened with JARVIS_DATA_DIR — PRAGMA shows the 3 columns; the 3 legacy search-only rows read search_only=True with NULL origins and derive the manual escape
        status: pass
    human_judgment: false

# Metrics
duration: 9min
completed: 2026-08-21
status: complete
---

# Phase 1 Plan 01: Registry Foundation & Degradation Reporting Summary

**Persisted failure cause (origin + one-line reason + untruncated stderr) in three additive registry columns, recorded by the index_repo hard-failure hook, explained with recovery commands by `jarvis status`, and marked ✗/◐/✓ by `jarvis list` — with legacy registries migrating in place.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-08-21T16:45:58Z
- **Completed:** 2026-08-21T16:55:40Z
- **Tasks:** 3 (all tdd; 6 RED/GREEN commits)
- **Files modified:** 4

## Accomplishments
- Hard-failed runs now leave a full cause record: `status_origin='failed_hard'`, single-line `status_reason`, complete verbatim `status_stderr` (a >1000-char marker round-trips byte-identical) — D-02/D-03/D-05/D-06
- `jarvis status` explains what happened (origin/cause) and how to recover (`jarvis index <path>` for failed_hard, per-origin read-time mapping) — STAT-01, D-09/D-12
- `jarvis list` shows repo health at a glance with glyphs and reason one-liners while keeping the 5-column TSV contract for scripts — D-08
- Success upserts NULL all three failure fields (D-04) and pre-v1.0 databases gain the columns additively with search-only semantics untouched (SC5) — verified against a copy of the live 14-row registry

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: End-to-end hard-failure record** — `b20a241` (test) + `a671d53` (feat)
2. **Task 2: D-04 NULL-clearing + legacy migration** — `f3a454d` (test) + `4c0da77` (feat)
3. **Task 3: list markers + status stderr tail** — `5e7e170` (test) + `c1fc05d` (feat)

## Files Created/Modified
- `src/jarvis/registry.py` — three columns in `_SCHEMA`+`_ensure_column`; `RegisteredRepo`/`_row_to_repo`/`get`/`list` extended positionally; `ORIGIN_*` constants; `origin_of()`/`recovery_for()`; `record_failure()`; upsert conflict-list NULL-clearing
- `src/jarvis/index_cli.py` — hard-failure hook records cause; `_cmd_status` origin/cause/recovery + stderr-tail block; `_cmd_list` glyph markers + 6th reason field
- `tests/test_registry.py` — record_failure create/overwrite, recovery mapping (incl. legacy + unknown origins), D-04 clearing, legacy migration, roundtrip extension
- `tests/test_index_cli.py` — e2e failure-persistence test, status explanation/tail/absent tests, list marker test

## Decisions Made
- Reason extraction uses the first **non-empty** line of the exception text (plan's `splitlines()[0]` hardened for empty-message exceptions — must_haves require a non-empty one-liner)
- `record_failure()` returns the freshly-read row via `get()` (pattern 1h), so callers see persisted truth, not a reconstruction
- Single combined migration test for all three columns rather than three parameterized copies (executor's choice offered by the plan)
- List glyph derives from the status string, not the `search_only` flag, so `partial` stays in the ✓ family (RESEARCH Pitfall 9)

## Deviations from Plan

None - plan executed exactly as written (all six RED/GREEN commits present, acceptance criteria all pass).

## Issues Encountered
- Two edit-hunk mistakes during Task 1 GREEN briefly dropped existing lines (`RegisteredRepo.search_only` field; `_row_to_repo`'s `semantic_include`/`language_override` kwargs) — caught immediately by the very roundtrip tests RESEARCH Pitfall 3 prescribes, restored before any commit. No shipped impact.
- Bare `uv run pytest` on this host resolves to a Homebrew pytest outside the project venv (`ModuleNotFoundError: jarvis`); all test runs used the equivalent `uv run python -m pytest`. No code impact.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Registry schema, origin taxonomy, and recovery derivation are ready for Plan 01-02 (origin stamping on the manual/signature search-only paths) and Plan 01-03 (`getIndexStatus`/nav-tool payloads can import `origin_of`/`recovery_for` from `jarvis.registry` directly)
- `degraded` (Phase 3) slots in as one new constant + one `recovery_for` branch — no schema change (add-alongside decision held)
- Known gap carried forward from the plan's flagged assumptions: `jarvis reindex <slug>` on a signature row re-publishes search-only today (D-11 wording kept as locked; Phase 3 changes reindex semantics)

## Self-Check: PASSED

- All 4 modified files exist on disk
- All 6 task commits present in git log (b20a241, a671d53, f3a454d, 4c0da77, 5e7e170, c1fc05d)
- `uv run python -m pytest -m "not integration" -q -rs`: 455 passed, 25 skipped (semantic extra absent), 12 deselected (integration) — CI gate green
- `uv run python -m pytest tests/test_registry.py tests/test_index_cli.py -q`: 164 passed, 1 skipped
- Manual SC5 spot-check on a copy of the live registry: 3 new columns present, 14 rows intact, legacy search-only rows untouched with read-time manual-escape recovery

---
*Phase: 01-registry-foundation-degradation-reporting*
*Completed: 2026-08-21*
