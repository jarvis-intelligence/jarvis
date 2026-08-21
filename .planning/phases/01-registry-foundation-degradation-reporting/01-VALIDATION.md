---
phase: 1
slug: registry-foundation-degradation-reporting
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
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

*Rows seeded when plans are finalized (task IDs 01-NN-MM). Test files mirror source modules 1:1: `tests/test_registry.py`, `tests/test_index_cli.py`, `tests/test_server_tools.py`, `tests/test_query.py` are the expected homes for STAT-01..03 verification.*

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
