---
phase: 05-semantic-install-onboarding
verified: 2026-08-23T05:55:13Z
status: human_needed
score: 13/13 must-haves verified
behavior_unverified: 0
overrides_applied: 0
human_verification:
  - test: "Live y-consent leg of the D5 smoke (the only D5 leg not exercised in this verification): at a real terminal, in a base-install venv (`uv venv /tmp/p5-venv && uv pip install --python /tmp/p5-venv/bin/python -e .`), run `JARVIS_DATA_DIR=/tmp/p5-live/data /tmp/p5-venv/bin/jarvis index <small repo>` and answer `y` at the prompt"
    expected: "uv pip install --python <venv python> jarvis-mcp[semantic] runs for real (network download — torch-scale, may take minutes), and the SAME invocation completes with semantic search enabled (semantic stage runs, `jarvis status <slug>` shows a semantic timestamp, no second command)"
    why_human: "Real network install of the PyPI distribution plus a first-run sentence-transformers model download — deliberately not triggered by an automated verifier; CI cannot exercise the missing-extra branch honestly (CI installs --extra semantic, 05-RESEARCH Pitfall 5). Recipe: 05-VALIDATION.md Manual-Only Verifications. Note: every other D5 leg WAS exercised live in this verification (see Behavioral Spot-Checks) — prompt at a real PTY with the extra genuinely absent, Enter=decline remembered, declined re-run silent, piped run silent with the skip hint)."
---

# Phase 5: Semantic Install Onboarding Verification Report

**Phase Goal:** Interactive `jarvis index` makes the semantic extra discoverable — offered once per repo at a TTY, installed on consent, remembered when declined — while watch and MCP paths stay silent and non-blocking
**Verified:** 2026-08-23T05:55:13Z
**Status:** human_needed (13/13 truths verified; 1 designed-manual UAT item remains)
**Re-verification:** No — initial verification (no prior *-VERIFICATION.md in the phase dir)

## Goal Achievement

### Observable Truths

