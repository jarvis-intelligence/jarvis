---
phase: 01-registry-foundation-degradation-reporting
reviewed: 2026-08-21T17:30:45Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/jarvis/registry.py
  - src/jarvis/index_cli.py
  - src/jarvis/server.py
  - tests/test_registry.py
  - tests/test_index_cli.py
  - tests/test_server_tools.py
findings:
  critical: 0
  warning: 3
  info: 5
  total: 8
status: findings
---

# Phase 01: Code Review Report

**Reviewed:** 2026-08-21T17:30:45Z
**Depth:** standard (per-file analysis; cross-file contract tracing into `query.py`, `config.py`, `index_reader.py`)
**Files Reviewed:** 6 (scope extracted from the three plan SUMMARY key-files lists + `git log --grep='01-0'`; `git diff --stat b20a241^..HEAD` confirms exactly these 6 source/test files changed)
**Status:** findings

## Summary

The implementation is solid: additive schema migration via the proven `_ensure_column` idiom, parameterized SQL throughout, `status_stderr` verified absent from every MCP payload (grep: docstring mentions only), never-raise conventions layered correctly (helper-internal broad except + call-site belt), and the claimed tests all exist and assert real contracts (registry row truth, not reconstructed returns). Full unit suite re-run for this review: **478 passed, 25 skipped, 12 deselected** — matches the summaries' self-check exactly.

No Critical findings. Three Warnings: one robustness hole in the new degradation-reporting feature itself (interrupted runs destroy the prior failure cause and leave a permanent `indexing` row), one missing `finally` on an error path, and one machine-readable reason that asserts staleness without evidence — a wording-level violation of the codebase's own "never stale without evidence" honesty principle.

Notable non-findings verified adversarially:
- **No SQL injection**: all queries parameterized; `_ensure_column`'s f-string interpolates module constants only.
- **No payload leak**: `status_stderr` (potentially megabytes) never enters `_capability_fields`/`_error_payload`; payloads built key-by-key, never `asdict(entry)`.
- **No glob traversal/metachar risk**: `zoekt_dir.glob(f"{repo}_v*.zoekt")` takes unvalidated client input, but any pathological pattern either stays inside `zoekt_dir` or raises into the never-raise try → nulls; slugs created via the CLI are restricted to `[a-z0-9._-]` by `config.repo_slug`.
- **Keyword-compat**: all 5 `upsert()` call sites pass overrides by keyword; `RegisteredRepo` constructions are keyword-based — the two new appended parameters break no caller.
- The deferred `from jarvis.registry import Registry` inside `_registry_entry` correctly picks up the monkeypatched module attribute in the unreadable-registry test (test relies on this; it is sound, not accidental-passing).

## Critical Issues

None found.

## Warnings

### WR-01: Interrupted runs (Ctrl-C) leave a permanent `indexing` row and destroy the previously persisted failure cause

**File:** `src/jarvis/index_cli.py:813-815, 839-840` (transitional upserts) and `798-809, 825-834, 925-934` (the three `except Exception` handlers)
**Issue:** The transitional `upsert(..., "indexing", ...)` runs *before* the pipeline and its ON CONFLICT list clears `status_origin`/`status_reason`/`status_stderr` to NULL (the D-04 conflict list fires on *every* upsert, not only success). If the run is then interrupted — `KeyboardInterrupt` during `subprocess.run` is the canonical case for aborting a multi-minute Gradle build — none of the three `except Exception` handlers fire (`KeyboardInterrupt` is a `BaseException`). Net effect for a retry of a previously-`failed` repo that gets Ctrl-C'd:
1. the prior failure record (origin/reason/stderr) is wiped with nothing written in its place;
2. the row is left `status='indexing'` forever (`jarvis list` shows it in the ✓ family as `✓ indexing`; `getIndexStatus.last_index_run.outcome='indexing'`, origin `None`);
3. no recovery guidance exists anywhere — the exact gap phase 01 exists to close.

The stuck-`indexing` status is pre-existing behavior, but the destruction of the prior cause record is newly introduced by this phase's conflict-list change interacting with the uncaught-interrupt path.
**Fix:** Catch `BaseException` in the three failure handlers (record the cause, then re-raise so the interrupt still propagates), or restrict the D-04 clearing to terminal upserts by excluding the `status_*` columns from the `"indexing"` write:

```python
# Option A (smallest): record cause on interrupts too
    except BaseException as exc:   # KeyboardInterrupt must not orphan the row
        text = str(exc) or exc.__class__.__name__
        reason = next((line for line in text.splitlines() if line.strip()),
                      exc.__class__.__name__)
        registry.record_failure(slug, str(repo_path), language,
                                ORIGIN_FAILED_HARD, reason, text)
        raise
```

### WR-02: Pre-pipeline failure handler skips `registry.close()` when `record_failure` itself raises

**File:** `src/jarvis/index_cli.py:798-810`
**Issue:** The pre-pipeline handler does `record_failure(...)` → `registry.close()` → `raise` with no `try/finally`. If `record_failure` raises (e.g. `sqlite3.OperationalError` on a lock timeout > the 5s `busy_timeout`, plausible while `jarvis watch` reindexes), the connection is never closed on this path. Both sibling handlers (825-836, 925-936) correctly use `finally: registry.close()`; in `jarvis watch`'s long-lived process an unclosed connection lingers until GC. The duplicate-slug gate above it (755-757) also does close-then-raise correctly.
**Fix:**

