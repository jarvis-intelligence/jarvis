---
phase: 03-opt-in-self-healing-fallback
reviewed: 2026-08-22T18:02:48Z
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
  critical: 1
  warning: 1
  info: 4
  total: 6
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-08-22T18:02:48Z
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Reviewed the phase-3 diff (`6e76115^..HEAD`) implementing the opt-in self-healing
search-only fallback: the `fallback_enabled` tri-state column, `DEGRADED_STATUS` +
`ORIGIN_FALLBACK`, `_resolve_fallback` precedence, the degrade gate in
`index_repo`, the publish-then-retire reorder, the MCP degraded nav.reason branch,
and the watch sha-skip. Full current file contents of all four source modules were
read; every phase-3 test addition was read; the suites pass (`tests/test_config.py`
+ `tests/test_registry.py`: 64 passed; `tests/test_index_cli.py` unit: 181 passed,
12 deselected; `tests/test_server_tools.py`: 45 passed).

All eight locked design constraints check out in the primary paths: degraded runs
exit 0 with exactly one `warning:` line (`index_repo` returns the slug; the test
pins the single "degraded to search-only" line); the terminal write persists
`status='degraded'`/`origin='fallback'` with reason+stderr; degraded rows keep
`search_only=0` so the next run retries the full build; pre-pipeline failures
raise inside the early wrap and never reach the degrade gate; the watch skip
predicate declines only `degraded` rows at an unchanged sha (and fails open); a
failed degraded publish falls through to `record_failure` + raise with the old
pointer intact; `recovery_for('fallback')` names `jarvis reindex <slug>`; and the
server diff adds no payload keys (only the nav.reason branch).

However, the degrade gate's scope is wrong: it wraps the *entire* main pipeline
try, including the success bookkeeping that runs **after** `_publish_atomically`
has already flipped the pointer. A registry write failure at that point (locked
`registry.db` — a race the code itself documents as legitimate — or disk-full)
makes the gate retire a fully-published, current SCIP index and record the run as
`degraded` citing the sqlite error as the "indexer failure". This was reproduced
end-to-end (see CR-01). A sibling scope problem in the degrade branch's inner
catch produces a factually false "nothing published" message (WR-01).

## Narrative Findings (AI reviewer)

## Critical Issues

### CR-01: Degrade gate fires on post-publish failures — destroys the just-published good index and mislabels the run `degraded`

**File:** `src/jarvis/index_cli.py:1063-1070, 1092-1097`
**Classification:** BLOCKER (incorrect behavior + data-loss risk)
**Issue:** The main-pipeline `try:` spans everything from the indexer through the
terminal bookkeeping. The only code after `_publish_atomically(target_dir,
versioned_name, sha)` (line 1063) is registry writes: the terminal
`registry.upsert(..., final_status, ...)` (1066), `mark_tracked_files` (1068),
and `mark_semantic_indexed` (1070). If any of those raise — a
`sqlite3.OperationalError("database is locked")` from a concurrent `jarvis watch`
reindex (the exact race `registry.py`'s `busy_timeout` comment calls
"legitimate"), or disk-full — the exception lands in the same `except` as a real
indexer failure, and the degrade gate at 1092-1094 passes (`fallback_enabled`,
not `MissingBinaryError`, not bash-shim). The branch then calls
`_publish_search_only`, whose `_retire_scip_artifacts` **rmtree's the
fully-published index directory** (`index_cli.py:804`), republishes zoekt-only,
and writes `status='degraded' / origin='fallback'` with `"database is locked"`
persisted as the failure reason. The run exits 0. So a registry hiccup after a
successful publish converts success into index destruction plus a lying row.

Reproduced with the phase-3 test mocks plus a `Registry.upsert` that raises only
on the terminal `status="indexed"` write, `fallback_search_only=True`:

```
warning: repo degraded to search-only — database is locked. ...
  published index dir still on disk: False
  registry row: status='degraded' origin='fallback' reason='database is locked'
```

The pointer existed and was current before the degrade branch deleted it. Note
the phase-3 `_publish_search_only` reorder explicitly exists so "a failed run
never destroys a good one" — this gate scope reintroduces exactly that destruction
through a different door.

**Fix:** Track whether anything is already published, and refuse to degrade past
that point:

```python
    published = False
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-index-") as scratch:
            ...
            _publish_atomically(target_dir, versioned_name, sha)
            published = True

        final_status = "indexed" if has_nav else PARTIAL_STATUS
        ...
    except Exception as exc:
        ...
        if (not published
                and fallback_enabled
                and not isinstance(exc, MissingBinaryError)
                and not _bash_shim_failure(text)):
```

Post-publish failures then fall through to `record_failure` + raise, leaving the
live pointer untouched — which is precisely the "failed run with a live pointer"
case `_capability_fields` already reports as outcome='failed' with
available-but-stale navigation. Add a regression test modeled on the repro above.

## Warnings

### WR-01: Degrade-branch inner catch conflates "publish failed" with "bookkeeping failed" — prints a false "nothing published" and mislabels a half-landed fallback

