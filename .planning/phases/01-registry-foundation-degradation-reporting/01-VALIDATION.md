---
phase: 1
slug: registry-foundation-degradation-reporting
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-21
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x |
| **Config file** | `pyproject.toml` ([tool.pytest.ini_options], custom `integration` marker) |
| **Quick run command** | `uv run pytest -m "not integration" -q` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -m "not integration" -q`
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01-01 | 1 | STAT-01 | T-01-01 | Failure text reaches `record_failure()` via parameterized placeholders only; recovery derived at read time, never persisted per-row | unit (e2e tracer) | `uv run pytest tests/test_index_cli.py -k "failed or status" -q && uv run pytest tests/test_registry.py -q` | yes | ⬜ |
| 01-01-02 | 01-01 | 1 | STAT-01 | T-01-01 | D-04 NULL-clearing on success prevents stale failure causes masquerading as current state; legacy migration leaves `search_only` values untouched (SC5) | unit | `uv run pytest tests/test_registry.py -q` | yes | ⬜ |
| 01-01-03 | 01-01 | 1 | STAT-01 | T-01-02 | Persisted stderr re-rendered as terminal data only — no shell interpolation; truncation is display-only (last ~20 lines), column stays unbounded (D-02) | unit | `uv run pytest tests/test_index_cli.py -k "list or status" -q` | yes | ⬜ |
| 01-02-01 | 01-02 | 2 | STAT-01 | T-01-04 | Origin values are fixed module constants, never free text; reason/stderr stay parameterized data in `upsert()` | unit | `uv run pytest tests/test_registry.py -q` | yes | ⬜ |
| 01-02-02 | 01-02 | 2 | STAT-01 | T-01-04, T-01-05 | Signature reason text persisted and rendered as data; remedy prose never enters the row; reindex/search-only semantics byte-identical (Out-of-Scope lock) | unit | `uv run pytest tests/test_index_cli.py -k "search_only or search-only or signature or fallback" -q` | yes | ⬜ |
| 01-02-03 | 01-02 | 2 | STAT-01 | T-01-04 | Pre-pipeline exception text via parameterized `record_failure`; duplicate-slug rejection writes nothing (no cross-repo row clobber) | unit | `uv run pytest tests/test_index_cli.py -k "pre_pipeline or version or override or duplicate" -q && uv run pytest -m "not integration" -rs` | yes | ⬜ |
| 01-03-01 | 01-03 | 2 | STAT-02 | T-01-06 | `state`/`cause`/`recovery` are additive JSON data values; `status_stderr` never enters any payload; broken registry degrades to the bare error dict | unit | `uv run pytest tests/test_server_tools.py -k "error_payload or missing_repo" -q` | yes | ⬜ |
| 01-03-02 | 01-03 | 2 | STAT-03, STAT-01 (MCP) | T-01-06, T-01-07, T-01-08 | Capability/last_index_run dicts built manually — no `asdict` stderr leak; non-spawning filesystem derivation asserted via monkeypatch (no ZoektLifecycle/subprocess) | unit | `uv run pytest tests/test_server_tools.py -k "index_status or capabilities" -q` | yes | ⬜ |

*Test files mirror source modules 1:1: `tests/test_registry.py`, `tests/test_index_cli.py`, `tests/test_server_tools.py` carry all STAT-01..03 verification (rows seeded 2026-08-21 from plans 01-01/01-02/01-03). All three files pre-exist and are extended in place — no Wave 0 scaffolding required.*

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements — pytest suite (`tests/`, 17 files) with unit/integration marker split and 1:1 module mirroring convention; no new framework needed.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|

*All phase behaviors have automated verification (registry migration on a real pre-v1.0 registry fixture is unit-testable via temp SQLite DBs; MCP payload shapes via `tests/test_server_tools.py` patterns).*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
