---
phase: 03-opt-in-self-healing-fallback
reviewed: 2026-08-23T10:05:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - src/jarvis/registry.py
  - src/jarvis/config.py
  - src/jarvis/index_cli.py
  - src/jarvis/server.py
  - tests/test_registry.py
  - tests/test_config.py
  - tests/test_index_cli.py
  - tests/test_server_tools.py
  - README.md
findings:
  critical: 0
  warning: 0
  info: 9
  total: 9
status: issues_found
iteration: 3
---

# Phase 3: Code Review Report — Iteration 3 (final)

**Reviewed:** 2026-08-23T10:05:00Z (post-fix commits `5cf30bd`, `1dbefde`, `d76eb08`, `d70e084`)
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found — **0 critical / 0 warning / 9 info.** No critical or
warning findings remain; every in-scope defect from iterations 1–2 is verified
fixed with a passing regression test. The 9 Info findings are out of the
`critical_warning` fix scope (6 restated unchanged from iteration 2, 3 new).

## Summary

Final re-review of the phase-3 surface after all four fix commits. Full current
contents of all nine files were read; both iteration-2 fix diffs were inspected
in isolation (`git show d76eb08`, `git show d70e084`); the full gate was re-run
from the reviewed tree: `uv run pytest -m "not integration"` → **633 passed,
17 deselected** — exactly matching the iteration-2 fix report, so all five new
regression tests are live and green in this tree.

**All four fixes are verified holding:**

- **CR-01 (published-flag gate, `5cf30bd`)** — `published = False` is
  initialized before the pipeline `try` (`index_cli.py:1053`) and flipped at
  `:1127` immediately after `_publish_atomically` (`:1123`) with only comments
  between; the degrade gate leads with `not published` (`:1162`). Nothing can
  set the flag without a live pointer, and nothing between the pointer flip and
  the flag can raise. Pinned by
  `test_post_publish_registry_failure_stays_hard_not_degraded`
  (tests/test_index_cli.py:3139): locked terminal upsert → hard failure,
  pointer survives, row `failed/failed_hard` citing the bookkeeping cause.
- **WR-01 (honest bookkeeping failure, `1dbefde`)** — the inner `try` covers
  only `_publish_search_only` (`:1166-1168`); the degraded-row writes sit in
  `else:` (`:1205-1242`) with their own handler (`:1216-1235`) that prints the
  honest "(search-only IS published), but recording the degraded status failed"
  warning, records the bookkeeping error as the proximate cause, and raises
  instead of exiting 0. Pinned by
  `test_degraded_row_write_failure_reports_what_landed` (:3202), including the
  absence of the false "nothing published" line.
