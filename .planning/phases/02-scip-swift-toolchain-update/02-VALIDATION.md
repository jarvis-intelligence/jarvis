---
phase: 2
slug: scip-swift-toolchain-update
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-22
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + POSIX-sh harness (`tests/test_setup_sh.py` runs setup.sh under `JARVIS_SETUP_SOURCED`) |
| **Config file** | `pyproject.toml` ([tool.pytest.ini_options]); `tests/test_setup_sh.py` for setup.sh |
| **Quick run command** | `uv run pytest -m "not integration" -q` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -m "not integration" -q`
- **After every plan wave:** Run `uv run pytest` + `sh tests/test_setup_sh.py`-covered dash check
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|

*Rows seeded when plans are finalized (task IDs 02-NN-MM). Expected homes: `tests/test_setup_sh.py` (pin resolution + digest verification + floor), `tests/test_index_cli.py` (`--cache-dir` argv, version floor gate, forget cache sweep), `tests/test_watch.py` (ignore patterns). CI smoke in `.github/workflows/setup-smoke.yml` + new `tests/fixtures/` .xcodeproj fixture.*

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers phase requirements — pytest suite + test_setup_sh.py POSIX harness; no new framework needed. The new `.xcodeproj` fixture (D-10) is plan work, not Wave 0.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|

*All automatable: setup.sh resolution verified under the JARVIS_SETUP_SOURCED seam with a fake API payload; live GitHub API calls stay in CI smoke, not unit tests.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
