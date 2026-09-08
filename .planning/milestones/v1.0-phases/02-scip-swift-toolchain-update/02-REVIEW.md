---
phase: 02-scip-swift-toolchain-update
reviewed: 2026-08-21T19:01:23Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - setup.sh
  - src/jarvis/config.py
  - src/jarvis/index_cli.py
  - src/jarvis/watch.py
  - .github/workflows/setup-smoke.yml
  - tests/fixtures/mini_xcode_repo/scip-swift-test.xcodeproj/project.pbxproj
  - tests/fixtures/mini_xcode_repo/scip-swift-test.xcodeproj/project.xcworkspace/contents.xcworkspacedata
  - tests/fixtures/mini_xcode_repo/scip-swift-test/SwiftFile.swift
  - tests/test_config.py
  - tests/test_index_cli.py
  - tests/test_setup_sh.py
  - tests/test_watch.py
findings:
  critical: 1
  warning: 2
  info: 2
  total: 5
status: fixed
---

# Phase 02: Code Review Report

**Reviewed:** 2026-08-21T19:01:23Z
**Depth:** standard (per-file analysis + empirical probes; every Critical/Warning claim below was reproduced or verified against the real code paths, not inferred)
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Phase 02 replaced the scip-swift version pin with latest-at-install resolution (GitHub API tag + asset digest + 0.3.0 floor), plumbed a per-repo `--cache-dir` through every Swift indexer invocation, added a Swift-gated runtime floor, a forget-time cache sweep, six watch ignore names, and a CI smoke fixture + workflow step.

The security-critical surface — untrusted GitHub API JSON parsed in POSIX sh — is genuinely well done: tag/digest/URL are shape-validated (case pattern + character-complement strip + length checks) before any use, every expansion is quoted, and `sh -n`/`dash -n` both parse clean. The `_stage_scip_swift_release` seam makes the installer tests exercise the real parsing path under dash. Tests are non-vacuous (digest-tamper, metacharacter payload, sibling-slug survival, non-Swift sentinel all assert side effects, not just exit codes); all 247 tests in the four touched files pass in this checkout.

