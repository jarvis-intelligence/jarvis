---
phase: 04-swift-failure-signatures
verified: 2026-08-23T02:27:00Z
status: human_needed
score: 7/7 must-haves verified
behavior_unverified: 0
overrides_applied: 0
prohibition_flags: 1 # P2 (no-remedy-prose) is test-tier with NO wired enforcement — flagged, not silently passed
human_verification:
  - test: "Inspect the two Swift reasons in _SEARCH_ONLY_SIGNATURES (src/jarvis/index_cli.py:135-142) and confirm cause-only prose; decide whether to accept the documented prose-drift risk or add reason-literal assertions (review IN-01's optional fix)."
    expected: "Reasons remain causes only (no remedy/recovery wording); either accept the risk or convert to a mechanical pin."
    why_human: "Prohibition declared verification: test, but no test pins reason content — the tripwire compares the persisted status_reason against the same constant (self-consistent by construction). Verifier inspection confirms both current reasons ARE cause-only, so the must-NOT did not happen; the missing enforcement cannot be silently passed (ADR-550 D4 fail-closed)."
---

# Phase 4: Swift Failure Signatures Verification Report

**Phase Goal:** Known-unfixable scip-swift failures degrade automatically with no opt-in — matching the Kotlin/AGP pattern — using signatures captured from the pinned binary's real stderr
**Verified:** 2026-08-23T02:27:00Z
**Status:** human_needed
**Re-verification:** No — initial verification (no previous VERIFICATION.md found)

## Goal Achievement

### Observable Truths

Roadmap SCs 1-3 merged with the plan's 7 must-have truths (probe-derived truths included; no scope reduction — plan truths strictly refine the SCs).

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC1-A: no-build-system carrier degrades to search-only automatically, registry row reports search-only / search_only / origin signature / reason verbatim | ✓ VERIFIED | `test_swift_no_build_system_signature_degrades_search_only` passed (verifier run); **live smoke**: fresh repo copy through the real pinned binary, exit 0, degrade note, `jarvis status` → status `search-only`, origin `signature`, cause = matched reason verbatim, recovery present — with no opt-in flag/env anywhere |
| 2 | SC1-B: no-IndexStore carrier degrades identically | ✓ VERIFIED | `test_swift_no_index_store_signature_degrades_search_only` passed (verifier run) — end-to-end through `index_repo` → registry with all four field assertions; carrier template byte-identical to the raw capture (modulo interpolated store path) |
| 3 | SC2: both entries embed only path-free tokens quoted verbatim from real scip-swift 0.3.0 stderr, pinned by unit tests embedding the exact captured stderr with provenance comments | ✓ VERIFIED | **Byte-verified against the raw captures** (`/tmp/jarvis-p4-capture/out/swift-nobuildsystem/stderr.txt`, `…/xcode-emptysources/stderr.txt`): every token is a byte-exact substring; test carriers template-equal the captures (path substitution only); provenance comments present in both tests; binary provenance re-confirmed (`/opt/homebrew/bin/scip-swift` sha256 `b0de7201…85a5`, `0.3.0 (swift 6.2.4)`); cross-shape scan over all 41 capture files: each token set matches ONLY its own class (plus its end-to-end jarvis carrier) — zero false-match surface |
| 4 | SC3 + empty probe: unmatched Swift failures fail hard — wrappers, empty/whitespace/stdout-only carriers all return None → raise, non-zero exit, nothing published | ✓ VERIFIED | `test_swift_generic_build_failure_wrapper_never_matches` + `test_empty_or_stdout_only_failure_carriers_never_match` passed (verifier run); **live smoke keep-hard**: broken-manifest shape → exit 1, status `failed`, origin `failed_hard`, carrier persisted, no scip index dir published |
| 5 | Adjacency probe: signature path stays separate from the phase-3 opt-in degrade path; matched signature preempts regardless of fallback flag | ✓ VERIFIED | Pre-existing `test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo` passed (verifier regression filter); consult branch (`index_cli.py:1084-1097`) byte-identical — phase diff is +17/−0 lines in one file; live degrade happened with fallback off |
| 6 | Ordering probe: first matching entry in list order wins; Swift pair mutually exclusive; Kotlin/AGP entries and matcher byte-identical | ✓ VERIFIED | `test_search_only_reason_first_listed_match_wins` passed (verifier run) — asserts both the Swift-pair composite and Kotlin-precedence-over-Swift; matcher + 3 Kotlin/AGP entries unchanged (git diff `ef070ea^..79f8af5`: 17 insertions, 0 deletions, single hunk inside the tuple) |
| 7 | Every new test runs binary-free (mocked `_run`, both version checks monkeypatched) and passes binary-free CI legs | ✓ VERIFIED | **Live PATH-stripped run**: `scip-swift` demonstrably absent from PATH (`BINARY-ABSENT`) → all 5 tests passed in 0.47s; mocks confirmed by reading both pinning tests (check_scip_version + check_scip_swift_version + `_run` + `_run_semantic_stage`); ubuntu CI leg itself is [INFERENCE] — the binary-free property is proven directly |

