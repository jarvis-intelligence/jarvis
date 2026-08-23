---
phase: 05-semantic-install-onboarding
fixed_at: 2026-08-23T07:05:00Z
review_path: .planning/phases/05-semantic-install-onboarding/05-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 5: Code Review Fix Report

**Fixed at:** 2026-08-23T07:05:00Z
**Source review:** .planning/phases/05-semantic-install-onboarding/05-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 2 (WR-01, WR-02; fix_scope=critical_warning — IN-01/IN-02 out of scope)
- Fixed: 2
- Skipped: 0

**Verification (ran in the main checkout — `workflow.use_worktrees` is `false` in this
project's config, so fixes were edited and committed directly on `gsd/v1.0-milestone`):**
- Reproduce-then-fix honored for both findings: each regression test failed pre-fix with
  the exact escaping exception, passed post-fix.
- Full unit gate: `uv run pytest -m "not integration"` → **657 passed, 17 deselected**
  (was 656 pre-fix; +1 is the new WR-02 test). All 18 original phase-5 tests green.
- Syntax checks: `ast.parse` on `src/jarvis/index_cli.py` after each fix.

## Fixed Issues

### WR-01: `input()` raises uncaught `UnicodeDecodeError` on undecodable bytes — traceback after a successful publish

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** f7c21df
**Applied fix:** Added `UnicodeDecodeError` to the prompt loop's catch
(`except (EOFError, KeyboardInterrupt, UnicodeDecodeError)` → `answer = ""`), with a
comment tying it to the locked remembered-decline contract and the parse table's
"garbage declines" rule. Regression: the existing
`test_cmd_index_offer_eof_or_keyboard_interrupt_at_prompt_declines_remembered` loop now
also feeds `UnicodeDecodeError("utf-8", b"\x80\x81", 0, 1, "invalid start byte")` —
pre-fix it propagated out of `_cmd_index` (after `indexed <slug>` had already printed);
post-fix: rc 0, prompt seen exactly once, no "Traceback" in output, `semantic_declined`
persisted, install seam never called.

### WR-02: `_install_semantic_extra` escapes `OSError`/decode failures — violates "install failure = one stderr warning + continue"

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** 3f978fe
**Applied fix:** Broadened the helper's catch to
`except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError): return False`
(timeout path unchanged) and added `errors="replace"` to the `subprocess.run` call so a
legacy-locale uv/pip emitting non-UTF-8 bytes cannot crash the captured-pipe decode at
all. Every spawn/decode failure now lands in the caller's locked one-warning + rc 0 +
decline-not-remembered path. Regression:
`test_install_semantic_extra_swallows_spawn_and_decode_failures` feeds
`FileNotFoundError` / `PermissionError` / `UnicodeDecodeError` through the
`subprocess.run` seam — pre-fix all three escaped the helper; post-fix all return False.
The pinned-argv assertion in `test_install_semantic_extra_argv_and_failure_paths` was
updated to include `errors: "replace"` (part of this finding's fix).

**Scope note:** the review's optional suggestion to also wrap the accept branch for
`KeyboardInterrupt` during the install was NOT applied — it is flagged optional in the
review and belongs with IN-01 (silent 600s install), which is out of scope for this pass.

## Skipped Issues

None.

---

_Fixed: 2026-08-23T07:05:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
