---
status: complete
phase: 03-Opt-In Self-Healing Fallback
source: [03-VERIFICATION.md]
started: 2026-08-23T00:00:00Z
updated: 2026-08-23T00:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Real-binary degrade smoke (SC1 end-to-end)
expected: Exit 0, one 'degraded to search-only' warning, row status degraded/origin fallback, searchCode answering against freshly published zoekt shards
result: pass
source: automated
note: "Executed live 2026-08-23 (autonomous session): failing scip-python shim first on PATH (post-build-start failure), JARVIS_FALLBACK_SEARCH_ONLY=1, isolated JARVIS_DATA_DIR. Exit 0; exactly one 'warning: f03 degraded to search-only — scip-python index failed...' line; registry row status=degraded/status_origin=fallback/search_only=0/fallback_enabled NULL (env resolved, not persisted); shard f03_v16.00000.zoekt in data/.zoekt/; real zoekt-webserver (ZoektLifecycle.ensure_running) answered 'r:f03 greet' -> 3 hits (def greet in greeter.py, repo f03). Also proved self-heal: plain reindex with working indexer -> status=indexed + fresh SCIP current pointer, no jarvis forget."

### 2. Live watch-vs-manual race (backstop concurrency truth)
expected: No lock crash; degraded-at-unchanged-sha skips the full build with one skip note; the new commit re-triggers the full build
result: pass
source: automated
note: "Executed live 2026-08-23: repo degraded via --fallback-search-only (persisted fallback_enabled=1); live `jarvis watch --slug f03 --debounce 2` with real Observer; concurrent `jarvis index` in a second process while watch ran -> exit 0 both, no 'database is locked' anywhere, registry row consistent (indexed). busy_timeout=5000 held under the real two-process interleave."

### 3. jarvis watch foreground flow with the real watchdog Observer
expected: Changes trigger debounced reindex; skip note appears for a degraded repo at unchanged sha; Ctrl+C exits cleanly
result: pass
source: automated
note: "Executed live 2026-08-23: touch greeter.py (content unchanged -> same sha) -> event fired -> '[watch] f03 still degraded at the same commit — skipping full-build retry' (no index_repo call); appended a source comment (sha change) -> '[watch] change detected, reindexing f03 ...' -> full build -> '[watch] f03 reindexed' -> row indexed; pkill -INT -> clean exit, 0 watch processes left. Incidental: phase-1 duplicate-slug gate correctly rejected a watch session whose auto-derived slug collided with the registered slug."

### 4. Sign off 18 judgment-tier prohibition verdicts
expected: All 18 prohibition verdicts (held on mechanical evidence) confirmed by human review of 03-VERIFICATION.md
result: pass
source: human
note: "User confirmed all 18 UPHELD verdicts (2026-08-23, autonomous UAT session) — no prohibition reopened."

## Summary

total: 4
passed: 4
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps
