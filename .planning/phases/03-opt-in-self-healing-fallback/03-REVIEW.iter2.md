---
phase: 03-opt-in-self-healing-fallback
reviewed: 2026-08-22T18:25:00Z
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
  warning: 2
  info: 6
  total: 8
status: issues_found
iteration: 2
---

# Phase 3: Code Review Report — Iteration 2

**Reviewed:** 2026-08-22T18:25:00Z (post-fix commits `5cf30bd`, `1dbefde`)
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found (0 critical / 2 warnings / 6 info)

## Summary

Re-review of the phase-3 surface after the iteration-1 fixes. Full current
contents of all nine files were read; both fix commits' diffs were inspected
in isolation (`git show 5cf30bd`, `git show 1dbefde`); the suites were re-run
from the reviewed tree: `tests/test_config.py` + `tests/test_registry.py`
(64 passed), `tests/test_index_cli.py` unit (183 passed, 12 deselected),
`tests/test_server_tools.py` (45 passed), and the full gate
`uv run pytest -m "not integration"` — **630 passed, 17 deselected**, matching
the fix report. Two residual edge cases were probed with executable
reproductions (scripts under `/tmp`, never the repo tree) rather than
asserted from reading.

**Both iteration-1 findings are verified fixed and holding:**

- **CR-01 (published-flag gate)** — `published = False` is initialized before
  the pipeline `try` (`index_cli.py:1000`) and flipped immediately after
  `_publish_atomically` (`:1074`) with no statement between; the degrade gate
  now leads with `not published` (`:1109`). Nothing can set the flag without a
  live pointer, and nothing between the pointer flip and the flag can raise.
  The regression test
  (`test_post_publish_registry_failure_stays_hard_not_degraded`) pins the
  locked-terminal-upsert scenario: hard failure, pointer survives, row
  `failed/failed_hard` with the real bookkeeping cause.
- **WR-01 (honest bookkeeping-failure reporting)** — the inner `try` is
  narrowed to the `_publish_search_only` call (`:1113-1115`); the degraded-row
  writes moved to the `else:` block with their own handler (`:1137-1155`)
  that prints the honest "(search-only IS published), but recording the
  degraded status failed" warning, records `failed/failed_hard` naming the
  bookkeeping error, and raises instead of exiting 0.
  `test_degraded_row_write_failure_reports_what_landed` pins it, including
  the absence of the false "nothing published" line for registry-write
  failures.

**All eight locked constraints re-verified against code + the passing pinned
tests** (audit footer below). No regression from either fix.

Two residual defects remain in the degrade branch's error handling — both
narrow double-failure windows, both probed and reproduced, neither data
destroying:

1. **WR-02 (this iteration):** the "fallback publish failed … nothing
   published" message is still reachable *falsely* — `_publish_search_only`
   is not atomic (zoekt publishes first, `_retire_scip_artifacts` runs after),
   so a retire-step failure lands in the "nothing published" handler with zoek
   shards already live on disk.
2. **WR-03 (this iteration):** `record_failure` can itself fail (the same
   locked `registry.db` that triggered the handler) and the raw
   `sqlite3.OperationalError` escapes `index_repo` — `_cmd_index`'s except
   tuple does not include it, so the CLI prints a traceback instead of the
   clean `error:` line. The new WR-01 handler adds a call site of this
   pre-existing pattern in exactly the locked-db scenario it exists to handle.

The four iteration-1 Info findings are restated unchanged as IN-01..IN-04
(still present, still Info); two new Info findings were recorded (IN-05, IN-06).

## Narrative Findings (AI reviewer)

## Critical Issues

None. Both iteration-1 blockers/warnings are fixed; no security, data-loss,
or incorrect-behavior defects were found in the current state.

## Warnings

### WR-02: The degrade branch's "fallback publish failed … nothing published" message is still false when `_publish_search_only` fails *after* its zoekt step

