# Project Research Summary

**Project:** jarvis — Indexing Robustness & scip-swift Update (v1.0)
**Domain:** Local-first code-intelligence MCP server (brownfield integration)
**Researched:** 2026-08-16
**Confidence:** HIGH

## Executive Summary

Jarvis is a local-first MCP server providing code-intelligence (go-to-definition, find-references, type hierarchy, semantic search) via SCIP indexes and Zoekt. This milestone makes Swift indexing robust: currently a failed Swift SCIP build leaves the repo with nothing — no navigation, no search. The fix is an opt-in, self-healing fallback that degrades to search-only mode while preserving the ability to retry the full build on subsequent reindexes.

The recommended approach layers five features into the existing three-layer architecture (CLI → pipeline → registry) without new modules or runtime dependencies. The critical sequencing constraint is that the scip-swift pin bump (v0.1.2 → a future release cut from main) **must precede** signature collection, because failure-signature strings are version-specific to the pinned binary's stderr. Equally critical: degradation reporting in `jarvis status` / `getIndexStatus` **must ship together with** the opt-in fallback — reporting is the safety valve that makes silent degradation acceptable. Without it, the fallback re-creates the exact anti-pattern the milestone exists to fix.

The highest-risk item is not a code change but a supply-chain fix: scip-swift v0.2.0 and v0.2.1 silently broke xcodebuild dispatch, and both changed the release-asset naming contract (dropping the `.sha256` sidecar and platform suffix). The pin cannot bump to either 0.2.x release; instead, a new scip-swift release must be cut from main (where the dispatch fix lives), with the release workflow restored to publish checksum sidecars. All other features are additive registry-schema changes using the proven `_ensure_column` migration pattern.

## Key Findings

### Recommended Stack

No new runtime dependencies. The entire change set uses Python stdlib (`sys`, `input`, `os.environ`, `shutil.which`, `subprocess`), the existing `uv` package manager (for programmatic semantic-extra installation), and the existing `sqlite3` registry. The scip-swift pin must move from v0.1.2 to a **yet-to-be-cut release from main** — v0.2.0/v0.2.1 both carry a regression where `--build-tool xcodebuild` is silently ignored, breaking `.xcodeproj` repos.

**Core technologies:**
- **scip-swift (next release from main):** Swift SCIP indexer — main branch restores xcodebuild dispatch, adds relationship metadata (enabling real Swift typeHierarchy), `--destination` flag, and universal binary; v0.2.0/v0.2.1 are broken for `.xcodeproj` repos
- **sqlite3 registry (stdlib):** Persist fallback opt-in, degradation reason, and semantic-prompt decline per-repo via additive columns using the existing `_ensure_column` migration — no schema breakage for existing registries
- **Python stdlib TTY detection:** `sys.stdin.isatty()` + `sys.stderr.isatty()` + `CI` env check for the semantic-install prompt — no CLI framework needed for a single y/N question

### Expected Features

