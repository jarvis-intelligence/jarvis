---
status: testing
phase: 02-scip-swift-toolchain-update
source: [02-VERIFICATION.md]
started: 2026-08-22T00:00:00Z
updated: 2026-08-22T00:00:00Z
---

## Current Test

number: 1
name: First real CI run of the new setup-smoke macOS step (flagged assumption A1)
expected: |
  macOS leg resolves + digest-verifies + installs v0.3.0, `jarvis index` of the fixture
  exits 0, tree-cleanliness assertions pass; Linux legs take the documented skip branches
  and stay green. Workflow commit b342fea is on no remote yet — trigger via push or
  workflow_dispatch of setup-smoke.
awaiting: user response

## Tests

### 1. First real CI run of the new setup-smoke macOS step (A1)
expected: macOS leg green end-to-end (install v0.3.0 digest-verified, fixture indexed, tree clean); Linux legs skip cleanly
result: [pending]

### 2. Live `jarvis watch` session over a Swift repo
expected: |
  Source edit fires exactly one debounced reindex; artifact-path events never fire; at most
  the accepted one-shot xcshareddata write costs one reindex on first index. (The
  should_ignore_path truth table and handler wiring are already machine-verified — this is
  the live end-to-end confirmation.)
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
