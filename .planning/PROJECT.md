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

- [ ] scip-swift pin updated to latest release (from v0.1.2), CLI compatibility with `_swift_indexer_cmd` verified
- [ ] Generic opt-in fallback: any scip indexer failure degrades to a search-only publish (Zoekt + semantic live, nav tools explain)
- [ ] Generic fallback is self-healing: next reindex/watch retries the full build, falls back again only if it still fails
- [ ] Fallback opt-in via CLI flag (persisted per-repo, like `--scheme`/`--language`) and env var (global default)
- [ ] Known scip-swift failure signatures added to the automatic (non-opt-in) `_SEARCH_ONLY_SIGNATURES` fallback
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
| WR-01 interrupted-retry edge (Ctrl-C wipes prior failure record) accepted as known edge; WR-03 unified "stale" wording kept | Human product decisions at Phase 1 UAT; Phase 3 FALL-03 mitigates the stranded row | ✓ Decided — Phase 1 UAT |

## Evolution

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
*Last updated: 2026-08-22 after Phase 1*

