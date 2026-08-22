---
phase: 03-opt-in-self-healing-fallback
fixed_at: 2026-08-22T18:41:48Z
review_path: .planning/phases/03-opt-in-self-healing-fallback/03-REVIEW.md
iteration: 2
findings_in_scope: 4
fixed: 4
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

None in scope across either iteration. Out of scope by `fix_scope: critical_warning`: IN-01 (`_SCHEMA` omits `fallback_enabled`), IN-02 (`_watch_skip_check` docstring wording), IN-03 (unused `import time` in tests), IN-04 (degraded-repos prose in `_error_payload`), IN-05 (bookkeeping row keeps only the proximate cause), IN-06 (README status enumeration omits `search-only`/`degraded`).

---

## Iteration 2

**Fixed at:** 2026-08-22T18:41:48Z (post-review commits `d76eb08`, `d70e084`)
**Source review:** iteration-2 section of 03-REVIEW.md (0 critical / 2 warnings / 6 info)
**Scope:** critical_warning only — the 6 Info findings (IN-01..IN-06) remain out of scope.

**Summary:**
- Findings in scope: 2 (WR-02, WR-03)
- Fixed: 2 (each reproduce-then-fix with new regression tests)
- Skipped: 0

### WR-02: "fallback publish failed … nothing published" is false when `_publish_search_only` fails *after* its zoekt step

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** `d76eb08`
**Root cause:** `_publish_search_only` is not atomic — zoekt shards land first, `_retire_scip_artifacts` runs after, and a retire failure (a `GraphStore` write on the locked `registry.db` that triggered the degrade, or an `rmtree` EPERM) landed in the same `except Exception` as a zoekt-step failure, printing "nothing published" while zoekt IS live. Worse, `_retire_scip_artifacts` ran its irreversible `rmtree` *before* its failure-prone GraphStore writes, so the realistic locked-db failure destroyed the previous navigation index on a run that was about to fail anyway — breaking the module's own "a failed run never destroys a good one" invariant one level below where phase 3 fixed it.
**Applied fix:** Two changes. (1) `_retire_scip_artifacts` now clears graph edges BEFORE the `rmtree`: the db writes are the failure-prone half and the rmtree the irreversible half, so a retire that fails partway leaves the already-modeled "failed run with a live pointer" state (stale edges are rebuilt by the next successful populate — rebuild-not-accumulate). (2) The retire step inside `_publish_search_only` is wrapped so its failure raises `SearchPublishedButIncomplete` (IS-A `IndexingError`, so the manual/signature callers' except-clauses are untouched); the degrade handler gained a dedicated `except` before the generic one that prints `"{slug} … did not complete — search shards ARE published, but retiring the previous SCIP index failed ({reason}); the previous navigation index is untouched. Recording the original failure."` and appends a `— degraded publish did not complete —` addendum to the row's `status_stderr` (the one-line `status_reason` stays the ORIGINAL indexer failure). It then falls through to the existing hard-failure record + raise — a failed degraded publish stays a hard failure per the locked constraint, never a further degrade, never exit 0. The generic `except Exception` keeps the "nothing published" wording, now true (reachable only at-or-before the zoekt step).
**Test proving it:** `test_degraded_publish_retire_failure_reports_partial_landing` (tests/test_index_cli.py) — two-phase harness like the FALL-01 ordering test: phase 1 publishes a real pointer, phase 2 fails the indexer with fallback on, lets the degraded zoekt publish succeed, and locks `GraphStore` construction (keeping the REAL retire code — and its teardown ordering — under test). Reproduced pre-fix (stderr showed the false "nothing published" line; with the pre-fix retire order the pointer was also destroyed before the GraphStore raise), passes post-fix: raises `IndexingError` matching the ORIGINAL failure; stderr has the partial-landing wording and no "nothing published"; the pointer survives; the row is `failed`/`failed_hard` with `status_reason` = the original failure and `status_stderr` carrying the addendum (search shards published, retire failed with "database is locked", prior navigation untouched).
**Constraint audit:** `test_failed_degraded_publish_preserves_the_previous_current_pointer` (zoekt-step failure → old wording + pointer survival), `test_degraded_row_write_failure_reports_what_landed`, `test_post_publish_registry_failure_stays_hard_not_degraded`, `test_degraded_publish_on_post_build_start_failure`, `test_degraded_run_prints_exactly_one_warning_line`, and the signature/search-only publish suites all pass unchanged — the degraded success contract and both hard-failure paths are untouched.

### WR-03: `record_failure` inside the failure handlers can itself raise and escape as a raw `sqlite3.OperationalError` — traceback instead of the clean `error:` line

**Files modified:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Commit:** `d70e084`
**Root cause:** All four failure-recording sites (pre-pipeline wrap, search-only branch handler, WR-01 bookkeeping handler, outer pipeline handler) called `registry.record_failure(...)` bare before raising. When the database is the problem (the same locked `registry.db` that triggered the handler, disk-full), the write raised inside the `except`/`else` block and the new exception REPLACED the original — propagating out of `index_repo` as a raw `sqlite3.OperationalError` that `_cmd_index`/`_cmd_reindex`'s except tuple does not include: traceback at the CLI boundary, original failure lost, row stranded.
**Applied fix:** New `_record_failure_best_effort(registry, slug, repo_path, language, reason, text)` helper — `record_failure` with the write demoted to one stderr warning naming BOTH the recording failure and the original failure (`"recording the failed run for {slug} also failed — {rec_reason}; the original failure ({reason}) is still raised and reported, but the registry row was not updated."`) when it raises. All four sites now call the helper and then raise their ORIGINAL error unchanged (bare re-raise in the pre-pipeline wrap preserving e.g. `UnsupportedLanguageError`/version-gate `RuntimeError`; `IndexingError(str(exc)) from exc` at the other three). The primary failure is never swallowed and the secondary never raised. The mirror in the pre-pipeline wrap is covered by the same helper; `_cmd_watch` was already safe (its `_reindex` catches broadly).
**Test proving it:** `test_degraded_row_write_and_record_failure_both_locked` — end-to-end through `_cmd_index` (the review's repro surface) with `JARVIS_DATA_DIR` isolated: degraded-row upsert locked ("database is locked") AND `record_failure` failing with a distinct "disk I/O error". Reproduced pre-fix (the raw `sqlite3.OperationalError: disk I/O error` escaped `_cmd_index` uncaught), passes post-fix: rc 1; stderr ends with the clean `error: database is locked` line (the ORIGINAL bookkeeping failure, no "Traceback"); the WR-01 honest warning plus the new warning naming both failures; the row honestly left at `indexing` (nothing laundered past the failed record). `test_pre_pipeline_record_failure_failure_does_not_mask_the_original` pins the mirror: a version-gate `RuntimeError` plus a failing record write still propagates the `RuntimeError` (not the sqlite error, not an `IndexingError`) with the both-failures warning.
**Constraint audit:** every pinned iteration-1 test plus the D-05 pre-pipeline/search-only failure-row tests pass unchanged (633 on the full gate); the failure-row contract itself is untouched — the helper is a no-op wrapper whenever the write succeeds, which is the overwhelmingly common case.
**Deliberately not changed:** the review's File line also names the transitional `upsert` + `set_fallback_enabled` writes (index_cli.py, both branches) as exposed to the same raw-sqlite escape. Those are success-path writes, not failure bookkeeping — a failure there means nothing has landed to report and the row cannot be written either; guarding them would change success-path semantics and was not part of the review's fix suggestion (the handler-level guard was chosen over the alternative except-tuple widening). Left as-is; a future iteration can widen `_cmd_index`/`_cmd_reindex`'s tuple with `sqlite3.Error` if the traceback bothers anyone.

## Iteration 2 Verification

- Reproduce-then-fix: all three new regression tests were run against pre-fix code first (WR-02: false "nothing published" + destroyed pointer; WR-03 both: raw `sqlite3.OperationalError: disk I/O error` escaping/replacing the original), then against the fixed code (all pass).
- Syntax: `ast.parse` clean on `src/jarvis/index_cli.py` after each fix; `tests/test_index_cli.py` exercised via pytest itself.
- Focused: `tests/test_index_cli.py` unit 186 passed / 12 deselected (183 pre-existing + 3 new), including all pinned iteration-1 regression tests and the FALL-01..05 suites.
- Full CI gate: `uv run pytest -m "not integration"` → **633 passed, 17 deselected** (630 baseline + 3 new).
- Verification ran in the **main checkout** on `gsd/v1.0-milestone` (`workflow.use_worktrees=false` — no isolated worktree was created; numbers are reproducible directly from this tree).
- All 8 locked phase-3 design constraints re-verified holding via the passing pinned tests (degraded exit 0 + single warning line; status/origin taxonomy; self-heal `search_only=0`; pre-pipeline hardness; sha-gated watch skip with fail-open; degraded-publish failure = hard failure with pointer survival — now also for retire-step failures; `recovery_for('fallback')` verb; zero MCP payload reshaping — server.py untouched).

---

_Fixed: 2026-08-22T18:41:48Z (iteration 2)_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2_
