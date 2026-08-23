---
phase: 4
slug: swift-failure-signatures
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-23
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (testpaths `["tests"]`, `integration` marker — pyproject.toml) |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/test_index_cli.py -m "not integration" -q` |
| **Full suite command** | `uv run pytest -m "not integration" -rs` (CI gate) |
| **Estimated runtime** | quick ~16s (measured 2026-08-23: 186 passed, 12 deselected); full ~40s (measured 2026-08-23: 633 passed, 17 deselected) |

---

## Sampling Rate

- **After every task commit:** `uv run pytest tests/test_index_cli.py -m "not integration" -q` (~16s)
- **After every plan wave:** `uv run pytest -m "not integration" -rs` (single-wave phase — once after Task 3)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 4-01-01 | 01 | 1 | SWFT-04 | T-4-02 | tokens class-specific + path-free; matched reason persisted via existing parameterized upsert (no new parsing) | unit | `uv run pytest "tests/test_index_cli.py::test_swift_no_build_system_signature_degrades_search_only" -q` | ❌ W0 (authored by task, RED-first) | ⬜ pending |
| 4-01-02 | 01 | 1 | SWFT-04 | T-4-02 | same as 4-01-01 for the no-IndexStore class | unit | `uv run pytest "tests/test_index_cli.py::test_swift_no_index_store_signature_degrades_search_only" -q` | ❌ W0 (authored by task, RED-first) | ⬜ pending |
| 4-01-03 | 01 | 1 | SWFT-04 | T-4-01 / T-4-02 | negative matcher pins: generic wrappers, empty/stdout-only carriers, first-match order (SC3 + probe predicates) | unit | `uv run pytest "tests/test_index_cli.py::test_swift_generic_build_failure_wrapper_never_matches" "tests/test_index_cli.py::test_empty_or_stdout_only_failure_carriers_never_match" "tests/test_index_cli.py::test_search_only_reason_first_listed_match_wins" -q` | ❌ W0 (authored by task) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] Framework installed (pytest via uv — nothing to install)
- [x] Fixtures: `SWIFT_FIXTURE_REPO` (tests/fixtures/mini_swift_repo) exists — no new fixture
- [ ] `tests/test_index_cli.py` — the five new test functions are authored by plan tasks 4-01-01..4-01-03 themselves (RED-first for the two pinning tests); no stubs are needed ahead of execution

Existing infrastructure covers all phase requirements; the only Wave-0 gap (the test functions) is created by the plan's own tasks.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live degrade smoke: both captured failure shapes through real `jarvis index` (real scip-swift 0.3.0 + zoekt), plus one keep-hard shape staying hard | SWFT-04 (SC1/SC3 live proof) | Needs the real darwin/arm64 scip-swift binary + Xcode toolchain; CI unit legs are binary-free and committed integration tests are deliberately avoided (phase-2 02-03 precedent) | Recreate shapes from 04-RESEARCH.md capture recipes (or reuse /tmp/jarvis-p4-capture/repos/<shape>) with scratch JARVIS_DATA_DIR: no-build-system and empty-Sources shapes exit 0 with the `note: … cannot be SCIP-indexed` stderr line and `jarvis status` origin=signature; broken-manifest shape exits non-zero with a failed_hard row |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 4s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** {pending / approved YYYY-MM-DD}
