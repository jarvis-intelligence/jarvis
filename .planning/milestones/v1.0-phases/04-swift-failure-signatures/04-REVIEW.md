---
phase: 04-swift-failure-signatures
reviewed: 2026-08-23T02:13:40Z
depth: standard
files_reviewed: 2
files_reviewed_list:
  - src/jarvis/index_cli.py
  - tests/test_index_cli.py
findings:
  critical: 0
  warning: 0
  info: 2
  total: 2
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-08-23T02:13:40Z
**Depth:** standard
**Files Reviewed:** 2 (`src/jarvis/index_cli.py`, `tests/test_index_cli.py`)
**Status:** issues_found (0 critical, 0 warning, 2 info)

## Summary

The diff (`22f74c3e^..HEAD`) is exactly what the phase promised: +17 lines in
`index_cli.py` (a provenance comment block and two entries appended inside
`_SEARCH_ONLY_SIGNATURES`) and +227 lines in `test_index_cli.py` (five tests).
Nothing else in the range touches source outside these two files. Every locked
constraint was verified against independent ground truth, not just the
executor's own claims:

**Token verbatim-ness (the transcription-drift BLOCKER class) — verified
byte-exact against the raw captures, not just 04-RESEARCH.md's prose.** The
ephemeral capture workspace still exists at `/tmp/jarvis-p4-capture/out/`, so
every shipped token was programmatically checked as a byte-exact substring of
the real scip-swift 0.3.0 stderr:

| Shipped token | Ground-truth capture | Result |
|---|---|---|
| `Could not detect a build system` | `out/swift-nobuildsystem/stderr.txt` (exit=1) | verbatim substring ✓ |
| `no Package.swift and no .xcodeproj/.xcworkspace found` | same | verbatim substring ✓ |
| `Build succeeded but no IndexStore was produced` | `out/xcode-emptysources/stderr.txt` (exit=1) | verbatim substring ✓ |

A cross-shape scan over all 14 captures (including the 17KB xcodebuild log,
the `--index-only` probe whose wording is "no IndexStore was **found**", both
controls, and both live end-to-end jarvis carriers) confirms each token set
hits **only** its own failure class — zero false-match surface across every
captured shape. Tokens are path-free; the one `/` that does appear
(`.xcodeproj/.xcworkspace`) is literal error-message wording, not an
interpolated path.

**Other locked constraints:**

- **Append-only** — the diff is a single hunk inside the tuple (+17/-0); the
  Kotlin/AGP entries, `_search_only_reason`, and the consult branch at
  `index_cli.py:1081-1104` are byte-identical (diff-verified; the only repo-wide
  consumers of the constant are the matcher and its single call site).
- **Reasons are causes (D-11)** — both reasons describe why indexing is
  impossible; no remedy prose. Recovery stays the read-time per-origin mapping.
- **Substring only, no regex** — the new entries ride the unchanged
  `all(token in output ...)` loop.
- **Generic wrappers never match** — pinned by
  `test_swift_generic_build_failure_wrapper_never_matches` (both wrappers +
  all five keep-hard class lines) and re-proven live: indexing the real
  `spm-broken-manifest` capture still exits 1 with no degrade.
- **Empty/stdout-only carriers never match** — pinned by
  `test_empty_or_stdout_only_failure_carriers_never_match`, which reconstructs
  the exact `_run` carrier shape (header line embedding per-run paths, stdout,
  stderr); positive-test carriers omitting the header are safe by monotonicity
  (substring match over a superset).
- **CI-hermetic** — both pinning tests monkeypatch `check_scip_version` AND
  `check_scip_swift_version` (Pitfall 3), mock `_run`/`_run_semantic_stage`;
  the three negative tests are pure functions of `_search_only_reason`. No
  subprocess spawns scip-swift; no network.
- **First-match order pinned** — `test_search_only_reason_first_listed_match_wins`
  covers both the Swift-pair composite and Kotlin-precedence-over-Swift.
- **Separate from the phase-3 degrade path** — the consult branch is untouched;
  `test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo` still
  passes, and the live degrade below happened with no fallback opt-in.

**Test runs (this review, not copied from the summary):** targeted 6/6 passed;
`uv run pytest tests/test_index_cli.py -m "not integration" -q` → 191 passed,
12 deselected; full gate `uv run pytest -m "not integration" -q` → 638 passed,
17 deselected — both exactly matching the plan's predicted counts (186+5 file
baseline, 633+5 CI baseline). TDD commit order is correct (RED `ef070ea`/
`8ab9778` precede GREEN `550cd3c`/`f6e72b8`).

**Independent live smoke (scratch `JARVIS_DATA_DIR`, fresh copy of the capture
repo — capture originals untouched):** the no-build-system shape through the
real pinned binary degrades automatically: exit 0, stderr
`note: … cannot be SCIP-indexed — the repo has Swift sources but neither a
Package.swift nor an Xcode project…`, and `jarvis status` reports
`status: search-only`, `origin: signature`, `cause:` the matched reason
verbatim, `recovery: jarvis reindex …`. The keep-hard broken-manifest shape
still exits 1. This is the SC1/SC3 behavior end-to-end, not just under mocks.

The two Info findings below are strength-of-pinning observations, not defects:
the shipped code causes no incorrect behavior in any scenario this review
could construct or capture.

## Narrative Findings (AI reviewer)

Both findings are Info-tier (test-strength and documentation surface); no
Critical or Warning issues were found.

### IN-01: Reason text has no mechanical pin despite the plan claiming it fails loudly on drift

**File:** `tests/test_index_cli.py:2005-2012` (tripwire lookups in both pinning tests)
**Issue:** The plan's key_links assert "tests look the reason up by the exact
token tuple, so drift in tokens **or reason text** fails loudly". The token
half is true (tuple equality + carrier matching both break). The reason half
is not: `status_reason == swift_reason` compares the persisted value against
the same entry looked up from `_SEARCH_ONLY_SIGNATURES`, so it is
self-consistent by construction — any edit to the reason prose (including one
that reintroduces remedy language, violating D-11) passes the suite. The
carrier is pinned to the captured stderr but the reason is pinned to nothing.
**Fix:** Optionally assert the full reason literal in each pinning test
(mirroring the embedded-carrier philosophy), e.g.
`assert entry.status_reason == ("the repo has Swift sources but neither a "
"Package.swift nor an Xcode project, so scip-swift has no build system to run")`,
or accept prose drift and soften the plan's claim. Cosmetic-risk only; cannot
affect matching or degradation.

### IN-02: Swift provenance comment omits the permanent-consequence / forget-escape note

**File:** `src/jarvis/index_cli.py:123-130`
**Issue:** A signature match persists `search_only=True`, so a user who later
fixes the repo shape (e.g. adds a `Package.swift`) and runs `jarvis reindex`
silently gets another search-only refresh, never a SCIP retry — the only
escape is `jarvis forget` + reindex. The research (Pitfall 5) recommended
documenting this escape in the signature comment block. The shipped comment
covers capture provenance, path-free tokens, and the drift-fails-hard rule
(all the plan required), and the adjacent `_BASH_SHIM` comment documents the
forget-escape concept for its own case — but a future maintainer reading only
the Swift block won't know reindex cannot self-heal this class.
**Fix:** One sentence in the comment block, e.g. "A match persists
search_only=1 — like AGP, the escape is `jarvis forget` + reindex, not
`jarvis reindex`." Behavior is locked (D-11/Kotlin parity) and must not
change; this is a documentation-surface suggestion only.

---

_Reviewed: 2026-08-23T02:13:40Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
