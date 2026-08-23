---
phase: 05-semantic-install-onboarding
plan: "01"
subsystem: cli
tags: [tty-gating, argparse, sqlite, subprocess, uv, onboarding, semantic-search]

requires:
  - phase: 01-registry-foundation-degradation-reporting
    provides: the repos registry table, _ensure_column migration mechanism, record_failure
  - phase: 03-opt-in-self-healing-fallback
    provides: the fallback_enabled additive-column precedent (nullable INTEGER + dedicated setter outside upsert) mirrored here
provides:
  - Per-repo semantic_declined decline-memory column + set_semantic_declined setter (additive, migration-safe onto phase-3-era databases)
  - TTY-gated one-shot y/N install offer in `jarvis index` (post-publish) that installs jarvis-mcp[semantic] via uv and enables semantic search in the same invocation
  - offer_semantic argparse default — the structural SEMA-02 gate making watch/reindex/MCP prompt-free
  - Module-level test seams _semantic_extra_missing / _at_interactive_tty / _install_semantic_extra
affects: [verify-work UAT (live TTY smoke), milestone close, any future MCP-triggered reindex tooling]

actuals:
  tokens: 9928   # chars/4 over the realized diff (39,712 chars across 4 files)
  tasks: 3
  commits: 4

tech-stack:
  added: []   # stdlib only: importlib.util, shutil.which, subprocess.run — no new dependencies
  patterns:
    - "argparse set_defaults as a structural reachability gate (offer_semantic on the index subparser only)"
    - "Module-level 'Isolated for tests to monkeypatch' seams for environment-dependent facts (extra presence, TTY, uv)"
    - "Dedicated UPDATE setter outside upsert's column lists — the tracked_files/fallback_enabled NULL-reset trap avoided a third time"

key-files:
  created: []
  modified:
    - src/jarvis/registry.py
    - src/jarvis/index_cli.py
    - tests/test_registry.py
    - tests/test_index_cli.py

key-decisions:
  - "Decline memory is a plain bool (NULL reads False) — no tri-state, unlike fallback_enabled there is no precedence chain (plan Artifacts table)"
  - "The offer lives post-publish in _cmd_index, never inside index_repo/_run_semantic_stage — the CLI layer owns stdin; the offer also fires after search-only/degraded publishes (05-RESEARCH Pattern 2)"
  - "Install spec unpinned per lock: jarvis-mcp[semantic] verbatim, no ==<version> (planner decision 2); uv subprocess timeout=600s with TimeoutExpired caught into the same warn+continue path (planner decision 3)"
  - "Reindex never offers — the synthetic Namespace omits offer_semantic (planner decision 1); index-only per SEMA-01's literal scope"
  - "Detection set is exactly {lancedb, sentence_transformers} (the stage's own lazy imports); tree_sitter_language_pack deliberately excluded — chunker falls back, never disables semantic"

patterns-established:
  - "Four-gate offer predicate ordered cheapest/most-structural first: offer_semantic → extra-missing → both-stream TTY → not-declined"
  - "Three-outcome prompt contract: yes+success (install, no bit), yes+failure (one-line warn, no bit), no/EOF/Ctrl-C (bit)"
  - "Pipeline-vs-offer stage-call distinction in CLI-driven tests: ordering-based recorders (first call per slug is the pipeline's in-run stage)"

requirements-completed: [SEMA-01, SEMA-02]

