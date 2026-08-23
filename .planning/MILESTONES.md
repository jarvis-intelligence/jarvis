# Milestones

## v1.0 Indexing Robustness & scip-swift Update (Shipped: 2026-08-23)

**Phases completed:** 5 phases, 11 plans, 28 tasks

**Key accomplishments:**

- Persisted failure cause (origin + one-line reason + untruncated stderr) in three additive registry columns, recorded by the index_repo hard-failure hook, explained with recovery commands by `jarvis status`, and marked ✗/◐/✓ by `jarvis list` — with legacy registries migrating in place.
- Origin-stamped search-only writes (manual/signature slugs with the matched reason verbatim) plus a pre-pipeline failure wrap so version-gate, bad-override, and detection failures leave recoverable failed_hard rows — closing D-05 with no silent failure path left in index_repo().
- Machine-readable degradation reporting at the MCP boundary: nav-tool errors carry additive state/cause/recovery structured keys (D-14), and getIndexStatus exposes last_index_run + pointer-truth capabilities.{navigation,search,semantic} (D-13/D-15) — additive only, never spawning, never leaking stderr.
- setup.sh now resolves the latest scip-swift release from the GitHub API with an inclusive 0.3.0 floor and digest-verified install, and every Swift indexer invocation carries --cache-dir to a per-slug directory under the jarvis data dir — both proven live end-to-end.
- Swift-gated scip-swift >= 0.3.0 runtime floor inside jarvis index (reusing the proven parser, warn-by-omission, phase-1 failure wrap), a forget-time sweep of the per-repo scip-swift cache, and watch ignores for six Swift build-artifact directory names — all TDD-pinned with 10 new unit tests.
- mini_xcode_repo .xcodeproj fixture adapted byte-identically from upstream XcodeTestProject@v0.3.0 (proven swift/indexed through a real local `jarvis index`), plus the setup-smoke macOS-leg post-install index step asserting xcodebuild dispatch and the out-of-tree cache contract
- Degraded publish wired end-to-end: a post-build-start failure with the tri-state fallback on (CLI > persisted > env) publishes search-only with exit 0 and a degraded/fallback row, self-heals on the next run, keeps pre-build failures loud, and never destroys an existing index on a failing fallback publish.
- Degraded state is now visible everywhere agents and users look: getIndexStatus's navigation.reason names the actual failure cause (the one real gap), list renders ◐ degraded with the cause as a 6th TSV field, and the phase-1 last_index_run/_error_payload contracts are pinned as carrying degraded/fallback verbatim with zero reshaping.
- The watch treadmill is stopped: a persistently-failing degraded repo at an unchanged sha gets one stderr note instead of a doomed multi-minute build per save, while any source change — or an explicit `jarvis index` — re-triggers the full build; watch also accepts and forwards the tri-state fallback flag.
- Two scip-swift 0.3.0 failure signatures (no-build-system, no-IndexStore) joined `_SEARCH_ONLY_SIGNATURES` with verbatim-captured pinning tests — Swift known-unfixables now degrade to search-only automatically with origin 'signature', while every unmatched failure keeps failing hard.
- TTY-gated `jarvis index` install offer (locked uv command, same-invocation semantic enablement) with a per-repo `semantic_declined` registry column — while watch/reindex/MCP/non-TTY paths stay provably prompt-free via an `offer_semantic` argparse gate.

---
