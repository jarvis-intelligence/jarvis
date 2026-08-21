---
status: testing
phase: 01-Registry Foundation & Degradation Reporting
source: [01-VERIFICATION.md]
started: 2026-08-21T17:40:00Z
updated: 2026-08-21T17:40:00Z
---

## Current Test

number: 1
name: Sign off 9 judgment-tier prohibition verdicts
expected: |
  All 9 descriptor-less prohibitions in the phase plans were upheld by LLM judgment with
  code+test evidence recorded per item in 01-VERIFICATION.md (unverified-prohibition tier —
  human review recommended, never silently passed). Human confirms the verdicts.
awaiting: user response

## Tests

### 1. Sign off 9 judgment-tier prohibition verdicts
expected: All 9 prohibition verdicts (UPHELD) confirmed by human review of 01-VERIFICATION.md
result: [pending]

### 2. WR-01: accept vs fix interrupted-run wiping prior failure record
expected: |
  Decision on code-review warning WR-01: during Ctrl-C/interrupt of a retry on a
  previously-failed repo, the transitional 'indexing' upsert clears status_origin/reason/stderr
  (D-04 conflict list) and except Exception misses BaseException — the prior cause record is
  destroyed and the row can strand at outcome='indexing' with no recovery guidance
  (index_cli.py:813/839 + 798/825/925). Options: accept as-is, or fix (catch BaseException in
  the three record_failure handlers / defer failure-field clearing to terminal upserts).
result: [pending]

### 3. WR-03: keep vs split 'stale' wording without staleness evidence
expected: |
  Decision on code-review warning WR-03: capabilities.navigation.reason reports
  'stale — indexed at <commit>' whenever the last run failed, even when freshness.stale is
  False by construction (no repo_path) or HEAD == published commit (server.py:173-179).
  Options: keep plan-literal wording, or split reason so 'stale' only appears with
  freshness evidence.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
