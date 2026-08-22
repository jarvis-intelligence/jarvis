---
phase: 03-opt-in-self-healing-fallback
verified: 2026-08-23T00:00:00Z
status: human_needed
score: 13/14 must-haves verified
behavior_unverified: 1
overrides_applied: 0
re_verification:
  previous_status: none
gaps: []
behavior_unverified_items:
  - truth: "Concurrency (verification: backstop): a watch reindex racing a manual index on the same registry relies on busy_timeout=5000 retry rather than corruption; an interrupted run may strand a transitional 'indexing' row, mitigated because the next reindex always retries the full build"
    test: "Run `jarvis watch <repo>` in one terminal and `jarvis index <repo>` in another on the same repo (optionally induce a failure mid-run); inspect registry.db after both settle"
    expected: "No 'database is locked' crash and no corrupted row; a stranded 'indexing' row (if any) is retried by the next reindex — the full build runs again (no skip)"
    why_human: "Backstop tier: no two-process race test exists. The 5 review regression tests simulate locked-db single-process (sqlite3.OperationalError('database is locked') monkeypatches, all passing), busy_timeout=5000 is present in Registry.__init__, and the skip predicate provably never skips a non-degraded row — but a live concurrent interleave is not exercisable by grep or the unit suite"
coincidental_reliance_items: []
human_verification:
  - test: "Real-binary degrade smoke: with a real repo and real binaries, break one post-build-start step (or point JARVIS_FALLBACK_SEARCH_ONLY=1 at a repo whose indexer fails) and run `jarvis index <repo> --fallback-search-only`"
    expected: "Exit 0, one 'degraded to search-only' warning, row status degraded/origin fallback, and searchCode answering against the freshly published zoekt shards (SC1 'stay queryable' end-to-end)"
    why_human: "Unit tracer mocks _run, so zoekt shards are never actually written in tests; the publish mechanism itself is integration-proven (test_zoekt_git_index_excludes_gitignored_content spins up a real zoekt-webserver), but no integration test drives the degrade path with real binaries — the plan lists this smoke as the optional manual verification"
  - test: "Live watch-vs-manual race: run `jarvis watch <repo>` and a concurrent `jarvis index <repo>` on the same registry.db; then commit a source change while the repo is degraded"
    expected: "No lock crash; degraded-at-unchanged-sha saves skip the full build with one '[watch] still degraded' note; the new commit (sha change) re-triggers the full build"
    why_human: "Backstop truth — two-process interleaving and real-time watchdog behavior cannot be verified by the unit harness (which drives _reindex through a fake Observer)"
  - test: "Review the 18 judgment-tier prohibitions (Prohibition Verdicts below): all recorded as held on mechanical evidence (grep/git-diff/passing tests), but the tier is non-authoritative by decision (ADR-550 D4)"
    expected: "Confirm no must-NOT was violated; any concern becomes a follow-up"
    why_human: "Descriptor-less prohibitions are judgment-tier: an LLM-judge verdict plus code evidence is recorded, but it is non-authoritative and flagged `unverified-prohibition — human review recommended`, never a silent pass"
  - test: "`jarvis watch` foreground flow with the real watchdog Observer (real filesystem events, debounce, Ctrl+C exit)"
    expected: "Changes trigger debounced reindex; skip note appears for a degraded repo at unchanged sha; Ctrl+C exits cleanly"
    why_human: "Real-time observer-thread behavior; 03-03's harness covers the _reindex closure only (03-03 SUMMARY flags this as manual-acceptance-only)"
---

# Phase 3: Opt-In Self-Healing Fallback Verification Report

**Phase Goal:** With fallback enabled, a post-build-start indexer failure degrades the repo to a working search-only index instead of leaving it with nothing — and every later reindex automatically retries the full build
**Verified:** 2026-08-23
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

