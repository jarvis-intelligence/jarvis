---
phase: 05-semantic-install-onboarding
reviewed: 2026-08-23T05:36:03Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - src/jarvis/index_cli.py
  - src/jarvis/registry.py
  - tests/test_index_cli.py
  - tests/test_registry.py
findings:
  critical: 0
  warning: 2
  info: 2
  total: 4
status: issues_found
---

# Phase 5: Code Review Report

**Reviewed:** 2026-08-23T05:36:03Z
**Depth:** standard
**Files Reviewed:** 4 (diff `ecd04d2^..HEAD`: +851/−3 across 4 commits)
**Status:** issues_found

## Summary

The semantic install onboarding (SEMA-01/02) is correctly implemented against every locked constraint, with unusually strong test pinning (16 phase-5 tests, all passing; full `test_registry.py` 42/42 and `test_index_cli.py` non-integration 207/207 verified during review). Verified point-by-point:

- **Offer gating (SEMA-02)** — `offer_semantic` is set only by the `index` subparser (`index_cli.py:1677`); `_cmd_reindex`'s synthetic Namespace omits it (getattr default False), `_cmd_watch` calls `index_repo` directly (1589), and `server.py` imports no index path at all — reindex/watch/MCP structurally cannot reach the offer. `_at_interactive_tty` requires both stdin and stdout TTYs. The offer block sits strictly after publish (post `indexed {slug}`, post `return 1` failure paths).
- **Prompt contract** — exact locked text; `.strip().lower() in ("y","yes")` parse table; Enter/garbage declines; EOF/Ctrl-C caught → remembered decline, rc 0, no traceback.
- **Install command** — fixed argv `[resolved uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"]`, no shell, no user-derived bytes anywhere in argv (slug/path never reach it), timeout 600s. `shutil.which` PATH trust is equivalent to the tool's existing `_run` PATH-binary usage (scip-python, git, zoekt) — accepted project posture, not a new exposure.
- **Three-outcome contract** — install failure (uv absent / rc≠0 / timeout) → exactly one stderr warning, rc 0, no decline bit (offers again); consent → `importlib.invalidate_caches()` + stage re-run + `mark_semantic_indexed` in the same invocation. The same-invocation enablement is sound: `lancedb` (semantic.py:157) and `sentence_transformers` (embeddings.py:102) are both method-level lazy imports, so cache invalidation makes the fresh imports succeed; `jarvis.semantic`/`jarvis.embeddings` being already in `sys.modules` is harmless.
- **Registry memory semantics** — `semantic_declined` added via `_ensure_column` (NULL default), excluded from both `upsert`'s and `record_failure`'s INSERT column lists and ON CONFLICT SET lists, written only by the dedicated parameterized `set_semantic_declined`; dies with the row via `forget`. `_row_to_repo`/`get`/`list` SELECT lists updated in lockstep; GraphStore does not touch the `repos` table; the dataclass field is additive-with-default so every existing construction site stays valid. Legacy-DB migration and roundtrip/preservation are pinned by tests.
- **Detection seam** — `find_spec` on exactly `lancedb` + `sentence_transformers`, matching the lazy-import set; monkeypatchable. Extra installed → gate 2 fails before the decline gate → never prompts, never writes. `--semantic-include` on a declined repo runs the in-run stage and persists prefixes without clearing the bit. The non-TTY silent skip still leaves the existing `semantic indexing skipped — install jarvis-mcp[semantic]` stderr hint from the in-run stage.

No security findings at ASVS L1: no shell, no injection surface (parameterized SQL, fixed argv), no secrets, no eval. Two robustness warnings remain in the interactive path — both are edge-input escapes of the "no traceback / one-warning-and-continue" contract, not mainline behavior violations.

## Warnings

### WR-01: `input()` raises uncaught `UnicodeDecodeError` on undecodable bytes — traceback after a successful publish

