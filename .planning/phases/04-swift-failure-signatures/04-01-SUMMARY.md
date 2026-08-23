---
phase: 04-swift-failure-signatures
plan: 01
subsystem: infra
tags: [scip-swift, swift, error-signatures, search-only, pytest, tdd]

requires:
  - phase: 02-scip-swift-toolchain-update
    provides: pinned scip-swift 0.3.0 binary (the capture instrument) and the check_scip_swift_version pre-pipeline gate the tests monkeypatch
  - phase: 01-registry-foundation-degradation-reporting
    provides: _SEARCH_ONLY_SIGNATURES mechanism, _search_only_reason matcher, signature consult branch, ORIGIN_SIGNATURE registry stamping
provides:
  - Two verified scip-swift failure signatures in _SEARCH_ONLY_SIGNATURES (no-build-system, no-IndexStore) with capture-provenance comment
  - Five pinning/negative unit tests embedding the exact captured stderr (binary-free, CI-safe)
affects: [05-semantic-install-onboarding, verify-work UAT, scip-swift version bumps (re-capture duty)]

actuals:
  tokens: 2800   # chars/4 over the realized diff (11,277 added chars across 2 files)
  tasks: 3
  commits: 5

tech-stack:
  added: []       # data-only phase — no new libraries, pure stdlib substring matching
  patterns:
    - "Verbatim-stderr signature pinning: tests embed the exact captured error line with a provenance comment (binary version, sha256, trigger shape, capture date) and look the reason up by exact token tuple, so token or reason drift fails loudly"

key-files:
  created: []
  modified:
    - src/jarvis/index_cli.py
    - tests/test_index_cli.py

key-decisions:
  - "Swift entry 2 uses a single token (\"Build succeeded but no IndexStore was produced\",) per the AGP single-token precedent — the headline is unique across all nine capture shapes and a second token from the explanatory parenthetical would couple the signature to rewordable prose"
  - "Tripwire reason-lookup placed AFTER index_repo() in both pinning tests so the RED failure is the IndexingError itself (the plan's <behavior> contract); the research skeleton's before-placement would have failed RED with StopIteration instead"
  - "Tokens are path-free by design — both scip-swift error lines interpolate repo/cache paths; documented in the constant's comment block with the re-capture-on-bump / drift-fails-hard rule"

patterns-established:
  - "Append-only signature growth: new failure classes join _SEARCH_ONLY_SIGNATURES as (tokens, cause-only reason) entries with a provenance comment; matcher, consult branch, and existing entries stay byte-identical"

requirements-completed: [SWFT-04]

coverage:
  - id: D1
    description: "no-build-system Swift failure degrades to search-only automatically with origin 'signature' and the matched reason persisted verbatim"
    requirement: SWFT-04
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py::test_swift_no_build_system_signature_degrades_search_only"
        status: pass
    human_judgment: false
  - id: D2
    description: "no-IndexStore Swift failure (empty-Sources xcodeproj; also covers swiftpm backend wording) degrades identically"
    requirement: SWFT-04
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py::test_swift_no_index_store_signature_degrades_search_only"
        status: pass
    human_judgment: false
  - id: D3
    description: "unmatched Swift failures keep failing hard — generic build-failure wrappers (all five keep-hard lines), empty/whitespace/stdout-only carriers, and first-match ordering all pinned to never degrade"
    requirement: SWFT-04
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py::test_swift_generic_build_failure_wrapper_never_matches"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_empty_or_stdout_only_failure_carriers_never_match"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_search_only_reason_first_listed_match_wins"
        status: pass
    human_judgment: false

duration: 9min
completed: 2026-08-23
status: complete
---

# Phase 4 Plan 01: Swift Failure Signatures Summary

**Two scip-swift 0.3.0 failure signatures (no-build-system, no-IndexStore) joined `_SEARCH_ONLY_SIGNATURES` with verbatim-captured pinning tests — Swift known-unfixables now degrade to search-only automatically with origin 'signature', while every unmatched failure keeps failing hard.**

## Performance

- **Duration:** 9 min (01:56:50Z → 02:05:15Z)
- **Started:** 2026-08-23T01:56:50Z
- **Completed:** 2026-08-23T02:05:15Z
- **Tasks:** 3
- **Files modified:** 2 (+244 lines, append-only)

## TDD Gate Record

| Gate | Task | Commit | Recorded run |
|---|---|---|---|
| RED 1 | Task 1 (no-build-system) | `ef070ea` test(04-01) | FAILED — `IndexingError` propagated out of `index_repo` (no signature matched, degrade gate fell through to re-raise at index_cli.py:1245); no search-only publish |
| GREEN 1 | Task 1 | `550cd3c` feat(04-01) | 1 passed; quick suite **187** passed (186 baseline + 1), 12 deselected |
| RED 2 | Task 2 (no-IndexStore) | `8ab9778` test(04-01) | FAILED — `IndexingError` propagated (same branch) |
| GREEN 2 | Task 2 | `f6e72b8` feat(04-01) | 1 passed; quick suite **188** passed, 12 deselected |
| — | Task 3 (negative pins) | `79f8af5` test(04-01) | 3 passed; quick suite **191** passed (186 + 5), 12 deselected |

