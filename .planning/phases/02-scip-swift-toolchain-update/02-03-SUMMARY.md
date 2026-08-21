---
phase: 02-scip-swift-toolchain-update
plan: "03"
subsystem: infra
tags: [scip-swift, xcodeproj-fixture, xcodebuild-dispatch, github-actions, setup-smoke, ci-guard, cache-dir-contract]

requires:
  - phase: 02-scip-swift-toolchain-update/02-01
    provides: digest-verified latest-resolution install (the binary the smoke drives), swift_cache_dir + --cache-dir argv (the tree-cleanliness contract the step asserts)
provides:
  - tests/fixtures/mini_xcode_repo/ — git-tracked .xcodeproj fixture adapted from upstream XcodeTestProject@v0.3.0, proven indexable through jarvis's own argv (D-10)
  - setup-smoke macOS-leg post-install `jarvis index` step with tree-cleanliness + cache-location assertions (D-09/SWFT-03 CI half)
affects: [phase 4 (signatures captured against v0.3.0 stderr — the fixture is the manual-verification substrate), verify-work UAT (first real CI run of the new step resolves flagged assumption A1)]

actuals:
  tokens: 3276     # chars/4 over the realized diff (13,104 added chars across 4 files); plan estimated 12,000
  tasks: 2
  commits: 2

tech-stack:
  added: []        # no libraries — fixture data + workflow YAML only (T-02-SC mitigated by construction)
  patterns:
    - "CI-only compatibility proof: fixture is data for CI + manual verification, never a committed @pytest.mark.integration test (D-09)"
    - "if-inside-run platform gate so the non-macOS leg executes the step and proves the skip path (existing install-step guard style)"
    - "Standalone git-init'd fixture copy under RUNNER_TEMP — language detection reads git ls-files, so indexing the in-checkout dir would read jarvis's own git metadata"
    - "Verbatim step-script simulation: extract the run block from parsed YAML and execute it with a faked RUNNER_TEMP to prove the script text locally"

key-files:
  created:
    - tests/fixtures/mini_xcode_repo/scip-swift-test.xcodeproj/project.pbxproj
    - tests/fixtures/mini_xcode_repo/scip-swift-test.xcodeproj/project.xcworkspace/contents.xcworkspacedata
    - tests/fixtures/mini_xcode_repo/scip-swift-test/SwiftFile.swift
  modified:
    - .github/workflows/setup-smoke.yml

key-decisions:
  - "Inner names preserved (scip-swift-test.xcodeproj, scip-swift-test/SwiftFile.swift) — the pbxproj references them throughout; the fixture's identity is its directory mini_xcode_repo, matching the mini_swift_repo/mini_py_repo convention (per plan)"
  - "Fixture bytes copied verbatim (cmp-verified), not hand-edited: upstream v0.3.0's committed fixture is the proven-buildable artifact (Don't-Hand-Roll); zero generator artifacts"
  - "Local validation ran the real pipeline (git-init'd tmp copy → uv run jarvis index → status indexed) as verification only — nothing committed as a test (D-09)"
  - "git commit in CI uses inline -c user.email/-c user.name: GitHub runners ship no git identity and the standalone fixture repo needs a commit for zoekt/detection"
  - "Beyond the plan's structural inspection, the step's run block was extracted from the parsed YAML and executed verbatim with a faked RUNNER_TEMP + real binaries — proving script text, assertions, and slug wiring before CI ever runs it"

patterns-established:
  - "Step-script verbatim simulation: yaml.safe_load → step['run'] → bash -e with faked RUNNER_* env — reusable for future workflow steps with local-runnable bodies"

requirements-completed: [SWFT-01, SWFT-03]