coverage:
  - id: D1
    description: "semantic_declined registry column with dedicated setter — additive migration onto phase-3-era databases, provably never reset by upsert/record_failure, dies with the row via forget"
    requirement: SEMA-01
    verification:
      - kind: unit
        ref: "tests/test_registry.py#test_semantic_declined_column_migrates_onto_an_existing_database"
        status: pass
      - kind: unit
        ref: "tests/test_registry.py#test_semantic_declined_roundtrip_and_preservation"
        status: pass
    human_judgment: false
  - id: D2
    description: "TTY offer with per-repo decline memory — exact locked prompt text, once per repo, other repos still offered, no traceback on EOF/Ctrl-C"
    requirement: SEMA-01
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_tty_offer_decline_answer_persists_semantic_declined"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_tty_offer_not_repeated_for_a_declined_repo"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_tty_offer_still_made_for_a_different_repo"
        status: pass
    human_judgment: false
  - id: D3
    description: "Consent path — locked parse table, fixed-argv uv install (timeout=600), same-invocation enablement (invalidate_caches → stage re-run with row prefixes → mark_semantic_indexed), failure never remembered as decline"
    requirement: SEMA-01
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_offer_accept_parse_table"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_offer_yes_installs_and_enables_semantic_same_invocation"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_offer_install_failure_warns_and_does_not_remember_decline"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_install_semantic_extra_argv_and_failure_paths"
        status: pass
    human_judgment: false
  - id: D4
    description: "SEMA-02 silence — non-TTY, watch (even at a forced TTY), reindex delegation, and parser-level offer_semantic gating never prompt or block stdin; extra-present and --semantic-include lock edges hold"
    requirement: SEMA-02
    verification:
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_non_tty_never_prompts_or_blocks"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_watch_reindex_never_prompts_even_at_a_tty"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_reindex_never_offers_semantic_install"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_offer_semantic_defaults_true_only_on_index_subparser"
        status: pass
      - kind: unit
        ref: "tests/test_index_cli.py#test_cmd_index_semantic_include_runs_on_declined_repo_without_clearing_bit"
        status: pass
    human_judgment: false
  - id: D5
    description: "Live TTY smoke: real `jarvis index` at a terminal with the extra genuinely absent — prompt appears, y installs via real uv and completes semantic in the same run, n remembered, piped run never prompts"
    requirement: SEMA-01
    verification: []
    human_judgment: true
    rationale: "CI installs --extra semantic so the missing-extra branch cannot be exercised honestly there, and a real TTY cannot be faked (05-RESEARCH Pitfall 5); manual procedure is recorded in 05-VALIDATION.md Manual-Only Verifications"

duration: 19min
completed: 2026-08-23
status: complete
---

# Phase 5 Plan 01: Semantic Install Onboarding Summary

**TTY-gated `jarvis index` install offer (locked uv command, same-invocation semantic enablement) with a per-repo `semantic_declined` registry column — while watch/reindex/MCP/non-TTY paths stay provably prompt-free via an `offer_semantic` argparse gate.**

## Performance

- **Duration:** 19 min
- **Started:** 2026-08-23T05:07:43Z
- **Completed:** 2026-08-23T05:26:27Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- Additive `semantic_declined` column + `set_semantic_declined` setter mirroring the `fallback_enabled` precedent — migration-safe onto phase-3-era databases, provably never NULL-reset by `upsert`/`record_failure`, dies with the row via `jarvis forget`
- Post-publish four-gate offer in `_cmd_index` with the locked prompt `Install semantic search support for this repo? [y/N] `: consent installs `jarvis-mcp[semantic]` via fixed-argv uv (timeout 600s) and re-enables semantic in the same invocation; failure warns once and re-offers next time; no/EOF/Ctrl-C persists the per-repo decline
- SEMA-02 proven structurally: `offer_semantic` set only on the `index` subparser; reindex's synthetic Namespace, watch's direct `index_repo` call, MCP, and either-stream-redirected runs can never prompt (pinned by 8 tests, including watch with isatty forced True)
- 18 new tests (2 registry + 16 CLI); full unit suite 656 passed / 17 deselected (measured 638 baseline + 18)

## Task Commits

Each task was committed atomically:

