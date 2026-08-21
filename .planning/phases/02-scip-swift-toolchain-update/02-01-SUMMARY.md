---
phase: 02-scip-swift-toolchain-update
plan: "01"
subsystem: infra
tags: [scip-swift, setup-sh, posix-sh, github-releases-api, sha256-digest, version-floor, xcodebuild, cache-dir]

requires: []
provides:
  - setup.sh latest-release resolution for scip-swift with inclusive 0.3.0 floor and API-digest verification (SWFT-01/SWFT-02 install side)
  - SCIP_SWIFT_API_URL test seam (ZOEKT_BASE_URL pattern) for offline install tests
  - version_ge POSIX numeric compare helper and install_tarball_binary_with_digest sibling helper
  - config.swift_cache_dir(slug) path helper (D-05)
  - --cache-dir injection on every Swift indexer invocation, both swiftpm and xcodebuild paths (SWFT-03)
affects: [02-02 (runtime floor + forget sweep reuse swift_cache_dir), 02-03 (CI smoke proves dispatch through the new argv), phase 4 (signatures captured against v0.3.0 stderr)]

actuals:
  tokens: 6685      # chars/4 over the realized diff (26,742 chars across 6 files); plan estimated 22,000
  tasks: 2
  commits: 4

tech-stack:
  added: []          # no new libraries — GitHub REST API asset `digest` field is an external service, not a dependency
  patterns:
    - "Latest-at-install resolution guarded by an inclusive version floor (auto-roll, never presence-skip)"
    - "Digest-verified tarball install as a sibling of the sidecar-verified helper (one checksum source per route)"
    - "Env-var API-URL seam serving file:// JSON fixtures for offline POSIX-sh installer tests"
    - "Untrusted-shell-input shape validation: case pattern + character-complement strip + length check"

key-files:
  created: []
  modified:
    - setup.sh
    - src/jarvis/config.py
    - src/jarvis/index_cli.py
    - tests/test_setup_sh.py
    - tests/test_config.py
    - tests/test_index_cli.py

key-decisions:
  - "Floor is inclusive >= 0.3.0: the dispatch fix landed in 0.3.0 (upstream 9bcf1688), so a hypothetical 0.2.2 cut from the pre-fix branch must stay excluded (Pitfall 5)"
  - "Checksum source is the GitHub API asset digest (immutable, server-computed) — no .sha256 sidecar probing, one code path (orchestrator resolution #1); sidecar route for zoekt/scip left untouched"
  - "Tag shape validation pairs the plan's case pattern with a character-complement strip because a wildcard case pattern alone accepts `v0.3.0; touch pwned` (the trailing * swallows the payload)"
  - "Skip gate compares the installed binary's --version against the RESOLVED tag (installed_scip_matches_pin pattern) so auto-roll upgrades a stale v0.1.2 instead of stranding it"

patterns-established:
  - "version_ge(): numeric 3-field POSIX compare (tr -d 'v' + cut, no sort -V) — reusable for future floors"
  - "SCIP_SWIFT_API_URL seam + _stage_scip_swift_release() fixture builder: GitHub-shaped releases/latest JSON over file:// for installer tests"
  - "swift_cache_dir(slug) delegation shape mirroring lancedb_dir: jarvis computes the path, upstream owns creation"

requirements-completed: [SWFT-01, SWFT-02, SWFT-03]

coverage:
  - id: D1
    description: "scip-swift installer resolves latest at install time with 0.3.0 floor and API-digest verification; tampered/malformed inputs fail closed; stale installs upgrade"
    requirement: SWFT-01
    verification:
      - kind: unit
        ref: "tests/test_setup_sh.py::test_version_ge_compares_numeric_fields_not_strings"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_resolves_and_installs_latest"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_fails_loudly_when_latest_below_floor"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_fails_on_digest_mismatch"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_rejects_tag_with_shell_metacharacters"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_skips_when_installed_version_is_current"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_reinstalls_when_installed_version_outdated"
        status: pass
      - kind: unit
        ref: "tests/test_setup_sh.py::test_install_scip_swift_linux_skips_before_any_fetch"
        status: pass
      - kind: integration
        ref: "live run 2026-08-21: fresh-bin-dir `sh setup.sh --only scip-swift` -> 'installing v0.3.0 (digest-verified)' -> installed; binary reports 0.3.0 (swift 6.2.4)"
        status: pass
  - id: D2
    description: "Per-repo swift cache dir plumbed as --cache-dir on every Swift indexer invocation, proven end-to-end with a real install + real jarvis index of an .xcodeproj repo"
    requirement: SWFT-03
    verification:
      - kind: unit
        ref: "tests/test_config.py::test_swift_cache_dir_under_data_dir"
        status: pass
      - kind: unit
        ref: "tests/test_config.py::test_swift_cache_dir_honors_explicit_root"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_swift_indexer_cmd_appends_cache_dir_without_xcodeproj"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_swift_indexer_cmd_adds_xcodebuild_then_cache_dir"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py::test_swift_indexer_cmd_orders_scheme_before_cache_dir"
        status: pass
      - kind: e2e
        ref: "live run 2026-08-21: jarvis index of upstream XcodeTestProject@v0.3.0 (git-init'd tmp copy) -> status indexed / language swift; cache under JARVIS_DATA_DIR/cache/scip-swift/xcode-test-fixture/ (manifest.json, derived-data, index-db, docs); repo tree free of .scip-cache and .build"
        status: pass

