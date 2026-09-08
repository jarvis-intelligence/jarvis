---
phase: 01-registry-foundation-degradation-reporting
verified: 2026-08-21T17:36:40Z
status: passed
score: 27/27 must-haves verified
behavior_unverified: 0
overrides_applied: 0
unverified_prohibitions: 9 # judgment-tier MUST-NOTs upheld by non-authoritative LLM judgment — human review recommended
human_verification:

  - test: "Sign off the 9 judgment-tier prohibitions (see Prohibition Verdicts table)"
    expected: "Human confirms the UPHELD verdicts (or rejects specific ones, reopening them as gaps)"
    why_human: "Autonomous verify cannot authoritatively judge value/semantics prohibitions; verdicts are non-authoritative per ADR-550 D4"

  - test: "Decide on WR-01: interrupt a retrying reindex (Ctrl-C during the indexer subprocess) of a previously failed repo"
    expected: "Decide accept-as-edge vs schedule fix. Today the prior failure record is wiped and the row is left status='indexing' with no recovery guidance"
    why_human: "Accept/fix is a product decision; no must-have truth covers interrupted runs"

  - test: "Decide on WR-03: getIndexStatus with repo_path omitted (or HEAD == published commit) on a failed-run repo"
    expected: "Decide whether capabilities.navigation.reason may say 'stale' without a staleness comparison. Today it does when last run failed, even though freshness.stale is False by construction"
    why_human: "Wording-level honesty trade-off on a machine-readable field; matches plan's literal spec but violates the codebase's own never-stale-without-evidence rule"
---

# Phase 1: Registry Foundation & Degradation Reporting Verification Report

**Phase Goal:** Failure and degradation state is persisted and explained — every surface a user or agent consults (status CLI, `getIndexStatus`, nav-tool errors) says what happened and how to recover
**Verified:** 2026-08-21T17:36:40Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

All 27 must-haves (5 roadmap success criteria + 22 plan truths) verified against the actual codebase with behavioral evidence. The phase goal IS achieved in code; `human_needed` comes solely from judgment-tier prohibitions requiring human sign-off (autonomous verdicts are non-authoritative) plus two review-warning decisions — no truth failed.

### Observable Truths — Roadmap Success Criteria

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | Hard-failed run shows persisted cause + recovery in `jarvis status` AND `getIndexStatus` | ✓ VERIFIED | `tests/test_index_cli.py::test_hard_failed_index_persists_cause_and_full_stderr` (row: failed/failed_hard/one-line reason/full stderr verbatim) + `test_cmd_status_explains_a_failed_repo` (origin/cause/`recovery: jarvis index <path>` lines); `getIndexStatus`: `last_index_run` origin='failed_hard' asserted in `test_get_index_status_failed_run_with_live_pointer_reports_stale_navigation`; all green in the 215-test phase run |
| SC2 | Search-only via existing paths reports origin + recovery; taxonomy accepts Phase 3's opt-in origin | ✓ VERIFIED | `test_manual_search_only_publish_stamps_manual_origin`, `test_signature_fallback_stamps_signature_origin_and_matched_reason` (origin slugs persist); `test_recovery_for_derives_per_origin_commands` + `test_recovery_for_returns_none_for_successful_and_unknown_rows` — unknown origins return None and the taxonomy is additive constants (`registry.py:25-33` comment names the Phase 3 slot); `degraded` needs one constant + one `recovery_for` branch, no schema/payload change (confirmed: no enum, no origin parsing) |
| SC3 | Nav tool on search-only repo returns error payload naming state, cause, recovery — not bare "index not found" | ✓ VERIFIED | `test_error_payload_carries_state_cause_recovery_for_a_signature_search_only_repo` (state='signature', cause=matched reason, recovery=`jarvis reindex <slug>`, prose `error` intact); all 5 nav tools route through `_error_payload` (server.py: documentSymbols/goToDefinition/findReferences/callHierarchy/typeHierarchy) |
| SC4 | `getIndexStatus` exposes machine-readable capability fields branchable without parsing prose | ✓ VERIFIED | `capabilities.{navigation:{available,reason,recovery},search:{available,reason},semantic:{available,reason}}` in `_capability_fields` (server.py:138-207); 8 capability tests incl. `test_get_index_status_adds_capability_fields_without_reshaping_existing_keys` |
| SC5 | Pre-v1.0 registry upgrades in place additively; `search_only=1` semantics kept | ✓ VERIFIED | `test_failure_columns_migrate_onto_an_existing_database` — raw-sqlite3 legacy CREATE TABLE (verified in test source: no failure columns), both rows survive, search_only=True untouched with NULL origins, PRAGMA confirms the 3 columns after open |