**Score:** 7/7 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/jarvis/index_cli.py` | two appended Swift entries in `_SEARCH_ONLY_SIGNATURES` + provenance/drift comment; append-only | ✓ VERIFIED | 5 entries total; comment block names binary 0.3.0, capture date, path-free rule, re-capture-on-bump / drift-fails-hard; diff +17/−0, single hunk — matcher, consult branch, Kotlin/AGP entries byte-identical |
| `tests/test_index_cli.py` | five pinning/negative tests | ✓ VERIFIED | All five present (lines 1950-2175), grouped after the Kotlin analog, substantive (registry-row assertions / pure-function pins) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| Swift entries | `_search_only_reason` loop | substring scan on the `_run` carrier | ✓ WIRED | `_search_only_reason(str(exc))` at `index_cli.py:1084` (unchanged consult site) |
| Signature consult branch | `registry.py` ORIGIN_SIGNATURE upsert | `upsert(..., search_only=True, status_origin=ORIGIN_SIGNATURE, status_reason=reason)` | ✓ WIRED | `index_cli.py:1097`; proven live (status row shows origin `signature`, cause = matched reason) |
| Pinning tests | `_SEARCH_ONLY_SIGNATURES` constant | token-tuple tripwire lookup | ✓ WIRED | `tokens == ("Could not detect a build system", …)` in both pinning tests + ordering test — token drift fails loudly (StopIteration) |
| New Swift tests | `check_scip_swift_version` gate | monkeypatch `lambda: None` | ✓ WIRED | Present in both pinning tests (hermetic; proven by the PATH-stripped run) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `index_cli.py` signature path | `reason` | `_SEARCH_ONLY_SIGNATURES` → `_search_only_reason(carrier)` → `registry.upsert(status_reason=reason)` → `jarvis status` cause | Yes — traced to rendered output live | ✓ FLOWING |
| Pinning tests' `status_reason` | entry reason | real registry row read back via `Registry.get(slug)` | Yes | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Five new phase-4 tests | `uv run python -m pytest tests/test_index_cli.py -k "<the five>"` | 5 passed in 0.54s | ✓ PASS |
| Regression filter (Kotlin pin, preempt/adjacency, scip_swift_version gates) | `… -k "signature or scip_swift_version or preempt" -m "not integration"` | 13 passed | ✓ PASS |
| Full unit gate | `uv run python -m pytest -m "not integration" -q` | **638 passed, 17 deselected** (exactly plan's 633+5) | ✓ PASS |
| Binary-free execution | PATH stripped of `/opt/homebrew/bin` (`command -v scip-swift` → absent) | 5 passed | ✓ PASS |
| Token byte-verification | Python substring check vs raw capture files | all tokens byte-exact; carrier templates equal | ✓ PASS |
| False-match surface | cross-shape scan, all 41 capture files | each token set hits only its own class | ✓ PASS |
| Live smoke — degrade (no opt-in) | `jarvis index` fresh no-build-system copy, scratch `JARVIS_DATA_DIR`, real binary | exit 0; `note: … published search-only`; status `search-only` / origin `signature` / cause verbatim / recovery present | ✓ PASS |
| Live smoke — keep-hard (SC3) | `jarvis index` fresh broken-manifest copy | exit 1; status `failed` / origin `failed_hard`; full carrier persisted; no scip index dir published | ✓ PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` probes exist for this phase. The plan's probe-derived truths (adjacency / empty-carrier / ordering) are pinned as named unit tests, all executed and passed above (Step 7b), not merely declared.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SWFT-04 | 04-01 | Verified scip-swift failure signatures join `_SEARCH_ONLY_SIGNATURES`, captured from the pinned binary's real stderr | ✓ SATISFIED | Two entries byte-verified against raw captures; automatic degrade + keep-hard both proven live and unit-pinned |

