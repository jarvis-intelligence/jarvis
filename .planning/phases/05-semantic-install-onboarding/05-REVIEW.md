---
phase: 05-semantic-install-onboarding
reviewed: 2026-08-23T05:48:01Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - src/jarvis/index_cli.py
  - src/jarvis/registry.py
  - tests/test_index_cli.py
  - tests/test_registry.py
findings:
  critical: 0
  warning: 0
  info: 2
  total: 2
status: issues_found
iteration: 2
---

# Phase 5: Code Review Report (Re-review, iteration 2)

**Reviewed:** 2026-08-23T05:48:01Z
**Depth:** standard
**Files Reviewed:** 4 (phase diff `ecd04d2^..HEAD`; deltas since iteration 1 are exactly the two fix commits `f7c21df` and `3f978fe` plus docs)
**Status:** issues_found — both Warnings resolved and verified; only the two previously-recorded Info findings remain (reproduced below, unchanged in severity)

## Summary

Re-review after fixes `f7c21df` (WR-01) and `3f978fe` (WR-02). Both fixes hold, both carry real regression coverage, and no new Critical or Warning issues were introduced. All locked SEMA-01/SEMA-02 constraints still hold.

**Fix verification:**

- **WR-01 (f7c21df) — VERIFIED FIXED.** `src/jarvis/index_cli.py:1352` now catches `(EOFError, KeyboardInterrupt, UnicodeDecodeError)` around the prompt, setting `answer = ""` — which falls to the decline branch and persists `semantic_declined=1` exactly per the locked "abnormal prompt input declines" contract. The catch is narrow (explicitly-named exceptions; `UnicodeDecodeError` is a `ValueError` subclass but is named individually, so no over-catch), and `.strip().lower()` cannot raise on a returned `str`, so the widened try scope is inert for normal answers. Regression: `test_cmd_index_offer_eof_or_keyboard_interrupt_at_prompt_declines_remembered` (tests/test_index_cli.py:4517) now loops over `EOFError`, `KeyboardInterrupt`, **and** `UnicodeDecodeError("utf-8", b"\x80\x81", ...)` — asserting per shape: rc 0, exactly one prompt, no "Traceback" in output, decline bit persisted, and `_install_semantic_extra` monkeypatched to raise AssertionError if ever reached. Passes.
- **WR-02 (3f978fe) — VERIFIED FIXED.** `_install_semantic_extra` (src/jarvis/index_cli.py:759-781) now (a) passes `errors="replace"` to `subprocess.run(..., text=True)` (index_cli.py:770-773), making a strict-decode `UnicodeDecodeError` from legacy-locale uv/pip output impossible in practice, and (b) catches `(subprocess.TimeoutExpired, OSError, UnicodeDecodeError)` → `return False`, so spawn failures (ENOENT on a broken interpreter after `which` said yes, TOCTOU unlink, EACCES) land in the caller's locked one-stderr-warning + rc 0 + decline-not-remembered path instead of tracebacking the just-published index. Regression: new `test_install_semantic_extra_swallows_spawn_and_decode_failures` (tests/test_index_cli.py:4612) drives the real helper with `FileNotFoundError` / `PermissionError` / `UnicodeDecodeError` through the `subprocess.run` seam — all return False (the closure-captured `exc` is asserted within its own loop iteration, so no late-binding hazard). The pinned-argv test `test_install_semantic_extra_argv_and_failure_paths` was updated to pin `errors: "replace"` in the recorded kwargs. Both pass.
- **No collateral changes.** `git diff e2999fc..HEAD` on source is exactly the two hunks above (+17 lines in index_cli.py) plus test additions and `.planning` docs; the registry.py phase diff remains the purely-additive `semantic_declined` work (column via `_ensure_column`, dataclass field, `_row_to_repo` unpack-last with the None→False guard, `get()`/`list()` SELECT appends, dedicated `set_semantic_declined` setter) — `upsert` and `record_failure` column lists remain provably free of `semantic_declined`.

**Evidence (all run in this re-review):**

