---
phase: 02-scip-swift-toolchain-update
verified: 2026-08-22T02:55:00Z
status: passed
score: 24/24 must-haves verified
behavior_unverified: 0
overrides_applied: 0
human_verification:

  - test: "Push the branch (or workflow_dispatch) and watch the first real run of the new 'Index the Swift fixture through jarvis (macOS only)' step on GitHub's macos-latest runner"
    expected: "macOS leg: scip-swift resolves v0.3.0 from the live API, digest-verifies, installs; jarvis index of the git-init'd fixture copy exits 0; tree-cleanliness assertions pass (no .scip-cache/.build in tree; cache root under JARVIS_DATA_DIR). Linux leg: install step and index step both take their documented skip branches and stay green."
    why_human: "Flagged assumption A1 (RESEARCH): macos-latest = arm64 with full Xcode is a CI-runner environment property that cannot be exercised locally. The workflow commit b342fea is not on any remote (git branch -r --contains is empty) and the newest setup-smoke run predates it (2026-08-08), so the first post-change execution has not happened. Abstaining per honest-verifier rules — never a silent pass."

  - test: "Run a live `jarvis watch` session over a Swift repo (e.g. the mini_xcode_repo copy) with a short debounce; edit a .swift file, then trigger an indexer/build artifact write"
    expected: "The source edit fires exactly one debounced reindex; artifact writes (anything under .scip-cache/.build/DerivedData/.index-store/IndexStore/.swiftpm) never trigger; at most the accepted one-shot xcshareddata write costs a single reindex on first index."
    why_human: "should_ignore_path's truth table is exhaustively unit-pinned and the handler wiring is code-verified (on_any_event consults it before debouncer.notify), but real watchdog event delivery over a live session is the plan's own documented manual-UAT scope (02-02 flagged assumption for SWFT-03)."
---

# Phase 2: scip-swift Toolchain Update Verification Report

**Phase Goal:** jarvis installs and drives a working scip-swift release — the pin moves off v0.1.2 to a fixed release cut from main, with setup.sh, caching, and watch adapted to the new binary's contract
**Verified:** 2026-08-22T02:55:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

**Verdict: goal achieved in the codebase.** Every roadmap success criterion and every plan must-have is verified against actual sources with live behavioral evidence — including an independent end-to-end re-run of the install→index pipeline and falsification proofs of all three review fixes. Two items remain structurally unresolvable locally (first CI run of the new smoke step; live watch session) and are routed to human verification per the escalation gate — neither indicates missing jarvis-side work.

### Observable Truths