```python
    except Exception as exc:
        ...
        try:
            registry.record_failure(...)
        finally:
            registry.close()
        raise
```

### WR-03: `capabilities.navigation.reason` claims "stale" with no staleness evidence

**File:** `src/jarvis/server.py:173-179`
**Issue:** `nav_reason` is set to `"stale — indexed at <commit>"` whenever `last_run_failed OR stale_reported`. But `freshness.stale` is `False` **by construction** when `repo_path` is omitted (query.py: "never stale=True without evidence"), and can legitimately be `False` with `repo_path` given when HEAD equals the published commit (transient reindex failure, nothing changed). In both cases the payload tells an MCP client the navigation data is stale when no comparison supports that — misleading machine-readable degradation signal, inconsistent with the codebase's own freshness honesty rule.
**Fix:** Distinguish the two triggers:

```python
            if last_run_failed or stale_reported:
                commit = freshness.commit if freshness is not None else None
                if stale_reported:
                    nav_reason = f"stale — indexed at {commit}" if commit else "stale — indexed commit unknown"
                else:
                    nav_reason = (f"indexed at {commit}; the latest run failed"
                                  if commit else "latest run failed; indexed commit unknown")
```

## Info

### IN-01: `Registry.mark_status()` is now production-dead

**File:** `src/jarvis/registry.py:222-228`
**Issue:** After 01-02 removed the last bare `mark_status` flip (the manual-branch publish failure), no caller remains anywhere in `src/` (grep confirms definition + docstring mention only). Only `test_mark_status_transition` still exercises it. Per the repo's clean-cutover taste this is weightless code kept alive by its own test.
**Fix:** Delete the method and its test, or add a comment declaring it intentional API surface for external callers.

### IN-02: `jarvis list` field-2 values changed (`failed` → `✗ failed`) — exact-match parsers break

**File:** `src/jarvis/index_cli.py:963-975`
**Issue:** Column *count/order* is preserved (planned, D-08) but the field *value* gained a glyph prefix, so scripts doing `fields[1] == "failed"` now fail silently against `"✗ failed"`. Also, a `status_reason` containing a tab would split the 6th field (low likelihood: reasons come from the constructed first line of `_run`'s `IndexingError`, which contains spaces only). Pre-phase-1 output verified via `git show b20a241^` for this comparison.
**Fix:** Acceptable as planned; consider documenting the glyph in the README/`--help` ("strip before the space for machine parsing") or adding a `--porcelain` flag in a later phase.

### IN-03: `jarvis reindex` on a signature-origin search-only row re-stamps origin as `manual`

**File:** `src/jarvis/index_cli.py:818-821`
**Issue:** The search-only success upsert hardcodes `status_origin=ORIGIN_MANUAL`, and a reindex of a signature row re-enters this branch via `_resolve_search_only` — the signature origin and matched reason are replaced with `manual`/`None`, and recovery flips from `jarvis reindex` to `forget && index`. This is the documented D-11 tension locked by the plan and deferred to Phase 3's reindex-semantics change, recorded here so it survives into that phase.
**Fix:** None now (plan-locked). Phase 3: preserve `ORIGIN_SIGNATURE` when the branch was reached via a persisted search-only row rather than an explicit `--search-only`.

### IN-04: `_registry_entry`'s None-conflation can report a false "no published index"

**File:** `src/jarvis/server.py:66-77, 196-198`
**Issue:** `_registry_entry` returns `None` both for "no row" and "registry unreadable" (broad except). In `_capability_fields`, an unreadable registry on a repo with `indexed=False` yields `navigation.reason="no published index"` — a false statement (a row may exist; the read failed). Same conflation already exists in `_search_coverage_fields`, so this is an accepted convention rather than a regression; noting it because honesty-of-reason is this phase's contract.
**Fix:** If ever needed, return a sentinel tuple `(entry, ok: bool)` from the lookup and use `reason="registry unreadable"` on the failure branch.

### IN-05: Recovery commands render unquoted paths

**File:** `src/jarvis/registry.py:127-135`
**Issue:** `recovery_for` interpolates `entry.path` raw: `jarvis index /repos/my project` does not round-trip when copy-pasted for a path containing spaces. Display-only guidance (never executed by jarvis), so impact is limited to the user's shell.
**Fix:** `shlex.quote(entry.path)` in the three format strings (note: changes golden strings in 4 tests).

---

**Verification performed for this review:** full unit suite (`uv run python -m pytest -m "not integration" -q`): 478 passed, 25 skipped, 12 deselected; diff scope confirmed via `git diff --stat b20a241^..HEAD -- src tests`; pre-change `jarvis list` output confirmed via `git show b20a241^:src/jarvis/index_cli.py`; `status_stderr` leak check via grep on `server.py`; `upsert`/`RegisteredRepo` call-site keyword-compat via grep; `FreshnessSnapshot.stale` semantics traced into `query.py:63-95, 489-521`; slug charset traced into `config.py:49-69`.

_Reviewed: 2026-08-21T17:30:45Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
