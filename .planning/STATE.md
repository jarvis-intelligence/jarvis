---
gsd_state_version: 1.0
milestone: v1.0
current_phase: 3
current_phase_name: Opt-In Self-Healing Fallback
status: executing
stopped_at: Phase 2 complete, ready to plan Phase 3
last_updated: "2026-08-22T17:03:38.406Z"
last_activity: 2026-08-22
last_activity_desc: Phase 2 complete, transitioned to Phase 3
state_head: 91545ddb1df98fa002f5660d11c847cb1e5c9bbe
progress:
  total_phases: 5
  completed_phases: 2
  total_plans: 9
  completed_plans: 6
milestone_name: Indexing Robustness & scip-swift Update
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-22)

**Core value:** An indexing failure never leaves a repo with nothing — search keeps working, and the system explains why and how to recover.
**Current focus:** Phase 3 — Opt-In Self-Healing Fallback

## Current Position

Phase: 3 (Opt-In Self-Healing Fallback) — READY TO EXECUTE
Plan: Not started
Status: Ready to execute
Last activity: 2026-08-22 — Phase 2 complete, transitioned to Phase 3

Progress: [░░░░░░░░░░░░░░░░░░] 6/6 plans

### Milestone Phase List

1. Registry Foundation & Degradation Reporting — STAT-01..03
2. scip-swift Toolchain Update — SWFT-01..03 (externally gated on upstream release)
3. Opt-In Self-Healing Fallback — FALL-01..05
4. Swift Failure Signatures — SWFT-04
5. Semantic Install Onboarding — SEMA-01..02 (floats after Phase 1)

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 3 | - | - |
| 2 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 9min | 3 tasks | 4 files |
| Phase 01 P02 | 9min | 3 tasks | 4 files |
| Phase 01 P03 | 9min | 2 tasks | 2 files |
| Phase 02 P01 | 13min | 2 tasks | 6 files |
| Phase 02 P02 | 7min | 3 tasks | 4 files |
| Phase 02 P03 | 9min | 2 tasks | 4 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Reporting (Phase 1) ships before fallback (Phase 3) — degradation reporting is the safety valve that makes opt-in fallback acceptable
- [Roadmap]: scip-swift pin bump (Phase 2) precedes signature capture (Phase 4) — stderr signature strings are version-specific to the pinned binary
- [Roadmap]: Generic fallback uses a distinct self-healing DEGRADED state, never reuses permanent `search_only=1`
- [Phase 01]: Failure cause persists as origin slug + one-line reason + untruncated stderr in three additive registry columns; recovery commands are derived at read time per origin, never persisted (D-02/D-03/D-09)
- [Phase 01]: record_failure is an INSERT..ON CONFLICT write that overwrites run facts but preserves search_only/overrides/tracked_files; upsert's conflict list NULL-clears the failure fields on success (D-04/D-05/D-06)
- [Phase 01]: jarvis list keeps the 5-column TSV contract with glyph-prefixed status (✗/◐/✓) and a 6th reason field on failed rows only; jarvis status prints origin/cause/recovery plus a 20-line stderr tail with persistence pointer (D-08, resolution #4)
- [Phase 01]: 01-02: duplicate-slug gate rejects the REQUEST (path already under another slug) — stays a plain close-and-raise; a failure stamp would create a phantom row that re-trips the gate forever
- [Phase 01]: 01-02: pre-pipeline failures record language='unknown' until resolution completes (resolved_language sentinel) — D-06 full-overwrite, never last-good facts
- [Phase 01]: 01-02: search-only writes are origin-stamped on the success upsert (manual/signature + matched reason verbatim); status_reason carries reasons only, remedies stay out (D-11)
- [Phase 01]: 01-03: last_index_run.origin derives from origin_of() not raw status_origin — keeps origin consistent with recovery_for on legacy search-only rows (manual fallback)
- [Phase 01]: 01-03: MCP degradation payloads are additive-only — nav errors gain conditional state/cause/recovery (D-14), getIndexStatus gains last_index_run + pointer-truth capabilities (D-13/D-15); Phase 3's degraded origin slots in with zero reshaping
- [Phase 01]: 01-03: capability derivation is double-layer never-raise and never spawns — filesystem zoekt glob + row read only, enforced by monkeypatched subprocess/ZoektLifecycle raiser tests (A5)
- [Phase 02]: Phase 02-01: scip-swift installs resolve latest via one anonymous releases/latest API call with inclusive >= 0.3.0 floor (dispatch fix landed in 0.3.0; a hypothetical 0.2.2 stays excluded) — no exact-tag pin, auto-roll
- [Phase 02]: Phase 02-01: checksum is the GitHub API asset digest (immutable, server-computed); no .sha256 sidecar probing — sidecar route for zoekt/scip untouched (add-alongside)
- [Phase 02]: Phase 02-01: --cache-dir rides every Swift invocation to config.swift_cache_dir(slug); jarvis computes the path only, upstream scip-swift creates the dir and its manifest invalidates on binary/toolchain change
- [Phase 02]: Phase 02-02: runtime scip-swift floor fires as the first statement of index_repo's swift branch, inside phase-1's pre-pipeline failure wrap — a too-old binary persists failed_hard with the cause via the existing hook, zero new wiring (D-04)
- [Phase 02]: Phase 02-02: check_scip_swift_version reuses parse_scip_version verbatim (v-optional regex parses the no-v scip-swift format); warn-by-omission on unparseable output, identical policy to check_scip_version
- [Phase 02]: Phase 02-02: watch ignores are a pure component-membership set extension (.scip-cache, .build, DerivedData, .index-store, IndexStore, .swiftpm); the one-shot xcshareddata write is accepted not ignored — ignoring it would start suppressing legitimate .xcodeproj triggers (orchestrator resolution #2)
- [Phase 02]: Phase 02-02: forget sweeps cache/scip-swift/<slug>/ with ignore_errors=True beside the lancedb sweep — path built solely from the slug so sibling caches provably survive (D-06)
- [Phase 02]: [Phase 02]: Phase 02-03: mini_xcode_repo fixture is byte-copied from upstream XcodeTestProject@v0.3.0 with inner names preserved (pbxproj references them; identity is the directory) — proven locally via real jarvis index (swift/indexed, xcodebuild dispatch) as verification only, no committed integration test (D-09/D-10)
- [Phase 02]: [Phase 02]: Phase 02-03: smoke step gates RUNNER_OS inside the run block (Linux leg runs the step and proves the skip); fixture copy is a standalone git-init'd repo under RUNNER_TEMP because detection reads git ls-files; cache-dir contract asserted via no in-tree .scip-cache/.build + cache root under JARVIS_DATA_DIR
- [Phase 01 UAT]: All 9 judgment-tier prohibition verdicts confirmed (UPHELD) by human review — no prohibition reopened
- [Phase 01 UAT]: WR-01 (Ctrl-C during retry wipes prior failure record, row can strand at 'indexing') accepted as known edge — Phase 3 FALL-03 mitigates; WR-03 unified "stale — indexed at <commit>" wording kept plan-literal

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 2 → resolved 2026-08-22]: upstream gate cleared — scip-swift v0.3.0 (dispatch fix) released; installed live locally + on CI (PR #39 setup-smoke, digest-verified). Residual: anonymous api.github.com 403 rate-limit flake on shared runner IPs (one rerun; hardening candidate for milestone audit: honor GH_TOKEN in CI or retry on 403)

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-08-21T18:50:24.407Z
Stopped at: Phase 2 complete, ready to plan Phase 3
Resume file: None

### Planning Overrides

- [Phase 3]: decision-coverage-plan gate returned could-not-parse (smart-discuss CONTEXT.md has area-formatted decisions, no D-NN ids). Substantive coverage verified by gsd-plan-checker dedicated CONTEXT-translation check (VERIFICATION PASSED). Accepted as format gap, not a coverage gap.
