# Jarvis — Indexing Robustness & scip-swift Update

## What This Is

Jarvis is a personal, local-first code-intelligence MCP server: SCIP-backed navigation
(go-to-definition, references, call/type hierarchy), Zoekt-backed lexical search, a package
dependency graph, and optional semantic search — exposed as MCP tools over stdio to Claude
Code and Codex. This milestone hardens the indexing pipeline's failure behavior and updates
the Swift indexer toolchain.

## Core Value

An indexing failure should never leave a repo with nothing: when full SCIP navigation isn't
possible, search still works and the system explains why and how to recover.

## Current Milestone: v1.0 Indexing Robustness & scip-swift Update

**Goal:** An indexing failure never leaves a repo with nothing — search keeps
working, and the system explains why and how to recover.

**Target features:**
- scip-swift pin bump v0.1.2 → v0.2.1 with `_swift_indexer_cmd` compatibility verification
- Generic opt-in fallback: any scip indexer failure degrades to a search-only publish
- Self-healing fallback: every reindex retries the full build, degrades only on fresh failure
- Opt-in controls: persisted per-repo CLI flag + global env var
- scip-swift failure signatures join the automatic `_SEARCH_ONLY_SIGNATURES` fallback
- Degradation reporting in `jarvis status` / `getIndexStatus`: cause, origin, recovery
- TTY-gated semantic-extra install prompt with per-repo decline memory
- Non-TTY paths (watch, MCP reindex) keep silent skip + stderr hint

## Requirements

### Validated

- ✓ Degradation/failed reporting in `jarvis status` / `getIndexStatus` (persisted cause, origin, recovery) — Phase 1
- ✓ 9 judgment-tier prohibitions from Phase 1 confirmed by human review — Phase 1 UAT
- ✓ scip-swift pin updated off v0.1.2 (auto-roll latest ≥ 0.3.0, digest-verified install, runtime floor) with `_swift_indexer_cmd` compatibility verified live + on CI — Phase 2
- ✓ Generic opt-in fallback: post-build-start failure degrades to a queryable search-only publish; tri-state CLI flag + env var, precedence CLI > persisted > env > off — Phase 3
- ✓ Known scip-swift failure signatures (no build system, no IndexStore) degrade automatically — no opt-in, Kotlin/AGP parity; tokens captured verbatim from scip-swift 0.3.0, pinned by unit tests; unmatched failures stay hard — Phase 4
- ✓ Zoekt lexical search (`searchCode`) with lazy webserver lifecycle — existing
- ✓ Package dependency graph + `blastRadius` (2-hop BFS) — existing
- ✓ Semantic search (LanceDB + sentence-transformers, RRF fusion) behind optional `semantic` extra — existing
- ✓ Atomic publish: failed index never disturbs the live index — existing
- ✓ Language detection from git-tracked files with `--language` override — existing
- ✓ Automatic search-only degradation for Kotlin-version-mismatch and Android/AGP signatures — existing
- ✓ Manual `--search-only` publish mode — existing
- ✓ `jarvis watch` debounced foreground reindexing — existing
- ✓ setup.sh distribution of scip/zoekt/scip-swift binaries via jarvis-index releases — existing

### Active

- [ ] Interactive `jarvis index` (TTY) with semantic extra missing: y/N prompt, auto-install on yes, decline remembered per-repo
- [ ] Non-TTY contexts (watch, MCP-triggered reindex) keep today's silent skip + stderr hint for semantic

### Out of Scope

- Auto-fallback on any failure without opt-in — deliberate design intent (`index_cli.py:84`): transient breaks and missing binaries must fail loudly, not be laundered into apparent success
- Reusing permanent `search_only=1` semantics for the generic fallback — it's a one-way trap (only `jarvis forget` escapes); self-healing retry chosen instead
- Semantic install prompts outside a TTY — watch/MCP runs must never block on stdin
- Un-setting `--search-only` for the existing manual/signature paths — unchanged behavior, out of this milestone
- Windows support — existing platform constraint (macOS/Linux only)