However, one **regression** ships in the runtime gate placement: `--search-only` indexing of Swift repos now hard-fails on any host without scip-swift (all Linux hosts, by setup.sh's own design) — a path that worked before this phase and never invokes the language indexer. Reproduced live during this review. Two latent setup.sh weaknesses (wrong-asset selection, fail-open `version_ge`) were also confirmed empirically and are graded Warning because they need an external trigger (upstream publishing a second asset; an unusual `--version` output format).

## Critical Issues

### CR-01: Swift floor gate breaks `--search-only` indexing on hosts without scip-swift (regression)

**File:** `src/jarvis/index_cli.py:841-853`
**Issue:** `check_scip_swift_version()` fires inside `if language == "swift":` unconditionally — *before* the `search_only` branch is taken. A search-only run never invokes the language indexer (`_publish_search_only` runs zoekt + semantic only), yet the gate spawns `scip-swift --version` and raises `IndexingError` when the binary is absent.

Live reproduction (this review, real binaries): `scip` and `zoekt` on PATH, no `scip-swift` → `jarvis index <swift-repo> --slug swift-searchless --search-only` fails with `error: scip-swift not found on PATH — run setup.sh`. Before phase 02 this command succeeded (the swift branch only built an argv list; no subprocess). Impact:

- On **Linux** hosts, setup.sh's platform gate *deliberately never installs* scip-swift — search-only indexing (zoekt-only search) of Swift repos is now impossible there, and `jarvis reindex` of a persisted search-only Swift repo fails the same way (reindex re-resolves `search_only` from the registry but still passes through this branch).
- Same failure on any macOS host where the user ran `setup.sh --only scip zoekt`.

The existing sentinel test pins the *non-Swift* side (`test_index_repo_non_swift_never_probes_scip_swift_version`) but nothing pins the *search-only Swift* side, which is why this escaped.

**Fix:**
```python
        if language == "swift" and not search_only:
            # Swift-only floor check (D-04) ... (search-only runs never
            # invoke the language indexer, and setup.sh skips scip-swift
            # entirely off darwin/arm64, so probing would break --search-only
            # Swift repos on Linux hosts.)
            check_scip_swift_version()
            indexer_cmd = _swift_indexer_cmd(
                indexer_cmd, repo_path, scheme, config.swift_cache_dir(slug)
            )
```
`search_only` is already resolved a few lines above (`_resolve_search_only`), so the guard is available. Add a regression test mirroring the non-Swift sentinel: search-only Swift repo, `_scip_swift_version_output` monkeypatched to raise `AssertionError`, assert indexing proceeds to the zoekt publish.

**Resolution:** FIXED in `bf4e200` — gate is now `language == "swift" and not search_only`; sentinel test `test_index_repo_search_only_swift_never_probes_scip_swift_version` added (fails on pre-fix source, passes after).

## Warnings

### WR-01: Asset selection takes the first `digest` and the first `.tar.gz` URL independently — no platform correlation

**File:** `setup.sh:650-651`
**Issue:** `_digest` is the *first* `"digest"` occurrence in the API JSON and `_url` the *first* `.tar.gz` `browser_download_url` — neither is correlated with a macos-arm64 asset. The old helper encoded the platform in the asset name (`scip_swift_asset_name()` → `...-macos-arm64.tar.gz`); that pin was dropped with the rewrite.

Empirically verified with a two-asset `releases/latest` JSON (a linux tarball listed first): extraction picks the **linux** asset's URL *and* digest — a consistent pair, so digest verification **passes** — meaning on a fresh install setup.sh would download, verify, and install the wrong-platform binary and log `scip-swift: installed`. The failure surfaces only later as an exec-format error. Conversely, if asset[0] is ever a non-`.tar.gz` asset, digest and URL come from *different* assets → guaranteed checksum mismatch → hard install failure.

Latent today (upstream publishes exactly one macos-arm64 tar.gz), but the milestone's own comments note the asset-naming convention already changed once, and Linux builds of a Swift CLI are a plausible upstream addition.

**Fix:** Anchor extraction to the macos-arm64 asset. Simplest dash-safe tightening: require the platform in the URL itself before accepting it —
```sh
	case "$_url" in
	*macos-arm64*) : ;;
	*)
		log_error "scip-swift: no macos-arm64 asset in release ${_tag} (${_url})"
		rm -rf "$_meta"; trap - EXIT; return 1
		;;
	esac
```
— and, ideally, extract digest and URL from the same asset block (grep the `"name": *"...macos-arm64..."` line, then read that object's fields) so the pair can never come from different assets.

**Resolution:** FIXED in `3b301fb`, adapted: the literal `*macos-arm64*` URL check was NOT applied — the live v0.3.0 release asset is named `scip-swift-0.3.0.tar.gz` (platform dropped from the name at v0.2.0; verified against the live API during the fix), so that check would fail every real install. Instead the macOS asset is selected by accepted name shapes (`scip-swift-<version>.tar.gz` or `scip-swift-<version>-macos-arm64.tar.gz`, first match in file order via `grep -oE`), and digest+URL are then read from that one asset's JSON object (awk block keyed on the name line) — the pair can never come from different assets, and a release with no matching asset fails loudly at metadata time. Sentinel tests: linux-asset-listed-first (installs the macOS stub, never the decoy), linux-only release (loud refusal), non-.tar.gz asset first (no cross-asset digest/URL mixing).

### WR-02: `version_ge` treats comparison errors as equality — a garbage `--version` token counts as "current" and strands the auto-roll upgrade

**File:** `setup.sh:272-273` (comparisons), `setup.sh:726-731` (skip gate)
**Issue:** Inside `version_ge`, `[ "$_a" -gt "$_b" ]` errors (exit 2) on a non-numeric field; under the `if version_ge …` call context `set -e` is suspended, both the `-gt` and `-lt` tests short-circuit as false, and the loop falls through to `return 0` ("greater or equal"). Verified empirically against the shipped function:

- `version_ge "scip-swift" "0.3.0"` → **0** (garbage first token ≥ latest)
- `version_ge "garbage" "v0.3.0"` → **0**
- `version_ge "0.2.1-rc1" "v0.2.1"` → **0** (suffix in the deciding field compares "equal")

In the skip gate, `_installed` is the raw first token of the installed binary's `--version` output (`setup.sh:726`) with **no shape validation** — unlike `_tag`, which gets the case+complement treatment. A scip-swift (or PATH-shadowing wrapper) whose version output's first token isn't bare digits — a `name version` format, a warning line printed first — makes the gate log `scip-swift: <garbage> already installed (latest is v0.3.0) — skipping` and skip the install. The jarvis runtime floor then fails the index with "Re-run setup.sh" — which skips again: a deadlock loop until manual PATH surgery, defeating the exact stale-binary scenario the gate exists to fix.

**Fix:** Shape-validate `_installed` before comparing, mirroring the tag check, and treat unparseable output as outdated (proceed to install):
```sh
		_installed=$(printf '%s' "$_installed" | cut -d' ' -f1)
		case "$_installed" in
		v[0-9]*.[0-9]*.[0-9]*) : ;;
		*) log_info "scip-swift: installed version unparseable (${_installed}) — reinstalling"; _installed="" ;;
		esac
		if [ -n "$_installed" ]; then
			_stray=$(printf '%s' "$_installed" | tr -d 'v0123456789.')
			[ -n "$_stray" ] && { log_info "scip-swift: installed version unparseable (${_installed}) — reinstalling"; _installed=""; }
		fi
```
(the `if version_ge` compare then naturally treats empty as outdated and upgrades).

**Resolution:** FIXED in `98b5cf3`, adapted twice: (1) the accepted shape is `[0-9]*.[0-9]*.[0-9]*` OR `v[0-9]*.[0-9]*.[0-9]*` — the suggested `v`-mandatory pattern rejects scip-swift's real `0.3.0 (swift 6.2.4)` output (no `v` prefix) and would force a reinstall on every run (caught by the existing `test_install_scip_swift_skips_when_installed_version_is_current`); (2) the suggested `&& { … }` stray-check is written as `if`-form, per setup.sh's own set -e convention. Unparseable tokens (garbage first token, `0.3.0-rc1` suffix) now log "reinstalling" and proceed to install. Sentinel tests: `test_install_scip_swift_reinstalls_when_installed_version_unparseable`, `test_install_scip_swift_reinstalls_when_installed_version_carries_suffix` (both fail on pre-fix source).

## Info

### IN-01: `install_tarball_binary_with_digest` reports success when `mv`/`chmod` fail

**File:** `setup.sh:380-382`
**Issue:** The helper is invoked as `if install_tarball_binary_with_digest …; then`, which suspends `set -e` for the whole call; an `mv` onto an unwritable/full `bin_dir()` or a failing `chmod` therefore doesn't abort — the trailing `trap - EXIT` returns 0 and the caller logs `scip-swift: installed` with nothing on disk. This faithfully replicates the pre-existing `install_tarball_binary`/`install_raw_binary` pattern (deliberate sibling parity), so it is a pattern weakness inherited, not a regression — but the new helper copies it.
**Fix:** Guard the tail: `mv … || { log_error "install failed: cannot write $(bin_dir)/${_dest_name}"; rm -rf "$_tmp"; trap - EXIT; return 1; }` (same for `chmod`), ideally across all three sibling helpers.

### IN-02: `.swiftpm` ignore also swallows root-level `.swiftpm/Package.resolved` edits

**File:** `src/jarvis/watch.py:31`
**Issue:** Xcode 16+ stores `Package.resolved` at `.swiftpm/Package.resolved` for xcodeproj-based projects; ignoring the whole `.swiftpm` component means dependency-resolution changes no longer trigger a watch reindex. The trade-off was consciously made (D-07/D-08; orchestrator resolution #2) and `project.pbxproj` edits still trigger — recorded for the watch-UAT session, not a required change.
**Fix:** None required. If dependency-resolution changes ever need to trigger, match the specific file (`xcshareddata/swiftpm/Package.resolved`, `.swiftpm/Package.resolved`) rather than widening the component set.

---

## Verified sound (adversarial checks that did NOT find defects)

- **POSIX compliance:** `sh -n` and `dash -n` both parse setup.sh clean; no bashisms introduced; `head -1`/`grep -o`/`${#var}`/`#`-param-expansion are all within the repo's de-facto utility set (macOS BSD + GNU both provide them).
- **Untrusted-JSON injection:** every extracted field (`_tag`, `_digest`, `_url`) is quoted at every use; tag validation (case + complement strip) genuinely rejects `v0.3.0; touch pwned` (verified: the test's payload file never appears); digest validation (`sha256:` prefix + 64 chars + `[!0-9a-f]` complement) is tight; URL restricted to `https://`/`file://`. The `file://` seam sits inside the "HTTPS GitHub API response is trusted" model and adds no attack surface.
- **Trap hygiene:** `install_scip_swift` clears its `_meta` trap before delegating to `install_tarball_binary_with_digest` (no nested-trap clobbering); all error paths clean up.
- **Floor parity & placement:** `MIN_SCIP_SWIFT_VERSION`/`check_scip_swift_version` mirror the proven scip pair; the gate sits inside the phase-1 failure wrap after `resolved_language` is set, so a raise persists a `failed_hard` row naming swift (verified by reading the wrap and the sentinel test).
- **swiftpm-path `--cache-dir`:** executed for real against scip-swift 0.3.0 on this host (exit 0, cache populated at the custom dir — `manifest.json`, `index-db`, `build-scratch`, `docs` — and no `.build`/`.scip-cache` in the repo tree), closing the gap that only the xcodebuild path had live e2e proof.
- **Forget sweep:** path built solely from `data_dir()/cache/scip-swift/<slug>` with a sanitized slug (no traversal); sibling-sparing and absent-dir cases test-pinned.
- **Watch ignores:** pure set extension; matcher byte-identical; over-broad-matching regression tests (`Builders/`, `build_tools.swift`) pin the boundary.
- **Workflow step:** assertions are real (`test ! -e` under `set -eu` fails the step); inline `git -c user.email/-c user.name` handles runner identity; `RUNNER_TEMP` isolation keeps the runner's `~/.jarvis` untouched; the Linux leg exercises the skip branch as designed. The macOS/Xcode runner assumption (A1) is documented and routed to the first CI run.
- **Tests:** 247 passed / 12 deselected (integration) across the four touched files in this checkout. Stale `SCIP_SWIFT_VERSION` references fully purged (workflow comment refreshed).

_Reviewed: 2026-08-21T19:01:23Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
