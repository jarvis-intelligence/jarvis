---
phase: 02-scip-swift-toolchain-update
plan: "02"
subsystem: infra
tags: [scip-swift, version-floor, watchdog-ignore, forget-sweep, derived-data, swift-build-artifacts]

requires:
  - phase: 02-scip-swift-toolchain-update/02-01
    provides: setup.sh auto-roll installs (making a runtime floor necessary) and config.swift_cache_dir(slug) (the path this plan's forget sweep deletes)
provides:
  - Swift-gated runtime scip-swift version floor in jarvis index (MIN_SCIP_SWIFT_VERSION = (0, 3, 0), D-04)
  - jarvis forget sweeps the repo's cache/scip-swift/<slug>/ directory (D-06)
  - watch self-trigger prevention for six Swift build-artifact directory names (D-07/D-08)
affects: [02-03 (CI smoke indexes Swift on a fresh install — the floor must accept it), phase 4 (signatures captured against v0.3.0 stderr), verify-work UAT (live watch session on a Swift repo)]

actuals:
  tokens: 3433    # chars/4 over the realized diff (13,732 chars across 4 files); plan estimated 15,000
  tasks: 3
  commits: 6

tech-stack:
  added: []        # no new libraries — stdlib-only changes over existing modules
  patterns:
    - "Runtime floor mirroring the install floor: setup.sh auto-rolls since 02-01, so the per-index gate is the defense against a stale PATH-shadowing binary"
    - "Version probe isolated as _<tool>_version_output() for monkeypatching, floor check reusing the proven v-optional parser verbatim"
    - "Lifecycle sweep as one ignore_errors=True rmtree beside the existing forget sweeps (lancedb pattern)"
    - "Component-membership ignore-set extension only — no suffix matchers, no wholesale .xcodeproj ignoring"

key-files:
  created: []
  modified:
    - src/jarvis/index_cli.py
    - src/jarvis/watch.py
    - tests/test_index_cli.py
    - tests/test_watch.py

key-decisions:
  - "Floor gate fires as the first statement of index_repo's swift branch — inside the phase-1 pre-pipeline failure wrap, so a too-old binary persists a failed_hard row with the cause via the existing hook (zero new wiring)"
  - "check_scip_swift_version reuses parse_scip_version verbatim (v-optional regex already parses scip-swift's no-v `0.3.0 (swift 6.2.4)` output) with warn-by-omission on unparseable strings — identical policy to check_scip_version"
  - "The one-shot xcshareddata/swiftpm/configuration write xcodebuild makes on first index is accepted and documented, NOT ignored — it is never rewritten, and ignoring it would start down the path of ignoring .xcodeproj internals where legitimate pbxproj triggers live (orchestrator resolution #2)"
  - "Forget sweep uses ignore_errors=True because the cache legitimately may not exist (never-Swift repo or cache never created); the path is built solely from data_dir()/cache/scip-swift/<slug> so it cannot over-delete"

patterns-established:
  - "Sentinel test pattern for language-gated probes: monkeypatch _scip_swift_version_output to raise AssertionError, run a non-Swift index_repo, assert the expected IndexingError — the AssertionError would propagate through the failure wrap's bare `raise` if the gate leaked"
  - "Sibling-slug survival assertion pins rmtree blast radius in forget tests (T-02-08)"

requirements-completed: [SWFT-01, SWFT-03]

coverage:
  - id: D1
    description: "Swift-gated runtime scip-swift version floor: too-old binaries fail loudly naming installed version, 0.3.0 floor, and setup.sh recovery; unparseable output warns by omission; non-Swift repos structurally never invoke the probe"
    requirement: SWFT-01
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py::test_check_scip_swift_version_rejects_v021"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_check_scip_swift_version_accepts_v030_real_format"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_check_scip_swift_version_tolerates_unparseable"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_scip_swift_version_output_missing_binary_names_setup_sh"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_index_repo_non_swift_never_probes_scip_swift_version"
        status: pass
    human_judgment: false
  - id: D2
    description: "jarvis forget removes the repo's cache/scip-swift/<slug>/ directory, spares sibling slugs, and stays non-fatal when the dir is absent"
    requirement: SWFT-03
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py::test_forget_removes_swift_cache_dir_sparing_siblings"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_forget_succeeds_when_swift_cache_dir_absent"
        status: pass
    human_judgment: false
  - id: D3
    description: "watch drops file events under .scip-cache, .build, DerivedData, .index-store, IndexStore, .swiftpm wherever they appear, without ever suppressing Swift-source or project.pbxproj triggers"
    requirement: SWFT-03
    verification:
      - kind: unit
        ref: "tests/test_watch.py::test_swift_artifacts_are_ignored_wherever_they_appear"
        status: pass
      - kind: unit
        ref: "tests/test_watch.py::test_real_swift_layouts_are_ignored"
        status: pass
      - kind: unit
        ref: "tests/test_watch.py::test_swift_ignores_never_suppress_legitimate_triggers"
        status: pass
    human_judgment: true
    rationale: "should_ignore_path's truth table is fully unit-pinned, but a live `jarvis watch` session over a real Swift repo (real watchdog Observer, the accepted one-shot xcshareddata event) is manual-verification territory per the plan's flagged assumption for SWFT-03 — routed to /gsd-verify-work"

duration: 7min
completed: 2026-08-21
status: complete
---

# Phase 02 Plan 02: Runtime Guards & Lifecycle Summary

**Swift-gated scip-swift >= 0.3.0 runtime floor inside jarvis index (reusing the proven parser, warn-by-omission, phase-1 failure wrap), a forget-time sweep of the per-repo scip-swift cache, and watch ignores for six Swift build-artifact directory names — all TDD-pinned with 10 new unit tests.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-08-21T18:33:44Z
- **Completed:** 2026-08-21T18:40:55Z
- **Tasks:** 3 (all TDD: RED -> GREEN)
- **Files modified:** 4

## Accomplishments

- `MIN_SCIP_SWIFT_VERSION = (0, 3, 0)` beside `MIN_SCIP_VERSION`, with `_scip_swift_version_output()` + `check_scip_swift_version()` mirroring the scip pair: FileNotFoundError wrapped into an IndexingError naming setup.sh, `parse_scip_version` reused verbatim (its v-optional regex already parses scip-swift's no-`v` `0.3.0 (swift 6.2.4)` output), warn-by-omission on unparseable strings
- The gate fires as the first statement of `index_repo`'s `language == "swift"` branch — inside the phase-1 pre-pipeline failure wrap, so the raise persists a failed_hard row with the cause through the existing hook (D-04; no new wiring). A sentinel test proves non-Swift repos never invoke the probe
- `jarvis forget` now sweeps `config.swift_cache_dir(slug)` with `ignore_errors=True` right after the lancedb sweep — the forgotten repo's cache dies with it, sibling slugs provably survive, and never-Swift repos still forget cleanly (D-06)
- `watch.py`'s `_IGNORED_PATH_PARTS` gained exactly `.scip-cache`, `.build`, `DerivedData`, `.index-store`, `IndexStore`, `.swiftpm` — a pure set extension; the component-membership matcher is byte-identical. Swift sources and `.xcodeproj/project.pbxproj` edits provably still trigger; substring near-misses (`Builders/`, `build_tools.swift`) provably stay live (D-07/D-08)

## Task Commits

Each task was committed atomically (TDD: test first, then implementation):

1. **Task 1: Swift-gated runtime scip-swift version floor** — `4d459b4` (test) + `857d256` (feat)
2. **Task 2: watch ignores Swift build artifacts wherever they appear** — `8e2ff1f` (test) + `a5733ec` (feat)
3. **Task 3: forget sweeps the scip-swift cache dir** — `a1dae56` (test) + `f4b7a05` (feat)

## Files Created/Modified

- `src/jarvis/index_cli.py` — `MIN_SCIP_SWIFT_VERSION`, `_scip_swift_version_output()`, `check_scip_swift_version()`; the swift-branch call site; the forget sweep line
- `src/jarvis/watch.py` — `_IGNORED_PATH_PARTS` + 6 names with the why-comment (belt-and-suspenders rationale + the accepted one-shot xcshareddata write)
- `tests/test_index_cli.py` — 5 floor-gate tests + 2 forget-sweep tests
- `tests/test_watch.py` — 3 Swift-artifact ignore tests (six-names/any-depth, real layouts, non-ignore guards)

## TDD Gate Compliance

All three tasks followed RED -> GREEN with gate commits in order:

- Task 1: `test(02-02)` `4d459b4` precedes `feat(02-02)` `857d256` — RED run showed 5 failing (AttributeError: no `_scip_swift_version_output`)
- Task 2: `test(02-02)` `8e2ff1f` precedes `feat(02-02)` `a5733ec` — RED run showed 2 failing (the six names not yet ignored); the False-guard test passed already in RED, as designed — it pins over-broad-matching regressions for GREEN
- Task 3: `test(02-02)` `a1dae56` precedes `feat(02-02)` `f4b7a05` — RED run showed 1 failing (cache dir survived forget); the absent-dir test passed already in RED, as designed — it pins the ignore_errors contract the GREEN sweep must keep

## Decisions Made

- Gate placement inside the existing `try:` (swift branch) rather than a new wrap: truth #7 of the plan required the phase-1 failed_hard persistence with zero new wiring — structurally satisfied because the pre-pipeline wrap's bare `raise` re-raises the original after `record_failure`
- Reject-message wording mirrors `check_scip_version`'s shape: installed version, `need >= v0.3.0`, the concrete breakage (mis-dispatched xcodebuild), and the recovery (re-run setup.sh + remove PATH-shadowing binary)
- Kept the existing `test_should_ignore_path_skips_vendor_and_git_dirs` untouched and added sibling tests, per the plan's action alternative; the `build`/`dist` regression pins ride in the non-ignore-guard test

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- One line-anchoring slip during Task 1 GREEN (an edit anchored on pre-renumbering line numbers landed the new functions between `_scip_version_output` and `check_scip_version` with off blank-line spacing); re-read and corrected before any test run or commit — no behavioral effect, no extra commit

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The runtime floor, forget sweep, and watch ignores are in place; 02-03's CI smoke (fresh install + Swift index) exercises the floor's accept path end-to-end on a macOS runner
- A real `jarvis watch` session on a Swift repo remains manual UAT (coverage D3's human_judgment rationale) — the should_ignore_path truth table is unit-exhaustive
- Full unit CI gate green: 495 passed / 25 skipped (pre-existing semantic/lancedb env skips), exactly +10 tests over the 02-01 baseline

## Self-Check: PASSED

All 4 modified files exist on disk; all 6 task commits (4d459b4, 857d256, 8e2ff1f, a5733ec, a1dae56, f4b7a05) found in git log in TDD order; acceptance re-checks green: `MIN_SCIP_SWIFT_VERSION = (0, 3, 0)` at index_cli.py:81, gate call inside the `language == "swift"` branch (index_cli.py:843-847), error message contains installed version + 0.3.0 + setup.sh (test-asserted), non-Swift sentinel test passes, `_IGNORED_PATH_PARTS` holds all six additions with matcher logic unchanged, forget sweep positioned after the lancedb sweep with sibling-survival assertion; plan-level verification green: 144 passed on the two-scope suite, 495 passed / 25 skipped on the full unit gate.

---
*Phase: 02-scip-swift-toolchain-update*
*Completed: 2026-08-21*