Roadmap success criteria (the contract) plus plan truths, merged and deduplicated:

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| SC1 | setup.sh installs the newly pinned scip-swift release with checksum verification, and setup-smoke guards that the pin resolves | ✓ VERIFIED | Live run this session: fresh JARVIS_BIN_DIR + minimal PATH → `scip-swift: installing v0.3.0 (digest-verified)` → installed; binary reports `0.3.0 (swift 6.2.4)`. Old `SCIP_SWIFT_VERSION` pin and `scip_swift_asset_name()` deleted from setup.sh (grep: zero occurrences). Workflow's install step exercises the real API on macOS and the skip branch on Linux (prior runs of the workflow machinery: green, 2026-08-08). **Recorded plan-time deviation:** checksum source is the GitHub API asset `digest` (immutable, server-computed), not a `.sha256` sidecar — upstream dropped sidecars at v0.2.0; resolved at plan time (orchestrator resolution #1, PLAN 02-01 flagged assumptions) and matches SWFT-02's "checksum verification retained". Digest-verify tests confirmed below. |
| SC2 | `jarvis index` on a Swift `.xcodeproj` repo produces a SCIP index via the pinned binary with `--build-tool xcodebuild` genuinely dispatched | ✓ VERIFIED | Live e2e re-run this session (verifier-executed, isolated `JARVIS_DATA_DIR`): fixture copy git-init'd → `uv run jarvis index … --slug gsd-vfy-xcode` → exit 0, `language: swift`, `status: indexed` (SCIP db + current pointer published at `scip/_/gsd-vfy-xcode/_/`); `derived-data/` inside the cache proves xcodebuild genuinely ran (SwiftPM-only builds produce no DerivedData). Unit: `_swift_indexer_cmd` appends `--build-tool xcodebuild` (+ optional `--scheme`) on the xcodebuild path (index_cli.py:211-231; tests pass). |
| SC3 | scip-swift's incremental cache lives under `~/.jarvis/` (via `--cache-dir`); no indexer cache/build artifacts in the repo tree | ✓ VERIFIED | Same live run: cache at `<data>/cache/scip-swift/gsd-vfy-xcode/` (manifest.json, derived-data, index-db, docs); tree assertions — no `.scip-cache`, no `.build`, `git status --porcelain` empty. `config.swift_cache_dir` = `data_dir(root)/cache/scip-swift/slug` (config.py:33-40); `--cache-dir` on BOTH return paths of `_swift_indexer_cmd`; no `mkdir` anywhere near the swift cache path (upstream owns creation — grep-verified). |
| SC4 | `jarvis watch` over a Swift repo never self-triggers reindexes from indexer artifacts (`.scip-cache`, `.build`, `DerivedData`) | ✓ VERIFIED | `_IGNORED_PATH_PARTS` contains all six additions beside the untouched originals (watch.py:25-27); matcher is component-membership only. Handler wiring code-verified: `on_any_event` returns early on `should_ignore_path(event.src_path)` before `debouncer.notify()` (index_cli.py:1227-1231). Exhaustive unit truth table passes: six names at any depth, real layouts (`.build/x86_64-apple-macosx/debug`, `DerivedData/Build/Products`, `.scip-cache/index-db`), and the False guards (`Sources/App/main.swift`, `App.xcodeproj/project.pbxproj`, `Builders/`, `build_tools.swift`). Live watch session → Human Verification #2 (plan-deferred UAT). |

Plan 02-01 truths (9):

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 1.1 | darwin/arm64 resolves latest from the API, accepts only ≥ 0.3.0, installs a sha256-verified binary that runs | ✓ VERIFIED | Live install (above) + `SCIP_SWIFT_MIN_VERSION="0.3.0"` at setup.sh:51 + `test_install_scip_swift_resolves_and_installs_latest` pass |
| 1.2 | latest below 0.3.0 fails loudly naming the floor, "no good release exists yet" | ✓ VERIFIED | setup.sh:730-733 both log_error lines present; `test_install_scip_swift_fails_loudly_when_latest_below_floor` pass |
| 1.3 | tampered/malformed digest fails install before anything installed | ✓ VERIFIED | `verify_sha256` gate in `install_tarball_binary_with_digest`; `test_install_scip_swift_fails_on_digest_mismatch` pass |
| 1.4 | tag failing `v<digits>.<digits>.<digits>` rejected before any shell use | ✓ VERIFIED | case pattern + `tr -d 'v0123456789.'` complement strip (setup.sh:674-690); `test_install_scip_swift_rejects_tag_with_shell_metacharacters` pass (asserts no payload side-effect file) |
| 1.5 | outdated installed binary upgraded, current skipped (version-aware, never presence-gated) | ✓ VERIFIED | Skip gate compares installed `--version` first token against the RESOLVED tag (setup.sh:775-792); 4 tests pass: skip-when-current, reinstall-when-outdated, reinstall-when-unparseable, reinstall-when-suffixed |
| 1.6 | non-macOS/non-arm64 skips with exit 0, no API request (gate first) | ✓ VERIFIED | Gate is the first statement of `install_scip_swift` (setup.sh:621-626); `test_install_scip_swift_linux_skips_before_any_fetch` (nonexistent seam URL, still exit 0) pass |
| 1.7 | every Swift invocation passes `--cache-dir <data>/cache/scip-swift/<slug>/` on both swiftpm and xcodebuild paths | ✓ VERIFIED | Both return paths of `_swift_indexer_cmd` append it; 4 argv tests pass (incl. swiftpm-without-xcodeproj and scheme-before-cache-dir ordering); call site passes `config.swift_cache_dir(slug)` (index_cli.py:855-857) |
| 1.8 | jarvis never creates the cache directory — computes the path only | ✓ VERIFIED | grep: no mkdir/mkdirparents near swift_cache_dir in src/; live run shows upstream created manifest.json et al. |
| 1.9 | real end-to-end run: fresh install → jarvis index on .xcodeproj fixture → indexed/swift, cache under data dir, tree clean | ✓ VERIFIED | Independently re-executed by this verifier (SC2/SC3 evidence above), matching the recorded 02-01/02-03 runs |