- Targeted regressions: `pytest tests/test_index_cli.py::test_cmd_index_offer_eof_or_keyboard_interrupt_at_prompt_declines_remembered ::test_install_semantic_extra_swallows_spawn_and_decode_failures ::test_install_semantic_extra_argv_and_failure_paths` → **3 passed**.
- Scoped gate: `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration" -q` → **250 passed, 12 deselected** (plan's 249 + the new WR-02 test).
- Full unit gate: `uv run pytest -m "not integration" -q` → **657 passed, 17 deselected** — matches the fixer's recorded claim exactly.

**SEMA constraints re-verified against current HEAD (fixes did not disturb any gate):**

- Offer placement/gating (SEMA-01/SC1-offer, SEMA-02/SC3-silence): the offer block sits strictly after `print(f"indexed {slug}")` and after the failure `return 1`; gate order is `offer_semantic` (set only by the `index` subparser at index_cli.py:1688 — reindex's synthetic Namespace, watch's direct `index_repo` call, and MCP paths all read False via the getattr default) → `_semantic_extra_missing()` (exactly `lancedb` + `sentence_transformers`, matching the stage's lazy imports) → `_at_interactive_tty()` (both streams) → per-repo `semantic_declined` check.
- Prompt contract: exact locked prompt text; `.strip().lower() in ("y", "yes")` parse table; Enter/garbage declines; EOF/Ctrl-C/undecodable bytes → remembered decline, rc 0, no traceback.
- Install command (T-5-01/T-5-02): fixed argv `[resolved uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"]`, list-form (no shell), answer never reaches command construction, timeout 600s, no subprocess unless consented (helper invoked only inside the yes branch).
- Three-outcome contract: consent → `importlib.invalidate_caches()` → `_run_semantic_stage(Path(args.path), slug, None, tuple(entry.semantic_include))` → `mark_semantic_indexed` in the same invocation, no decline bit; install failure → exactly one stderr line naming the tried command, rc 0, no decline bit, no stage re-run (pinned by `test_cmd_index_offer_install_failure_warns_and_does_not_remember_decline` across uv-absent/non-zero-exit/timeout shapes); decline → `set_semantic_declined(slug, True)`.
- Registry memory safety (T-5-04): `semantic_declined` written only by the dedicated parameterized setter; excluded from `upsert`'s INSERT/ON CONFLICT lists and from `record_failure`'s; NULL default migrates onto legacy DBs; dies with the row via the existing `forget` DELETE. `--semantic-include` on a declined repo runs the stage and does not clear the bit; extra installed → gate 2 precedes the decline gate → never prompts, never writes.
- Prohibitions intact: no remedy-prose additions to stderr (failure warning remains exactly one line); the non-TTY semantic-skip hint pin (`test_semantic_stage_skips_cleanly_when_extra_missing`) stays green untouched.

No security findings: no shell, no injection surface (parameterized SQL, fixed argv, answer maps to a boolean branch only), no secrets, no eval. Both fixed paths eliminate the remaining traceback escapes of the "publish already succeeded" contract.

## Info

### IN-01: Consented install is silent for up to 600 seconds (restated, iteration 1)

**File:** `src/jarvis/index_cli.py:770-773`
**Issue:** `capture_output=True` swallows uv's progress and nothing is printed before the call — after answering "y" the terminal appears frozen for minutes (torch-scale downloads). Users will assume a hang and Ctrl-C; that interrupt still tracebacks (`KeyboardInterrupt` during the install propagates out of `_cmd_index`). Note: this is now the ONLY remaining traceback path in the offer block — the WR-02 spawn/decode escapes are fixed; the interrupt tail was the explicitly-optional part of WR-01/WR-02's fix suggestions, deliberately deferred by the fixer to this finding's scope, and it is outside every locked constraint (the plan enumerates install failure as "uv absent, non-zero exit, or subprocess timeout"; a user abort mid-install is not a locked outcome — and not remembering a decline on abort is the correct behavior).
**Fix:** Print one stderr line before installing, e.g. `print("installing semantic extra (may take a few minutes)…", file=sys.stderr)` immediately before `_install_semantic_extra()` (or drop `capture_output` so uv streams its own progress); optionally then wrap the accept branch with `except KeyboardInterrupt: print(one-line notice, file=sys.stderr)` to complete the no-traceback contract for the whole offer path.

### IN-02: Failure warning claims `tried: uv pip install …` even when uv was absent (restated, iteration 1)

**File:** `src/jarvis/index_cli.py:1372-1377`
**Issue:** All failure shapes (uv absent, rc≠0, timeout, and now also the WR-02 spawn/decode shapes) print the same `tried: uv pip install …` line. When `shutil.which("uv")` returned None nothing was tried; the user needs `brew install uv` / `pip install uv`, not a retry.
**Fix:** Have `_install_semantic_extra` return a small status (or have `_cmd_index` re-check `shutil.which`) and emit e.g. `warning: semantic extra install failed — uv not found on PATH; index completed without semantic` for the uv-absent shape, keeping the `tried:` wording for the spawned-command shapes.

---

_Reviewed: 2026-08-23T05:48:01Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard — re-review iteration 2 (fixes f7c21df, 3f978fe)_