The goal is achieved in the codebase. The degrade branch is wired into `index_repo`'s main-pipeline except handler behind a tri-state resolution (CLI > persisted > env > off) with an exclusion ladder (MissingBinaryError, bash-shim, post-publish bookkeeping failures); it publishes through the existing `_publish_search_only` (now zoekt-before-retire), writes the terminal `degraded`/`fallback` row with reason + full stderr + attempt sha and `search_only=False`, and returns the slug (exit 0). Degraded rows never trap: every `jarvis index`/`reindex` retries the full build; only the watch driver declines at an unchanged sha. All 5 roadmap success criteria are code-verified with passing behavioral tests. One `verification: backstop` truth (live concurrency) and the judgment-tier prohibition block route to human verification per the honest-verifier contract — nothing failed.

### Observable Truths

| # | Truth (source) | Status | Evidence |
|---|---|---|---|
| 1 | SC1/FALL-01: induced post-build-start failure with fallback on publishes search-only, returns slug (exit 0); row degraded/fallback, reason=first line, stderr=full text, search_only=False, commit_sha=attempt sha (03-01 T1) | ✓ VERIFIED | `test_degraded_publish_on_post_build_start_failure` (tests/test_index_cli.py:2888) asserts every field incl. `search_only is False` and `commit_sha == git HEAD`; body read; 51-test filter green. Degrade gate read at index_cli.py:1162-1242 (`not published and fallback_enabled and not MissingBinaryError and not _bash_shim_failure`) |
| 2 | FALL-01 stderr contract: exactly one "degraded to search-only" warning, run is a success | ✓ VERIFIED | `test_degraded_run_prints_exactly_one_warning_line`; single `print(...degraded to search-only...)` on the success path (index_cli.py:1236-1241) |
| 3 | SC1/FALL-01 ordering: degraded publish with failing zoekt step leaves previous current pointer intact, row failed/failed_hard — zoekt published before SCIP retire | ✓ VERIFIED | `test_failed_degraded_publish_preserves_the_previous_current_pointer` (two-phase); `_publish_search_only` read: `_retire_scip_artifacts` follows the zoekt `_run` + sweep and is wrapped into `SearchPublishedButIncomplete`; WR-02 regression `test_degraded_publish_retire_failure_reports_partial_landing` also proves pointer survival on retire failure |
| 4 | SC4/FALL-04: missing-binary, bash-shim, version-floor (pre-pipeline), language-detection, duplicate-slug failures stay hard (raise, failed/failed_hard) with fallback enabled | ✓ VERIFIED | `test_missing_binary_failure_stays_hard_with_fallback_enabled`, `test_bash_shim_failure_stays_hard_with_fallback_enabled`, `test_pre_pipeline_version_gate_stays_hard_with_fallback_enabled`, `test_run_translates_file_not_found_into_missing_binary_error` (MissingBinaryError at index_cli.py:166, raised at _run's FileNotFoundError site :431); pre-pipeline wrap (:951-1005) has no degrade branch — language-detection and duplicate-slug raise inside/before it (`test_duplicate_slug_rejection_writes_no_failure_row` tripwire); post-publish exclusion via `published` flag (CR-01 regression test) |
| 5 | SC2/FALL-02: precedence CLI > persisted > env > off across the full matrix; only explicit CLI value persisted; no-flag run leaves fallback_enabled NULL | ✓ VERIFIED | `test_fallback_precedence_matrix_cli_persisted_env` — 18 parametrized cells (3 CLI × 3 persisted × 2 env; plan said "12-cell", implementation exceeds); `test_explicit_cli_fallback_flag_persists_after_a_healthy_run`; `test_no_cli_flag_leaves_fallback_enabled_null` (env off AND on); `set_fallback_enabled` gated on `fallback_search_only is not None` at both transitional upserts (index_cli.py:1016-1017, 1045-1046) — never the resolved bool |
| 6 | FALL-02 env tier: NULL defers to env; JARVIS_FALLBACK_SEARCH_ONLY accepts exactly 1/true/yes/on case-insensitive, warns once on garbage, unset=off | ✓ VERIFIED | config.py `_FALLBACK_TRUTHY` frozenset + `fallback_search_only_from_env()` (read); `test_config.py` strict matrix, unset, garbage-warn-once (3 test groups); env var read ONLY in config.py (repo-wide grep) |
| 7 | FALL-02 opt-out immediate: --no-fallback-search-only on degraded repo → next still-broken run is a hard failure (no trap) | ✓ VERIFIED | `test_explicit_fallback_opt_out_governs_immediately_on_a_degraded_repo` |
| 8 | SC3/FALL-03: degraded self-heals — repair + reindex (no CLI flag) ends indexed with origin/reason/stderr all NULL (D-04); still-broken degrades again on fresh failure; degraded stays distinct from search-only (signature match pre-empts to search_only=1/origin signature) | ✓ VERIFIED | `test_degraded_repo_self_heals_to_indexed_on_a_successful_rerun` (asserts `(origin, reason, stderr) == (None, None, None)`); `test_degraded_repo_degrades_again_on_a_fresh_failure` (fresh reason text); `test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo`; degraded terminal write never passes `search_only=True` |
| 9 | FALL-01 MCP visibility: getIndexStatus degraded → navigation.available false, reason=persisted status_reason (default "indexer failure — degraded to search-only"), recovery with fallback verb; last_index_run outcome=degraded/origin=fallback; _error_payload state=fallback/cause/recovery | ✓ VERIFIED | `test_get_index_status_navigation_unavailable_for_degraded_reports_cause_and_recovery`, `..._degraded_reason_defaults_when_status_reason_missing`, `..._reports_last_index_run_for_a_degraded_repo`, `test_error_payload_carries_state_cause_recovery_for_a_degraded_repo`; server.py:186-190 read; phase-3 server.py diff = import + 1 elif only (zero reshaping confirmed) |
| 10 | FALL-01 CLI rendering: list renders ◐ degraded with reason as 6th TSV field; status prints origin: fallback, cause, recovery naming jarvis reindex; search-only rows keep 5 fields | ✓ VERIFIED | `test_cmd_list_renders_degraded_rows_with_glyph_and_reason` (asserts 6 fields), `test_cmd_status_explains_a_degraded_repo`, `test_cmd_list_keeps_search_only_rows_five_field_beside_degraded`; _cmd_list read (index_cli.py:1283-1290, 6th field only for failed/degraded) |
| 11 | SC5/FALL-05 predicate: `_watch_should_retry_full_build` False ONLY for degraded + non-NULL commit_sha == current sha; None entry, failed (even matching sha), indexed, search-only, degraded+NULL, degraded+changed all retry | ✓ VERIFIED | `test_watch_should_retry_full_build_matrix` — 7 parametrized rows, exactly `degraded-same-sha` False; pure body read (index_cli.py:369-386: single boolean, no subprocess/Registry/watchdog) |
| 12 | SC5/FALL-05 watch idempotency + source change: second fire at unchanged sha skips with one stderr note and does NOT invoke index_repo; explicit index always retries; a new commit (sha change) re-triggers the full build | ✓ VERIFIED | `test_cmd_watch_skips_full_build_retry_on_degraded_row_at_same_sha` (fake-Observer harness proves non-invocation + one note), `test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo`, `test_watch_skip_check_skips_only_while_sha_unchanged` (real Registry + real git commit → consult flips to retry), `test_watch_skip_check_fails_open_when_the_consult_raises`; skip consult called only from `_cmd_watch._reindex` (grep: single call site, index_repo never) |
| 13 | FALL-02 watch surface: watch accepts the tri-state --fallback/--no- pair (BooleanOptionalAction, default None) and forwards it; reindex honors persisted value with no new flag | ✓ VERIFIED | `test_watch_parser_accepts_tri_state_fallback_flag` (True/False/None); pass-through at index_cli.py:1481-1482; `reindex_parser` declares only `slug` (grep :1573-1575); both parsers read at :1556-1563, :1594-1601 |
| 14 | Concurrency (03-01, `verification: backstop`): watch-vs-manual race relies on busy_timeout=5000 retry rather than corruption; stranded transitional 'indexing' row mitigated because next reindex always retries the full build | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | Components present + wired: `PRAGMA busy_timeout = 5000` in Registry.__init__ (read); 5 locked-db regression tests pass (CR-01/WR-01/WR-02/WR-03 ×2 — simulate exactly the watch-race locked write); skip predicate provably never skips a non-degraded row (matrix). But backstop tier requires directly-observed/held-out evidence of the live two-process interleave — none exists. See behavior_unverified_items + Human Verification |

**Score:** 13/14 truths verified (1 present, behavior-unverified)

### Prohibition Verdicts

All 18 descriptor-less prohibitions (judgment-tier, non-authoritative per ADR-550 D4) — LLM-judge verdict **held** on mechanical evidence; flagged `unverified-prohibition — human review recommended`, never counted as silent passes:

| # | Plan | Prohibition (abridged) | Verdict | Evidence |
|---|---|---|---|---|
| 1 | 03-01 | Never persist the resolved fallback bool | held | `set_fallback_enabled` called only with the CLI param `fallback_search_only` at :1016/:1045; no other call sites (grep) |
| 2 | 03-01 | Never set search_only=True on a degraded row | held | Degraded terminal upsert (:1207-1211) omits `search_only` → False default; tracer test asserts `search_only is False` |
| 3 | 03-01 | Never retire SCIP artifacts before zoekt publish succeeds | held | `_publish_search_only`: retire follows zoekt `_run` + sweep; wrapped in `SearchPublishedButIncomplete`; pointer-preservation tests green |
| 4 | 03-01 | No degrade handling in the pre-pipeline wrap; no second publish path | held | Pre-pipeline wrap (:951-1005) has no fallback branch; single `_publish_search_only` definition |
| 5 | 03-01 | Never accept non-1/true/yes/on env value as on | held | `_FALLBACK_TRUTHY` frozenset; strict matrix + garbage-warn-once tests green |
| 6 | 03-01 | Never read JARVIS_FALLBACK_SEARCH_ONLY outside config.py | held | Repo-wide grep: env read only at config.py:85 (other hits are docstrings/help text) |
| 7 | 03-01 | Never add --fallback-search-only to reindex parser | held | `reindex_parser.add_argument` = `slug` only |
| 8 | 03-01 | Terminal upserts never reset fallback_enabled | held | upsert INSERT + ON CONFLICT column lists exclude `fallback_enabled` (read); tri-state roundtrip test asserts plain upsert leaves stored value |
| 9 | 03-01 | `jarvis index` never skips the full build for a degraded row | held | index_repo body contains no skip consult; `_watch_skip_check` grep: single call site in `_cmd_watch` |
| 10 | 03-02 | Never reshape phase-1 payload contracts | held | Phase-3 `git diff server.py` = DEGRADED_STATUS import + one elif (7 lines total) |
| 11 | 03-02 | No degraded prose branches in _error_payload | held | Same diff — `_error_payload` untouched |
| 12 | 03-02 | status_stderr never enters an MCP payload | held | server.py key-by-key builders; `status_stderr` appears only in docstring rationale (:157, :234) |
| 13 | 03-02 | list's 5-column TSV contract never changes | held | 6th field emitted only for `status in ("failed", DEGRADED_STATUS)`; 5-field regression test green |
| 14 | 03-03 | sha-keyed skip never lives inside index_repo | held | Predicate + consult module-level; called from `_cmd_watch` only |
| 15 | 03-03 | Skip helper/tests never require the watchdog extra | held | Predicate body is pure; tests use fake sys.modules Observer (03-03 harness); no `import watchdog` in tests |
| 16 | 03-03 | Skip consult never writes registry state | held | `_watch_skip_check` = Registry open + `get` + `_git_head` only (read :389-407) |
| 17 | 03-03 | NULL commit_sha must never skip | held | Predicate requires `commit_sha is not None`; `degraded-null-sha` matrix row → True (retry) |
| 18 | 03-03 | Never gate skip on time/clock state | held | Predicate consults entry + sha only; no time module in body |

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| src/jarvis/registry.py | DEGRADED_STATUS, ORIGIN_FALLBACK, fallback_enabled column, set_fallback_enabled, upsert status_stderr, recovery_for branch | ✓ VERIFIED | All read in source; `_ensure_column("fallback_enabled", "INTEGER")` additive; `_SCHEMA` untouched; SELECTs in get/list carry the column |
| src/jarvis/config.py | fallback_search_only_from_env + strict truthy set | ✓ VERIFIED | Read; strict frozenset, warn-once, unset=off |
| src/jarvis/index_cli.py | MissingBinaryError, _resolve_fallback, degrade gate, ordering fix, parsers, watch skip | ✓ VERIFIED | All read in source at cited lines |
| src/jarvis/server.py | navigation.reason degraded branch only | ✓ VERIFIED | Read + git diff (7 lines) |
| tests/test_registry.py | migration, tri-state, recovery wording, stderr carrier + D-04 | ✓ VERIFIED | 4 tests present (:577-:648), 64-test suite green |
| tests/test_config.py | strict env matrix | ✓ VERIFIED | 3 test groups (:91-:104) green |
| tests/test_index_cli.py | full FALL-01..05 coverage | ✓ VERIFIED | 51-test filter green; tracer/matrix/watch bodies read and substantive |
| tests/test_server_tools.py | degraded MCP pins | ✓ VERIFIED | 5 degraded tests green |
| README.md | env-var entry + usage line | ✓ VERIFIED | :167-168 usage, :323-327 env entry naming 1/true/yes/on + warning behavior |

### Key Link Verification

| From | To | Via | Status |
|---|---|---|---|
| index_cli degrade gate | registry.upsert(DEGRADED_STATUS, ORIGIN_FALLBACK, status_stderr=text) | terminal write at :1207-1211 | ✓ WIRED (tracer test proves the row) |
| index_cli._resolve_fallback | config.fallback_search_only_from_env | env tier read once, only from config.py | ✓ WIRED (:682; grep isolation) |
| registry.set_fallback_enabled | both transitional 'indexing' upserts in index_repo | explicit-CLI-only persistence | ✓ WIRED (:1016-1017, :1045-1046; persistence tests) |
| degraded terminal write's commit_sha | _watch_should_retry_full_build | attempt-sha key | ✓ WIRED (tracer asserts sha==HEAD; consult test writes the same shape and skips) |
| server._capability_fields degraded branch | registry.recovery_for ORIGIN_FALLBACK verb | nav_recovery line | ✓ WIRED (server tests assert "jarvis reindex" in recovery) |
| _cmd_watch._reindex | index_repo(fallback_search_only=...) | pass-through beside scheme/language | ✓ WIRED (:1481-1482; forwarding test) |
| degraded row's status_origin='fallback' | server._error_payload origin keys | phase-1 verbatim flow, no change | ✓ WIRED (payload pin test) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| index_cli degrade gate | reason / text | live exception from `_run` (indexer stderr) | Yes — persisted verbatim to row (tracer asserts full multi-line text) | ✓ FLOWING |
| _cmd_list / _cmd_status | repo.status_reason | registry row via SELECT | Yes (capsys tests) | ✓ FLOWING |
| server._capability_fields | nav_reason ← entry.status_reason | registry row | Yes (server tests) | ✓ FLOWING |
| _watch_skip_check | entry.commit_sha vs _git_head | registry + real git subprocess | Yes (consult test uses a real commit) | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Full unit suite (CI gate) | `uv run python -m pytest -m "not integration" -q` | 633 passed, 17 deselected (34.5s) | ✓ PASS |
| Registry + config carriers | `uv run python -m pytest tests/test_registry.py tests/test_config.py -q` | 64 passed | ✓ PASS |
| FALL-01..04 core (degraded/fallback/hard_failure/preserves/precedence/self_heal/degrades_again/preempts) | `uv run python -m pytest tests/test_index_cli.py -k "..." -q` | 51 passed | ✓ PASS |
| 5 review-fix regression tests (CR-01, WR-01, WR-02, WR-03 ×2) | `uv run python -m pytest tests/test_index_cli.py -k "post_publish_registry_failure... or degraded_row_write... or degraded_publish_retire... or degraded_row_write_and_record... or pre_pipeline_record_failure..."` -q | 5 passed — the four fixes are load-bearing (each fails on the pre-fix behavior it pins) | ✓ PASS |
| MCP degraded surfaces | `uv run python -m pytest tests/test_server_tools.py -k "degraded" -q` | 5 passed | ✓ PASS |
| Watch (skip matrix, consult, parser, pass-through, driver) | `uv run python -m pytest tests/test_index_cli.py -k "watch" -q` | 13 passed | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| FALL-01 | 03-01, 03-02 | Post-build-start failure publishes search-only instead of nothing | ✓ SATISFIED | Truths 1-3, 9-10 |
| FALL-02 | 03-01, 03-03 | Opt-in tri-state CLI flag + env var; CLI > persisted > env > off | ✓ SATISFIED | Truths 5-7, 13 |
| FALL-03 | 03-01 | Degraded self-heals; re-degrades only on fresh failure | ✓ SATISFIED | Truth 8 |
| FALL-04 | 03-01 | Pre-build failures stay hard with fallback enabled | ✓ SATISFIED | Truth 4 |
| FALL-05 | 03-03 | Watch sha-keyed retry skip; no treadmill | ✓ SATISFIED | Truths 11-12 |

Orphaned requirements: none — REQUIREMENTS.md Phase 3 set (FALL-01..05) equals the union of plan `requirements` frontmatter fields.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| tests/test_server_tools.py | 132, 181 | "PLACEHOLDER" match | ℹ️ Info | Test-fixture script template (`PYTHON_SHEBANG_PLACEHOLDER` substituted with sys.executable) — not a stub |

No TODO/FIXME/XXX/HACK markers in any of the 9 phase files. All 18 phase commits verified present in git log (e699fcd..846af66 incl. review fixes 5cf30bd, 1dbefde, d76eb08, d70e084). Phase footprint: 9 files, +1817/−39 — exactly the union of plan `files_modified` declarations.

### Human Verification Required

1. **Real-binary degrade smoke (SC1 queryability)** — break a post-build-start step on a real repo, run `jarvis index <repo> --fallback-search-only`; expect exit 0, one warning, degraded row, and searchCode answering against the published zoekt shards. Unit tracer mocks `_run`; no integration test drives the degrade path with real binaries.
2. **Live watch-vs-manual race + degraded watch flow** — the backstop concurrency truth (behavior_unverified_items[0]): two concurrent processes on one registry.db; skip note at unchanged sha; full-build retry on a new commit. No two-process test exists; real-time Observer behavior is uncovered by the unit harness.
3. **Judgment-tier prohibition block (18 items)** — all verdicts "held" on mechanical evidence but non-authoritative per tier; confirm none was violated (`unverified-prohibition — human review recommended`).
4. **`jarvis watch` foreground flow with real watchdog Observer** — real filesystem events, debounce, Ctrl+C exit (03-03 flags this manual-acceptance-only).

### Gaps Summary

No gaps. All 13 code-level truths verified with passing behavioral tests I ran myself; all 9 artifacts exist, substantive, wired, data flowing; all 7 key links wired; requirements FALL-01..05 fully covered with no orphans; the four review fixes are pinned by load-bearing regression tests that pass. The status is **human_needed** solely because one `verification: backstop` truth (live concurrency) cannot be confirmed without direct evidence and the judgment-tier prohibitions are non-authoritative by contract — both route to human verification rather than a silent pass.

---

_Verified: 2026-08-23_
_Verifier: Claude (gsd-verifier)_