Plan 02-02 truths (7):

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 2.1 | Swift repo with scip-swift < 0.3.0 fails loudly naming installed version, floor, setup.sh recovery | ✓ VERIFIED | `MIN_SCIP_SWIFT_VERSION = (0, 3, 0)` (index_cli.py:81); error message contains all three (index_cli.py:437-445); `test_check_scip_swift_version_rejects_v021` pass |
| 2.2 | non-Swift repos never invoke the probe (gated in swift branch) | ✓ VERIFIED | Sentinel `test_index_repo_non_swift_never_probes_scip_swift_version` pass (monkeypatched probe raises AssertionError if invoked) |
| 2.3 | unparseable `--version` does not block indexing (warn-by-omission) | ✓ VERIFIED | `check_scip_swift_version` returns on `parse_scip_version` None (index_cli.py:430-436); `test_check_scip_swift_version_tolerates_unparseable` pass |
| 2.4 | `jarvis forget <slug>` removes that slug's cache dir; never-Swift repos still succeed | ✓ VERIFIED | `shutil.rmtree(config.swift_cache_dir(slug), ignore_errors=True)` in `_cmd_forget` (index_cli.py:1181); sibling-sparing + absent-dir tests pass |
| 2.5 | watch drops events with `.scip-cache`, `.build`, `DerivedData`, `.index-store`, `IndexStore`, `.swiftpm` as components, wherever they appear | ✓ VERIFIED | Set contents verified (watch.py:25-27) + `test_swift_artifacts_are_ignored_wherever_they_appear` / `test_real_swift_layouts_are_ignored` pass |
| 2.6 | Swift sources and pbxproj edits still trigger | ✓ VERIFIED | `test_swift_ignores_never_suppress_legitimate_triggers` pass (False cases for Sources/*.swift and *.xcodeproj/project.pbxproj) |
| 2.7 | floor failure persists a failed_hard row via the phase-1 wrap (no new wiring) | ✓ VERIFIED | Gate sits inside the pre-pipeline `try` (index_cli.py:843-851, after `resolved_language = language`); behavioral pin: `test_pre_pipeline_version_gate_failure_creates_a_recoverable_row` exercises the identical wrap with a same-shaped version-gate raise → failed row, origin failed_hard, reason+stderr persisted. |

Plan 02-03 truths (4):

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 3.1 | git-tracked .xcodeproj fixture under tests/fixtures/mini_xcode_repo that indexes as swift/indexed, exercising `_prefers_xcodebuild` → xcodebuild dispatch | ✓ VERIFIED | Exactly 3 git-tracked files; no Package.swift (so `_prefers_xcodebuild` fires); **verifier-verified byte-identical to upstream `Fixtures/XcodeTestProject` at tag v0.3.0 via fresh clone + cmp (all 3 IDENTICAL)**; live-indexed this session (SC2 evidence) |
| 3.2 | setup-smoke macOS leg runs setup.sh then a real jarvis index on the fixture with tree-cleanliness assertions | ✓ VERIFIED | Step present after unit tests: git-init'd RUNNER_TEMP copy, ci-bin PATH prepend, isolated JARVIS_DATA_DIR, `scip-swift --version` echo, `uv run jarvis index --slug mini-xcode-repo`, `test ! -e .scip-cache` / `test ! -e .build` / `test -d cache root` under `set -eu`. First real CI execution → Human Verification #1 (A1) |
| 3.3 | non-macOS legs skip the index smoke without failing | ✓ VERIFIED | `RUNNER_OS = macOS` gate inside the run block with explicit else-echo skip (mirrors the install-step guard style) — Linux executes the step and takes the skip |
| 3.4 | smoke step isolates from runner home (RUNNER_TEMP for data dir + fixture, ci-bin on PATH) | ✓ VERIFIED | Workflow text verified: `export PATH="${RUNNER_TEMP}/ci-bin:$PATH"`, `export JARVIS_DATA_DIR="${RUNNER_TEMP}/jarvis-data"`, fixture copy under RUNNER_TEMP; trigger paths extended with `tests/fixtures/mini_xcode_repo/**` and `src/jarvis/**` on both push and PR |

**Score:** 24/24 truths verified (4 roadmap SCs + 20 plan truths; 0 present-but-behavior-unverified)

### Review Fix Verification (1 Critical + 2 Warnings)

All three fixes verified REAL via falsification: in a detached worktree at 98b5cf3, each fix's source file was reverted to its pre-fix revision and the sentinel tests were proven to FAIL with the review's exact diagnosis, then the worktree was removed.

| Fix | Commit | Pre-fix sentinel result (verifier-executed) | Status |
| --- | --- | --- | --- |
| CR-01 search-only regression (`language == "swift" and not search_only`, index_cli.py:843) | bf4e200 | `test_index_repo_search_only_swift_never_probes_scip_swift_version` FAILED: `AssertionError: scip-swift must not be probed for a search-only run` | ✓ fix load-bearing |
| WR-01 asset correlation (name-shape anchored selection + same-asset digest/URL awk block, setup.sh:648-670) | 3b301fb | all 3 sentinels FAILED — pre-fix output shows it attempting to download the decoy linux asset (`couldn't open file …scip-swift-0.3.0-linux-amd64.tar.gz`), exactly the review's diagnosis | ✓ fix load-bearing |
| WR-02 installed-version shape validation before skip gate (setup.sh:756-782) | 98b5cf3 | both sentinels FAILED (`a suffixed version must not satisfy the skip gate`; binary not installed) | ✓ fix load-bearing |

Note on WR-01's adaptation: the live v0.3.0 asset is named `scip-swift-0.3.0.tar.gz` (platform dropped at v0.2.0) — verified indirectly by this session's live install succeeding through the name-shape selector (`scip-swift-[0-9][0-9.]*(-macos-arm64)?\.tar.gz`), and the no-macos-asset release fails loudly (test pass).

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `setup.sh` | latest-resolution + floor + digest-verified install (`SCIP_SWIFT_MIN_VERSION`) | ✓ VERIFIED | All helpers present; `sh -n` clean (CI-guarded); old pin/asset-helper deleted |
| `src/jarvis/config.py` | `swift_cache_dir(slug, root)` | ✓ VERIFIED | Delegates to `data_dir(root)/cache/scip-swift/slug`; D-05 docstring |
| `src/jarvis/index_cli.py` | `--cache-dir` on every Swift invocation; floor gate; forget sweep | ✓ VERIFIED | One `_swift_indexer_cmd` definition, 4-param, both paths append `--cache-dir`; sweep at :1181 |
| `src/jarvis/watch.py` | six Swift artifact names in `_IGNORED_PATH_PARTS` | ✓ VERIFIED | Pure set extension; matcher unchanged |
| `tests/test_setup_sh.py` | resolution/floor/digest/shape/skip suite over the seam | ✓ VERIFIED | 17 scip-swift tests incl. `_stage_scip_swift_release` fixture builder |
| `tests/test_index_cli.py`, `tests/test_watch.py`, `tests/test_config.py` | floor/forget/watch/cache-dir coverage | ✓ VERIFIED | All present and passing |
| `tests/fixtures/mini_xcode_repo/*` (3 files) | indexable .xcodeproj fixture | ✓ VERIFIED | Byte-identical to upstream v0.3.0 (verifier cmp); live-indexed |
| `.github/workflows/setup-smoke.yml` | post-install jarvis index step (`mini_xcode_repo`) | ✓ VERIFIED | Step + assertions + trigger paths verified; first run pending (A1) |

### Key Link Verification

| From | To | Via | Status |
| --- | --- | --- | --- |
| setup.sh `install_scip_swift` | api.github.com `/releases/latest` | `download_to` on the `SCIP_SWIFT_API_URL`-overridable URL; sed/grep/awk extraction | ✓ WIRED (live-proven) |
| `index_repo` swift branch | `config.swift_cache_dir` | `_swift_indexer_cmd(…, config.swift_cache_dir(slug))` appends `--cache-dir` | ✓ WIRED (index_cli.py:855-857) |
| `index_repo` swift branch | `check_scip_swift_version` | first statement of the branch (post-CR-01: gated `and not search_only`) | ✓ WIRED (index_cli.py:843-851) |
| `_cmd_forget` | `config.swift_cache_dir` | `shutil.rmtree(..., ignore_errors=True)` after lancedb sweep | ✓ WIRED (index_cli.py:1181) |
| `watch.should_ignore_path` | `_IGNORED_PATH_PARTS` | component-membership check; handler consults it on every event | ✓ WIRED (watch.py:36-38; index_cli.py:1229) |
| setup-smoke macOS leg | `tests/fixtures/mini_xcode_repo/` | RUNNER_TEMP copy + ci-bin PATH + `uv run jarvis index` | ✓ WIRED (structural; execution = Human #1) |

### Data-Flow Trace (Level 4)

No rendered/UI values in this phase (installer + CLI + config plumbing). The dynamic values that matter were traced to real sources: the installed binary's version string flows from the real GitHub API JSON (live-verified); the cache path flows from `data_dir()` env resolution to the real argv (live-verified by the cache materializing at the computed path); the SCIP index published by the live run (`index-<sha>.db` + `current` pointer) flows from the real pinned binary's output. No static returns, no mocks in the production path.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| --- | --- | --- | --- |
| 9 setup.sh sentinels (asset correlation, digest mismatch, metachar tag, floor, no-macos-asset, unparseable/suffix reinstall, linux no-fetch) | targeted pytest run | 9 passed in 2.15s | ✓ PASS |
| 8 index_cli sentinels (CR-01 search-only, non-swift, floor reject/accept, forget sibling/absent, argv both paths) + 3 watch tests | targeted pytest runs | 8 passed + 3 passed | ✓ PASS |
| CR-01 fix is load-bearing | revert index_cli.py to bf4e200^ in temp worktree, run sentinel | FAILED with `AssertionError: scip-swift must not be probed for a search-only run` | ✓ PASS (falsifies pre-fix) |
| WR-01 fix is load-bearing | revert setup.sh to 3b301fb^, run 3 sentinels | 3 FAILED (attempted decoy linux-asset download) | ✓ PASS (falsifies pre-fix) |
| WR-02 fix is load-bearing | revert setup.sh to 98b5cf3^, run 2 sentinels | 2 FAILED (skip stranded the upgrade) | ✓ PASS (falsifies pre-fix) |
| Live install through current installer | `env -i PATH=/usr/bin:/bin JARVIS_BIN_DIR=<fresh> sh setup.sh --only scip-swift` | `installing v0.3.0 (digest-verified)` → installed; `0.3.0 (swift 6.2.4)` | ✓ PASS |
| Live e2e index of fixture (SC2+SC3) | isolated-data `uv run jarvis index` on git-init'd fixture copy | exit 0; `indexed`; language swift; cache isolated (manifest.json, derived-data, index-db, docs); tree clean; porcelain empty (27.7s) | ✓ PASS |
| Full unit suite regression | `uv run python -m pytest -m "not integration" -q` | 501 passed, 25 skipped, 12 deselected in 31.38s | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| --- | --- | --- | --- | --- |
| SWFT-01 | 02-01, 02-02, 02-03 | Pin off v0.1.2 to fixed release cut from main; `_swift_indexer_cmd` compatibility verified | ✓ SATISFIED | v0.3.0 resolved live (dispatch fix landed upstream in 0.3.0 — the "fixed release cut from main" exists as v0.3.0, per plan's D-03 locked resolution); compatibility proven by live e2e + CI smoke step; runtime floor guards stale binaries |
| SWFT-02 | 02-01 | setup.sh handles current release-asset naming; checksum verification retained | ✓ SATISFIED | Name-shape anchored asset selection (both real naming generations) live-proven against the real API; digest verification retained via the immutable API digest (documented deviation from sidecar wording); tamper/malformed fail-closed tests |
| SWFT-03 | 02-01, 02-02, 02-03 | Cache outside repo trees (`--cache-dir` under `~/.jarvis`); watch never self-triggers | ✓ SATISFIED | Live e2e: cache isolated, tree clean; watch ignore set + wiring verified; CI asserts the same contract |

Orphaned requirements: none — REQUIREMENTS.md maps exactly SWFT-01/02/03 to Phase 2 (all marked Complete, consistent with this verification); SWFT-04 is Phase 4.

### Prohibition Checks (all must-NOT — none violated)

| Prohibition | Status |
| --- | --- |
| No platform-gate widening (x86_64 stays out) | ✓ HELD — gate still `darwin` + `arm64` only |
| No second checksum path in scip-swift install (digest-only; sidecar route untouched for zoekt/scip) | ✓ HELD — one digest route in `install_scip_swift`; `install_tarball_binary` sidecar helper unchanged |
| Skip must not be presence-gated | ✓ HELD — version-aware gate with upgrade path (4 tests) |
| Watch ignores never suppress legitimate triggers (no wholesale .xcodeproj/*.swift ignoring) | ✓ HELD — False-guard tests pin Sources/*.swift and pbxproj |
| `jarvis forget` never deletes outside the slug's own cache dir | ✓ HELD — path built solely from `swift_cache_dir(slug)`; sibling-survival test |
| Runtime floor: not for non-Swift repos, never blocks on unparseable output | ✓ HELD — sentinel + tolerate tests |
| CI smoke must not become a committed integration test | ✓ HELD — 0 added `@pytest.mark.integration` decorators in the phase diff (30 added test functions, all unit); no test references `mini_xcode_repo` |
| Fixture must not be a hand-rolled pbxproj | ✓ HELD — byte-identical to upstream v0.3.0 (verifier cmp) |

### Anti-Patterns Found

None. Debt-marker scan (TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER) across all 9 phase-modified files: zero matches. No stub patterns: all functions substantive; error paths fail loudly rather than returning empty defaults. Info-level (from review, out of fix scope by assignment): IN-01 (mv/chmod failure tail in installer helpers reports success — inherited sibling-parity pattern), IN-02 (`.swiftpm` ignore swallows root-level `Package.resolved` edits — conscious D-07/D-08 trade-off recorded for watch UAT). Neither blocks the goal.

### Human Verification Required

1. **First real CI run of the new macOS smoke step (A1)** — see frontmatter. The workflow change (b342fea) is not on any remote and no setup-smoke run postdates it; this is the milestone's own designated resolving evidence for the CI-runner half of SWFT-01. Everything else about the step was proven locally (structure + the executor's verbatim step-script simulation + this verifier's independent live e2e of the identical pipeline).
2. **Live `jarvis watch` session over a Swift repo** — see frontmatter. Plan-deferred manual UAT (02-02 flagged assumption); the filter truth table and wiring are fully machine-verified.

### Gaps Summary

No gaps. All 24 truths verified; all artifacts present, substantive, wired, and data-flowing; all 8 prohibitions held; all 3 review fixes proven load-bearing by falsification; requirements SWFT-01/02/03 satisfied with no orphans. The two open items are environment-gated (CI runner, live watch session), not missing jarvis-side work — hence `human_needed`, not `gaps_found`.

---

_Verified: 2026-08-22T02:55:00Z_
_Verifier: Claude (gsd-verifier)_
