# Roadmap: Jarvis — Indexing Robustness & scip-swift Update

## Overview

Jarvis today fails loudly and uselessly: when a SCIP indexer dies mid-build (Swift most of all), nothing publishes — no navigation, no search — and the repo is left with nothing. This milestone closes that gap. Phase 1 lays the registry foundation and makes failure/degradation visible everywhere users and agents look (`jarvis status`, `getIndexStatus`, nav-tool errors). Phase 2 moves the scip-swift pin off v0.1.2 to a fixed release cut from main and adapts the install/cache/watch surfaces to the new binary's contract. Phase 3 is the behavioral core: an opt-in, self-healing fallback that degrades post-build-start failures to a search-only publish and retries the full build on every reindex. Phase 4 captures real failure signatures from the pinned binary and folds known-unfixable Swift failures into the automatic fallback. Phase 5 floats free of the pipeline: TTY-gated semantic-extra install onboarding with per-repo decline memory. Sequencing is deliberate: reporting ships before fallback (the safety valve that makes opt-in degradation acceptable), and the pin bump precedes signature capture (stderr strings are version-specific to the pinned binary).

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Registry Foundation & Degradation Reporting** - Additive per-repo state plus cause/origin/recovery reporting in `jarvis status`, `getIndexStatus`, and nav-tool error payloads
- [ ] **Phase 2: scip-swift Toolchain Update** - Pin bump to a fixed release cut from main; setup.sh asset handling, out-of-repo cache dir, watch loop prevention
- [ ] **Phase 3: Opt-In Self-Healing Fallback** - Post-build-start indexer failures degrade to search-only publish; tri-state opt-in (CLI > persisted > env > off); full build retried every reindex; watch anti-treadmill
- [ ] **Phase 4: Swift Failure Signatures** - Verified scip-swift failure signatures captured from the pinned binary join the automatic `_SEARCH_ONLY_SIGNATURES` fallback
- [ ] **Phase 5: Semantic Install Onboarding** - TTY-gated y/N install offer for the `semantic` extra with per-repo decline memory; non-TTY paths stay silent

## Phase Details

### Phase 1: Registry Foundation & Degradation Reporting

**Goal**: Failure and degradation state is persisted and explained — every surface a user or agent consults (status CLI, `getIndexStatus`, nav-tool errors) says what happened and how to recover
**Depends on**: Nothing (first phase)
**Requirements**: STAT-01, STAT-02, STAT-03
**Success Criteria** (what must be TRUE):

  1. A repo whose index run failed hard shows its persisted failure cause and a recovery command in `jarvis status` and `getIndexStatus`
  2. A repo in search-only state via existing paths (manual `--search-only` or matched Kotlin/AGP signature) reports its origin and a recovery command — the origin taxonomy is designed to also accept the opt-in origin Phase 3 introduces
  3. Calling a navigation tool (e.g. `goToDefinition`) on a search-only repo returns an error payload naming the state, its cause, and the recovery command — not a bare "index not found"
  4. `getIndexStatus` exposes machine-readable capability fields (navigation availability, reason, recovery) that an MCP client can branch on without parsing prose
  5. An existing pre-v1.0 registry upgrades in place on next run (additive `_ensure_column` migrations only); persisted `search_only=1` repos keep their current semantics

**Plans**: 3/3 plans executed

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Registry failure record (tracer): status_origin/reason/stderr columns, record_failure, recovery_for, hard-failure hook, jarvis status/list reporting, D-04 clearing, legacy migration

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Search-only origin stamping (manual/signature) + pre-pipeline failure rows (D-05)
- [x] 01-03-PLAN.md — MCP surfaces: _error_payload state/cause/recovery (D-14) + getIndexStatus capabilities/last_index_run (D-13/D-15)

### Phase 2: scip-swift Toolchain Update

**Goal**: jarvis installs and drives a working scip-swift release — the pin moves off v0.1.2 to a fixed release cut from main, with setup.sh, caching, and watch adapted to the new binary's contract
**Depends on**: Nothing jarvis-side; externally gated on an upstream scip-swift release cut from main
**Requirements**: SWFT-01, SWFT-02, SWFT-03
**Success Criteria** (what must be TRUE):

  1. `setup.sh` installs the newly pinned scip-swift release with checksum verification passing against the release's published sidecar, and the setup-smoke workflow guards that the pin resolves
  2. `jarvis index` on a Swift `.xcodeproj` repo produces a SCIP index via the pinned binary with `--build-tool xcodebuild` genuinely dispatched (`_swift_indexer_cmd` compatibility verified against the pinned release)
  3. scip-swift's incremental cache lives under `~/.jarvis/` (via `--cache-dir`), and no indexer cache/build artifacts appear inside the indexed repo tree
  4. `jarvis watch` over a Swift repo never self-triggers reindexes from indexer artifacts (`.scip-cache`, `.build`, `DerivedData`)

**Plans**: TBD

