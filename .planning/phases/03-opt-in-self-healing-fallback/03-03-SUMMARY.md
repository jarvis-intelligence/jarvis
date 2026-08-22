---
phase: 03-opt-in-self-healing-fallback
plan: "03"
subsystem: cli
tags: [watch, anti-treadmill, sha-keyed-skip, tri-state-flag, argparse, pytest]

# Dependency graph
requires:
  - phase: 03-opt-in-self-healing-fallback
    provides: DEGRADED_STATUS carrier, degraded terminal write persisting the attempt sha in commit_sha, index_repo(fallback_search_only=...) tri-state param, index_parser --fallback-search-only pair
provides:
  - "_watch_should_retry_full_build(entry, current_sha) — pure FALL-05 skip predicate (skip only for degraded + unchanged non-NULL sha)"
  - "_watch_skip_check(repo_path, slug, root=None) — short-lived read-only Registry consult, fail-open to retry (T-3-06/T-3-07)"
  - "watch driver wiring — _cmd_watch._reindex consults the skip (one stderr note) and forwards fallback_search_only beside scheme/language"
  - "watch_parser --fallback-search-only/--no-fallback-search-only (BooleanOptionalAction, default None) — watch is just another reindex driver"
affects: [phase-close UAT, milestone audit]

actuals:
  tokens: 3946  # 15784 diff chars / 4 (plan estimate: 45000)
  tasks: 2
  commits: 4

tech-stack:
  added: []  # stdlib argparse + fake-sys.modules test harness only
  patterns:
    - "Watch-driver skip policy: the sha-keyed decline lives in the driver (_reindex), never in index_repo — FALL-03's explicit jarvis index always retries"
    - "CI-safe watch testing: fake watchdog modules installed into sys.modules + a scripted time.sleep drive the real _reindex closure without the `watch` extra (CI installs only `semantic`)"

key-files:
  created: []
  modified:
    - src/jarvis/index_cli.py
    - tests/test_index_cli.py

key-decisions:
  - "The predicate is the exact plan body — skip iff entry is degraded AND commit_sha is non-NULL AND equals the current sha; every other shape retries, so record_failure's NULL-sha rows can never skip (Pitfall 7)"
  - "_watch_skip_check wraps the ENTIRE consult (Registry open, get, _git_head) in one fail-open try/except — a broken consult costs one extra build, never a suppressed retry"
  - "Wiring tests drive _cmd_watch through a fake Observer in sys.modules instead of source inspection — proves the skip note, the non-invocation, and the kwarg pass-through behaviorally while keeping tests watchdog-free"
  - "Scripted time.sleep raises exception instances to break the watch loop (KeyboardInterrupt is _cmd_watch's own exit path; the skip path returns normally, so sleep #2 is its only deterministic exit)"

patterns-established:
  - "Anti-treadmill consult shape: pure decision predicate + fail-open I/O wrapper + driver-side consult — reusable for any future auto-reindex driver (e.g. an MCP-triggered watcher)"

requirements-completed: [FALL-05, FALL-02]

coverage:
  - id: D1
    description: "FALL-05 skip predicate: 7-row matrix — only degraded + same non-NULL commit_sha declines; missing row, failed (even matching sha), indexed, search-only, degraded+NULL sha, degraded+changed sha all retry"
    requirement: FALL-05
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_watch_should_retry_full_build_matrix
        status: pass
    human_judgment: false
  - id: D2
    description: "FALL-02 watch surface: watch_parser accepts the same tri-state --fallback-search-only/--no- pair (absent → None); jarvis reindex keeps honoring the persisted value with no new flag"
    requirement: FALL-02
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_watch_parser_accepts_tri_state_fallback_flag
        status: pass
    human_judgment: false
  - id: D3
    description: "FALL-05 consult against a real tmp Registry + git repo: degraded row at current HEAD skips; a new commit (sha change) re-triggers the full build (ROADMAP criterion 5); a missing row retries; any consult failure fails open to retry"
    requirement: FALL-05
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_watch_skip_check_skips_only_while_sha_unchanged
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_watch_skip_check_fails_open_when_the_consult_raises
        status: pass
    human_judgment: false
  - id: D4
    description: "Watch driver end-to-end: _reindex forwards fallback_search_only to index_repo (tri-state pass-through beside scheme/language) and, on a degraded row at the same sha, prints one stderr note and returns WITHOUT invoking index_repo"
    requirement: FALL-05
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_cmd_watch_skips_full_build_retry_on_degraded_row_at_same_sha
        status: pass
    human_judgment: false

# Metrics
duration: 9min
completed: 2026-08-22
status: complete
---

# Phase 3 Plan 03: Watch Anti-Treadmill Summary

**The watch treadmill is stopped: a persistently-failing degraded repo at an unchanged sha gets one stderr note instead of a doomed multi-minute build per save, while any source change — or an explicit `jarvis index` — re-triggers the full build; watch also accepts and forwards the tri-state fallback flag.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-08-22T17:39:45Z
- **Completed:** 2026-08-22T17:48:30Z
- **Tasks:** 2 (both tdd; 4 RED/GREEN commits)
- **Files modified:** 2