### Observable Truths — Plan 01-01 (8)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Failing `index_repo` leaves row: failed/failed_hard/non-empty one-line reason/complete untruncated stderr | ✓ VERIFIED | e2e test asserts `status_stderr == carrier` with a 1200+ char marker; reason single-line via `next(line for line in text.splitlines() if line.strip())` (index_cli.py:929-931) |
| 2 | `jarvis status <slug>` prints origin, cause, recovery `jarvis index <path>` | ✓ VERIFIED | `_cmd_status` (index_cli.py:999-1006) + passing capsys test asserting `recovery: jarvis index /abs/path/failing` |
| 3 | `recovery_for()` maps all four cases (failed_hard/signature/manual/None) | ✓ VERIFIED | `registry.py:127-138` + `test_recovery_for_derives_per_origin_commands` asserting the exact command strings |
| 4 | Legacy `search_only=1` NULL-origin row reads origin 'manual' at read time | ✓ VERIFIED | `origin_of()` fallback (registry.py:120-125) + `test_recovery_for_treats_legacy_search_only_as_manual` + server-side `test_error_payload_reports_manual_state_for_a_legacy_search_only_row` |
| 5 | Successful upsert after failure NULLs all three failure fields | ✓ VERIFIED | upsert ON CONFLICT sets all three from excluded.* (registry.py:197-203); `test_upsert_clears_failure_fields_on_success` |
| 6 | Pre-v1.0 DB gains the three nullable TEXT columns; search_only untouched | ✓ VERIFIED | `_SCHEMA` + three `_ensure_column` calls (registry.py:48,155-157); migration test |
| 7 | `jarvis list` glyphs ✗/◐/✓, 5-column TSV stable, 6th reason field on failed rows only | ✓ VERIFIED | `_cmd_list` (index_cli.py:960-975) + `test_cmd_list_marks_repo_health_at_a_glance` |
| 8 | `jarvis status` shows only ~last 20 stderr lines + persistence pointer; column unbounded | ✓ VERIFIED | `splitlines()[-20:]` + `full log: persisted in the registry (status_stderr column)` (index_cli.py:1007-1013); `test_cmd_status_prints_stderr_tail_and_pointer` + `test_cmd_status_omits_stderr_block_when_absent` |

