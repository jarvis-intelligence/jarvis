# SDD ledger — plan: docs/superpowers/plans/2026-07-31-scip-swift-signing-defaults.md

## Setup

- Disk precondition: 17 GiB free at start (plan warned at ~20 GiB; was 206 MiB when plan written).
  Adequate for Tasks 1-3 (small Swift package). Task 5 (iOS app build, ~30 SPM deps) still at risk
  — reassess at Task 5 Step 1.
- scip-swift was on `main`; created branch `fix/xcodebuild-signing-defaults` at ac415a1.
  **Plan deviation:** Task 3 Step 5 says `git push origin main --tags`. Since work is on a branch,
  Task 3 must merge to `main` first, then tag. Task 3 is outward-facing (GitHub release) and
  requires user confirmation regardless.
- codeintel on branch `docs/scip-swift-signing-defaults-spec` (holds spec 59f4c53, plan 8cd61b1).
  Four unrelated modified docs in working tree — leave uncommitted (code-standards.md,
  codebase-summary.md, project-roadmap.md, system-architecture.md).

## Progress

Task 1: complete (scip-swift commits ac415a1..27e746b, review clean)
  - Extracted `arguments` computed property; 5 new Swift Testing cases; full suite 28/28.
  - Reviewer verified argument list byte-identical, no duplicate xcodeConfiguration, only 2 files touched.

Task 2: complete (scip-swift commits 27e746b..b77edce, review clean)
  - Four CODE_SIGN* settings added between COMPILER_INDEX_STORE_ENABLE=YES and `build`; 2 new tests; suite 30/30.
  - Reviewer verified all four strings character-by-character (incl. bare trailing `=`), position, and
    that `-destination` is absent from the whole patch. Confirmed tests would catch typo/missing-`=`/
    quoted-empty/wrong-name/wrong-position defects.
Task 2: minor (deferred): no test asserts signing settings come after COMPILER_INDEX_STORE_ENABLE=YES
  (only that they precede `build`). Not a live risk — array is a fixed literal.
Task 2: minor (deferred): relative order among the four CODE_SIGN* settings unasserted. Cosmetic —
  xcodebuild build settings are unordered key=value pairs.

Task 3: STEPS 1-4 complete (scip-swift commits b77edce..99f71a2, review clean)
  - Version.swift -> "0.1.2" (no `v`); suite 30/30; release binary reports 0.1.2 (swift 6.2.4).
  - Assets at /Users/ddphuong/Projects/scip-swift/:
      scip-swift-v0.1.2-macos-arm64.tar.gz  (tar root = single flat entry `scip-swift`)
      scip-swift-v0.1.2-macos-arm64.tar.gz.sha256
      digest bacd88f3ab02269fdd948a38b28718651e3024b90fa96392d9ee7aff38f7fa16
  - Controller independently verified tar layout + `shasum -a 256 -c` => OK.
  - **Steps 5-7 WITHHELD** (git tag / push / gh release create / cleanup): outward-facing publish,
    needs explicit user confirmation. Also requires merging branch -> main first (see Setup deviation).

Task 4: complete (codeintel commits 8cd61b1..c1ffe26, review clean)
  - setup.sh pin -> "v0.1.2" (v kept), test_setup_sh.py:624 asset name, roadmap subsection before
    "### Semantic/Vector Search". Unit suite 301 passing.
  - Controller verified independently: `git diff --stat 8cd61b1..c1ffe26 -- src/` is EMPTY (no Python
    source touched). Reviewer grepped tree: no stray hardcoded version outside the single constant.
  - Reviewer ⚠️ "cannot verify from diff": roadmap claims "Reproduced against luz_epost_ios" and
    "disabling signing alone was verified sufficient".
    RESOLVED by controller — NOT a gap. Both were established empirically by the controller earlier
    in this session, before the spec was written:
      (a) ran xcodebuild with scip-swift's exact arg list -> 5 provisioning errors naming exactly
          ePostDev, notification_service, luz_epost_siri_intent, import_files_action,
          import_files_share;
      (b) re-ran with the four CODE_SIGN* settings -> zero provisioning errors, cleared
          GatherProvisioningInputs, proceeded to SwiftPM package checkout.
    Scope note: (b) proves the provisioning failure is gone, NOT that a full index build succeeds —
    that is Task 5's gate, and the roadmap block deliberately does not claim it.
  - Unrelated doc edits stashed during this task and restored afterwards (stash dropped, tree intact).