Roadmap SCs (the contract) are truths 1–3; plan must-have truths 4–13 extend them. No scope reduction: the plan's 10 truths fully cover the 3 SCs.

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | **SC1**: On a TTY, `jarvis index` for an extra-missing repo prompts y/N; yes installs `jarvis-mcp[semantic]` and the index completes with semantic enabled | ✓ VERIFIED | Offer block `index_cli.py:1329-1386` (post-`indexed <slug>`, four gates, exact prompt, yes-branch: `_install_semantic_extra` → `importlib.invalidate_caches()` → `_run_semantic_stage(..., tuple(entry.semantic_include))` → `mark_semantic_indexed`); `test_cmd_index_offer_accept_parse_table` + `test_cmd_index_offer_yes_installs_and_enables_semantic_same_invocation` + `test_install_semantic_extra_argv_and_failure_paths` pass (fixed argv, real helper); **live PTY run**: prompt fired with extra genuinely absent in a base-install venv |
| 2 | **SC2**: Answering no is remembered per-repo — later indexes not prompted, other repos still offered | ✓ VERIFIED | Decline branch persists via `set_semantic_declined` (`:1382-1385`); `test_cmd_index_tty_offer_not_repeated_for_a_declined_repo`, `test_cmd_index_tty_offer_still_made_for_a_different_repo`, `test_semantic_declined_roundtrip_and_preservation` pass; **live**: RUN1 (PTY, Enter) → registry row `semantic_declined=1`; RUN2 (PTY re-index) → rc 0, zero prompts |
| 3 | **SC3**: Non-TTY paths (watch, MCP reindex) never prompt or block stdin; semantic silently skipped with the existing stderr hint | ✓ VERIFIED | Structural: `offer_semantic` set ONLY on the index subparser (`:1688`); `_cmd_reindex` synthetic Namespace (`:1468`) omits it; `_cmd_watch._reindex` calls `index_repo` directly (`:1600`); the only `input()` in `src/jarvis/` sits inside the offer block; both-stream isatty gate (`:756`). Tests `test_cmd_index_non_tty_never_prompts_or_blocks`, `test_offer_semantic_defaults_true_only_on_index_subparser`, `test_cmd_reindex_never_offers_semantic_install`, `test_cmd_watch_reindex_never_prompts_even_at_a_tty` pass; hint pin `test_semantic_stage_skips_cleanly_when_extra_missing` green in the full suite; **live**: RUN3 (`stdin=/dev/null`, piped stdout) → no prompt, existing one-line skip hint printed verbatim |
| 4 | SC1-offer: prompt appears post-publish, exact text, only from the `index` path | ✓ VERIFIED | Placement between `print(f"indexed {slug}")` and `return 0` (`:1330-1332`); tracer test pins `prompts == ["Install semantic search support for this repo? [y/N] "]` byte-exact incl. trailing space (`tests/test_index_cli.py:4257`); gate 1 is `getattr(args, "offer_semantic", False)` |
| 5 | SC1-consent: y/Y/yes runs `uv pip install --python <sys.executable> jarvis-mcp[semantic]` (which-resolved, fixed argv, no shell); same invocation re-runs the stage after `invalidate_caches` and stamps `mark_semantic_indexed` | ✓ VERIFIED | `_install_semantic_extra` (`:759-781`): `shutil.which("uv")`, list argv `[uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"]`, `timeout=600`; argv-exactness pinned by `test_install_semantic_extra_argv_and_failure_paths` driving the REAL helper; enablement chain asserted call-by-call in `test_cmd_index_offer_yes_installs_and_enables_semantic_same_invocation` |
| 6 | SC1-failure: install failure (uv absent / non-zero exit / timeout) warns ONE stderr line naming the command, rc 0, decline NOT remembered | ✓ VERIFIED | Warn branch `:1374-1380` (single print, names `tried: uv pip install ...`, no bit); `test_cmd_index_offer_install_failure_warns_and_does_not_remember_decline` covers all three shapes; exact-count assertion `tests/test_index_cli.py:4499` pins the warning to exactly 1 line |
| 7 | SC2-memory: no/Enter/garbage or EOF/Ctrl-C persists `semantic_declined=1` per-repo, no traceback | ✓ VERIFIED | Decline branch + `except (EOFError, KeyboardInterrupt, UnicodeDecodeError) → answer = ""` (`:1350-1358`); `test_cmd_index_offer_eof_or_keyboard_interrupt_at_prompt_declines_remembered` loops all three exceptions; **live**: RUN1 persisted the bit via real Enter at a real PTY, no traceback |
| 8 | SC2-death: memory dies only with the row (`forget`), moot once extra exists anywhere (extra-missing gate precedes decline gate) | ✓ VERIFIED | Gate order in `_cmd_index`: `_semantic_extra_missing()` (`:1341`) evaluated BEFORE the registry decline read (`:1346-1347`); `registry.forget` is the pre-existing DELETE (no new wiring); `test_semantic_declined_roundtrip_and_preservation` forget leg + `test_cmd_index_no_prompt_when_extra_already_installed` pass |
| 9 | SC2-include: explicit `--semantic-include` on a declined repo runs the stage as given, bit not cleared | ✓ VERIFIED | Offer block never touches the include flag path; `test_cmd_index_semantic_include_runs_on_declined_repo_without_clearing_bit` asserts stage called with `("src/",)` and bit still True |
| 10 | SC3-silence: offer structurally reachable only from `_cmd_index` when the index subparser set `offer_semantic=True`; both-stream isatty (stdin-tty-stdout-piped edge pinned) | ✓ VERIFIED | See truth 3; `test_at_interactive_tty_requires_both_streams_tty` pins all four isatty combinations |
| 11 | SC3-hint: non-TTY runs keep the exact one-line semantic-skip stderr hint byte-identical | ✓ VERIFIED | Hint pin `test_semantic_stage_skips_cleanly_when_extra_missing` green untouched in the full suite; **live**: RUN3 stderr carried the hint verbatim |
| 12 | Extra already installed: no prompt ever, decline bit never written | ✓ VERIFIED | Gate 2 precedes registry open (no write path reachable when extra present); `test_cmd_index_no_prompt_when_extra_already_installed` asserts no prompt + `semantic_declined is False` |
| 13 | Registry safety: `semantic_declined` written only by `set_semantic_declined`; upsert/record_failure provably never NULL-reset it; pre-phase-5 DB migrates in place | ✓ VERIFIED | `registry.py`: column via `_ensure_column` (`:202`), field (`:115`), unpack-last with None→False (`:140`), SELECT appends (`:311`, `:321`), dedicated setter (`:357-371`); `upsert` INSERT/ON CONFLICT lists and `record_failure` lists contain NO `semantic_declined` (read verbatim); `_SCHEMA` unchanged (additive only); migration + preservation pinned by both registry tests |