**File:** `src/jarvis/index_cli.py:1113-1124` (handler), `:801-805` (ordering)
**Classification:** WARNING (residual of iteration-1 WR-01 — same defect class, narrower trigger)
**Issue:** The WR-01 fix narrowed the inner `try` to the
`_publish_search_only` call, which is correct for the registry-write race it
targeted — but `_publish_search_only` itself is not atomic. It runs
`zoekt-git-index` first (`:801`) and only then retires the previous SCIP
artifacts (`:804`, via `_retire_scip_artifacts`, which `rmtree`s the index
dir and then opens a `GraphStore` on `registry.db` — `graph.py:178-187` does
`executescript(_SCHEMA)` + `commit()`, a contended **write** under exactly the
locked-db race WR-01 was written for; `rmtree` can also fail on `EPERM`/`EBUSY`).
A failure there lands in the `except` at `:1116-1124`, which prints
`"warning: fallback publish failed for {slug} — nothing published; recording
the original failure."` — **false**: the zoekt publish completed and its
shards are live (an `rmtree` that fails mid-way may additionally leave a
partially-retired SCIP index). The registry row and exit code are correct
(`failed/failed_hard` with the original indexer failure, hard raise), so the
damage is the misleading operator message plus the disk/row contradiction
already noted in iteration 1 (`capabilities.search.available` reads the live
shards beside `outcome='failed'`).

Reproduced (degrade mocks + `_retire_scip_artifacts` raising after the zoekt
step succeeded):

```
warning: fallback publish failed for repo — nothing published; recording the original failure.
  raised IndexingError: error: simulated post-build-start failure
  row: status='failed' origin='failed_hard' reason='error: simulated post-build-start failure'
```

The fix report's claim "Publish-failure path unchanged (genuinely nothing
published → fall through)" holds only for failures at-or-before the zoekt
step; post-zoekt failures inside the same call are the uncovered remainder.

**Fix:** Either split the message by phase or split the call. Minimal: have
`_publish_search_only` raise (or return) a distinguishable signal once zoekt
has landed — e.g. wrap the retire step so its failure raises a
`SearchPublishedButIncomplete` marker — and in the handler print
`"fallback publish did not complete (search-only IS on disk; SCIP retirement
failed) — recording the original failure"` for that case, keeping the current
wording only for failures before zoekt writes anything. (Iteration 1's
parenthetical suggested the weaker wording fix — "did not complete" instead
of "nothing published" — which also resolves the falsehood.)

### WR-03: Failure-recording can itself fail and escape `index_repo` as a raw `sqlite3.OperationalError` the CLI does not catch — traceback instead of the clean `error:` line

**File:** `src/jarvis/index_cli.py:1153-1155` (new WR-01 handler site), `:1163-1165` (outer), `:981-983` (search-only branch), `:988-993` (transitional upsert + `set_fallback_enabled`, outside any try); `_cmd_index` tuple at `:1182`
**Classification:** WARNING (robustness; pattern pre-existing, but the WR-01 fix adds a call site in exactly the scenario where the follow-up write will also fail)
**Issue:** Every failure handler funnels into `registry.record_failure(...)`
before raising `IndexingError`. When the database is the problem (the
documented concurrent-watch lock, disk-full), the record write can fail with
the same error. The exception raised *inside* the `except`/`else` handler
then propagates out of `index_repo` as a raw
`sqlite3.OperationalError` — never converted to `IndexingError` — and
`_cmd_index`/`_cmd_reindex` catch only
`(UnsupportedLanguageError, NotAGitRepositoryError, IndexingError, ValueError)`
(`:1182`). The user gets a full traceback instead of the one-line
`error: ...`; the exit code stays 1 (uncaught exception), and the row is
stranded wherever the last successful write left it (unavoidable while the
db is unwritable, and the honest warning at `:1146-1152` has already printed).
The same exposure exists at the outer handler (`:1163`), the search-only
branch (`:981`), and the pre-try transitional upsert/`set_fallback_enabled`
(`:988-993`); the WR-01 handler (`:1153`) is the most likely to hit it,
because it runs *after* a registry write already failed on the same
connection. `_cmd_watch` is unaffected (its `_reindex` catches `Exception`
broadly).