duration: 13min
completed: 2026-08-21
status: complete
---

# Phase 02 Plan 01: scip-swift Latest-Resolution Install + Cache-Dir Plumbing Summary

**setup.sh now resolves the latest scip-swift release from the GitHub API with an inclusive 0.3.0 floor and digest-verified install, and every Swift indexer invocation carries --cache-dir to a per-slug directory under the jarvis data dir — both proven live end-to-end.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-08-21T18:18:13Z
- **Completed:** 2026-08-21T18:31:21Z
- **Tasks:** 2 (both TDD: RED -> GREEN)
- **Files modified:** 6

## Accomplishments

- `install_scip_swift` rewritten: one anonymous `releases/latest` API call serves tag + asset URL + sha256 digest; platform gate stays first (Linux never fetches); below-floor fails loudly ("no good release exists yet"); untrusted fields shape-validated before any shell use; version-aware skip upgrades stale v0.1.2 installs
- `SCIP_SWIFT_VERSION="v0.1.2"` pin and `scip_swift_asset_name()` deleted (anti-pattern: URL now comes from the API)
- `version_ge()` numeric POSIX compare and `install_tarball_binary_with_digest()` added; `install_tarball_binary()` sidecar route untouched (add-alongside per assumption-delta decision)
- `config.swift_cache_dir(slug)` added; `_swift_indexer_cmd` gains `cache_dir` and appends `--cache-dir` on both return paths; call site passes `config.swift_cache_dir(slug)`
- Live end-to-end proof: fresh-dir install of v0.3.0 through the new resolution, then a real `jarvis index` of upstream XcodeTestProject@v0.3.0 → `status: indexed`, `language: swift`, cache entirely under the temp data dir, repo tree clean

## Task Commits

Each task was committed atomically (TDD: test first, then implementation):

1. **Task 1: install_scip_swift latest-resolution + floor + digest** — `1504a3c` (test) + `3aff038` (feat)
2. **Task 2: swift cache dir plumbed as --cache-dir, proven e2e** — `da1ab81` (test) + `af650d2` (feat)

## Files Created/Modified

- `setup.sh` — `SCIP_SWIFT_MIN_VERSION="0.3.0"`; `version_ge()`; `install_tarball_binary_with_digest()`; rewritten `install_scip_swift()`; `SCIP_SWIFT_API_URL` seam; removed pin + asset-name helper
- `src/jarvis/config.py` — `swift_cache_dir(slug, root)` helper (D-05)
- `src/jarvis/index_cli.py` — `_swift_indexer_cmd(…, cache_dir)` with `--cache-dir` on both paths; `index_repo` call site passes `config.swift_cache_dir(slug)`
- `tests/test_setup_sh.py` — resolution/floor/digest/shape/version-aware-skip/no-fetch suite over the seam (8 new tests; 3 pin-era tests deleted, 3 kept unchanged)
- `tests/test_config.py` — `swift_cache_dir` path tests
- `tests/test_index_cli.py` — argv tests rewritten for the 4-param signature with cache-dir ordering assertions

## TDD Gate Compliance

Both tasks followed RED -> GREEN with the gate commits in order:

- Task 1: `test(02-01)` `1504a3c` precedes `feat(02-01)` `3aff038` — RED run showed 6 failing (version_ge undefined; old code installed v0.1.2 over the seam; presence-skip stranded the outdated stub)
- Task 2: `test(02-01)` `da1ab81` precedes `feat(02-01)` `af650d2` — RED run showed 6 failing (missing `swift_cache_dir`, wrong arity)

Note: `test_install_scip_swift_skips_when_installed_version_is_current` passed already in RED — the old presence-gate coincides with version-skip for the current-version case. It is a regression guard for the rewrite, not new behavior; the suite as a whole was RED.

## Decisions Made