**Notes — jarvis-side vs upstream-gated:** cutting the fixed scip-swift release from main (xcodebuild-dispatch fix + restored `.sha256` sidecars) is upstream work jarvis cannot do alone; v0.2.0/v0.2.1 are broken and out of the question. Jarvis-side work: the pin bump itself, setup.sh asset-naming adaptation, `--cache-dir` plumbing, watch-ignore coverage, and the macOS gate for the universal binary. Plan-time contingency required: if the upstream release isn't cut, hold this phase and proceed to Phase 3 (which verifies fallback via induced failures on working languages), or stay on v0.1.2 and defer Phase 4 signatures.

### Phase 3: Opt-In Self-Healing Fallback

**Goal**: With fallback enabled, a post-build-start indexer failure degrades the repo to a working search-only index instead of leaving it with nothing — and every later reindex automatically retries the full build
**Depends on**: Phase 1 (registry columns `fallback_enabled`/`status_reason` plus the reporting safety valve — shipping fallback without reporting re-creates the silent-degradation anti-pattern this milestone exists to fix)
**Requirements**: FALL-01, FALL-02, FALL-03, FALL-04, FALL-05
**Success Criteria** (what must be TRUE):

  1. With fallback enabled, an induced post-build-start indexer failure publishes a search-only index — Zoekt (and semantic, when installed) stay queryable — instead of leaving the repo with nothing
  2. Opt-in works at both levels with documented precedence: `--fallback-search-only` / `--no-fallback-search-only` persists per-repo (like `--scheme`/`--language`), `JARVIS_FALLBACK_SEARCH_ONLY` sets the global default, resolution is CLI > persisted > env > off
  3. Degraded state self-heals: once the underlying failure is fixed, the next `jarvis index`/watch reindex produces a full navigation index again without `jarvis forget`; a still-broken repo degrades again only on fresh failure
  4. Pre-build failures (missing binary, version-check failure, bash-shim path) stay hard failures — non-zero exit, nothing published — even with fallback enabled
  5. `jarvis watch` on a persistently-failing degraded repo skips the full-build retry for an unchanged source sha (no treadmill), and a source change re-triggers the full build

**Plans**: TBD

**Notes:** end-to-end verification against Swift assumes Phase 2's pinned binary, but this phase hard-depends only on Phase 1 — an upstream release slip must not block the milestone's core. The degraded publish uses corrected ordering (search published before SCIP artifacts are retired) so a Zoekt failure can't strip an existing index.

### Phase 4: Swift Failure Signatures

**Goal**: Known-unfixable scip-swift failures degrade automatically with no opt-in — matching the Kotlin/AGP pattern — using signatures captured from the pinned binary's real stderr
**Depends on**: Phase 2 (signature strings are version-specific; capture requires the pinned binary), Phase 3 (classification ladder and origin reporting in place so the failure branch isn't reworked twice)
**Requirements**: SWFT-04
**Success Criteria** (what must be TRUE):

  1. A Swift repo failing with a known-unfixable error (e.g. no IndexStore produced, no build system detected) degrades to search-only automatically — no opt-in — with status reporting the matched signature as origin
  2. Every added signature is captured from the pinned scip-swift binary's real stderr against a known-failing repo, and pinned by a unit test embedding that exact output
  3. A Swift failure matching no signature still fails hard — unverified failures are never silently degraded

**Plans**: TBD

### Phase 5: Semantic Install Onboarding

**Goal**: Interactive `jarvis index` makes the semantic extra discoverable — offered once per repo at a TTY, installed on consent, remembered when declined — while watch and MCP paths stay silent and non-blocking
**Depends on**: Phase 1 (`semantic_declined` registry column); otherwise independent of Phases 2–4 and free to float
**Requirements**: SEMA-01, SEMA-02
**Success Criteria** (what must be TRUE):

  1. On a TTY, `jarvis index` for a repo missing the `semantic` extra prompts y/N; answering yes installs `jarvis-mcp[semantic]` and the index completes with semantic search enabled
  2. Answering no is remembered per-repo — later indexes of that repo are not prompted again, while other repos still get the offer
  3. Non-TTY paths (`jarvis watch`, MCP-triggered reindex) never prompt or block on stdin — semantic stays silently skipped with the existing stderr hint

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 (Phase 5 may float earlier once Phase 1 lands; Phase 2 may slip on the upstream gate without blocking Phase 3)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Registry Foundation & Degradation Reporting | 3/3 | In Progress|  |
| 2. scip-swift Toolchain Update | 0/TBD | Not started | - |
| 3. Opt-In Self-Healing Fallback | 0/TBD | Not started | - |
| 4. Swift Failure Signatures | 0/TBD | Not started | - |
| 5. Semantic Install Onboarding | 0/TBD | Not started | - |

---
*Roadmap created: 2026-08-21 — milestone v1.0, 5 phases, 14/14 requirements mapped (granularity: standard)*