## Context

Brownfield: the codebase is mapped in `.planning/codebase/` (ARCHITECTURE, STACK, STRUCTURE,
CONVENTIONS, INTEGRATIONS, TESTING, CONCERNS). Key implementation surfaces for this milestone:

- `src/jarvis/index_cli.py` — `index_repo()` pipeline, `_SEARCH_ONLY_SIGNATURES` (line 87),
  fallback branch (lines 817–833), `_publish_search_only`, `_run_semantic_stage` (lines 583–612),
  `_swift_indexer_cmd`
- `src/jarvis/registry.py` — persisted per-repo state (`search_only`, overrides); generic fallback
  needs distinct state from permanent `search_only=1` so reindex retries the full build
- `setup.sh:48` — `SCIP_SWIFT_VERSION="v0.1.2"` pin; `.github/workflows/setup-smoke.yml` guards
  that the pin resolves
- `src/jarvis/server.py` — `getIndexStatus` MCP tool surface for failure reporting

Known gap that motivated this: a scip-swift failure matches no signature → hard failure; on a
first-time index nothing publishes at all (Zoekt runs after the SCIP indexer), despite
search being perfectly viable. Swift is the most failure-prone supported language (scheme
selection, xcodebuild vs swiftpm) and the only caveated one with no fallback.

The latest scip-swift release version could not be verified during questioning (network tools
denied); verify at plan time via `gh release list --repo jarvis-intelligence/scip-swift`.

## Constraints

- **Tech stack**: Python 3.12–3.14, direct sqlite3, frozen dataclasses, no new runtime deps without discussion — existing conventions
- **setup.sh**: strictly POSIX sh (must run under dash; tested by `tests/test_setup_sh.py`)
- **Compatibility**: existing registries must keep working — new per-repo state must be additive; persisted `search_only=1` repos keep current semantics
- **Non-interactive safety**: watch/MCP index paths must never prompt or block on stdin
- **Testing**: test files mirror source modules 1:1; unit tests mock subprocess/IO; integration tests gated on real binaries
- **scip-swift**: release assets are macOS arm64 only — other platforms skip install

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Generic fallback is opt-in (flag + env var), not default | Preserves "fail loudly" design intent; opt-in makes degradation a user choice | — Pending |
| Generic fallback self-heals (retry full build each reindex) | Transient failures recover automatically; avoids the permanent `search_only=1` trap | — Pending |
| Both CLI flag (per-repo, persisted) and env var (global) | Per-repo control for individual repos; env var covers MCP-triggered and fleet-wide use | — Pending |
| scip-swift signatures join the automatic fallback list | Same pattern as Kotlin/AGP: known-unfixable failures shouldn't require opt-in | — Pending |
| Failure cause persists as origin + one-line reason + untruncated stderr in 3 additive registry columns; recovery derived at read time | Additive migration keeps legacy registries working; no persisted recovery commands to go stale | ✓ Shipped — Phase 1 |
| scip-swift install auto-rolls to latest release with ≥ 0.3.0 floor (no exact-tag pin); checksum = GitHub API asset digest | Upstream v0.2.0/v0.2.1 were broken; 0.3.0 carries the dispatch fix; auto-roll keeps the pin off stale broken tags; digest is immutable and server-computed | ✓ Shipped — Phase 2 |
| Swift signatures use path-free verbatim tokens from the pinned binary; auto-roll wording drift fails hard by design | Only stable, path-free strings are signature-worthy; generic wrappers hide transient failures that must stay loud | ✓ Shipped — Phase 4 |
| Degrade gate keys on a `published` flag; degrade-branch bookkeeping failures are honest (partial-landing reported, primary error never masked by a record_failure failure) | Two review iterations hardened the failure-of-failure paths: a locked db after a good publish must never retire the index; a record_failure crash must never replace the original error | ✓ Shipped — Phase 3 review fixes |

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-23 after Phase 4*