1. **Task 1 (tracer): decline-memory slice** — `c69f862` (test, RED: 5 failed) → `ad783eb` (feat, GREEN: 5 passed, quick suite 236)
2. **Task 2: consent path** — `5ceb108` (test; passed immediately — see TDD Gate Compliance; quick suite 241)
3. **Task 3: SEMA-02 silence pins** — `d2a38c2` (test, 8 passed; quick suite 249)

**Plan metadata:** see final docs commit below.

## Files Created/Modified
- `src/jarvis/registry.py` — `semantic_declined` column via `_ensure_column`, `RegisteredRepo.semantic_declined` field, last-position `_row_to_repo` unpack, `get()`/`list()` SELECT appends, `set_semantic_declined` setter; `upsert`/`record_failure`/`_SCHEMA` byte-identical
- `src/jarvis/index_cli.py` — `import importlib.util`; three module-level seams above `_run_semantic_stage`; `index_parser.set_defaults(offer_semantic=True)`; the post-publish offer block in `_cmd_index`
- `tests/test_registry.py` — migration-onto-legacy-db + roundtrip/preservation/row-death tests
- `tests/test_index_cli.py` — Phase 5 section: shared offer-test helpers + 16 tests (offer matrix, consent, failure, silence pins, lock edges)

## Decisions Made
- Followed the plan's three planner decisions verbatim: reindex never offers (synthetic Namespace omits the attr); install spec unpinned (`jarvis-mcp[semantic]` exact); uv timeout 600s with `TimeoutExpired` caught into warn+continue
- The offer fires after search-only/degraded publishes too (a `_cmd_index` rc-0 consequence blessed by 05-RESEARCH Pattern 2) — those repos are precisely where semanticSearch matters
- Reindex-silence pin seeds its never-declined row via a non-TTY `_cmd_index` run (of the two seeding options the plan offered), so the pin proves the structural gate rather than accidentally leaning on the declined-bit gate
- No `--semantic-include`-on-declined-repo clearing path was added — the plan/research lock says the bit is moot once the extra exists (find_spec gate precedes the decline gate); dead code avoided

## Deviations from Plan

### Auto-fixed Issues

None — no Rule 1-4 deviations were triggered; no production bugs were found (production diff matched the plan on first implementation).

### Plan-noted shape deviations (documented, not weakening)

