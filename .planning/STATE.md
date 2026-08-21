---
gsd_state_version: 1.0
milestone: v1.0
current_phase: 02
current_phase_name: scip-swift Toolchain Update
status: executing
stopped_at: Completed 02-01-PLAN.md (scip-swift latest-resolution install + cache-dir plumbing)
last_updated: "2026-08-21T18:32:29.202Z"
last_activity: 2026-08-22
last_activity_desc: Phase 02 execution started
state_head: af650d23663d1ee763282ee23a53d503d0f1671e
progress:
  total_phases: 5
  completed_phases: 0
  total_plans: 6
  completed_plans: 4
milestone_name: Indexing Robustness & scip-swift Update
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-21)

**Core value:** An indexing failure never leaves a repo with nothing — search keeps working, and the system explains why and how to recover.
**Current focus:** Phase 02 — scip-swift Toolchain Update

## Current Position

Phase: 02 (scip-swift Toolchain Update) — EXECUTING
Plan: 2 of 3
Status: Ready to execute
Last activity: 2026-08-22 — Phase 02 execution started

Progress: [░░░░░░░░░░] 0%

### Milestone Phase List

1. Registry Foundation & Degradation Reporting — STAT-01..03
2. scip-swift Toolchain Update — SWFT-01..03 (externally gated on upstream release)
3. Opt-In Self-Healing Fallback — FALL-01..05
4. Swift Failure Signatures — SWFT-04
5. Semantic Install Onboarding — SEMA-01..02 (floats after Phase 1)

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

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

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 2]: Upstream-gated — requires a scip-swift release cut from main (v0.2.0/v0.2.1 are broken). Plan-time contingency: hold Phase 2 and proceed to Phase 3, or stay on v0.1.2 and defer Phase 4 signatures

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-08-21T18:32:29.150Z
Stopped at: Completed 02-01-PLAN.md (scip-swift latest-resolution install + cache-dir plumbing)
Resume file: None