**Must have (table stakes):**
- Generic opt-in self-healing fallback — a failed SCIP build never leaves a repo with nothing; search-only publish with automatic retry on next reindex
- Degradation reporting in `jarvis status` and `getIndexStatus` — visible cause, origin taxonomy, and recovery command (the safety valve making fallback acceptable)
- scip-swift pin kept current — bump to a fixed release, not the broken v0.2.0/v0.2.1
- scip-swift failure signatures — extend `_SEARCH_ONLY_SIGNATURES` for known-unfixable Swift failures (verified against the pinned binary's stderr)

**Should have (competitive):**
- TTY install offer for the `semantic` extra with per-repo remembered decline — Homebrew-style ask-once-persist, ahead of the pip/uv ecosystem norm of hint-only
- Machine-readable capability fields in `getIndexStatus` — lets MCP agent consumers adapt their tool selection

**Defer (v2+):**
- Read-time search supplementation of nav tools (Sourcegraph-style per-query fallback) — much larger design; publish-time fallback must prove itself first
- Degradation history/timestamps — polish, needs retry telemetry that doesn't exist yet

### Architecture Approach

All five features slot into the existing three-layer split without new modules. The pipeline function (`index_repo`) gains a failure-classification ladder in its indexer exception branch: bash-shim → signatures → opt-in fallback → hard failure with persisted reason. The registry gains additive columns (`status_reason`, `fallback_enabled` tri-state, `semantic_declined`) via the existing `_ensure_column` migration. The critical design decision: the generic fallback does **not** reuse `search_only=1` — it writes a new `DEGRADED_STATUS` + `status_reason`, leaving `search_only` at 0 so `_resolve_search_only()` naturally retries the full build on the next reindex (self-healing by construction). Server.py and the status CLI gain read-only reporting of the new state; `watch.py` is unchanged (it calls `index_repo()` directly and inherits all new behavior).

**Major components:**
1. **registry.py** — owns all persisted per-repo state; additive columns via `_ensure_column`; new `DEGRADED_STATUS` constant and `mark_failed(slug, reason)` method
2. **index_cli.py pipeline** — failure-classification ladder (rung order is a contract), `_resolve_fallback()` following the `_resolve_scheme` precedent, `_publish_search_only` reused for degraded publish
3. **server.py (read-only)** — `getIndexStatus` adds `statusReason`/remedy fields; nav-tool error translation covers both `search-only` and `degraded` states
4. **index_cli.py CLI** — TTY-gated semantic install prompt (only in `_cmd_index`, never in `index_repo`); env-var accessor in `config.py`

### Critical Pitfalls

1. **scip-swift release asset contract changed** — v0.2.x dropped the `.sha256` sidecar and changed asset naming; a naive pin bump 404s on both the tarball and its checksum. Fix the scip-swift release workflow to restore sidecars, cut a new release, then bump. Never drop checksum verification in setup.sh.

2. **In-repo `.scip-cache/` from v0.2.x causes watch self-trigger loops** — the new incremental-indexing cache defaults to `<repo>/.scip-cache/`, which `watch.py`'s ignore list doesn't cover. Pass `--cache-dir` explicitly under `~/.jarvis/` or extend `_IGNORED_PATH_PARTS`.

3. **Reusing `search_only=1` for the generic fallback creates a one-way trap** — it's settable but never clearable; self-healing becomes impossible. Use distinct additive registry state (`DEGRADED_STATUS` + `status_reason`) that leaves `search_only` at 0.

4. **Retire-then-fail ordering in `_publish_search_only` can leave a repo with nothing** — if Zoekt fails after SCIP artifacts are retired, the repo loses both nav and search. Reorder for the retryable (degraded) path: publish search first, retire SCIP only after search succeeds.

5. **Catch-all fallback launders missing binaries into "degraded"** — a missing `scip-swift` binary must remain a hard failure even when the user opted into fallback. Exclude pre-build failures (missing executable, version check, bash-shim) from the fallback trigger.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Registry Foundation + Failure-Reason Reporting
**Rationale:** Pure additive schema change + read-only reporting. Delivers immediate value (today's hard failures get persisted reasons and recovery hints in status) while creating the registry surface every subsequent phase depends on. No behavior change to the publish decision, so zero risk of regressions.
**Delivers:** Three additive registry columns (`status_reason`, `fallback_enabled`, `semantic_declined`), `mark_failed(slug, reason)`, `DEGRADED_STATUS` constant, reason/remedy in `jarvis status` and `getIndexStatus`, extended server.py nav-tool error translation.
**Addresses:** Status reporting (P1), registry backward compatibility
**Avoids:** Pitfall 8 (unhandled status consumers), Pitfall 3 (trap state design decided here)

### Phase 2: scip-swift Pin Bump + Supply-Chain Fixes
**Rationale:** The pin bump is a prerequisite for signature collection (stderr wording is version-specific) and unblocks relationship metadata for Swift typeHierarchy. But it requires upstream work first: cutting a new scip-swift release from main (dispatch fix) with the release workflow restored to publish `.sha256` sidecars. The jarvis-side changes are small (asset naming, `--cache-dir` handling, watch ignore list) but the upstream dependency makes this phase externally gated.
**Delivers:** scip-swift pinned to a fixed release, setup.sh adapted to new asset naming, `--cache-dir` pointed outside repo trees, `.scip-cache`/`.build`/`DerivedData` added to watch ignore list, macOS gate widened to universal binary.
**Uses:** scip-swift release from main (upstream), setup-smoke.yml guard
**Implements:** Asset-contract adaptation, watch-loop prevention
**Avoids:** Pitfall 1 (asset contract breakage), Pitfall 2 (watch self-trigger), Pitfall 6 (signatures against wrong version)

### Phase 3: Generic Opt-In Fallback with Self-Healing
**Rationale:** The behavioral core of the milestone. Depends on Phase 1's registry schema (needs `fallback_enabled` and `status_reason` columns) and Phase 2's pin bump (signatures must match the pinned binary). This is the largest single change — classification rung, `_resolve_fallback` with tri-state precedence (CLI > persisted > env > off), and the half-published-invariant fix (Zoekt-first ordering for the degraded path). Must include the sha-keyed retry policy for watch to prevent the retry treadmill on persistently-failing repos.
**Delivers:** Opt-in fallback with self-healing retry, CLI flag (`--fallback-search-only` / `--no-fallback-search-only`), `JARVIS_FALLBACK_SEARCH_ONLY` env var, fallback-trigger exclusions (missing binary, bash-shim, version check stay hard failures), sha-keyed retry skip under watch, degraded-state publish with corrected Zoekt-first ordering.
**Implements:** Classification rung c, `_resolve_fallback()`, config.py env accessor, server.py degraded-state translation
**Avoids:** Pitfall 3 (one-way trap), Pitfall 4 (retire-then-fail), Pitfall 5 (laundered missing binaries), Pitfall 7 (precedence ambiguity), Pitfall 10 (watch retry treadmill)

### Phase 4: scip-swift Failure Signatures
**Rationale:** Data-only change to `_SEARCH_ONLY_SIGNATURES`, but **must** be sequenced after Phase 2 (the pin bump) because signature strings are captured from the pinned binary's real failure output. Each signature needs a unit test embedding the exact v0.2.x-captured output. Depends on Phase 3's reporting surface to display the matched signature reason.
**Delivers:** New scip-swift entries in `_SEARCH_ONLY_SIGNATURES` for known-unfixable failures (no IndexStore produced, no build system detected), each with unit tests pinning real output from the pinned binary.
**Uses:** Pinned scip-swift binary for empirical capture
**Avoids:** Pitfall 6 (stale signature strings)

### Phase 5: Semantic Install TTY Prompt
**Rationale:** Fully independent of Phases 2–4; shares only Phase 1's `semantic_declined` column. Can float to any position after Phase 1. The bulk of the work is environment detection (uv tool vs source checkout vs uvx ephemeral) and ensuring non-TTY paths (watch, MCP) are unreachable-by-construction rather than guarded-by-conditional.
**Delivers:** TTY-gated y/N prompt for installing `jarvis-mcp[semantic]`, per-repo decline persistence, environment-aware install-command selection (uv tool / uv sync / print-only for uvx), `JARVIS_NO_PROMPT` env-var override.
**Implements:** Prompt in `_cmd_index` only, stdin/stderr isatty + CI guard, `uv` subprocess install
**Avoids:** Pitfall 9 (blocking prompts, wrong-env installs)

### Phase Ordering Rationale

- **Phase 1 first** because every later phase writes to or reads from the new registry columns; it's pure additive with no behavior change, so it's the safest foundation to land.
- **Phase 2 before Phase 4** because scip-swift failure signatures are version-specific — the pin bump must precede signature collection (this is the critical sequencing constraint from STACK.md and PITFALLS.md pitfall 6).
- **Phase 3 after Phases 1+2** because the fallback needs the registry schema (Phase 1) and the correct pinned binary (Phase 2); its reporting output needs the status surface (Phase 1). Degradation reporting (Phase 1) is the safety valve that makes the opt-in fallback (Phase 3) acceptable — shipping fallback without reporting re-creates the silent-degradation anti-pattern.
- **Phase 4 after Phase 2** because signature strings must be captured from the newly pinned binary, not the old one.
- **Phase 5 floats** — independent of the fallback pipeline; can parallelize with Phases 3–4.
- **Registry changes for fallback state and semantic-decline memory share the additive-migration discipline** — both use `_ensure_column` with NULL/0 defaults, so they can be designed together but shipped in separate phases.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2:** "Cut a new scip-swift release from main" is an external dependency with no guaranteed timeline. The phase needs a plan for what to do if the release isn't ready (proceed with v0.1.2 and defer signatures, or block). The `--cache-dir` strategy decision (out-of-repo vs watch-ignore-list) needs a design choice at plan time.
- **Phase 3:** The fallback trigger boundary (which `IndexingError` subclasses or message patterns to exclude) needs precise specification. The sha-keyed retry policy for watch needs a design decision on staleness tolerance. The Zoekt-first ordering refactor of `_publish_search_only` needs careful design to avoid breaking the existing signature-based search-only path.
- **Phase 5:** The environment-detection matrix (which `sys.prefix` path maps to which install command) needs empirical verification against real uv installations at plan time.

Phases with standard patterns (skip research-phase):
- **Phase 1:** Additive registry columns, `mark_failed`, status reporting — all follow patterns already present in `registry.py` and `server.py`. Direct source reading is sufficient.
- **Phase 4:** Data-only `_SEARCH_ONLY_SIGNATURES` additions — the pattern is established; the only new work is empirical capture against the pinned binary.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | scip-swift version facts, asset naming, and dispatch regression verified against primary source via `gh api` at both tags and main; CLI compatibility verified at source level |
| Features | MEDIUM | Comparator behavior cross-checked across docs/issues (Sourcegraph, clangd, rust-analyzer, flutter doctor); scip-swift version verified empirically; feature categorization is design judgment |
| Architecture | HIGH | All findings verified by direct source reading of `registry.py`, `index_cli.py`, `server.py`, `watch.py`, `config.py`; component boundaries traced to exact line numbers |
| Pitfalls | HIGH | Every jarvis-specific claim verified against current source; scip-swift asset/concurrency issues verified against live releases and source at tags via GitHub API; uv behavior claims are MEDIUM |

**Overall confidence:** HIGH

### Gaps to Address

- **Exact scip-swift failure signature strings for v0.2.x:** Cannot be determined from this codebase or web research; requires running the newly pinned binary against known-failing repos and capturing stderr. Flagged for empirical capture during Phase 4 execution, not during planning.
- **Semantic install command detection (uv tool vs source checkout vs uvx):** The `sys.prefix` path heuristics for environment detection are convention, not documented API. Verify the exact tool-dir path in a unit test against `uv tool dir` output at implementation time.
- **`uv tool install --force` with extras:** The exact flags for reinstalling a tool receipt with an additional extra are MEDIUM confidence from context7 docs. Verify empirically at Phase 5 plan time.
- **scip-swift release timeline:** The fix exists on main but is unreleased (Version.swift still reads 0.2.1). The milestone has an external dependency on cutting that release. Plan Phase 2 with a fallback path.

## Sources

### Primary (HIGH confidence)
- `gh release list/view --repo jarvis-intelligence/scip-swift` — release tags, dates, assets, sidecar presence
- `gh api repos/jarvis-intelligence/scip-swift/...` at tags v0.1.2, v0.2.1, and main — IndexCommand.swift (dispatch regression + fix), BuildError.swift (signature strings), release.yml (asset naming, universal binary)
- `src/jarvis/index_cli.py` — signatures, bash-shim precedence, fallback branch, `_publish_search_only`, `_resolve_*` helpers
- `src/jarvis/registry.py` — schema, `_ensure_column`, upsert semantics, `SEARCH_ONLY_STATUS`
- `src/jarvis/server.py` — `getIndexStatus`, `_registry_status`, `IndexNotFoundError` translation
- `src/jarvis/watch.py` — `_IGNORED_PATH_PARTS`
- `setup.sh` — asset construction, `install_tarball_binary`, `SCIP_SWIFT_VERSION`
- `.planning/PROJECT.md`, `.planning/codebase/ARCHITECTURE.md` — milestone decisions and system map

### Secondary (MEDIUM confidence)
- Context7 `/astral-sh/uv` — uv tool install extras, receipt preservation, uv sync --extra, uvx ephemeral envs
- Sourcegraph docs — precise code navigation with automatic search-based fallback
- clangd/rust-analyzer issues — degradation visibility patterns
- clig.dev, jez.io — TTY-gated prompting conventions
- Flutter doctor, brew doctor — doctor-pattern status reporting

### Tertiary (LOW confidence)
- MindStick, TheLinuxCode — TTY prompt patterns (corroborates stdlib conventions only)

---
*Research completed: 2026-08-16*
*Ready for roadmap: yes*
