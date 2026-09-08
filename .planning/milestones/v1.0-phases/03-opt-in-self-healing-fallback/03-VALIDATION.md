---
phase: 3
slug: opt-in-self-healing-fallback
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: true
created: 2026-08-22
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >=8.3 (dev group; custom `integration` marker) |
| **Config file** | `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`) |
| **Quick run command** | `uv run pytest -m "not integration" -rs -q` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~5 seconds (unit scope, mocked `_run`) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -m "not integration" -rs -q`
- **After every plan wave:** Run `uv run pytest` (integration self-skips without binaries)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 3-01-01 | 01 | 1 | FALL-02 | T-3-01 | `fallback_enabled` written via parameterized UPDATE only; env parse strict truthy set | unit | `uv run pytest tests/test_registry.py tests/test_config.py -q` | ✅ (tests added to existing files) | ⬜ pending |
| 3-01-02 | 01 | 1 | FALL-01, FALL-04 | T-3-01 / T-3-02 / T-3-03 | failure text persisted as data (parameterized SQL, never interpolated); missing-binary/bash-shim/pre-pipeline stay loud hard failures | unit | `uv run pytest tests/test_index_cli.py -k "degraded or fallback or hard_failure or preserves" -q` | ✅ (tests added) | ⬜ pending |
| 3-01-03 | 01 | 1 | FALL-02, FALL-03 | T-3-02 | resolved bool never persisted (tri-state preserved); persisted flag outranks attacker-flippable env | unit | `uv run pytest tests/test_index_cli.py -k "fallback or precedence or self_heal or degrades_again or preempts" -q` | ✅ (tests added) | ⬜ pending |
| 3-02-01 | 02 | 2 | FALL-01 | T-3-04 / T-3-05 | status_stderr stays out of MCP payloads; reason rendered as data, never executed | unit | `uv run pytest tests/test_server_tools.py -k "degraded or fallback" -q` | ✅ (tests added) | ⬜ pending |
| 3-02-02 | 02 | 2 | FALL-01 | T-3-04 | list/status degraded rendering keeps 5-column TSV parseability | unit | `uv run pytest tests/test_index_cli.py -k "list or status" -q` | ✅ (tests added) | ⬜ pending |
| 3-03-01 | 03 | 3 | FALL-05 | T-3-06 | pure skip predicate; no registry writes | unit | `uv run pytest tests/test_index_cli.py -k "watch_should_retry" -q` | ✅ (tests added) | ⬜ pending |
| 3-03-02 | 03 | 3 | FALL-05, FALL-02 | T-3-06 / T-3-07 | skip consult read-only + fail-open to retry | unit | `uv run pytest tests/test_index_cli.py -k "watch" -q` | ✅ (tests added) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements — tests land in the existing mirrored files (`tests/test_registry.py`, `tests/test_config.py`, `tests/test_index_cli.py`, `tests/test_server_tools.py`); fixtures reused as-is: `_init_git_repo`, `_fake_completed_process`, step-keyed `_run` mocks, `JARVIS_DATA_DIR` isolation, raw-sqlite3 legacy-DB builder. No stubs, no new framework installs.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end Swift degrade against the real pinned scip-swift binary | FALL-01 | Requires real binaries + macOS/Xcode (CI unit gate is mocked by design) | Optional smoke only: induce a Swift indexer failure on a fixture repo with `JARVIS_FALLBACK_SEARCH_ONLY=1`, confirm degraded publish + exit 0 |

*All phase behaviors have automated verification; the Swift row is an optional real-binary smoke, not a gate.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (none missing)
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter (at validate-phase)

**Approval:** pending