Reproduced end-to-end through `_cmd_index` (upsert locked on
`status="degraded"`, `record_failure` also locked):

```
warning: repo degraded to search-only — error: simulated post-build-start failure
         (search-only IS published), but recording the degraded status failed — database is locked.
_cmd_index propagated RAW sqlite3.OperationalError: database is locked
-> uncaught at main(): traceback + exit 1, no clean 'error:' line
```

**Fix:** Guarantee the conversion at the boundary. In each handler, guard the
record write and raise `IndexingError` regardless:

```python
                except Exception as bkexc:
                    ...
                    with contextlib.suppress(Exception):
                        registry.record_failure(slug, str(repo_path), language,
                                                ORIGIN_FAILED_HARD, bk_reason, bk_text)
                    raise IndexingError(str(bkexc)) from bkexc
```

(and the same `suppress` around the other `record_failure` sites, or simply
add `sqlite3.Error` to `_cmd_index`/`_cmd_reindex`'s except tuple — the
handler-level guard is preferable since it preserves the warning/raise shape
even when the row cannot be written).

## Info

### IN-01: `_SCHEMA` omits `fallback_enabled` — fresh databases get the column only via the post-create ALTER *(restated, iteration 1)*

**File:** `src/jarvis/registry.py:54, 193`
**Issue:** Every other migration-era column appears both in `_SCHEMA` and in
an `_ensure_column` call; `fallback_enabled` exists only as
`_ensure_column(self._conn, "fallback_enabled", "INTEGER")` (`:193`).
Functionally correct (the duplicate-column error is tolerated), but it breaks
the established belt-and-suspenders pattern.
**Fix:** Add `fallback_enabled INTEGER` to `_SCHEMA`.

### IN-02: `_watch_skip_check` docstring claims "Read-only by design" but `Registry(...)` construction mutates the data dir *(restated, iteration 1)*

**File:** `src/jarvis/index_cli.py:380-397`
**Issue:** `Registry.__init__` runs `mkdir(parents=True)` + `CREATE TABLE` +
the ALTER sweep (each with its own commit) on every debounced watch event,
materializing an empty `registry.db` when watching a never-indexed repo. The
consult is read-*intent*, not read-only; the predicate itself is correct and
fails open.
**Fix:** Reword to "no status writes", or read via a read-only sqlite URI
when the file exists, failing open when it does not.

### IN-03: Unused `import time` in `tests/test_index_cli.py` *(restated, iteration 1)*

**File:** `tests/test_index_cli.py:16`
**Issue:** Never referenced — `_drive_cmd_watch` builds its own
`types.ModuleType("time")` fake; the only `time.` occurrences in the file are
inside docstrings.
**Fix:** Delete the import.

### IN-04: Degraded repos hitting navigation tools get generic error prose, unlike search-only repos *(restated, iteration 1)*

**File:** `src/jarvis/server.py:225-246`
**Issue:** `_error_payload` gives `search-only` rows prose explaining that
"searchCode and semanticSearch do work on it" (`:239-245`); a `degraded` row
falls to the generic `{"error": str(exc)}` with only the structured
`state`/`cause`/`recovery` keys added afterward. The same reassurance is true
for degraded rows. Possibly deliberate under the zero-payload-reshaping
constraint (tests pin "No `_error_payload` change needed"), hence Info.
**Fix:** If desired, extend the existing prose branch to cover
`DEGRADED_STATUS` — a wording change inside the existing `error` string.

### IN-05: WR-01 bookkeeping handler persists only the bookkeeping error — the original indexer failure that triggered the degrade is not persisted anywhere