coverage:
  - id: D1
    description: "mini_xcode_repo fixture (3 files byte-identical to upstream XcodeTestProject@v0.3.0) that genuinely exercises _prefers_xcodebuild → --build-tool xcodebuild, proven indexable through jarvis locally"
    requirement: SWFT-01
    verification:
      - kind: e2e
        ref: "live run 2026-08-21: git-init'd tmp copy → `uv run jarvis index --slug mini-xcode-repo-local` → exit 0; `jarvis status` → language: swift, status: indexed (full navigation data, proving real xcodebuild dispatch); cache under JARVIS_DATA_DIR/cache/scip-swift/mini-xcode-repo-local/ (manifest.json, derived-data, index-db, docs); tree clean (no .scip-cache, no .build, git porcelain empty); temp dirs removed"
        status: pass
      - kind: unit
        ref: "structural: git ls-files tests/fixtures/mini_xcode_repo | wc -l == 3; cmp byte-identical vs upstream clone at v0.3.0; find Package.swift == 0; grep CODE_SIGN|PROVISIONING == 0"
        status: pass
    human_judgment: false
  - id: D2
    description: "setup-smoke macOS leg runs a post-install real `jarvis index` against the fixture with tree-cleanliness assertions (no in-tree .scip-cache/.build; cache root under JARVIS_DATA_DIR), Linux legs skip via the in-run gate; stale SCIP_SWIFT_VERSION comment refreshed; path filters extended"
    requirement: SWFT-03
    verification:
      - kind: unit
        ref: "plan automated gate: grep -c mini_xcode_repo == 10, grep -c JARVIS_DATA_DIR == 4, ! grep -q SCIP_SWIFT_VERSION (all pass)"
        status: pass
      - kind: integration
        ref: "verbatim step-script simulation 2026-08-21: run block extracted from parsed YAML, executed with faked RUNNER_TEMP + real ci-bin layout → scip-swift 0.3.0 echoed, 'indexed mini-xcode-repo', all three tree-cleanliness assertions pass, git-clean tree, exit 0"
        status: pass
    human_judgment: true
    rationale: "The authoritative proof is the macOS CI leg itself (flagged assumption A1: macos-latest = arm64 with full Xcode is not exercisable locally). The first push or workflow_dispatch of this workflow is the resolving evidence; local structural + verbatim simulation cover everything else."

duration: 9min
completed: 2026-08-21
status: complete
---

# Phase 02 Plan 03: CI Smoke Fixture & Workflow Summary

**mini_xcode_repo .xcodeproj fixture adapted byte-identically from upstream XcodeTestProject@v0.3.0 (proven swift/indexed through a real local `jarvis index`), plus the setup-smoke macOS-leg post-install index step asserting xcodebuild dispatch and the out-of-tree cache contract**

## Performance

- **Duration:** 9 min
- **Started:** 2026-08-21T18:44:04Z
- **Completed:** 2026-08-21T18:53:00Z
- **Tasks:** 2
- **Files modified:** 4 (3 created fixture files + 1 workflow)

## Accomplishments

- `tests/fixtures/mini_xcode_repo/` landed with exactly three files copied verbatim from upstream `Fixtures/XcodeTestProject` at tag v0.3.0 (`cmp`-verified byte-identical): `scip-swift-test.xcodeproj/project.pbxproj` (9,095 B, single `com.apple.product-type.tool` target, no signing/provisioning, no Package.swift), `project.xcworkspace/contents.xcworkspacedata` (135 B), `scip-swift-test/SwiftFile.swift` (517 B) — inner names preserved so the pbxproj stays untouched
- Local validation (verification only, per D-09): git-init'd tmp copy → `uv run jarvis index --slug mini-xcode-repo-local` with isolated `JARVIS_DATA_DIR` → exit 0, `language: swift`, `status: indexed` (full navigation data — `_prefers_xcodebuild` fired and xcodebuild genuinely dispatched, the v0.2.x regression path), cache entirely under the temp data dir, repo tree clean; temp dirs removed afterwards
- `.github/workflows/setup-smoke.yml` gained the "Index the Swift fixture through jarvis (macOS only)" step after the unit tests (setup-uv already present): git-init'd fixture copy under `RUNNER_TEMP`, `ci-bin` prepended to `PATH`, `JARVIS_DATA_DIR` under `RUNNER_TEMP`, `scip-swift --version` echoed before indexing, `uv run jarvis index --slug mini-xcode-repo`, then three assertions — no `.scip-cache`, no `.build` in the indexed tree (one-shot `xcshareddata` write accepted and not asserted against, per resolution #2), cache root exists at `JARVIS_DATA_DIR/cache/scip-swift/mini-xcode-repo`
- The `RUNNER_OS = macOS` gate lives inside the `run` block (existing install-step guard style) so the Linux leg executes the step and proves the skip branch; the stale `SCIP_SWIFT_VERSION` comment (variable deleted by 02-01) now describes the latest-resolution + floor + digest guard; both push and PR path filters extended with `tests/fixtures/mini_xcode_repo/**` and `src/jarvis/**`

