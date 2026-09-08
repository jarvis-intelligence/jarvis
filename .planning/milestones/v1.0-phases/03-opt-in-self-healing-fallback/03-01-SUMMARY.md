---
phase: 03-opt-in-self-healing-fallback
plan: "01"
subsystem: database
tags: [sqlite, registry, cli, self-healing-fallback, degraded-publish, tri-state-flag]

# Dependency graph
requires:
  - phase: 01-registry-foundation-degradation-reporting
    provides: status_origin/status_reason/status_stderr columns, origin taxonomy, record_failure, D-04 NULL-clearing upsert conflict list
provides:
  - registry carriers: DEGRADED_STATUS ("degraded"), ORIGIN_FALLBACK ("fallback"), fallback_enabled tri-state column (via _ensure_column), RegisteredRepo.fallback_enabled, Registry.set_fallback_enabled, upsert(status_stderr=...), recovery_for(ORIGIN_FALLBACK) branch
  - config.fallback_search_only_from_env() strict truthy accessor (1/true/yes/on; warn-once garbage; unset=off)
  - index_cli: MissingBinaryError(IndexingError) from _run's FileNotFoundError site, _resolve_fallback (CLI > persisted > env > off), index_repo(fallback_search_only=...), the degrade gate + degraded terminal write in the main-pipeline except, zoekt-before-retire _publish_search_only ordering, index_parser --fallback-search-only/--no-fallback-search-only (BooleanOptionalAction)
  - degraded terminal write's commit_sha = the attempt sha (FALL-05 key for Plan 03-03's _watch_should_retry_full_build)
affects: [03-02 status/list/MCP degraded reporting, 03-03 watch sha-keyed skip + flag pass-through]

actuals:
  tokens: 12764  # 51054 diff chars / 4 (plan estimate: 95000)
  tasks: 3
  commits: 6

tech-stack:
  added: []  # stdlib argparse.BooleanOptionalAction + sqlite3 + pytest only
  patterns:
    - "Tri-state persistence deviation: only the explicit CLI value is persisted (dedicated setter), never the resolved bool — the _resolve_scheme write-back idiom would collapse NULL to 0 (Pitfall 1)"
    - "Degrade gate as an ordered exclusion ladder in the single main-pipeline except: fallback_enabled AND NOT MissingBinaryError AND NOT bash-shim (pre-pipeline failures structurally never reach it)"
    - "Publish-then-retire in _publish_search_only — one path fixes all three callers (manual/signature/degraded)"

key-files:
  created: []
  modified:
    - src/jarvis/registry.py
    - src/jarvis/config.py
    - src/jarvis/index_cli.py
    - tests/test_registry.py
    - tests/test_config.py
    - tests/test_index_cli.py

key-decisions:
  - "Reason = first non-empty line (existing D-03 shape); the degraded row persists reason AND full stderr verbatim through the new upsert status_stderr param — record_failure was never touched"
  - "A failed degraded publish prints one stderr note (\"fallback publish failed ... nothing published\") then falls through to the ordinary record_failure/raise with the ORIGINAL failure text — nothing was published, the fallback promise is void (Area 3)"
  - "MissingBinaryError as an IndexingError subclass at _run's FileNotFoundError site — typed exclusion at the degrade gate; every existing except IndexingError site keeps working"
  - "set_fallback_enabled is a bare UPDATE mirroring mark_tracked_files (called only after a row exists via the transitional upserts); pre-pipeline failures intentionally leave the flag unpersisted, matching --scheme/--language semantics"
  - "Extended the three existing index_repo fake signatures (forwarding tests) for the new kwarg instead of switching them to **kwargs — they deliberately mirror the real signature"

patterns-established:
  - "Degraded ≠ search-only: status='degraded' keeps search_only=False so every later run retries the full build (the one-way search_only=1 trap is never set by the fallback)"
  - "BooleanOptionalAction tri-state CLI pair (--x/--no-x, default None) — the flag convention for future clearable persisted flags"

requirements-completed: [FALL-01, FALL-02, FALL-03, FALL-04]