- Inclusive `>= 0.3.0` floor (not `> v0.2.1`): the fix landed specifically in 0.3.0, so a hypothetical 0.2.2 from the pre-fix branch must stay excluded (Pitfall 5; D-02's intent preserved strictly better)
- Digest-only checksum route inside scip-swift's path — no sidecar probing, no second code path (orchestrator resolution #1); zoekt/scip keep their sidecar helper untouched
- Tag validation strengthened beyond the plan's "case pattern" wording: the case pattern accepts `v0.3.0; touch pwned` because the trailing `*` swallows the payload, so a `tr -d 'v0123456789.'` complement strip rejects anything left over; digest validated as `sha256:` + exactly 64 lowercase hex (length + complement checks); URL restricted to `https://` (plus `file://` as the documented test seam)
- `--version` probe failures tolerated (`|| true`) and version taken as the first space-delimited token — scip-swift prints `0.3.0 (swift 6.2.4)`, no `v` prefix (Pitfall 6)
- The e2e proof ran `setup.sh` with a minimal PATH and `SHELL=/nonexistent`: the host already carries a scip-swift 0.3.0 (the new skip gate correctly honored it), and `ensure_on_path` must not write a temp bin dir into the real shell rc

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Tag shape validation needed more than a case pattern**
- **Found during:** Task 1 (GREEN, malformed-tag test)
- **Issue:** The plan specified "tag must match the v<digits>.<digits>.<digits> family via a case pattern", but a wildcard case pattern (`v[0-9]*.[0-9]*.[0-9]*`) accepts `v0.3.0; touch pwned` — the trailing `*` swallows the metacharacter payload, so the plan's own malformed-tag test would fail and T-02-01 would not hold
- **Fix:** Paired the case pattern with a character-complement strip (`tr -d 'v0123456789.'` must leave nothing) plus structure via the case pattern; digest validated by prefix case + length check + `*[!0-9a-f]*` complement; documented why in the setup.sh comment
- **Files modified:** setup.sh
- **Verification:** `test_install_scip_swift_rejects_tag_with_shell_metacharacters` passes — non-zero exit, no payload side-effect file, nothing installed
- **Committed in:** `3aff038`

---

**Total deviations:** 1 auto-fixed (1 missing critical / security)
**Impact on plan:** Strengthens the plan's own security test to actually pass; no scope creep — same contract, tighter validation.

## Issues Encountered

- The first e2e attempt installed nothing: the new version-aware skip correctly detected the host's existing scip-swift 0.3.0 on PATH ("0.3.0 already installed (latest is v0.3.0) — skipping"). Working as designed — the fresh-install proof then ran `setup.sh` with a minimal PATH, which exercised the real API + CDN + digest path and confirmed the skip gate honors real version probes
- RED-phase runs of the installer tests made real GitHub downloads (the old code ignored the seam); one-time RED cost, eliminated by GREEN

## End-to-End Evidence (tracer verification, temp dirs cleaned, nothing committed)

- **Live install:** `sh setup.sh --only scip-swift` (fresh JARVIS_BIN_DIR) → `scip-swift: installing v0.3.0 (digest-verified)` / `scip-swift: installed`; installed binary `--version` → `0.3.0 (swift 6.2.4)`
- **Real index:** `jarvis index` on a git-init'd copy of upstream `Fixtures/XcodeTestProject` at tag v0.3.0 → `indexed xcode-test-fixture`; `jarvis status` → `language: swift`, `status: indexed`
- **Cache location:** `<tmp-data>/cache/scip-swift/xcode-test-fixture/` containing `manifest.json`, `derived-data`, `index-db`, `docs`
- **Repo-tree cleanliness:** no `.scip-cache`, no `.build`; `git status --porcelain` empty (the anticipated one-shot `xcshareddata` write did not even materialize)

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `swift_cache_dir` and the `--cache-dir` argv contract are in place for 02-02's forget sweep and runtime floor gate, and for 02-03's CI smoke
- `.github/workflows/setup-smoke.yml:58` comment still references the deleted `SCIP_SWIFT_VERSION` (stale prose only — the step itself calls `setup.sh`, which now resolves latest); 02-03's plan explicitly refreshes that comment block
- Post-push setup-smoke CI proof (the external leg of SWFT-01's flagged assumption A1) lands with 02-03

## Self-Check: PASSED

All key files exist on disk; all 4 task commits (1504a3c, 3aff038, da1ab81, af650d2) found in git log; acceptance re-checks green (SCIP_SWIFT_MIN_VERSION="0.3.0" exactly once, old pin assignment gone, one _swift_indexer_cmd definition, --cache-dir on both return paths); plan-level verification green (485 passed / 25 skipped full unit suite, sh -n setup.sh clean, live install smoke reports 0.3.0).

---
*Phase: 02-scip-swift-toolchain-update*
*Completed: 2026-08-21*
