---
status: complete
phase: 02-scip-swift-toolchain-update
source: [02-VERIFICATION.md]
started: 2026-08-22T00:00:00Z
updated: 2026-08-22T16:05:00Z
---

## Current Test

[testing complete]

## Tests

### 1. First real CI run of the new setup-smoke macOS step (A1)
expected: macOS leg green end-to-end (install v0.3.0 digest-verified, fixture indexed, tree clean); Linux legs skip cleanly
result: pass
source: automated
note: "Executed 2026-08-22 (autonomous session): pushed gsd/v1.0-milestone + opened draft PR #39. Run 32583243197 (commit cb5993a): macOS leg — 'scip-swift: installing v0.3.0 (digest-verified)' → installed → '0.3.0 (swift 6.2.4)' → 'indexed mini-xcode-repo' (27s) → tree-clean assertions (test ! -e .scip-cache / .build) passed under set -eu; ubuntu leg green via documented skip branch. Reproduced on the fix commit e6de1ec (run 32583460561 after one rate-limit rerun): all legs green. Bonus finding fixed en route: test_language_override_to_swift_still_gets_xcodebuild was non-hermetic (unpatched phase-02 floor probe raised 'scip-swift not found on PATH' on runners before the indexer cmd was built) — fixed in e6de1ec, test.yml 3/3 green. Known flake recorded for milestone audit: anonymous api.github.com call can 403 rate-limit on shared runner IPs (one rerun resolved; candidate hardening: honor GH_TOKEN in CI or retry on 403)."

### 2. Live `jarvis watch` session over a Swift repo
expected: |
  Source edit fires exactly one debounced reindex; artifact-path events never fire; at most
  the accepted one-shot xcshareddata write costs one reindex on first index. (The
  should_ignore_path truth table and handler wiring are already machine-verified — this is
  the live end-to-end confirmation.)
result: pass
source: automated
note: "Executed live 2026-08-22 (autonomous session): fixture copy git-init'd under /tmp, `jarvis watch --debounce 2` under isolated JARVIS_DATA_DIR. (a) 5 artifact writes across .scip-cache/.build/DerivedData/.index-store/.swiftpm → 0 reindex triggers (8s settle > 2s debounce). (b) SwiftFile.swift edit → exactly 1 'change detected' + 1 completed reindexed in 27s; registry row indexed/swift/search_only=0. (c) no follow-up reindex from the xcshareddata write (created once on first index; no new event on the second). Indexer cache stayed under the data dir (cache/scip-swift/<slug>/derived-data there; nothing indexer-created in-tree). Environmental note: run with `--extra semantic` installed; unauthenticated HF model download stalled on first attempt (process killed) — re-run with HF_HUB_OFFLINE=1 completed in 27s; semantic stage then failed non-fatally on the partial local HF cache with SCIP/Zoekt still published, incidentally exercising the documented degradation path."

## Summary

total: 2
passed: 2
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps
