---
phase: 01-registry-foundation-degradation-reporting
plan: "02"
subsystem: database
tags: [sqlite, registry, cli, origin-stamping, tdd]

# Dependency graph
requires:
  - phase: 01-01
    provides: status_origin/status_reason/status_stderr columns, record_failure(), ORIGIN_* constants, upsert() conflict-list NULL-clearing
provides:
  - upsert() status_origin/status_reason keyword parameters (origin-aware success writes, backward-compatible defaults)
  - Manual --search-only publishes stamped status_origin='manual'; signature fallbacks stamped 'signature' + the matched per-signature reason verbatim
  - Failures inside the manual search-only branch recorded as full failed_hard rows (no bare mark_status path remains)
  - Pre-pipeline failures (scip version gate, bad persisted language override, detection raise) leave recoverable rows — D-05 closed for this phase's hook list
affects: [01-03 getIndexStatus/nav payloads (origin-stamped rows to report), phase-3 degraded origin (slots into the same write pattern)]

actuals:
  tokens: 6400  # 25426 diff chars / 4 (plan estimate: 34000)
  tasks: 3
  commits: 6

tech-stack:
  added: []  # stdlib sqlite3 + pytest only — no new deps
  patterns:
    - "Origin stamping rides the existing success upsert (status_origin/status_reason kwargs) — never a second follow-up write"
    - "Pre-pipeline wrap: resolved_language sentinel, UNKNOWN_LANGUAGE when resolution never completed (D-06 full-overwrite honest record)"
    - "Rejected requests are not failed runs: the duplicate-slug gate stays a plain close-and-raise"

key-files:
  created: []
  modified:
    - src/jarvis/registry.py
    - src/jarvis/index_cli.py
    - tests/test_registry.py
    - tests/test_index_cli.py

key-decisions:
  - "Duplicate-gate test scenario implemented per the gate's actual semantics (path already registered under another slug) — the plan's prose scenario 'index path B as x' does not raise; must_haves truth #5's intent (no failure stamp, no phantom row) governs"
  - "Bad-override failures record language='unknown' (must_haves truth #4: 'unknown when detection never completed') over the feature-behavior phrase 'reflects the attempt' — the attempt never established a language, and D-06 forbids last-good facts lingering"
  - "reason one-liner reuses Plan 01's first-NON-EMPTY-line idiom in the two new handlers (plan's splitlines()[0] would persist an empty reason for empty-message exceptions; D-03 requires non-empty)"

patterns-established:
  - "Every new failure surface persists through record_failure with the resolved-language sentinel — no path back to mark_status"
  - "TDD RED/GREEN per task: every task has test(01-02) commit preceding its feat(01-02) commit"

requirements-completed: [STAT-01]

coverage:
  - id: D1
    description: "upsert() accepts status_origin/status_reason keyword parameters that persist verbatim, keep stderr NULL-by-omission, and leave defaults (all three failure fields NULL) untouched — D-04 preserved through the new parameters"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_registry.py#test_upsert_round_trips_origin_parameters
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_upsert_origin_parameters_replace_a_prior_failure_record
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_plain_upsert_leaves_failure_fields_null
        status: pass
    human_judgment: false
  - id: D2
    description: "Both search-only paths are self-describing: manual publishes stamp origin 'manual', signature fallbacks stamp 'signature' with the matched reason text verbatim (D-11: reason, never remedy); failures inside the manual branch record full failed_hard rows; unmatched failures are never laundered"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_manual_search_only_publish_stamps_manual_origin
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_signature_fallback_stamps_signature_origin_and_matched_reason
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_failed_search_only_publish_records_a_full_failure_row
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_unmatched_indexer_failure_never_gains_a_signature_origin
        status: pass
    human_judgment: false
  - id: D3
    description: "Pre-pipeline failures (version gate on a never-indexed repo, stale persisted language override) leave recoverable failed_hard rows — language 'unknown' when resolution never completed, slug resolvable for jarvis reindex; duplicate-slug rejection writes nothing"
    requirement: STAT-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_pre_pipeline_version_gate_failure_creates_a_recoverable_row
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_pre_pipeline_stale_language_override_failure_overwrites_the_row
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_duplicate_slug_rejection_writes_no_failure_row
        status: pass
    human_judgment: false

# Metrics
duration: 9min
completed: 2026-08-21
status: complete
---

# Phase 1 Plan 02: Origin Persistence Across Every index_repo Outcome Summary

**Origin-stamped search-only writes (manual/signature slugs with the matched reason verbatim) plus a pre-pipeline failure wrap so version-gate, bad-override, and detection failures leave recoverable failed_hard rows — closing D-05 with no silent failure path left in index_repo().**

## Performance

- **Duration:** 9 min
- **Started:** 2026-08-21T17:01:30Z
- **Completed:** 2026-08-21T17:10:30Z
- **Tasks:** 3 (all tdd; 6 RED/GREEN commits)
- **Files modified:** 4