**File:** `src/jarvis/index_cli.py:1345`
**Issue:** The prompt loop catches only `(EOFError, KeyboardInterrupt)`. `input()` decodes stdin with `errors='strict'`; bytes that are invalid in the terminal's encoding raise `UnicodeDecodeError`, which propagates out of `_cmd_index` → `main` → traceback and a nonzero exit — after the index has already published. Empirically confirmed during review: `printf '\x80\x81' | python3 -c "input('p ')"` raises `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x80`. Triggerable on a TTY by pasting raw binary garbage (bracketed-paste of invalid bytes, non-UTF-8 terminal locales). The locked contract's spirit ("remembered decline, no traceback" for abnormal prompt input) and the parse table's "garbage declines" rule both argue this should be a remembered decline like EOF.
**Fix:**
```python
try:
    answer = input("Install semantic search support for this repo? [y/N] ").strip().lower()
except (EOFError, KeyboardInterrupt, UnicodeDecodeError):
    # Locked: EOF/Ctrl-C (and undecodable garbage) at the prompt is a
    # decline — remembered, no traceback, index already complete.
    answer = ""
```

### WR-02: `_install_semantic_extra` escapes `OSError`/decode failures — violates "install failure = one stderr warning + continue"

**File:** `src/jarvis/index_cli.py:769-776`
**Issue:** Only `subprocess.TimeoutExpired` is caught. Two real failure shapes raise instead of returning False: (a) `OSError`/`FileNotFoundError` when the resolved `uv` cannot exec — e.g. `shutil.which` succeeds on an X_OK file whose interpreter is broken/missing (ENOENT on exec), a TOCTOU unlink, or an EACCES; (b) `UnicodeDecodeError` raised by `subprocess.run(..., text=True)` itself if uv/pip emit non-UTF-8 bytes on a legacy locale (the captured pipes decode strict). Either way the user who just consented gets a traceback and nonzero exit after a successful publish, instead of the locked single warning + rc 0 + decline-not-remembered. A `KeyboardInterrupt` during the (silent, up-to-600s — see IN-01) install likewise tracebacks; the lock doesn't cover it, but it is the same unhardened accept path.
**Fix:**
```python
    try:
        result = subprocess.run(
            [uv, "pip", "install", "--python", sys.executable, "jarvis-mcp[semantic]"],
            capture_output=True, text=True, errors="replace", timeout=600,
        )
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError):
        return False
```
(`errors="replace"` prevents (b) outright; the broadened `except` covers (a). Optionally wrap the accept branch in `_cmd_index` with `except KeyboardInterrupt: print(one-line notice)` to complete the no-traceback contract for the whole offer path.)

## Info

### IN-01: Consented install is silent for up to 600 seconds

**File:** `src/jarvis/index_cli.py:771-773`
**Issue:** `capture_output=True` swallows uv's progress, and nothing is printed before the call — after answering "y" the terminal appears frozen for minutes (torch-scale downloads). Users will assume a hang and Ctrl-C, which then produces the traceback of WR-02.
**Fix:** Print one stderr line before installing, e.g. `print("installing semantic extra (may take a few minutes)…", file=sys.stderr)` immediately before `_install_semantic_extra()`, or drop `capture_output` so uv streams its own progress.

### IN-02: Failure warning claims `tried: uv pip install …` even when uv was absent

**File:** `src/jarvis/index_cli.py:1360-1366`
**Issue:** All three failure shapes (uv absent, rc≠0, timeout) print the same `tried: uv pip install …` line. When `shutil.which("uv")` returned None nothing was tried; the remedy text should distinguish that case (the user needs `brew install uv`/`pip install uv`, not a retry).
**Fix:** Have `_install_semantic_extra` return a small status (or have `_cmd_index` re-check `shutil.which`) and emit e.g. `warning: semantic extra install failed — uv not found on PATH; index completed without semantic` for the uv-absent shape, keeping the `tried:` wording for the spawned-command shapes.

---

_Reviewed: 2026-08-23T05:36:03Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