Both RED runs failed for the *designed* reason (IndexingError propagating out of the signature branch), not an import/collection error. RED commits precede their GREEN commits in history.

## Accomplishments

- `_SEARCH_ONLY_SIGNATURES` gained two entries quoted verbatim from the pinned scip-swift 0.3.0 stderr (captured 2026-08-23): `("Could not detect a build system", "no Package.swift and no .xcodeproj/.xcworkspace found")` and `("Build succeeded but no IndexStore was produced",)`, each with a cause-only reason (D-11: no remedy prose)
- Capture-provenance comment block in the constant documents binary version, path-free-token rule, and the re-capture-on-bump / drift-fails-hard policy
- Five new binary-free unit tests: two end-to-end pinning tests (mocked `_run`, real registry-row assertions on all four fields), plus three pure `_search_only_reason` negative pins (wrapper negatives, empty/stdout-only carriers, first-match list order)
- Production diff is strictly append-only: matcher, signature consult branch, and the three Kotlin/AGP entries are byte-identical (verified via `git diff`)

## Task Commits

1. **Task 1: Signature-1 tracer — no-build-system carrier degrades search-only end-to-end** — RED `ef070ea` + GREEN `550cd3c`
2. **Task 2: Signature-2 — no-IndexStore carrier degrades search-only** — RED `8ab9778` + GREEN `f6e72b8`
3. **Task 3: SC3 negative pins — wrappers, empty carriers, first-match order** — `79f8af5`

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `src/jarvis/index_cli.py` — +17 lines: provenance comment + two Swift entries in `_SEARCH_ONLY_SIGNATURES` (lines 123-139); nothing else touched
- `tests/test_index_cli.py` — +227 lines: five test functions grouped directly after `test_signature_fallback_stamps_signature_origin_and_matched_reason`

## Verification Results

1. **Quick suite:** `uv run pytest tests/test_index_cli.py -m "not integration" -q` → **191 passed, 12 deselected** (plan target exactly met)
2. **Full CI gate:** `uv run pytest -m "not integration" -rs` → **638 passed, 17 deselected, zero failures** (633 baseline + 5 new)
3. **Regression set:** `test_signature_fallback_stamps_signature_origin_and_matched_reason` (Kotlin pin), `test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo` (adjacency probe), all `check_scip_swift_version` tests — all pass
4. **Live local smoke** (not committed; scratch `JARVIS_DATA_DIR=/tmp/jarvis-p4-smoke/data`, shapes reused from `/tmp/jarvis-p4-capture/repos/`):

| Shape | Result |
|---|---|
| `swift-nobuildsystem` → `jarvis index --slug p4-smoke-nobuild` | exit 0; stderr `note: p4-smoke-nobuild cannot be SCIP-indexed — the repo has Swift sources but neither a Package.swift nor an Xcode project…`; `jarvis status` → status search-only, origin **signature**, cause = matched reason verbatim, recovery `jarvis reindex p4-smoke-nobuild` |
| `xcode-emptysources` → `p4-smoke-emptysources` | exit 0; degrade note with the no-index-store reason; status → search-only / signature / cause verbatim / recovery present |
| `spm-broken-manifest` (keep-hard) → `p4-smoke-brokenmanifest` | **exit 1**; status failed, origin **failed_hard**, cause carries the real `Error: 'swift build' failed with exit code 1` carrier; no scip index dir published — SC3 proven live |

## Decisions Made

- Tripwire lookup placed after `index_repo()` in the pinning tests (see key-decisions frontmatter) — honors the plan's `<behavior>` RED contract over the research skeleton's illustrative placement
- Task 1's tracer feedback gate ran in auto mode (`workflow.auto_advance: true`): tracer `<verify>` re-run end-to-end (pinning test + quick suite green) before expansion tasks started

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 4 (single plan) is complete; SWFT-04 closed — all four Swift-toolchain requirements shipped
- Phase 5 (Semantic Install Onboarding, SEMA-01..02) is the remaining phase; no blockers from this plan
- Standing duty recorded in the constant: a scip-swift version bump that rewords either error line makes these signatures miss → hard failure by design → re-capture and update tokens/tests

## Self-Check: PASSED

- Files: `src/jarvis/index_cli.py`, `tests/test_index_cli.py` — both modified on disk (committed)
- Commits `ef070ea`, `550cd3c`, `8ab9778`, `f6e72b8`, `79f8af5` all present on `gsd/v1.0-milestone`
- All task acceptance criteria re-verified: source entries exact (4th + 5th, Kotlin/AGP unchanged), tests assert all four registry fields, TDD gate order correct, suite counts 187/188/191 at each checkpoint

---
*Phase: 04-swift-failure-signatures*
*Completed: 2026-08-23*