**File:** `src/jarvis/index_cli.py:1095-1121`
**Classification:** WARNING
**Issue:** The inner `try:` around the degraded publish wraps not just
`_publish_search_only` but also the `DEGRADED` upsert (1098-1102) and
`mark_tracked_files`/`mark_semantic_indexed` (1103-1105). If the search publish
succeeds (zoekt shards written, old SCIP artifacts retired) and the subsequent
registry write fails, the handler prints `"warning: fallback publish failed for
{slug} — nothing published"` — false: zoekt IS published and the previous SCIP
index WAS retired — then records `failed/failed_hard`. The row then contradicts
the disk (`capabilities.search.available` reads the shards and reports True
beside outcome='failed'), and if `record_failure` also fails the row is left
stuck at the transitional `'indexing'` status. Reproduced end-to-end (same harness
as CR-01, terminal upsert failing on both `'indexed'` and `'degraded'`):

```
warning: fallback publish failed for repo — nothing published; recording the original failure.
  published index dir still on disk: False   # scip retired; zoekt shards live
  registry row: status='failed' origin='failed_hard' reason='database is locked'
```

**Fix:** Narrow the inner `try` to the publish call only, so bookkeeping failures
aren't misreported:

```python
        if (...degrade gate...):
            try:
                semantic_ok, tracked = _publish_search_only(
                    repo_path, slug, root, semantic_include)
            except Exception:
                print(f"warning: fallback publish failed for {slug} — nothing "
                      "published; recording the original failure.", file=sys.stderr)
            else:
                registry.upsert(slug, str(repo_path), language, sha, DEGRADED_STATUS, ...)
                ...
                return slug
```

(The pre-existing search-only branch at 974-986 has the same breadth for its own
publish; at minimum the message should say "the fallback publish did not
complete" rather than "nothing published".)

## Info

### IN-01: `_SCHEMA` omits `fallback_enabled` — fresh databases get the column only via the post-create ALTER

**File:** `src/jarvis/registry.py:54-55, 193`
**Issue:** Every other migration-era column (`search_only`, `tracked_files`,
`status_origin`, `status_reason`, `status_stderr`) appears both in `_SCHEMA` and
in an `_ensure_column` call; `fallback_enabled` exists only as
`_ensure_column(self._conn, "fallback_enabled", "INTEGER")` (line 193). Fresh
databases are created without it and immediately ALTERed — functionally correct,
but it breaks the established belt-and-suspenders pattern for no stated reason
and leaves a window where the CREATE TABLE text disagrees with the shipped
schema.
**Fix:** Add `fallback_enabled INTEGER` to `_SCHEMA` (the duplicate-column
OperationalError is already tolerated by `_ensure_column`).

### IN-02: `_watch_skip_check` docstring claims "Read-only by design" but `Registry(...)` construction mutates the data dir

**File:** `src/jarvis/index_cli.py:380-397`
**Issue:** `Registry.__init__` runs `mkdir(parents=True)` + `CREATE TABLE` + ten
`ALTER TABLE` attempts (each with its own commit) on every debounced watch event,
and materializes an empty `registry.db` when watching a never-indexed repo. The
consult is read-*intent*, not read-only. Benign (the class contract, and the
predicate correctly fails open), but the docstring overstates the guarantee and
the per-event migration churn is avoidable.
**Fix:** Reword to "no status writes", or read via a plain
`sqlite3.connect(..., mode="ro")`/`file:...?immutable=0` URI when the file
exists, failing open when it does not.

### IN-03: Unused `import time` in `tests/test_index_cli.py`

**File:** `tests/test_index_cli.py:13`
**Issue:** Added in phase 3 but never referenced — `_drive_cmd_watch` builds its
own `types.ModuleType("time")` fake, and no test uses the real `time` module.
**Fix:** Delete the import.

### IN-04: Degraded repos hitting navigation tools get generic error prose, unlike search-only repos

**File:** `src/jarvis/server.py:239-248`
**Issue:** `_error_payload` gives `search-only` rows a prose explanation that
"searchCode and semanticSearch do work"; a `degraded` row falls to the generic
`{"error": str(exc)}` (line 248) with only the structured `state`/`cause`/
`recovery` keys added afterward. The same reassurance is true for degraded rows
(zoekt + semantic are published), so prose-only MCP clients get a less helpful
message for the state the fallback exists to create. Possibly deliberate
("zero payload reshaping" — the new tests pin "No _error_payload change
needed"), hence Info.
**Fix:** If desired, extend the existing prose branch to cover
`DEGRADED_STATUS` — a wording change inside the existing `error` string, not a
payload-structure change.

---

_Constraint audit (all verified against code + passing tests): exit-0/one-warning
degraded contract (`index_repo` return path, `test_degraded_run_prints_exactly_one_warning_line`);
degraded persistence incl. stderr (`index_cli.py:1098-1102`,
`test_upsert_round_trips_status_stderr_and_plain_upsert_clears_it`); no
`search_only=1` reuse (`test_degraded_repo_self_heals_to_indexed_on_a_successful_rerun`,
`test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo` pins the
signature path stays phase-2); pre-pipeline hardness
(`test_pre_pipeline_version_gate_stays_hard_with_fallback_enabled`); watch skip =
degraded AND unchanged sha with fail-open consult
(`test_watch_should_retry_full_build_matrix`, `test_watch_skip_check_*`);
degraded-publish failure = hard failure with pointer survival
(`test_failed_degraded_publish_preserves_the_previous_current_pointer`);
`recovery_for('fallback')` wording (`test_recovery_for_fallback_origin_names_the_self_heal`);
zero MCP payload reshaping (server diff is one `elif` +
import; `test_get_index_status_reports_last_index_run_for_a_degraded_repo`)._

_Reviewed: 2026-08-22T18:02:48Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