No orphaned requirements: ROADMAP Phase 4 declares SWFT-04 only; the plan declares exactly SWFT-04. (REQUIREMENTS.md already marks SWFT-04 Complete — this report independently confirms the substance behind that mark.)

### Prohibition Dispositions (ADR-550)

| # | Prohibition | Tier | Enforcement evidence | Disposition |
|---|-------------|------|---------------------|-------------|
| P1 | Generic wrappers (`'swift build' failed with exit code 1:`, `'xcodebuild' failed with exit code 65:`) must never match | test | `test_swift_generic_build_failure_wrapper_never_matches` (both wrappers + 5 keep-hard lines → None) + empty-carrier test — passed in verifier run; independent cross-shape scan | ✓ VERIFIED |
| P2 | No remedy prose in reason text (D-11 causes only) | test | **None** — tripwire compares persisted `status_reason` against the same constant (self-consistent; review IN-01). Verifier inspection: both reasons ARE cause-only, so the must-NOT did not happen — but nothing mechanical prevents regression | ⚠️ UNVERIFIED — flagged, human review recommended (never silently passed) |
| P3 | No regex — plain substring tokens only | test | Token-tuple tripwires pin the exact plain strings (regex-ifying a token breaks the lookup); matcher byte-identical `all(token in output …)` (outside the phase's +17/−0 diff) | ✓ VERIFIED |
| P4 | Kotlin/AGP entries, matcher, phase-3 degrade gate untouched | test | Kotlin pinning test + preempt test passed (verifier run); git diff proves 17 insertions / 0 deletions in one file | ✓ VERIFIED |
| P5 | No scip-swift binary requirement in unit tests | test | Mocks in both pinning tests; live PATH-stripped run with binary absent → 5 passed | ✓ VERIFIED |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | — | No TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER markers, no stubs, no empty implementations in either modified file | — | — |

Review IN-01 (reason text has no mechanical pin — same finding as P2 above) and IN-02 (comment block could document the forget-escape for the permanent search-only consequence) are recorded Info-tier observations, not defects.

### Human Verification Required

### 1. Prohibition P2 — reason text must stay cause-only (no mechanical pin)

**Test:** Inspect the two Swift reasons in `_SEARCH_ONLY_SIGNATURES` (`src/jarvis/index_cli.py:135-142`) and confirm cause-only prose; decide whether to accept the documented prose-drift risk or add reason-literal assertions (review IN-01's optional fix: assert the full reason literal in each pinning test).
**Expected:** Reasons remain causes only (no remedy/recovery wording); either accept the risk or convert this prohibition to a mechanical pin.
**Why human:** Declared `verification: test`, but no wired enforcement exists — the tripwire is self-consistent by construction. Verifier inspection confirms both current reasons are cause-only (the must-NOT did not happen); per the fail-closed rule the missing enforcement is flagged rather than silently passed.

### Gaps Summary

No gaps. All 7 truths verified with behavioral evidence (unit + live smoke), all artifacts present/substantive/wired with real data flow, all key links wired, requirement SWFT-04 satisfied, no anti-patterns. The single open item is the flagged P2 prohibition (enforcement gap on a factually-satisfied must-NOT), which requires a human accept-or-harden decision — hence `human_needed`, not `passed`.

---

_Verified: 2026-08-23T02:27:00Z_
_Verifier: Claude (gsd-verifier)_
