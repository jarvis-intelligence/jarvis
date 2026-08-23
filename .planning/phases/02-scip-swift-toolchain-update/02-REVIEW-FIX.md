---
phase: 02-scip-swift-toolchain-update
fixed_at: 2026-08-21T19:19:18Z
review_path: .planning/phases/02-scip-swift-toolchain-update/02-REVIEW.md
iteration: 1
findings_in_scope: 3
fixed: 3
skipped: 0
status: all_fixed
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-08-22
**Source review:** `.planning/phases/02-scip-swift-toolchain-update/02-REVIEW.md`
**Iteration:** 1
**Fix scope:** Critical + Warning (per assignment; IN-01/IN-02 out of scope)

**Summary:**
- Findings in scope: 3 (CR-01, WR-01, WR-02)
- Fixed: 3
- Skipped: 0

**Verification location:** all gates ran in the MAIN CHECKOUT (`workflow.use_worktrees=false` in `.planning/config.json` — no worktree was created, per the documented opt-out). Numbers are reproducible from this tree directly.

## Fixed Issues

### CR-01: Swift floor gate breaks `--search-only` indexing on hosts without scip-swift (regression)

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** `bf4e200`
**Status:** fixed: requires human verification (logic/placement fix — semantics verified by sentinel test, not by a live Linux run)
**Applied fix:** The gate at `index_cli.py` is now `if language == "swift" and not search_only:` — `search_only` is resolved earlier via `_resolve_search_only`, so the guard covers both a fresh `--search-only` run and a reindex of a persisted search-only Swift repo. The floor value and non-search-only behavior are untouched. Sentinel test `test_index_repo_search_only_swift_never_probes_scip_swift_version` mirrors the non-Swift sentinel (real Swift repo via `Package.swift`+`App.swift`, `_scip_swift_version_output` monkeypatched to raise `AssertionError`): verified to FAIL on the pre-fix source (probe fires, AssertionError propagates) and PASS after (clean zoekt publish, `search-only` status, no `current` pointer).

### WR-01: Asset selection takes the first `digest` and the first `.tar.gz` URL independently — no platform correlation

**Files modified:** `setup.sh`, `tests/test_setup_sh.py`
**Commit:** `3b301fb`
**Status:** fixed: requires human verification (selection logic fix; real-release behavior grounded against the live API, not a live fresh install)
**Applied fix — adapted from the review, deliberately:** the review's literal `*macos-arm64*`-in-URL check was NOT applied. Empirical check against the live `releases/latest` JSON during the fix: today's asset is named `scip-swift-0.3.0.tar.gz` — the platform component was dropped from the asset name at v0.2.0 (pre-v0.2.0 was `scip-swift-<ver>-macos-arm64.tar.gz`). The suggested check would therefore fail every real fresh install. Instead, extraction is now anchored to the asset NAME: `grep -oE '"name": *"scip-swift-[0-9][0-9.]*(-macos-arm64)?\.tar\.gz"'` accepts exactly the two real naming shapes (first match in file order), a release with no matching asset fails loudly at metadata time (`no macOS arm64 .tar.gz asset`), and digest+URL are then read from that single asset's JSON object (awk block keyed on the `"name":` line — release-title `name` and sibling assets excluded), so the pair can never come from different assets. `sh -n` and `dash -n` both clean. Tests: `_stage_scip_swift_release` gained a `decoy_assets` seam (real tarballs with their own correct digests, listed first); three sentinel tests added — linux-asset-listed-first (macOS stub installed, decoy never), linux-only release (loud refusal), non-`.tar.gz` asset first (no cross-asset digest/URL mixing). All three verified to FAIL on pre-fix `setup.sh` (the decoy linux binary gets installed — the review's exact scenario) and PASS after.

### WR-02: `version_ge` treats comparison errors as equality — garbage `--version` token counts as "current" and strands the auto-roll upgrade

**Files modified:** `setup.sh`, `tests/test_setup_sh.py`
**Commit:** `98b5cf3`
**Status:** fixed: requires human verification (logic fix — semantics pinned by regression tests, not a live upgrade run)
**Applied fix — adapted twice from the review:** (1) The accepted shape is `[0-9]*.[0-9]*.[0-9]*` OR `v[0-9]*.[0-9]*.[0-9]*`, not the suggested `v`-mandatory pattern: scip-swift prints `0.3.0 (swift 6.2.4)` with NO `v` prefix (pinned by `test_check_scip_swift_version_accepts_v030_real_format`), and the `v`-mandatory pattern rejects it — the existing `test_install_scip_swift_skips_when_installed_version_is_current` caught this immediately, forcing a reinstall on every real run. (2) The suggested `[ -n "$_stray" ] && { … }` is written in `if`-form, per setup.sh's own documented set -e convention (line ~1000: "`if` form rather than `&&`: unambiguous exit-status semantics under `set -e`"). Behavior: an unparseable installed-version token (garbage first token, warning line, `0.3.0-rc1` suffix) logs `installed version unparseable (…) — reinstalling`, clears the token, and proceeds to install — breaking the setup-skip/runtime-floor deadlock. Sentinel tests `test_install_scip_swift_reinstalls_when_installed_version_unparseable` and `test_install_scip_swift_reinstalls_when_installed_version_carries_suffix` verified to FAIL on pre-fix `setup.sh` and PASS after.

## Skipped Issues

None — all in-scope findings fixed.

## Verification

- `sh -n setup.sh` and `dash -n setup.sh` clean after every setup.sh edit (strictly POSIX sh maintained).
- `python -c "import ast; ast.parse(...)"` clean for both touched Python files.
- Every new sentinel test proven to fail on pre-fix source (via `git stash` of the source file only) and pass after — none are vacuous.
- Full suite after each fix and at completion: `uv run python -m pytest -m "not integration" -q` →
  - baseline (pre-fix): 495 passed, 25 skipped, 12 deselected
  - after CR-01: 496 passed
  - after WR-01: 499 passed
  - after WR-02 (final): **501 passed**, 25 skipped, 12 deselected — green, +6 regression tests.

---

_Fixed: 2026-08-22_
_Fixer: Claude (gsd-code-fixer / FixPh2)_
_Iteration: 1_