Task 3: complete — STEPS 5-7 done after explicit user confirmation.
  - scip-swift `main` fast-forwarded ac415a1..99f71a2, pushed.
  - Tag v0.1.2: annotated + GPG-signed, matching v0.1.1's convention. NOTE: bare `git tag v0.1.2`
    fails in this repo ("no tag message?") because tag.gpgsign=true — must use `git tag -a -m`.
  - Release published: https://github.com/phuongddx/scip-swift/releases/tag/v0.1.2
    Assets verified downloadable: .tar.gz (1825429 bytes) + .tar.gz.sha256 (103 bytes).
  - Local artifacts removed (Step 7).

Task 5 (in progress):
  - Step 2: `sh ./setup.sh --only scip-swift --force` => installed v0.1.2 to ~/.codeintel/bin.
    NOTE: `./setup.sh` is not executable — must invoke as `sh ./setup.sh`.
  - **ENVIRONMENT HAZARD FOUND (outside plan scope, not fixed):** two scip-swift copies on PATH.
      ~/.codeintel/bin/scip-swift => 0.1.2  (currently resolved first — correct)
      ~/.local/bin/scip-swift     => 0.1.0  (stale; predates the `index` subcommand entirely)
    Resolution is correct today, but any PATH reorder would silently hand codeintel a 0.1.0 binary.
    Recommend deleting/refreshing the ~/.local/bin copy. Reported to user.
  - Step 3 first attempt aborted before running: redirect target under the session scratchpad dir
    no longer existed (reaped during the earlier disk-full episode). EXIT=1 was the redirect, not
    the indexer. Re-run without redirect.

Task 5: complete (codeintel commits c1ffe26..556a26b, review clean)
  ACCEPTANCE GATE: **NOT PASSED** — but the fix itself is VERIFIED. Do not conflate these.
  - Fix verified: 0 provisioning errors (was 5); GatherProvisioningInputs passed; build reached
    146 `SwiftDriver Compilation Requirements` invocations, compiling for Debug-iphoneos
    (-sdk iPhoneOS26.2.sdk -target arm64-apple-ios16.6). The provisioning barrier is gone.
  - Gate blocked by a PRE-EXISTING, UNRELATED defect: the Xcode project references
    epost-app/luz_ios_login/Sources/luz_ios_login/Utilities/LoginResponseParser.swift.
    Verified absent: the `Utilities/` dir does not exist in that checkout and the file is in no
    git history there. Broken local project state — nothing to do with codeintel or scip-swift.
  - `codeintel status luz-epost-ios` => status `failed`, commit `-`, no index published.
    Atomic publish behaved exactly as designed (refused to publish a bogus index).
  - `My Mac` destination warning still present as expected (-destination deliberately not added),
    now warning-only, not fatal.
  - Step 6-7 doc edit authored by controller (sole evidence holder), applied+committed by subagent,
    then reviewed: verified verbatim, correctly placed, and NOT overclaiming.
  - Disk never became the blocker this run (15-16 GiB free throughout).

FINAL WHOLE-BRANCH REVIEW (opus): "Ready to merge: With fixes" — no code changes needed.
  Independently re-verified: scip-swift 30/30, codeintel 301 + 61 passing, both release assets
  present, ~/.codeintel/bin/scip-swift => 0.1.2.
  Notable positive finding: disabled signing CANNOT leak into a shippable artifact — IndexCommand.swift:29
  puts derivedDataPath under NSTemporaryDirectory()/scip-swift-<UUID>, never ~/Library/Developer/Xcode/
  DerivedData, so no archive/distribution flow can pick up unsigned products.
  Both deferred Task 2 minors triaged as ACCEPTABLE TO LEAVE (xcodebuild KEY=VALUE overrides are
  order-independent; the only ordering with semantics — settings before the action verb — IS asserted).