**File:** `src/jarvis/index_cli.py:1153-1154`
**Issue:** In the bookkeeping-failure path,
`record_failure(..., bk_reason, bk_text)` stores only the sqlite error
(`status_reason`/`status_stderr` = "database is locked"). The indexer failure
that actually caused the degrade survives solely in the printed warning's
`{reason}` interpolation — after the fact, `jarvis status` shows
`cause: database is locked` with no trace of the underlying build failure.
The fix report documents this as a deliberate choice (proximate cause), so
Info — but the original `text` is in scope at that point and could ride along.
**Fix:** Persist both, e.g. `status_reason=bk_reason` but
`status_stderr=f"{bk_text}\n— original indexer failure —\n{text}"`.

### IN-06: README status enumeration omits `search-only` and the new `degraded` statuses

**File:** `README.md:173-176`
**Issue:** "`status` … is usually `indexed` or `failed`, but can also be
`partial`" — `search-only` and phase 3's `degraded` are never listed as
status values; `degraded` appears only inside the `--fallback-search-only`
flag description (`:168`) and the env-var section (`:324-326`). A user who
enables the fallback will routinely see `◐ degraded` in `jarvis list` with no
README explanation of the glyph family or the self-heal semantics.
**Fix:** Extend the sentence: "…can also be `partial`, `search-only`, or
`degraded` (the opt-in fallback's state: search works, navigation doesn't,
and the next reindex retries the full build — see `--fallback-search-only`)."

---

_Constraint audit (iteration 2, all verified against code + passing tests):_
_degraded exit 0 + exactly one warning line (`index_cli.py:1156-1162` return
path; `test_degraded_run_prints_exactly_one_warning_line`); status/origin
taxonomy incl. degraded reason+stderr (`registry.py:32-46, 161-171`;
`test_upsert_round_trips_status_stderr_and_plain_upsert_clears_it`);
self-heal `search_only=0` on degraded rows (`index_cli.py:1127-1131` omit
`search_only`; `test_degraded_repo_self_heals_to_indexed_on_a_successful_rerun`,
`test_degraded_repo_degrades_again_on_a_fresh_failure`); pre-build hardness
(`index_cli.py:952-960` early wrap; `test_pre_pipeline_version_gate_stays_hard_with_fallback_enabled`,
`test_missing_binary_failure_stays_hard_with_fallback_enabled`,
`test_bash_shim_failure_stays_hard_with_fallback_enabled`); sha-gated watch
skip with fail-open consult (`index_cli.py:337-397`;
`test_watch_should_retry_full_build_matrix`,
`test_watch_skip_check_skips_only_while_sha_unchanged`,
`test_watch_skip_check_fails_open_when_the_consult_raises`); degraded-publish
failure = hard failure with pointer survival (`_publish_search_only` zoekt-first
ordering `:801-804`; `test_failed_degraded_publish_preserves_the_previous_current_pointer`);
recovery verb (`registry.py:171-175`;
`test_recovery_for_fallback_origin_names_the_self_heal`); zero MCP payload
reshaping (server.py diff is the degraded nav_reason `elif` + imports;
`test_get_index_status_reports_last_index_run_for_a_degraded_repo`,
`test_error_payload_carries_state_cause_recovery_for_a_degraded_repo`)._

_New in iteration 2 and verified: the `published` flag cannot be set without a
live pointer (`:1071-1074`) and the WR-01 handler cannot exit 0 on a
bookkeeping failure (`:1155` raises; `test_degraded_row_write_failure_reports_what_landed`)._

_Verification: all suites re-run from the reviewed tree (630 passed /
17 deselected on the full gate); WR-02 and WR-03 reproduced with throwaway
scripts under /tmp against the current source; no source files modified._

_Reviewed: 2026-08-22T18:25:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard — iteration 2 of the --auto loop_
