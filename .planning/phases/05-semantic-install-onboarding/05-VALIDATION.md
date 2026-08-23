---
phase: 5
slug: semantic-install-onboarding
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-23
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (testpaths `["tests"]`, `integration` marker — pyproject.toml) |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration" -q` |
| **Full suite command** | `uv run pytest -m "not integration" -rs` (CI gate) |
| **Estimated runtime** | quick ~18s (measured 2026-08-23: 231 passed, 12 deselected); full ~39s (measured 2026-08-23: 638 passed, 17 deselected) |

---

## Sampling Rate

- **After every task commit:** `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration" -q` (~18s)
- **After every plan wave:** `uv run pytest -m "not integration" -rs` (single-wave phase — once after Task 3)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 1 | SEMA-01 | T-5-04 | decline bit written only via dedicated parameterized setter; upsert/record_failure provably never reset it; memory dies with the row | unit | `uv run pytest "tests/test_registry.py::test_semantic_declined_column_migrates_onto_an_existing_database" "tests/test_registry.py::test_semantic_declined_roundtrip_and_preservation" "tests/test_index_cli.py::test_cmd_index_tty_offer_decline_answer_persists_semantic_declined" "tests/test_index_cli.py::test_cmd_index_tty_offer_not_repeated_for_a_declined_repo" "tests/test_index_cli.py::test_cmd_index_tty_offer_still_made_for_a_different_repo" -q` | ❌ W0 (authored by task, RED-first) | ⬜ pending |
| 5-01-02 | 01 | 1 | SEMA-01 | T-5-01 / T-5-02 | prompt answer maps to boolean branch only, never interpolated; uv argv fixed list (resolved uv, pip, install, --python, sys.executable, jarvis-mcp[semantic]); failure → one stderr warning, decline NOT remembered | unit | `uv run pytest "tests/test_index_cli.py::test_cmd_index_offer_accept_parse_table" "tests/test_index_cli.py::test_cmd_index_offer_yes_installs_and_enables_semantic_same_invocation" "tests/test_index_cli.py::test_cmd_index_offer_install_failure_warns_and_does_not_remember_decline" "tests/test_index_cli.py::test_cmd_index_offer_eof_or_keyboard_interrupt_at_prompt_declines_remembered" "tests/test_index_cli.py::test_install_semantic_extra_argv_and_failure_paths" -q` | ❌ W0 (authored by task, RED-first) | ⬜ pending |
| 5-01-03 | 01 | 1 | SEMA-02 (+SEMA-01 lock edges) | T-5-03 | non-TTY/watch/reindex structurally prompt-free (offer_semantic argparse gate + both-stream isatty); no subprocess off the consent path; extra-present and --semantic-include lock edges | unit | `uv run pytest "tests/test_index_cli.py::test_cmd_index_non_tty_never_prompts_or_blocks" "tests/test_index_cli.py::test_at_interactive_tty_requires_both_streams_tty" "tests/test_index_cli.py::test_offer_semantic_defaults_true_only_on_index_subparser" "tests/test_index_cli.py::test_cmd_reindex_never_offers_semantic_install" "tests/test_index_cli.py::test_cmd_watch_reindex_never_prompts_even_at_a_tty" "tests/test_index_cli.py::test_semantic_extra_missing_detects_missing_top_level_modules" "tests/test_index_cli.py::test_cmd_index_no_prompt_when_extra_already_installed" "tests/test_index_cli.py::test_cmd_index_semantic_include_runs_on_declined_repo_without_clearing_bit" -q` | ❌ W0 (authored by task) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] Framework installed (pytest via uv — nothing to install)
- [x] Shared fixtures exist: `_mock_healthy_full_run` / `_fake_completed_process` / `_init_git_repo` / `_drive_cmd_watch` in tests/test_index_cli.py; `BlockImportFinder` in tests/conftest.py
- [ ] `tests/test_index_cli.py` + `tests/test_registry.py` — the 18 new test functions are authored by plan tasks 5-01-01..5-01-03 themselves (RED-first for Tasks 1-2); no stubs are needed ahead of execution

Existing infrastructure covers all phase requirements; the only Wave-0 gap (the test functions) is created by the plan's own tasks. No new test files — tests land in the existing 1:1 mirror files per AGENTS.md.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live TTY offer: real `jarvis index` at a terminal with the extra genuinely absent — prompt appears, y installs via real uv and semantic completes in the same run; n remembered (second index silent); non-TTY (`< /dev/null`) never prompts and prints the existing hint | SEMA-01 (SC1/SC2 live), SEMA-02 (SC3 automation leg) | CI installs `--extra semantic` so the missing-extra branch cannot be exercised honestly there (05-RESEARCH Pitfall 5); forcing absence in CI would need a second wheel-install leg; a real TTY cannot be faked | Create a base-install venv without extras: `uv venv /tmp/p5-venv && uv pip install --python /tmp/p5-venv/bin/python -e .` (indexer binaries already on PATH from setup.sh). With JARVIS_DATA_DIR=/tmp/p5-live/data run: (1) `/tmp/p5-venv/bin/jarvis index <tiny-repo> --slug p5-live-a` at a real terminal → answer y → uv install visibly runs, run completes 0, `jarvis status p5-live-a` shows semantic indexed; (2) same for `--slug p5-live-b` → answer Enter → second index of p5-live-b prompts nothing, a fresh p5-live-c still prompts; (3) `/tmp/p5-venv/bin/jarvis index <repo> --slug p5-live-d < /dev/null` → no prompt, stderr carries the existing one-line skip hint |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