## Task Commits

Each task was committed atomically:

1. **Task 1: mini_xcode_repo fixture adapted from upstream XcodeTestProject@v0.3.0** — `d052728` (chore)
2. **Task 2: setup-smoke.yml post-install jarvis index step on the macOS leg** — `b342fea` (chore)

## Files Created/Modified

- `tests/fixtures/mini_xcode_repo/scip-swift-test.xcodeproj/project.pbxproj` — the minimal tool project that triggers `_prefers_xcodebuild()` → `--build-tool xcodebuild` dispatch
- `tests/fixtures/mini_xcode_repo/scip-swift-test.xcodeproj/project.xcworkspace/contents.xcworkspacedata` — standard self-referencing workspace (pbxproj requires it to resolve)
- `tests/fixtures/mini_xcode_repo/scip-swift-test/SwiftFile.swift` — Animal/Dog class hierarchy with `@main` (navigation-data-bearing source)
- `.github/workflows/setup-smoke.yml` — new post-install index step (48 insertions), refreshed install-step comment, extended path filters

## Decisions Made

- Kept upstream inner names rather than renaming (plan-directed): the pbxproj references `scip-swift-test` throughout; hand-editing it would violate the "byte-adapted, not authored fresh" prohibition. The fixture's identity is its directory name (`mini_xcode_repo`), matching the sibling convention
- Inline `-c user.email=ci@jarvis.local -c user.name=ci` for the fixture-repo commit in CI — runners have no git identity configured, and zoekt/language detection require the copy to be a real committed git repo
- Extra local verification beyond the plan's "structural inspection": the step's `run` block was extracted from the parsed YAML and executed verbatim (`bash -e`, faked `RUNNER_TEMP`/`RUNNER_OS`, real binaries symlinked into a fake `ci-bin`) — it ran the full pipeline in 15s and passed every embedded assertion, so the exact script text CI will execute is proven locally; the only thing left for the runner to prove is the runner environment itself (A1)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Two shell-pipeline aborts in this session's tooling (a `grep -v '^??'` status filter and a `grep '^+'` char count both exited 1 with no output in this harness's shell); both were verification-only conveniences, re-run via alternate tools (`awk`, direct `git status`) — no repository impact, no behavioral effect

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 02 is now fully executed (3/3 plans). The remaining external evidence is the first real CI run of the new smoke step (push or `workflow_dispatch` triggers it immediately) — that run resolves flagged assumption A1 and closes SWFT-01's CI verification half
- If the macOS leg fails on runner tooling rather than jarvis code, the documented fix path is a `macos-15` pin or an Xcode setup action (T-02-11, accepted-and-loud)
- Phase 4 (Swift failure signatures) can use the fixture as its manual-verification substrate; signatures are captured against v0.3.0 stderr as planned
- Full unit gate stays green: 495 passed / 25 skipped / 12 deselected — unchanged from the 02-02 baseline, as expected for a data+CI-only plan

## Self-Check: PASSED

All 4 key files exist on disk; both task commits (d052728, b342fea) found in git log on `gsd/v1.0-milestone`; acceptance re-checks green: `git ls-files tests/fixtures/mini_xcode_repo` returns exactly 3 paths, no Package.swift / no CODE_SIGN|PROVISIONING in the fixture, all three files `cmp`-identical to the upstream v0.3.0 clone, workflow gate (10× mini_xcode_repo, 4× JARVIS_DATA_DIR, 0× SCIP_SWIFT_VERSION) passes, YAML parses with the new step after the unit tests, `bash -n` clean on the extracted run block, both path filters carry the two new globs, zero test files touched by the plan (`git diff --name-only eb11976..HEAD` shows only the workflow + 3 fixture files); plan-level verification green: 495 passed / 25 skipped / 12 deselected on `uv run python -m pytest -m "not integration" -q -rs`.

---
*Phase: 02-scip-swift-toolchain-update*
*Completed: 2026-08-21*