FIX WAVE (one dispatch) — addressing:
  - Important #1: existing installs silently keep the old binary. setup.sh's already_installed() is
    presence-only, and README:74-75 says "Re-running is safe: anything already present is skipped."
    A v0.1.1 user re-running setup.sh sees "already installed, skipping" and still fails on
    GatherProvisioningInputs with no clue. Needs a doc sentence: signed iOS repos require >= v0.1.2;
    existing installs must run `sh ./setup.sh --only scip-swift --force`.
  - Minor #2: docs/project-roadmap.md:56 still says present-tense "setup.sh pins v0.1.1 as the floor".
  - Minor #3: floor test still asserts >= (0,1,1), so a downgrade to v0.1.1 reintroduces this bug.
    Raise to (0,1,2). NOT a plan conflict — the plan only observed it "passes untouched".
  - Minor #5: roadmap path imprecision + "codeintel's only change" reads absolute.

FIX WAVE DEFERRALS (with rulings):
  - Minor #4 (index_cli.py:44 comment "Verified against both v0.1.0 and v0.1.1" should add v0.1.2):
    DEFERRED. Ruling: real but cosmetic, and editing it would break the plan's explicit Definition of
    Done ("no codeintel Python source file modified"). Not worth trading a verifiable DoD for a comment.
  - Minor #6 (XcodebuildBuildRunnerTests firstIndex(of:"build") would mis-resolve a scheme named
    "build"): DEFERRED. Ruling: reviewer agrees harmless with current fixture; would put scip-swift
    main ahead of the v0.1.2 tag for no behavioral gain.
  - Minor #7 (scip-swift's own roadmap still lists v0.1.1 as unreleased future work; no changelog):
    DEFERRED. Ruling: pre-existing drift in the other repo, outside this plan's scope. Worth a
    follow-up in scip-swift.

DISK WARNING: down to 6.7 GiB free during the final review — below the plan's ~20 GiB precondition.
  Must free space before any acceptance-gate retry, or it fails on disk rather than on the fix.

FIX WAVE: complete (codeintel commits 556a26b..d486bd1). Scoped re-review: ALL 4 FINDINGS ADDRESSED,
  no new breakage. Re-reviewer independently confirmed README's new prose matches setup.sh's ACTUAL
  behavior (already_installed() at setup.sh:174-179 really is presence-only; --only/--force really
  work as described) rather than merely being plausible, and grepped that the renamed test is not
  referenced by its old name anywhere.
  Controller verified `git diff --stat 556a26b..d486bd1 -- src/` empty and `-- setup.sh` empty.

Out-of-scope observation (deferred, with ruling): the plan and spec files still cite the old test name
  test_scip_swift_pin_is_at_least_v0_1_1 and `>= (0, 1, 1)`.
  Ruling: leave them. Plans/specs in this repo are dated point-in-time artifacts recording what was
  decided then; docs/project-roadmap.md is the living record and is now correct. Rewriting history
  docs to match later changes would make them unreliable as history.

FINAL STATE — controller-verified, not taken from reports:
  - codeintel `uv run pytest -m "not integration"` => 301 passed, 11 deselected.
  - scip-swift `swift test --configuration debug` => 30 tests in 5 suites passed.
  - scip-swift main = 99f71a2, tag v0.1.2 pushed, release live with both assets.
  - codeintel branch docs/scip-swift-signing-defaults-spec = d486bd1 (5 commits ahead of main).
  - User's 4 unrelated modified docs still uncommitted and intact (stashed/restored 3x, never lost).

ALL 5 TASKS COMPLETE. Ready for finishing-a-development-branch.