**1. Task 2's RED run passed immediately (plan-anticipated)**
- **Found during:** Task 2 RED step
- **Issue:** The plan's Task 1 action text specified the FULL three-outcome consent branch (install → invalidate_caches → stage re-run → mark; one-line warn on failure, no bit), so Task 2's five tests passed against Task 1's GREEN state — no failing RED was possible without deleting already-specified behavior
- **Resolution (per the plan's own instruction — "verify it is testing the specified behavior, then note it in the SUMMARY rather than weakening the test"):** strength verified by five mutation checks against the source, each caught: accept-set shrink (`y` only), EOF-catch narrowing (KeyboardInterrupt escapes), failure-warning rewrite (command name dropped), timeout kwarg removal, decline-bit-on-failure insertion. Commit message uses the honest `pin ...` wording instead of the plan's `add failing ...` template
- **Consequence:** the plan's separate Task 2 `feat(05-01)` commit is subsumed by Task 1's `ad783eb` (an empty commit would be ceremony); recorded under TDD Gate Compliance

**2. CLI test placement: end-of-file section instead of the literal "~3648" anchor**
- **Found during:** Task 1 RED
- **Issue:** the plan's `~3648` anchor precedes the Phase 3 section marker AND the `_mock_healthy_full_run` helper the new tests use; wedging 16 tests there would split the related WR-03 regression pair
- **Resolution:** appended a clearly-marked `# --- Phase 5: semantic install onboarding (SEMA-01/02) ---` section at end-of-file (the file's established phase-section convention); tests remain contiguous in plan order (3 + 5 + 8). Cosmetic only — no behavioral difference

---

**Total deviations:** 2 plan-noted shape deviations, 0 auto-fixes
**Impact on plan:** None on behavior — all acceptance criteria met as specified; both notes are commit/placement bookkeeping the plan itself half-anticipated

## TDD Gate Compliance

| Task | RED gate | GREEN gate | Notes |
|------|----------|------------|-------|
| 1 (tracer) | ✅ `test(05-01)` c69f862 — recorded run: 5 failed (AttributeError: missing column / missing field / missing module seam) | ✅ `feat(05-01)` ad783eb — 5 passed, quick suite 236/12 | Tracer feedback gate (auto mode): `<verify>` re-run end-to-end after commit — 5 passed |
| 2 | ✅ `test(05-01)` 5ceb108 — run recorded as PASSING immediately (plan-anticipated; Task 1's action specified the full branch) | ⚠️ no separate feat commit — subsumed by ad783eb; "complete/verify" satisfied with zero production edits + 5 mutation checks | Strength proven: each mutation caught (accept-set, EOF-catch, warning text, timeout, decline-on-failure) |
| 3 | n/a (pin tests — designed to pass immediately; both initial failures were test-authoring bugs, fixed before commit) | ✅ `test(05-01)` d2a38c2 — 8 passed, quick suite 249/12 | Plan: "any failure is a real bug to fix before commit, not a test to weaken" — failures were in the tests themselves (fixture duplication), not production |

## Phase-Level Verification Results

1. Quick suite: `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration" -q` → **249 passed, 12 deselected** (231 measured baseline + 18 new) ✅
2. Full CI gate: `uv run pytest -m "not integration" -rs` → **656 passed, 17 deselected** (638 baseline + 18 new) ✅
3. Named regression set: `test_semantic_stage_skips_cleanly_when_extra_missing`, `test_fallback_enabled_tri_state_roundtrip`, `test_fallback_enabled_column_migrates_onto_an_existing_database`, `test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo` → **4 passed, untouched** ✅
4. Prohibition enforcement (each maps to a named failing-if-violated test): prompt-free non-index paths (T3 tests 1/3/4/5), no-decline-on-failure (T2 test 3), no upsert/record_failure reset (T1 registry test 2), one-line warning discipline (T2 test 3's exact-count assertion), no subprocess off the consent path (T2 test 4 + T3 test 1 AssertionError recorders) ✅
5. Live TTY smoke deliberately NOT automated — CI installs `--extra semantic` (05-RESEARCH Pitfall 5); it is the phase's manual/UAT item, recorded in 05-VALIDATION.md (coverage D5, human_judgment: true)

## Issues Encountered
- Task 2/3 test authoring, fixed in the tests (no production impact): (a) macOS case-insensitive filesystem made `accept-y`/`accept-Y` fixture dirs collide — switched to index-based slugs; (b) the recorders initially captured the pipeline's own in-run `_run_semantic_stage` call (which also passes root=None when CLI-driven, and stamps the row on True) — recorders now distinguish by call order per slug, keeping the pipeline call at the healthy-mock False so only the offer's re-run can stamp `semantic_indexed_at`

## User Setup Required

None — no external service configuration. (The live TTY smoke in 05-VALIDATION.md is a UAT item, not user setup.)

## Next Phase Readiness
- SEMA-01 and SEMA-02 are code-complete and unit-pinned; this was the milestone's final plan (5 of 5 phases, 11/11 plans)
- Remaining before milestone close: `/gsd-verify-work` (UAT — including the live TTY smoke of coverage D5), then transition/milestone completion
- No blockers; no known stubs; working tree clean

## Self-Check: PASSED

- Files exist: src/jarvis/registry.py, src/jarvis/index_cli.py, tests/test_registry.py, tests/test_index_cli.py — all FOUND (modified, committed)
- Commits found: c69f862, ad783eb, 5ceb108, d2a38c2 — all FOUND on gsd/v1.0-milestone

---
*Phase: 05-semantic-install-onboarding*
*Completed: 2026-08-23*