coverage:
  - id: D1
    description: "Registry + config carriers: fallback_enabled migrates additively (NULL default), tri-state roundtrip, locked recovery wording, upsert status_stderr carrier with D-04 clearing, strict env matrix"
    requirement: FALL-02
    verification:
      - kind: unit
        ref: tests/test_registry.py#test_fallback_enabled_column_migrates_onto_an_existing_database
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_fallback_enabled_tri_state_roundtrip
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_recovery_for_fallback_origin_names_the_self_heal
        status: pass
      - kind: unit
        ref: tests/test_registry.py#test_upsert_round_trips_status_stderr_and_plain_upsert_clears_it
        status: pass
      - kind: unit
        ref: tests/test_config.py#test_fallback_env_var_garbage_reads_off_with_exactly_one_warning
        status: pass
    human_judgment: false
  - id: D2
    description: "FALL-01 end-to-end: induced post-build-start failure with fallback on returns the slug (exit 0) and leaves a degraded/fallback row (reason, full stderr, search_only False, attempt sha) with exactly one stderr warning"
    requirement: FALL-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_degraded_publish_on_post_build_start_failure
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_degraded_run_prints_exactly_one_warning_line
        status: pass
    human_judgment: false
  - id: D3
    description: "FALL-04 boundary: missing-binary (MissingBinaryError), bash-shim (both tokens), and pre-pipeline version-floor failures stay hard failures (raise, failed/failed_hard) even with fallback enabled"
    requirement: FALL-04
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_missing_binary_failure_stays_hard_with_fallback_enabled
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_run_translates_file_not_found_into_missing_binary_error
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_bash_shim_failure_stays_hard_with_fallback_enabled
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_pre_pipeline_version_gate_stays_hard_with_fallback_enabled
        status: pass
    human_judgment: false
  - id: D4
    description: "Corrected publish ordering: a degraded publish whose zoekt step fails leaves the previously-written current pointer intact and the row failed/failed_hard"
    requirement: FALL-01
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_failed_degraded_publish_preserves_the_previous_current_pointer
        status: pass
    human_judgment: false
  - id: D5
    description: "FALL-02 precedence and persistence: full CLI×persisted×env matrix, explicit-only persistence (NULL stays NULL with env off AND on), immediate opt-out on a degraded repo"
    requirement: FALL-02
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_fallback_precedence_matrix_cli_persisted_env
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_explicit_cli_fallback_flag_persists_after_a_healthy_run
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_no_cli_flag_leaves_fallback_enabled_null
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_explicit_fallback_opt_out_governs_immediately_on_a_degraded_repo
        status: pass
      - kind: other
        ref: real-CLI smoke (isolated JARVIS_DATA_DIR, real binaries): --fallback-search-only indexes with exit 0 and persists fallback_enabled=1; no-flag rerun keeps 1; JARVIS_FALLBACK_SEARCH_ONLY=maybe on a fresh repo prints exactly one warning and leaves NULL
        status: pass
    human_judgment: false
  - id: D6
    description: "FALL-03 self-heal: degrade-then-repair ends indexed with origin/reason/stderr all NULL; still-broken rerun degrades again with a fresh failure record; signature match preempts the degrade branch (search_only=True, origin signature)"
    requirement: FALL-03
    verification:
      - kind: unit
        ref: tests/test_index_cli.py#test_degraded_repo_self_heals_to_indexed_on_a_successful_rerun
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_degraded_repo_degrades_again_on_a_fresh_failure
        status: pass
      - kind: unit
        ref: tests/test_index_cli.py#test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo
        status: pass
    human_judgment: false

# Metrics
duration: 23min
completed: 2026-08-22
status: complete
---

# Phase 3 Plan 01: Opt-In Self-Healing Fallback Core Summary

**Degraded publish wired end-to-end: a post-build-start failure with the tri-state fallback on (CLI > persisted > env) publishes search-only with exit 0 and a degraded/fallback row, self-heals on the next run, keeps pre-build failures loud, and never destroys an existing index on a failing fallback publish.**

## Performance

- **Duration:** 23 min
- **Started:** 2026-08-22T17:07:40Z
- **Completed:** 2026-08-22T17:30:43Z
- **Tasks:** 3 (all tdd; 6 RED/GREEN commits)
- **Files modified:** 6

## Accomplishments
- FALL-01: the degrade branch in `index_repo`'s main-pipeline except publishes Zoekt + semantic via the existing `_publish_search_only`, writes the terminal row (`status='degraded'`, `origin='fallback'`, one-line reason, full stderr, attempt sha, `search_only=False`), prints exactly one stderr warning, and returns the slug (exit 0)
- FALL-02: tri-state opt-in resolved CLI > persisted > env > off; only the explicit CLI value is ever persisted (`set_fallback_enabled` at both transitional upserts — NULL stays NULL, so a later env-on still governs); `JARVIS_FALLBACK_SEARCH_ONLY` parses a strict 1/true/yes/on set and warns once on garbage
- FALL-03: degraded rows keep `search_only=False` so every rerun retries the full build — success NULL-clears all three failure fields (D-04), a fresh failure degrades again, and a signature-matched failure still pre-empts with permanent search-only
- FALL-04: missing-binary (`MissingBinaryError`), bash-shim (both tokens), and all pre-pipeline gates stay loud hard failures even with fallback on
- Publish-ordering fix: `_publish_search_only` now retires SCIP artifacts only after the zoekt publish succeeds — a failing fallback publish can no longer destroy a previously-good index (benefits the manual and signature callers too)

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Registry + config carriers** — `e699fcd` (test) + `fb23997` (feat)
2. **Task 2: End-to-end degraded publish (tracer)** — `a224a80` (test) + `4a6df3d` (feat)
3. **Task 3: Persistence wiring, precedence matrix, self-heal pins** — `b6640b1` (test) + `0738513` (feat)