**Score:** 13/13 truths verified (0 present-but-behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/jarvis/registry.py` | additive column, field, SELECT appends, `set_semantic_declined` | ✓ VERIFIED | All present (see truth 13); `_SCHEMA`/`upsert`/`record_failure` untouched by the column |
| `src/jarvis/index_cli.py` | 3 offer helpers + `offer_semantic` default + post-publish offer block | ✓ VERIFIED | Helpers at `:737-781` with "Isolated for tests to monkeypatch" docstrings; `import importlib.util` in stdlib block; one `set_defaults(offer_semantic=True)` line at `:1688`; offer block `:1329-1386` |
| `tests/test_registry.py` | migration + roundtrip/preservation/row-death tests | ✓ VERIFIED | Both exist (`:637`, `:680`) and pass |
| `tests/test_index_cli.py` | 16 planned offer/gate/silence tests | ✓ VERIFIED (+1 review-fix test) | 16 planned + `test_install_semantic_extra_swallows_spawn_and_decode_failures` (WR-02) = 17; all pass |

All artifacts: exists ✓, substantive ✓ (no stubs/debt markers — anti-pattern scan clean), wired ✓ (helpers called from `_cmd_index`; tests import/drive real seams).

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `build_parser` index subparser | `_cmd_index` gate 1 | `set_defaults(offer_semantic=True)` + `getattr(args, "offer_semantic", False)` | ✓ WIRED | `:1688` / `:1340`; only `index` subparser carries the attr (all `set_defaults` sites enumerated) |
| `_cmd_index` offer block | registry (get / set_semantic_declined / mark_semantic_indexed) | short-lived Registry, try/finally close | ✓ WIRED | `:1344-1348`, `:1365-1371`, `:1381-1385` — the `_cmd_list` pattern |
| offer yes-branch | `_install_semantic_extra` → `invalidate_caches` → `_run_semantic_stage` | sequential same-invocation chain | ✓ WIRED | `:1361-1365`; stage receives the row's persisted include tuple |
| offer tests | 3 module seams + `builtins.input` | monkeypatch by full path | ✓ WIRED | `_force_offer_seams` / `_offer_run` / `_script_input` helpers centralize it; `builtins.input` monkeypatched (no real stdin — pytest would fail it) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| offer gate 4 | `entry.semantic_declined` | `registry.get(slug)` SELECT incl. the new column | Yes — bit written by `set_semantic_declined`, read back by gate 4 | ✓ FLOWING |
| yes-branch include | `tuple(entry.semantic_include)` | same registry row | Yes | ✓ FLOWING |
| prompt answer | `answer` | real `input()` (live PTY run consumed a real keystroke) | Yes | ✓ FLOWING |

No static returns, no hardcoded data, no mocks in production paths.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full unit gate (CI parity) | `uv run python -m pytest -m "not integration" -q` | **657 passed, 17 deselected** (46.6s) | ✓ PASS |
| All 19 phase-5 tests by name (18 planned + WR-02 regression) | `uv run python -m pytest <19 node ids> -q` | **19 passed** (9.4s) | ✓ PASS |
| WR-02 fix load-bearing | narrow `_install_semantic_extra` catch to `TimeoutExpired`-only → run its regression | **FAILED with FileNotFoundError** (fix reverted via git checkout, tree clean) | ✓ PASS (mutation caught) |
| WR-01 fix load-bearing | narrow prompt catch to `(EOFError, KeyboardInterrupt)` → run its regression | **FAILED with UnicodeDecodeError** (reverted, tree clean) | ✓ PASS (mutation caught) |
| **Live D5 leg 1** — prompt at a real PTY, extra genuinely absent | base venv (`uv pip install -e .`, lancedb + sentence_transformers specs confirmed None) → pty driver, `jarvis index <git repo>`, Enter | rc 0; prompt text appeared; registry row `('p5-verify-repo','indexed',1,None)`; no traceback | ✓ PASS |
| **Live D5 leg 2** — declined repo never re-prompted at a TTY | second pty run, same repo/data dir | rc 0, **zero prompts** | ✓ PASS |
| **Live D5 leg 3** — non-TTY never prompts, hint preserved | `jarvis index` with `stdin=/dev/null`, piped stdout | rc 0, no prompt; stderr carries the existing one-line semantic-skip hint verbatim | ✓ PASS |

Smoke artifacts (venv/repo/data/script under /tmp) cleaned up after the runs.

### Probe Execution

No `scripts/*/tests/probe-*.sh` probes declared or conventional — SKIPPED (not a probe phase; the plan's `<automated>` commands were run directly above).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SEMA-01 | 05-01-PLAN | TTY `jarvis index` with extra missing offers install (y/N), auto-installs on yes, remembers decline per-repo | ✓ SATISFIED | Truths 1, 2, 4–8, 12, 13 |
| SEMA-02 | 05-01-PLAN | Non-TTY paths (watch, MCP reindex) never prompt — silent skip + stderr hint preserved | ✓ SATISFIED | Truths 3, 10, 11 |

Orphan check: REQUIREMENTS.md traceability maps exactly SEMA-01 and SEMA-02 to Phase 5 — both claimed by the plan, no orphans.

### Prohibition Verification (must_haves.prohibitions — all test-tier, enforcement wired)

| Prohibition | Enforcement Evidence | Disposition |
|-------------|---------------------|-------------|
| No prompting/blocking outside the `index` TTY path (watch/reindex/MCP/either-stream-redirected) | 4 named silence tests pass; structural code reading confirms `input()` exists nowhere else in the package | ✓ ENFORCED |
| No decline remembered from a failed install | `test_cmd_index_offer_install_failure_warns_and_does_not_remember_decline` (3 failure shapes) passes | ✓ ENFORCED |
| upsert/record_failure never reset the decline bit | Both column lists read verbatim — column absent; roundtrip-preservation test passes | ✓ ENFORCED |
| No remedy prose on stderr — exactly one warning line | Exact-count assertion `tests/test_index_cli.py:4499` (`== 1`) passes; hint pin untouched | ✓ ENFORCED |
| No subprocess/network unless y/Y/yes answered | AssertionError-raising install monkeypatches in non-TTY + EOF tests never trip; helper invoked only inside the yes-branch | ✓ ENFORCED |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | none (no TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER, no empty-implementation returns, no console-only stubs across the 4 phase files) | — | — |

Commits verified on `gsd/v1.0-milestone`: `c69f862` (RED tests) → `ad783eb` (feat, registry.py +31/−3, index_cli.py +96) → `5ceb108` → `d2a38c2` → `f7c21df`/`3f978fe` (review fixes). TDD RED→GREEN ordering holds for Task 1; Task 2's pass-immediately case was plan-anticipated and mutation-strengthened (documented in SUMMARY, not a gap).

### Human Verification Required

One item — the designed D5 manual leg (see frontmatter `human_verification`). Every other D5 leg was exercised live in this verification (PT prompt with genuinely-absent extra, decline memory, silent re-run, piped silence + hint); the remaining leg is the real-network uv install with a real `y` and same-invocation semantic completion. All automated checks passed.

### Gaps Summary

None. All 13 truths verified with behavioral evidence (19 named tests, full suite 657 green, two mutation checks proving the review fixes load-bearing, and a live PTY smoke covering the prompt/decline/silence legs). The phase goal is achieved in the codebase; the only outstanding step is the one designed-manual UAT item (live `y` install over the network), after which SEMA-01/SEMA-02 close.

---

_Verified: 2026-08-23T05:55:13Z_
_Verifier: Claude (gsd-verifier)_