- **WR-02 (partial-landing marker + retire reorder, `d76eb08`)** — both halves
  verified. (a) `_retire_scip_artifacts` now clears graph edges **before** the
  irreversible `rmtree` (`:773-789`): a retire that dies on the locked-db race
  leaves the already-modeled "failed run with a live pointer" state. (b) The
  retire step inside `_publish_search_only` is wrapped (`:829-832`) to raise
  `SearchPublishedButIncomplete` (IS-A `IndexingError`, `:176-184`, so the
  manual/signature callers' except-clauses are untouched), and the degrade
  handler gained a dedicated `except` **before** the generic one
  (`:1169-1195` vs `:1196-1204`) printing "search shards ARE published … the
  previous navigation index is untouched" and augmenting the row's
  `status_stderr`. Pinned by
  `test_degraded_publish_retire_failure_reports_partial_landing` (:3262) —
  real retire code under test via a locked `GraphStore` constructor; asserts
  the original failure stays the one-line reason, the pointer survives, and no
  "nothing published" line appears.
- **WR-03 (best-effort record_failure, `d70e084`)** — new helper
  `_record_failure_best_effort` (`:876-897`) demotes a failed
  `record_failure` write to one stderr warning naming BOTH failures; all four
  recording sites call it (`:1000` pre-pipeline wrap, `:1034` search-only
  branch, `:1233` WR-01 bookkeeping handler, `:1243` outer handler) and then
  raise their ORIGINAL error unchanged (bare re-raise in the pre-pipeline
  wrap, `IndexingError(str(exc)) from exc` at the other three). A repo-wide
  grep confirms no bare `registry.record_failure(` call remains in
  index_cli.py. Pinned by
  `test_degraded_row_write_and_record_failure_both_locked` (:3347 — end-to-end
  through `_cmd_index`: rc 1, last stderr line is the clean
  `error: database is locked`, no traceback, row honestly left at `indexing`)
  and `test_pre_pipeline_record_failure_failure_does_not_mask_the_original`
  (:3424 — the original `RuntimeError` version-gate error still propagates).

**No new critical or warning issues were introduced by either iteration-2
fix.** Three residual narrow-diagnostic corners were probed and are recorded
as Info only (IN-07..IN-09): none misstates the row, loses data, destroys a
good index, or changes an exit code — the same defects at realistic-trigger
severity were WR-02/WR-03 and are now fixed; what remains requires a corrupted
install or a triple-failure window.

**All eight locked phase-3 design constraints re-verified against code + the
passing pinned tests** (audit footer below).

## Narrative Findings (AI reviewer)

## Critical Issues

None.

## Warnings

None.

## Info

### IN-01: `_SCHEMA` omits `fallback_enabled` — fresh databases get the column only via the post-create ALTER *(restated, iterations 1–2)*

**File:** `src/jarvis/registry.py:54-72, 193`
**Issue:** Every other migration-era column appears both in `_SCHEMA` and in
an `_ensure_column` call; `fallback_enabled` exists only as
`_ensure_column(self._conn, "fallback_enabled", "INTEGER")`. Functionally
correct (the duplicate-column error is tolerated), but it breaks the
established belt-and-suspenders pattern.
**Fix:** Add `fallback_enabled INTEGER` to `_SCHEMA`.

### IN-02: `_watch_skip_check` docstring claims "Read-only by design" but `Registry(...)` construction mutates the data dir *(restated, iterations 1–2)*

**File:** `src/jarvis/index_cli.py:389-397` ("Read-only by design" at `:393`)
**Issue:** `Registry.__init__` runs `mkdir(parents=True)` + `CREATE TABLE` +
the ALTER sweep (each with its own commit) on every debounced watch event,
materializing an empty `registry.db` when watching a never-indexed repo. The
consult is read-*intent*, not read-only; the predicate itself is correct and
fails open.
**Fix:** Reword to "no status writes", or read via a read-only sqlite URI when
the file exists, failing open when it does not.

### IN-03: Unused `import time` in `tests/test_index_cli.py` *(restated, iterations 1–2)*

**File:** `tests/test_index_cli.py:13`
**Issue:** Never referenced — `_drive_cmd_watch` builds its own
`types.ModuleType("time")` fake (`:3873-3875`); the only other `time.`
occurrences in the file are inside docstrings.
**Fix:** Delete the import.

### IN-04: Degraded repos hitting navigation tools get generic error prose, unlike search-only repos *(restated, iterations 1–2)*

**File:** `src/jarvis/server.py:225-246`
**Issue:** `_error_payload` gives `search-only` rows prose explaining that
"searchCode and semanticSearch do work on it"; a `degraded` row falls to the
generic `{"error": str(exc)}` with only the structured `state`/`cause`/
`recovery` keys added afterward. The same reassurance is true for degraded
rows. Deliberate under the zero-payload-reshaping constraint (pinned by
`test_error_payload_carries_state_cause_recovery_for_a_degraded_repo`), hence
Info.
**Fix:** If desired, extend the existing prose branch to cover
`DEGRADED_STATUS` — a wording change inside the existing `error` string.

### IN-05: WR-01 bookkeeping handler persists only the bookkeeping error — the original indexer failure that triggered the degrade is not persisted anywhere *(restated, iteration 2)*

**File:** `src/jarvis/index_cli.py:1233-1234`
**Issue:** In the bookkeeping-failure path, the record stores only the sqlite
error (`status_reason`/`status_stderr` = "database is locked"); the indexer
failure that caused the degrade survives solely in the printed warning. The
fix report documents this as a deliberate proximate-cause choice, so Info.
**Fix:** Persist both, e.g. `status_reason=bk_reason` but
`status_stderr=f"{bk_text}\n— original indexer failure —\n{text}"`.

### IN-06: README status enumeration omits `search-only` and `degraded` *(restated, iteration 2)*

**File:** `README.md:175-178`
**Issue:** "`status` … is usually `indexed` or `failed`, but can also be
`partial`" — `search-only` and phase 3's `degraded` are never listed as status
values; `degraded` appears only inside the `--fallback-search-only` flag
description (`:168`) and the env-var section (`:321-326`). A user who enables
the fallback will routinely see `◐ degraded` in `jarvis list` with no README
explanation.
**Fix:** Extend the sentence: "…can also be `partial`, `search-only`, or
`degraded` (the opt-in fallback's state: search works, navigation doesn't, and
the next reindex retries the full build — see `--fallback-search-only`)."

### IN-07: README names the wrong Zoekt binary and a pinning mechanism that no longer exists *(new this iteration; pre-existing at diff_base, untouched by the phase-3 diff)*

**File:** `README.md:113, 141, 202, 290`
**Issue:** The requirements table lists "`zoekt-index` · `zoekt-webserver`"
(`:113`), and the pipeline prose says "runs `zoekt-index`" (`:141`, `:202`).
But setup.sh installs **`zoekt-git-index`** (setup.sh:566-574: "zoekt-git-index,
not zoekt-index: jarvis indexes from the git tree … Nothing calls zoekt-index")
and `_run` raises "zoekt-git-index not found on PATH". Worse, `:290` still
claims shard naming happens "via `zoekt-index -meta`" — no `-meta` flag exists;
pinning has been `git config zoekt.name` (`_pin_zoekt_repo_name`) for the
whole milestone. A user inspecting their install against the table would look
for a binary that is never installed or invoked.
**Fix:** s/zoekt-index/zoekt-git-index/ at `:113`, `:141`, `:202`; rewrite the
`:289-293` limitation bullet to describe the `git config zoekt.name` pin.

### IN-08: Signature-fallback branch loses the signature diagnosis if `_publish_search_only` or the terminal upsert fails inside the inner except *(new this iteration)*

**File:** `src/jarvis/index_cli.py:1076-1084`
**Issue:** In the signature path (`except IndexingError` around the indexer
step), a `SearchPublishedButIncomplete` from `_publish_search_only` (`:1076`)
or a locked-db failure of the `SEARCH_ONLY_STATUS` upsert (`:1078-1081`)
escapes into the OUTER `except Exception` handler, which recomputes
`reason`/`text` from the *new* exception — the matched-signature explanation
("scip-kotlinc ABI mismatch" etc.) is dropped from the recorded row, and with
fallback enabled the degrade gate re-runs `_publish_search_only` a second
time. Behavior still converges correctly (hard failure with honest disk
state, or a degraded row), so this is diagnostic-loss in a double-failure
corner only — same tier as IN-05, not a warning.
**Fix:** Mirror the WR-01 shape inside the signature branch: wrap
`_publish_search_only` + row writes in their own try/except that records the
original signature `reason` alongside the secondary failure.

### IN-09: A non-ImportError failure of the semantic import inside `_publish_search_only` still lands in the "nothing published" branch *(new this iteration; residual of the WR-02 class with a much narrower trigger)*

**File:** `src/jarvis/index_cli.py:727-729` (import try catches only
`ImportError`), `:833` (semantic stage after retire), `:1196-1204` (generic
handler)
**Issue:** `_run_semantic_stage`'s first `try` catches only `ImportError`; a
non-ImportError import-time failure (e.g. `SyntaxError` from a corrupted
`jarvis/semantic.py`) propagates out of `_publish_search_only` *after* the
zoekt publish and retire completed, and the degrade handler's generic
`except Exception` prints "nothing published" — false in that corner (search
shards ARE live; the old SCIP index IS retired). Unlike WR-02's realistic
locked-db race, this trigger requires a broken jarvis installation, the row
and exit code stay correct (`failed/failed_hard`, hard raise), and the same
corruption fails every earlier call site identically — hence Info, not
Warning.
**Fix:** Broaden the import catch to `except Exception` (print the skip
warning + return False), or wrap the semantic stage in `_publish_search_only`
the way the retire step now is.

---

_Constraint audit (iteration 3, all verified against current code + the
passing 633-test gate):_
_1. degraded exit 0 + exactly one warning line (`index_cli.py:1205-1242` else
branch — one print at `:1236-1241`, `return slug` at `:1242`;
`test_degraded_run_prints_exactly_one_warning_line`, test_index_cli.py:2933)._
_2. status/origin taxonomy incl. degraded reason+stderr (registry.py:26-46,
253-292; `test_upsert_round_trips_status_stderr_and_plain_upsert_clears_it`,
test_registry.py:648)._
_3. self-heal `search_only=0` on degraded rows (the DEGRADED upsert at
index_cli.py:1207-1211 omits `search_only`;
`test_degraded_repo_self_heals_to_indexed_on_a_successful_rerun`, :3602;
`test_degraded_repo_degrades_again_on_a_fresh_failure`, :3635)._
_4. pre-build hardness (early wrap index_cli.py:939-1007; tests :2964
missing-binary, :3012 bash-shim, :3050 version-gate)._
_5. sha-gated watch skip with fail-open consult (index_cli.py:353-407; tests
:3736 decision matrix, :3761 sha-gated skip, :3796 fail-open)._
_6. degraded-publish failure = hard failure with pointer survival — now on
BOTH sub-paths: zoekt-step failure (`_publish_search_only` zoekt-first
ordering :799-827; test :3082) and retire-step failure (graph-before-rmtree
:773-789 + pointer-survival assertions in test :3262); row-write failure
raises rather than exiting 0 (test :3202)._
_7. `recovery_for('fallback')` self-heal verb (registry.py:161-175;
`test_recovery_for_fallback_origin_names_the_self_heal`,
test_registry.py:637)._
_8. zero MCP payload reshaping (server.py's phase-3 diff is exactly +7 lines:
the degraded `nav_reason` elif and imports; pinned by
`test_get_index_status_reports_last_index_run_for_a_degraded_repo` and
`test_error_payload_carries_state_cause_recovery_for_a_degraded_repo` in
tests/test_server_tools.py)._

_Additionally re-verified this iteration: the `published` flag cannot be set
without a live pointer (:1123-1127); `except SearchPublishedButIncomplete`
precedes the generic `except` (:1169 < :1196) so the marker is reachable; no
bare `record_failure` call site remains in index_cli.py (all four route through
`_record_failure_best_effort`, which never raises and never masks); the
signature path still preempts the degrade branch on opted-in repos
(`test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo`,
:3669)._

_Verification: full gate re-run from the reviewed tree —
`uv run pytest -m "not integration"` → 633 passed, 17 deselected (matches the
iteration-2 fix report exactly); `git show d76eb08` / `git show d70e084`
inspected; no source files modified._

_Reviewed: 2026-08-23T10:05:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard — iteration 3 of 3 (final)_
