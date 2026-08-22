---
phase: 03-opt-in-self-healing-fallback
fixed_at: 2026-08-22T18:14:21Z
review_path: .planning/phases/03-opt-in-self-healing-fallback/03-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-08-22T18:14:21Z
**Source review:** .planning/phases/03-opt-in-self-healing-fallback/03-REVIEW.md
**Iteration:** 1
**Scope:** critical_warning only — the 4 Info findings (IN-01..IN-04) are out of scope per `fix_scope` and remain unfixed.

**Summary:**
- Findings in scope: 2 (CR-01, WR-01)
- Fixed: 2
- Skipped: 0

## Fixed Issues

### CR-01: Degrade gate fires on post-publish failures — destroys the just-published good index and mislabels the run `degraded`

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** `5cf30bd`
**Root cause:** The main-pipeline `try:` spans the terminal registry bookkeeping that runs after `_publish_atomically` has already flipped the `current` pointer. A registry failure at that point (locked `registry.db` from a concurrent watch reindex, disk-full) landed in the same `except` as a real indexer failure and passed the degrade gate, which then `_publish_search_only` → `_retire_scip_artifacts` **rmtree'd the fully-published index directory**, republished zoekt-only, and persisted `degraded/fallback` citing the sqlite error as the indexer failure.
**Applied fix:** A `published` flag (initialized `False` before the pipeline `try:`, set `True` immediately after `_publish_atomically`) now gates the degrade branch via `not published`. Post-publish failures fall through to the existing `record_failure` + raise: the run stays a hard failure (`failed/failed_hard`) with the real (bookkeeping) cause, and the live pointer survives untouched — the "failed run with a live pointer" case `_capability_fields` already reports. Gate comment documents the exclusion.
**Test proving it:** `test_post_publish_registry_failure_stays_hard_not_degraded` (tests/test_index_cli.py) — locked-db simulation via monkeypatched `Registry.upsert` raising `sqlite3.OperationalError("database is locked")` only on the terminal `status="indexed"` write, `fallback_search_only=True`, otherwise the healthy full-run mocks. Reproduced the finding pre-fix (run returned normally printing `warning: repo degraded to search-only — database is locked`, pointer deleted — "DID NOT RAISE"), passes post-fix (raises `IndexingError` matching "database is locked"; `current` pointer still on disk; row `failed`/`failed_hard` with reason `database is locked`, `search_only` False).
**Constraint audit:** genuine degrade cases (pre-publish failures) are unaffected — all 68 degrade/fallback/watch/signature/pre-pipeline tests pass; degraded exit-0-with-one-warning, taxonomy, self-heal, sha-gated watch skip, and degraded-publish-failure hardness all re-verified green.

### WR-01: Degrade-branch inner catch conflates "publish failed" with "bookkeeping failed" — prints a false "nothing published" and mislabels a half-landed fallback

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** `1dbefde`
**Root cause:** The inner `try:` around the degraded publish wrapped not just `_publish_search_only` but also the `DEGRADED` upsert and `mark_tracked_files`/`mark_semantic_indexed`. When the search publish succeeded and the subsequent registry write failed, the handler printed the false `"fallback publish failed … — nothing published"` (zoekt WAS published, the old SCIP index WAS retired) and recorded `failed/failed_hard` with the *original* build-failure reason, leaving a row that contradicted the disk.
**Applied fix:** The inner `try` is narrowed to the `_publish_search_only` call only; the row writes moved to an `else:` block with their own catch. Publish-failure path unchanged (genuinely nothing published → fall through to `record_failure` with the original failure). A bookkeeping failure after a successful publish now: (a) prints one honest warning — `"{slug} degraded to search-only — {reason} (search-only IS published), but recording the degraded status failed — {bk_reason}"`; (b) persists `failed/failed_hard` naming the *bookkeeping* error (the run's proximate cause) via `record_failure`; (c) raises `IndexingError` from the bookkeeping error instead of exiting 0, so the row is never silently stranded at `indexing`. The success path (publish + row write both land) is byte-identical to before: one degrade warning line, `return slug`, exit 0.
**Test proving it:** `test_degraded_row_write_failure_reports_what_landed` (tests/test_index_cli.py) — same locked-db harness with `Registry.upsert` raising only on `DEGRADED_STATUS`, indexer step failing so the degrade branch runs and its zoekt publish succeeds. Reproduced pre-fix (stderr showed the false "nothing published" line and the raise carried the original failure text), passes post-fix (raises matching "database is locked"; stderr contains "degraded to search-only", the original reason, and "database is locked", and does NOT contain "nothing published"; row `failed`/`failed_hard` reason `database is locked`).
**Constraint audit:** `test_degraded_run_prints_exactly_one_warning_line`, `test_degraded_publish_on_post_build_start_failure`, `test_failed_degraded_publish_preserves_the_previous_current_pointer`, and the FALL-02/03/05 suites all pass unchanged — the degraded success contract and publish-failure hardness are untouched.

## Verification

- Reproduce-then-fix for both findings: each regression test was run against pre-fix code first (CR-01: `DID NOT RAISE` + pointer deleted + "degraded to search-only — database is locked" warning; WR-01: false "nothing published" line + wrong raised message), then against the fixed code (both pass).
- Syntax: `ast.parse` clean on both modified files.
- Focused: 68 degrade/fallback/watch/signature/publish/pre-pipeline tests pass.
- Suites: `tests/test_index_cli.py` unit 183 passed / 12 deselected (181 pre-existing + 2 new); `tests/test_registry.py` + `tests/test_config.py` + `tests/test_server_tools.py` 109 passed.
- Full CI gate: `uv run pytest -m "not integration"` → **630 passed, 17 deselected**.
- Verification ran in the **main checkout** on `gsd/v1.0-milestone` (`workflow.use_worktrees=false` — no isolated worktree was created; numbers are reproducible directly from this tree).
- All 8 locked phase-3 design constraints re-verified holding via the passing pinned tests (degraded exit 0 + single warning line; status/origin taxonomy incl. degraded reason+stderr; self-heal `search_only=0`; pre-pipeline hardness; sha-gated watch skip with fail-open; degraded-publish failure = hard failure with pointer survival; `recovery_for('fallback')` verb; zero MCP payload reshaping — server.py untouched).

## Skipped Issues

None in scope. Out of scope by `fix_scope: critical_warning`: IN-01 (`_SCHEMA` omits `fallback_enabled`), IN-02 (`_watch_skip_check` docstring wording), IN-03 (unused `import time` in tests), IN-04 (degraded-repos prose in `_error_payload`).

---

_Fixed: 2026-08-22T18:14:21Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