### Observable Truths — Plan 01-02 (6)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Manual `--search-only` publish: status='search-only', origin='manual', NULL stderr | ✓ VERIFIED | `upsert(..., status_origin=ORIGIN_MANUAL)` at index_cli.py:821 + `test_manual_search_only_publish_stamps_manual_origin` |
| 2 | Signature fallback: origin='signature', status_reason = matched per-signature reason; recovery `jarvis reindex <slug>` | ✓ VERIFIED | index_cli.py:867 + `test_signature_fallback_stamps_signature_origin_and_matched_reason`; recovery via read-time mapping |
| 3 | Search-only publish failing records full failed row via record_failure | ✓ VERIFIED | Manual-path except → `record_failure` (index_cli.py:832-833); `test_failed_search_only_publish_records_a_full_failure_row`; no `mark_status` call remains in index_repo (grep) |
| 4 | Pre-pipeline failure leaves failed_hard row, language 'unknown' when detection never completed | ✓ VERIFIED | Pre-pipeline wrap with `resolved_language` sentinel (index_cli.py:769-810) + `test_pre_pipeline_version_gate_failure_creates_a_recoverable_row`, `test_pre_pipeline_stale_language_override_failure_overwrites_the_row` |
| 5 | Duplicate-slug gate raises without writing a failure row | ✓ VERIFIED | Plain close-and-raise preserved (index_cli.py:753-757) + `test_duplicate_slug_rejection_writes_no_failure_row` (test scenario corrected to the gate's real semantics — documented deviation, production behavior as planned) |
| 6 | Plain success upsert (no origin) leaves all three failure fields NULL | ✓ VERIFIED | `test_plain_upsert_leaves_failure_fields_null` + `test_upsert_round_trips_origin_parameters` |

### Observable Truths — Plan 01-03 (8)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Nav tool error payload carries additive state/cause/recovery alongside unchanged prose error | ✓ VERIFIED | `_error_payload` IndexNotFoundError branch (server.py:231-249); `test_error_payload_carries_state_cause_recovery_for_a_signature_search_only_repo` |
| 2 | Legacy NULL-origin search_only rows report state 'manual' + forget&&index recovery | ✓ VERIFIED | `test_error_payload_reports_manual_state_for_a_legacy_search_only_row` |
| 3 | `last_index_run: {outcome, origin, reason, recovery}`, outcome = registry status verbatim | ✓ VERIFIED | `"outcome": entry.status` (server.py:163); `test_get_index_status_reports_last_index_run_for_a_search_only_repo` |
| 4 | `capabilities` three-branch shape, branchable without prose parsing | ✓ VERIFIED | server.py:180-207; search-only/semantic/shard/never-raise/additive tests all green |
| 5 | Failed run + live pointer: outcome='failed' AND navigation.available=true with stale commit named | ✓ VERIFIED | `test_get_index_status_failed_run_with_live_pointer_reports_stale_navigation` — asserts all three properties against a stubbed live pointer + real record_failure row |
| 6 | Navigation availability from pointer/indexed read, never row status | ✓ VERIFIED | `"available": indexed` where `indexed` comes from `_service().get_index_status` (server.py:399,187); the failed-run test is the discriminating proof (row says failed, available stays True) |
| 7 | Capability derivation never raises, never spawns zoekt-webserver/subprocess | ✓ VERIFIED | Broad except in helper (server.py:158-208) + call-site belt (server.py:402-406); `test_get_index_status_capability_failure_degrades_to_nulls` + `test_get_index_status_capability_derivation_never_spawns` (monkeypatches subprocess.run/Popen/ZoektLifecycle.ensure_running to raise) |
| 8 | search.available from on-disk shard glob; semantic.available from `semantic_indexed_at` | ✓ VERIFIED | `zoekt_dir.glob(f"{repo}_v*.zoekt")` with `_v` guard (server.py:193-195); `bool(entry.semantic_indexed_at)` (server.py:196); both tested incl. the `_v` guard case |

**Score:** 27/27 truths verified (0 present-but-behavior-unverified — every behavior-dependent truth has a passing behavioral test)

### Prohibition Verdicts (judgment-tier — non-authoritative, human review recommended)

| # | Prohibition (abbreviated) | LLM verdict | Basis |
|---|---------------------------|-------------|-------|
| 1 | 01-01: no truncation of persisted status_stderr | UPHELD | `record_failure` writes `text` verbatim; e2e test asserts 1200+-char equality; truncation only in `_cmd_status` display |
| 2 | 01-01: no remedy prose in status; no persisted recovery command | UPHELD | `_SEARCH_ONLY_SIGNATURES` texts are causal reasons (verified text); no recovery column in `_SCHEMA`; `recovery_for` read-time |
| 3 | 01-01: migration must not rewrite search_only | UPHELD | `_ensure_column` is ADD COLUMN only; migration test asserts search_only=True survives with NULL origin |
| 4 | 01-02: no per-signature REMEDY prose as status_reason | UPHELD | `status_reason=reason` from `_search_only_reason`; texts carry cause, no install/downgrade advice |
| 5 | 01-02: reindex/search-only resolution semantics unchanged | UPHELD | `git diff b20a241^..HEAD` on index_cli.py: only an indentation shift of the `_resolve_search_only` call (moved into the wrap); `_cmd_reindex` and the "will not repeat the build" note byte-identical |
| 6 | 01-03: prose `error` key meaning unchanged (additive only) | UPHELD | `payload["error"]` set first in every branch; tests assert prose retained for search-only |
| 7 | 01-03: no masking genuine query faults as degradation | UPHELD | Structured keys only in the IndexNotFoundError branch when a row with origin exists; `test_error_payload_does_not_mask_other_faults_on_a_degraded_repo` |
| 8 | 01-03: navigation availability never from row status | UPHELD | See truth 6 above — code + discriminating test |
| 9 | 01-03: no spawn from status/capability computation | UPHELD | Filesystem glob + row read only; no-spawn test with raising subprocess/Popen/ensure_running monkeypatches |

**unverified-prohibition — human review recommended**: 9 judgment-tier MUST-NOTs upheld by non-authoritative LLM judgment. None may be silently absorbed into `passed`.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/jarvis/registry.py` | 3 columns, RegisteredRepo fields, record_failure, ORIGIN_* constants, origin_of, recovery_for, D-04 clearing | ✓ VERIFIED | All present and substantive (311 lines); wired by index_cli + server |
| `src/jarvis/index_cli.py` | hard-failure hook, status origin/cause/recovery/stderr-tail, list markers, origin stamps, pre-pipeline wrap | ✓ VERIFIED | All present; 3 record_failure call sites + 2 origin-stamped upserts |
| `src/jarvis/server.py` | _registry_entry, _error_payload keys, _capability_fields, get_index_status spread | ✓ VERIFIED | All present; _registry_status delegates to _registry_entry |
| `tests/test_registry.py` | migration, roundtrip, record_failure, recovery-mapping, origin-parameter tests | ✓ VERIFIED | 11 phase tests found, substantive assertions |
| `tests/test_index_cli.py` | e2e failure persistence, status/list output, origin stamps, pre-pipeline | ✓ VERIFIED | 13 phase tests found, substantive assertions |
| `tests/test_server_tools.py` | structured-error, capability-branch, never-raise, no-spawn tests | ✓ VERIFIED | 13 phase tests found, substantive assertions |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `index_repo` failure handlers (×3: pre-pipeline 805, manual-path 832, main 932) | `Registry.record_failure()` | direct calls with ORIGIN_FAILED_HARD | ✓ WIRED | The load-bearing write hooks (D-05/D-06) |
| `_cmd_status`/`_cmd_list` | `recovery_for()`/`origin_of()` | import from jarvis.registry (index_cli.py:28-36) | ✓ WIRED | One derivation function, read-time only |
| All 5 nav tools | `_error_payload` | except → return _error_payload(repo, exc) | ✓ WIRED | Single choke point (D-14) |
| `get_index_status` | `_service().get_index_status` + `_capability_fields` | pointer truth + registry truth spread additively | ✓ WIRED | Service-failure path unchanged (`{"error": ...}`) |
| `server.py` | `jarvis.registry.recovery_for`/`origin_of` | module-level import (server.py:16) | ✓ WIRED | Shared derivation with CLI (D-09) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| registry rows | status_origin/reason/stderr | sqlite INSERT..ON CONFLICT writes from live pipeline exceptions | Yes — e2e test round-trips a real exception carrier | ✓ FLOWING |
| `jarvis status` output | repo.* fields | `Registry.get(slug)` | Yes | ✓ FLOWING |
| `jarvis list` output | `registry.list()` rows | sqlite SELECT | Yes | ✓ FLOWING |
| `_error_payload` keys | entry via `_registry_entry` | `Registry.get` | Yes — tests seed real registry rows and assert payload values | ✓ FLOWING |
| `_capability_fields` | entry + zoekt glob + `indexed` | registry row + filesystem + pointer read | Yes — tests seed rows/shards and stub the pointer | ✓ FLOWING |

No static fallbacks, no mocks in production paths.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase test files (registry + index_cli + server_tools) | `uv run python -m pytest tests/test_registry.py tests/test_index_cli.py tests/test_server_tools.py -q` | 215 passed, 1 skipped (39.9s) | ✓ PASS |
| Full unit suite (CI gate) | `uv run python -m pytest -m "not integration" -q` | 478 passed, 25 skipped, 12 deselected (23.8s) | ✓ PASS — matches SUMMARY and REVIEW claims exactly |
| TDD commit ordering | `git log --oneline --grep="01-0"` | 16 RED/GREEN commits; every `test(01-0x):` precedes its `feat(01-0x):` | ✓ PASS |
| Working tree clean (src/tests) | `git status --porcelain -- src tests` | empty | ✓ PASS |

### Probe Execution

Step 7c: SKIPPED — no `scripts/*/tests/probe-*.sh` exist and no PLAN/SUMMARY declares probes; this phase's runnable evidence is the pytest suites above.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| STAT-01 | 01-01, 01-02, 01-03 | status/getIndexStatus report degradation: origin, persisted cause, recovery command | ✓ SATISFIED | SC1+SC2 truths; CLI status/list lines, last_index_run payload, recovery_for mapping |
| STAT-02 | 01-03 | Nav-tool error payloads explain degraded/search-only state with cause + recovery | ✓ SATISFIED | SC3 truths; 5 structured-error tests |
| STAT-03 | 01-03 | getIndexStatus machine-readable capability fields | ✓ SATISFIED | SC4 truths; 8 capability tests |

Orphan check: REQUIREMENTS.md Traceability maps exactly STAT-01/02/03 to Phase 1 (all marked Complete) — no orphaned requirements for this phase.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| tests/test_server_tools.py | 132 | `PYTHON_SHEBANG_PLACEHOLDER` grep hit | ℹ️ Info | Pre-existing fixture template substitution (initial commit 9918d5b), not a stub |
| src/jarvis/index_cli.py | 813-815, 839-840 | Transitional `indexing` upsert clears failure fields; KeyboardInterrupt bypasses the three `except Exception` handlers → prior cause wiped, row stranded at 'indexing' (REVIEW WR-01) | ⚠️ Warning | Robustness hole in the new feature's edge (interrupted retry); not a must-have truth; human decision requested |
| src/jarvis/index_cli.py | 798-810 | Pre-pipeline handler lacks try/finally around record_failure (connection leaks if record_failure raises) (REVIEW WR-02) | ⚠️ Warning | Resource cleanup edge; siblings use finally correctly |
| src/jarvis/server.py | 173-179 | nav_reason says "stale" when freshness.stale is False by construction (REVIEW WR-03) | ⚠️ Warning | Machine-readable reason asserts staleness without evidence; matches plan's literal wording |
| src/jarvis/registry.py | 222-228 | `mark_status()` production-dead (REVIEW IN-01) | ℹ️ Info | Weightless code kept by its own test |

No TBD/FIXME/XXX debt markers in any phase-modified file. No stubs: every rendering path traces to live data.

### Deferred Items

None — the three review warnings are not covered by later-phase goals (Phase 3 FALL-03 self-heal retries mitigate the stuck-'indexing' row on the next reindex but do not address the wiped prior cause or the interrupt path), so they are surfaced for human decision rather than deferred.

### Human Verification Required

### 1. Sign off the 9 judgment-tier prohibitions

**Test:** Review the Prohibition Verdicts table above (all UPHELD by non-authoritative LLM judgment with code+test basis).
**Expected:** Human confirms the verdicts, or rejects specific ones (a rejection reopens that prohibition as a gap).
**Why human:** Autonomous verify cannot authoritatively judge value/semantics MUST-NOTs; per ADR-550 D4 they are flagged, never silently passed.

### 2. Decide on WR-01 — interrupted retry wipes the prior failure record

**Test:** Let a previously-failed repo's reindex run, then Ctrl-C during the indexer subprocess.
**Expected:** Decision: accept as known edge, or schedule a fix (catch BaseException in the three handlers, or keep failure fields across the transitional `indexing` upsert). Today the prior origin/reason/stderr are cleared and the row is left `status='indexing'` with no recovery guidance anywhere — the exact silence this phase exists to close.
**Why human:** Accept/fix is a product decision; no must-have truth or roadmap SC covers interrupted runs.

### 3. Decide on WR-03 — "stale" claimed without staleness evidence

**Test:** Call `getIndexStatus` on a failed-run repo with `repo_path` omitted (or with HEAD == published commit).
**Expected:** Decision: keep the unified "stale — indexed at <commit>" wording (plan's literal spec), or split the trigger so a failed-run-with-fresh-pointer reads "latest run failed; indexed at <commit>". Today the reason names staleness even when `freshness.stale` is False by construction.
**Why human:** Honesty trade-off on a machine-readable field vs plan wording; both defensible.

### Gaps Summary

No gaps. All 22 plan truths and all 5 roadmap success criteria verified in the actual code with passing behavioral tests; all 6 artifacts exist, are substantive, wired, and data-flowing; all 3 requirements satisfied with no orphans; full unit suite green (478 passed). `human_needed` derives exclusively from (a) 9 judgment-tier prohibitions verified non-authoritatively and (b) 2 review-warning decisions (WR-01 interrupted-run data loss, WR-03 stale wording) escalated per the Escalation Gate. A third review warning (WR-02, missing finally) and the infos (dead `mark_status`, list-field value change for exact-match parsers, reindex→manual re-stamp IN-03, unquoted recovery paths IN-05, None-conflation IN-04) are recorded in the table above for the next planner without requiring a decision to close this phase.

---

_Verified: 2026-08-21T17:36:40Z_
_Verifier: Claude (gsd-verifier)_
