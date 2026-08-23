# Requirements: Jarvis — Indexing Robustness & scip-swift Update

**Defined:** 2026-08-21
**Core Value:** An indexing failure never leaves a repo with nothing — search keeps working, and the system explains why and how to recover.

## v1 Requirements

Requirements for milestone v1.0. Each maps to roadmap phases.

### Fallback & Degradation

- [x] **FALL-01**: With fallback enabled, a post-build-start SCIP indexer failure publishes a search-only index (Zoekt + semantic queryable) instead of leaving the repo with nothing
- [x] **FALL-02**: Fallback is opt-in — persisted per-repo tri-state CLI flag + global `JARVIS_FALLBACK_SEARCH_ONLY` env var; precedence CLI > persisted > env > off
- [x] **FALL-03**: Degraded state self-heals — every reindex/watch retries the full build first, degrading again only on fresh failure
- [x] **FALL-04**: Pre-build failures (missing binary, version check, bash-shim) stay hard failures even with fallback enabled
- [x] **FALL-05**: Watch doesn't treadmill a persistently-failing degraded repo (sha-keyed retry skip)

### Status & Reporting

- [x] **STAT-01**: `jarvis status` / `getIndexStatus` report degradation: origin (signature/opt-in/manual), persisted cause, recovery command
- [x] **STAT-02**: Nav tools' error payloads explain degraded/search-only state with cause + recovery
- [x] **STAT-03**: `getIndexStatus` exposes machine-readable capability fields (navigation availability, reason, recovery)

### Swift Toolchain

- [x] **SWFT-01**: scip-swift pin moves off v0.1.2 to a fixed release cut from main (v0.2.0/v0.2.1 carry an xcodebuild-dispatch regression); `_swift_indexer_cmd` compatibility verified against the pinned release
- [x] **SWFT-02**: setup.sh handles the current release-asset naming; checksum verification retained
- [x] **SWFT-03**: scip-swift cache lives outside repo trees (`--cache-dir` under `~/.jarvis`); watch never self-triggers on it
- [x] **SWFT-04**: Verified scip-swift failure signatures join `_SEARCH_ONLY_SIGNATURES`, captured from the pinned binary's real stderr

### Semantic Onboarding

- [ ] **SEMA-01**: TTY `jarvis index` with semantic extra missing offers install (y/N), auto-installs on yes, remembers decline per-repo
- [ ] **SEMA-02**: Non-TTY paths (watch, MCP reindex) never prompt — silent skip + stderr hint preserved

## v2 Requirements

Deferred to future milestones. Tracked but not in current roadmap.

### Navigation Supplementation

- **NAVS-01**: Read-time search supplementation of nav tools (Sourcegraph-style per-query fallback)
- **NAVS-02**: Degradation history/timestamps ("degraded since, N retries") telemetry

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Silent auto-fallback on any failure without opt-in | Fail-loudly design intent (`index_cli.py:84`): transient breaks and missing binaries must fail loudly, not be laundered into apparent success |
| Reusing permanent `search_only=1` for the generic fallback | One-way trap (only `jarvis forget` escapes); distinct self-healing `DEGRADED` state chosen instead |
| Pinning scip-swift v0.2.0 or v0.2.1 | Both silently ignore `--build-tool xcodebuild` and changed the release-asset contract; a fixed release must be cut from main |
| Semantic install prompts outside a TTY | Watch/MCP index paths must never block on stdin |
| Un-setting `--search-only` for existing manual/signature paths | Unchanged behavior, out of this milestone |
| Windows support | Existing platform constraint (macOS/Linux only) |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FALL-01 | Phase 3 | Complete |
| FALL-02 | Phase 3 | Complete |
| FALL-03 | Phase 3 | Complete |
| FALL-04 | Phase 3 | Complete |
| FALL-05 | Phase 3 | Complete |
| STAT-01 | Phase 1 | Complete |
| STAT-02 | Phase 1 | Complete |
| STAT-03 | Phase 1 | Complete |
| SWFT-01 | Phase 2 | Complete |
| SWFT-02 | Phase 2 | Complete |
| SWFT-03 | Phase 2 | Complete |
| SWFT-04 | Phase 4 | Complete |
| SEMA-01 | Phase 5 | Pending |
| SEMA-02 | Phase 5 | Pending |

**Coverage:**

- v1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0 ✓

---
*Requirements defined: 2026-08-21*
*Last updated: 2026-08-21 after roadmap creation (v1.0 — 5 phases)*