## Accomplishments
- FALL-05 predicate: `_watch_should_retry_full_build` — pure, watchdog-free, module-level beside `_git_head`; the only skip is degraded + non-NULL `commit_sha` equal to the current sha, pinned by the full 7-row matrix (record_failure's NULL-sha rows can never skip)
- FALL-05 consult: `_watch_skip_check` opens a short-lived read-only Registry, reads the row + `_git_head`, and wraps everything in one fail-open try/except (T-3-06 read-only parameterized SELECT, T-3-07 fail-open to retry)
- FALL-05 wiring: `_cmd_watch._reindex` consults the skip before `index_repo` — skip prints exactly one stderr note ("still degraded at the same commit — skipping full-build retry") and returns without invoking; a sha change re-triggers (ROADMAP criterion 5); `jarvis index`/`reindex` never consult it (FALL-03 preserved — the helpers are called from `_cmd_watch` only)
- FALL-02 watch surface: `watch_parser` gains the identical tri-state `--fallback-search-only`/`--no-fallback-search-only` pair; `_reindex` forwards `fallback_search_only=getattr(args, ...)` beside `scheme`/`language`

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Pure sha-skip predicate `_watch_should_retry_full_build`** — `72dea79` (test) + `41c78c5` (feat)
2. **Task 2: Watch wiring — skip consult, flag pass-through, parser** — `b47e753` (test) + `f192e26` (feat)

## Files Created/Modified
- `src/jarvis/index_cli.py` — `RegisteredRepo` import, `_watch_should_retry_full_build` (pure predicate), `_watch_skip_check` (fail-open consult), `_cmd_watch._reindex` skip consult + `fallback_search_only` pass-through, `watch_parser` tri-state flag
- `tests/test_index_cli.py` — 7-row skip matrix, watch parser tri-state, real-Registry consult (skip/sha-change/missing-row) + fail-open, `_cmd_watch` behavioral harness (fake watchdog sys.modules entries + scripted `time.sleep`) proving pass-through, skip note, and non-invocation (12 tests + `_watch_entry`/`_FakeObserver`/`_drive_cmd_watch` helpers)

## Decisions Made
- Kept the predicate body exactly as planned (single `return not (...)`) — no cleverness; the docstring carries the FALL-05 policy and the FALL-03 boundary (the skip must never move into `index_repo`)
- Drove `_cmd_watch` behaviorally instead of source-inspecting: a fake `Observer` in `sys.modules` delivering one synthetic event + `debounce=0.0` fires the real `_reindex` closure on the first `poll()`; the fake `index_repo` raises `KeyboardInterrupt` (a BaseException `_reindex`'s broad `except Exception` must not swallow) to exit the loop deterministically
- The skip-path test terminates via the scripted `time.sleep` (iteration 2 raises `KeyboardInterrupt`) because the skip path returns normally — sleep is its only deterministic exit

## Deviations from Plan

None - plan executed exactly as written.

## TDD Gate Compliance

Both tasks (tdd="true") produced a `test(03-03):` RED commit before their `feat(03-03):` GREEN commit. Task 1 RED failed on the predicted ImportError (7/7 rows); Task 2 RED failed on the predicted gaps — SystemExit 2 on the unknown watch flag, ImportError `_watch_skip_check` (×2), `'UNSET' is not True` (pass-through absent), and `the skip path must not invoke index_repo` (no skip logic yet). GREEN runs: 7 passed, then 13 passed (`-k watch`).

## Issues Encountered
- One test-harness bug surfaced only at GREEN: the scripted-sleep lambda `next(sleep_results)` *returned* the `KeyboardInterrupt()` instance instead of raising it (StopIteration on exhaustion) — the skip path worked but the loop never exited; fixed by making `_sleep` raise `BaseException` instances (committed within the Task 2 GREEN commit, which names it)
- One edit-tool boundary mistake while inserting the `_reindex` wiring (line anchors had shifted after the helper insert) briefly clobbered `_cmd_watch`'s docstring/import block; caught by the edit tool's parse warning, repaired by re-read before any test run or commit
- Plan's read_first line ranges for index_cli.py pointed at the pre-03-02 snapshot (e.g. `_cmd_watch` at 1186-1253 vs actual 1279+); located by grep, no impact — same as 03-02

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 3 (FALL-01..05) is fully shipped across 03-01 (core degrade branch), 03-02 (reporting surfaces), and 03-03 (watch anti-treadmill) — the phase is ready for code review + verify-work UAT
- The `jarvis watch` foreground flow with the real watchdog Observer remains manual-acceptance-only (as before — the unit harness covers the closure, not the observer thread)

## Self-Check: PASSED

- Both modified files exist on disk (`src/jarvis/index_cli.py`, `tests/test_index_cli.py`)
- All 4 task commits present in git log (72dea79, 41c78c5, b47e753, f192e26)
- `uv run python -m pytest tests/test_index_cli.py -k "watch_should_retry" -q`: 7 passed (Task 1 verify)
- `uv run python -m pytest tests/test_index_cli.py -k "watch" -q`: 13 passed (Task 2 verify + plan verification)
- `uv run python -m pytest -m "not integration" -rs -q`: 628 passed, 17 deselected — CI gate green (616 → 628: +12 new tests)
- No test file imports watchdog (grep: only a docstring mention); `_watch_skip_check` is consulted from `_cmd_watch` only — `jarvis index`/`reindex` never skip (FALL-03)
- reindex_parser gained no new flag (persisted fallback value still honored with no flag — FALL-02)

---
*Phase: 03-opt-in-self-healing-fallback*
*Completed: 2026-08-22*