## Files Created/Modified
- `src/jarvis/registry.py` — DEGRADED_STATUS/ORIGIN_FALLBACK, fallback_enabled column (additive `_ensure_column`, `_SCHEMA` untouched), RegisteredRepo field + SELECTs, upsert `status_stderr` param, `recovery_for` fallback branch, `set_fallback_enabled` setter
- `src/jarvis/config.py` — `_FALLBACK_TRUTHY` + `fallback_search_only_from_env()` (strict set, warn-once, unset=off)
- `src/jarvis/index_cli.py` — MissingBinaryError, `_resolve_fallback`, `index_repo(fallback_search_only=...)`, degrade gate + degraded terminal write, explicit-only persistence at both transitional upserts, zoekt-before-retire `_publish_search_only`, `--fallback-search-only` parser flag + `_cmd_index` forwarding
- `tests/test_registry.py` — migration, tri-state roundtrip, recovery wording, stderr carrier + D-04 clearing (4 tests)
- `tests/test_config.py` — strict truthy env matrix, unset, garbage warn-once (3 parametrized groups)
- `tests/test_index_cli.py` — degraded happy path, stderr contract, three FALL-04 exclusions, `_run` translation, pointer preservation, 18-cell precedence matrix, persistence semantics, self-heal, re-degrade, signature precedence (13 tests + 2 helpers)

## Decisions Made
- A failed degraded publish prints one stderr note then falls through to the ordinary `record_failure`/raise with the original failure text — nothing was published, so the fallback promise is void (CONTEXT Area 3); the note makes the double failure debuggable
- `MissingBinaryError(IndexingError)` raised at `_run`'s FileNotFoundError site — typed exclusion at the degrade gate, zero churn at existing except-sites
- Persistence call sites sit immediately after BOTH transitional `indexing` upserts (branches diverge before their first upsert); pre-pipeline failures intentionally leave the flag unpersisted, matching `--scheme`/`language` semantics — the retry re-supplies the flag
- Kept the three existing `index_repo` fakes as exact signature mirrors (extended with the new kwarg) rather than loosening to `**kwargs`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Existing index_repo fake signatures broke on the new kwarg**
- **Found during:** Task 2 (full unit suite run)
- **Issue:** `_cmd_index` now always passes `fallback_search_only=`; three pre-existing forwarding tests monkeypatch `index_repo` with exact-signature fakes → TypeError
- **Fix:** Added `fallback_search_only=None` to the three fake signatures (`test_reindex_forwards_stored_scheme_override`, `test_semantic_include_flag_reaches_index_repo_as_a_tuple`, `test_reindex_forwards_stored_language_override`)
- **Files modified:** tests/test_index_cli.py
- **Verification:** `uv run python -m pytest -m "not integration" -rs -q` green (609 passed)
- **Committed in:** 4a6df3d (Task 2 GREEN)

---

**Total deviations:** 1 auto-fixed (1 bug directly caused by the task's change)
**Impact on plan:** None — mechanical signature extension, no behavior change to the tests' assertions.

## TDD Gate Compliance

All three tasks (tdd="true") produced a `test(03-01):` RED commit before their `feat(03-01):` GREEN commit; every RED run failed on missing symbols/behavior first (Task 1: 14 AttributeError/ImportError; Task 2: 7 TypeError/ImportError; Task 3: 3 persistence failures incl. the predicted no-wiring hard failure). Tracer feedback gate re-ran Task 2's `<verify>` end-to-end after its commit (14 passed + full suite green) before expansion into Task 3.

## Issues Encountered
- Several edit-tool boundary mistakes during GREEN phases briefly duplicated/dropped lines; each was caught by parse-check or the very next test run and repaired before any commit (no shipped impact)
- The first filtered `-k` run took ~3.5 min because the filter also selects pre-existing integration tests that run real toolchains; unit-only runs are the CI gate and stay ~35 s

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All carriers, resolution, gate, ordering, and parser surface for Plans 03-02 (status/list/MCP degraded reporting) and 03-03 (watch sha-keyed skip + flag pass-through) are shipped; `DEGRADED_STATUS`/`ORIGIN_FALLBACK`/`recovery_for` are importable, and the degraded terminal write already persists the FALL-05 attempt sha in `commit_sha`
- `jarvis list`/`status` and `server.py` rendering of degraded rows is NOT yet wired (Plan 03-02) — degraded rows currently render in the ✓ family with origin/cause/recovery lines from `jarvis status` only
- Watch still passes no `fallback_search_only` and has no skip logic (Plan 03-03)

## Self-Check: PASSED

- All 6 modified files exist on disk
- All 6 task commits present in git log (e699fcd, fb23997, a224a80, 4a6df3d, b6640b1, 0738513)
- `uv run python -m pytest tests/test_registry.py tests/test_config.py -q`: 64 passed
- `uv run python -m pytest tests/test_index_cli.py -k "degraded or fallback or hard_failure or preserves or precedence or self_heal or degrades_again or preempts" -q`: 38 passed
- `uv run python -m pytest -m "not integration" -rs -q`: 609 passed, 17 deselected — CI gate green
- Real-CLI smoke (isolated data dir, real binaries): `--fallback-search-only` → exit 0 + `fallback_enabled=1`; no-flag rerun keeps the persisted 1; garbage env on a fresh repo → exactly one warning + NULL row

---
*Phase: 03-opt-in-self-healing-fallback*
*Completed: 2026-08-22*