## Accomplishments
- `upsert(status_origin=..., status_reason=...)` — additive keyword parameters threaded into INSERT + the existing conflict list; no stderr parameter (NULL-by-omission IS D-04), return value carries the stamp
- Manual `--search-only` publishes record `status_origin='manual'`; signature fallbacks record `'signature'` with the per-signature REASON text verbatim (D-11: remedy prose stays out; recovery stays the generic read-time mapping)
- The manual branch's publish failure now writes a full `failed_hard` row via `record_failure` — the last bare `mark_status` flip in the pipeline is gone
- Pre-pipeline failures (scip version gate, unsupported persisted language override, detection raise) persist `failed_hard` rows with language `'unknown'` when resolution never completed, so `jarvis reindex <slug>` resolves the slug (D-05 complete for this phase's hook list)

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: upsert() origin parameters — registry contract** — `5c9d4fc` (test) + `1cc2dd5` (feat)
2. **Task 2: Wire manual + signature search-only paths** — `2feccca` (test) + `6500cb5` (feat)
3. **Task 3: Pre-pipeline failures create rows** — `0b597f6` (test) + `1ee9518` (feat)

## Files Created/Modified
- `src/jarvis/registry.py` — `upsert()` gains `status_origin`/`status_reason` keyword parameters (INSERT column list, params tuple, constructed return); ON CONFLICT entries from 01-01 unchanged
- `src/jarvis/index_cli.py` — ORIGIN_MANUAL/ORIGIN_SIGNATURE imports; origin stamps on both search-only success upserts; record_failure in the manual-path except branch; pre-pipeline try/except wrap with `resolved_language` sentinel
- `tests/test_registry.py` — 3 origin-parameter contract tests (roundtrip, replace-prior-failure, plain-defaults guard)
- `tests/test_index_cli.py` — 7 persistence tests (manual/signature stamps, publish-failure row, fail-loudly guard, version-gate row, bad-override overwrite, duplicate-gate guard)

## Decisions Made
- Duplicate-gate test follows the gate's real semantics (rejects a *path* already registered under another slug); a stamped failure would create a phantom row for the rejected slug that re-trips the gate on every later attempt
- Bad-override failures record `language='unknown'`: must_haves truth #4 ("'unknown' when detection never completed") governs over the feature behavior's "language reflects the attempt" — the attempt never established a language and D-06 forbids the seeded last-good value lingering
- Reason extraction in both new handlers reuses Plan 01's first-non-empty-line idiom rather than the plan's literal `splitlines()[0]` (D-03: the one-liner must be non-empty; an empty-message exception would otherwise persist an empty reason)
- `_git_head` failures documented as outside the write surface (they precede Registry construction — a malformed request, not a failed run), via the wrap's leading comment

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Duplicate-gate test scenario corrected to the gate's actual rejection case**
- **Found during:** Task 3 (RED authoring)
- **Issue:** The plan's prose scenario — "register slug 'x' for path A, index path B as 'x'" — does not raise in `_reject_duplicate_slug_for_path` (the gate rejects a *path* already registered under a different slug, protecting zoekt.name uniqueness); the first RED run proved it by reaching the version-gate tripwire
- **Fix:** Test re-scenario'd to index a registered path under a new slug (raises "already indexed as 'first'"), asserting the existing row stays byte-identical and no phantom row appears — must_haves truth #5's actual contract
- **Files modified:** tests/test_index_cli.py
- **Verification:** test_duplicate_slug_rejection_writes_no_failure_row passes; gate code itself untouched
- **Committed in:** 0b597f6 (Task 3 test commit)

---

**Total deviations:** 1 auto-fixed (1 bug — test scenario alignment; no production-code behavior changed by it)
**Impact on plan:** None — the production change (gate stays a plain close-and-raise) is exactly what the plan specified.

## Issues Encountered
- One edit-hunk misanchor while authoring Task 3's RED tests briefly truncated one test and dropped another; caught by the immediately-following test run and repaired before any commit. No shipped impact.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Every degradation write is now origin-stamped; Plan 01-03's `getIndexStatus`/nav-tool payloads can read `origin_of()`/`recovery_for()` and report per-origin recovery for real rows (manual/signature/failed_hard all persist now)
- Phase 3's `degraded` origin slots into the same pattern: one new constant + one `upsert(status_origin=...)` call site + one `recovery_for` branch
- Locked semantics verified untouched: `_resolve_search_only`, `_cmd_reindex`, and the "will not repeat the build" note are byte-identical (diff-checked)
- D-11 tension carried forward unchanged: `jarvis reindex <slug>` on a signature row re-publishes search-only today; Phase 3 changes reindex semantics

## Self-Check: PASSED

- All 4 modified files exist on disk
- All 6 task commits present in git log (5c9d4fc, 1cc2dd5, 2feccca, 6500cb5, 0b597f6, 1ee9518)
- TDD gate: every `test(01-02):` commit precedes its `feat(01-02):` commit
- `uv run python -m pytest -m "not integration" -q -rs`: 465 passed, 25 skipped (semantic extra absent), 12 deselected (integration) — CI gate green
- `uv run python -m pytest tests/test_registry.py tests/test_index_cli.py -q`: 174 passed, 1 skipped

---
*Phase: 01-registry-foundation-degradation-reporting*
*Completed: 2026-08-21*
