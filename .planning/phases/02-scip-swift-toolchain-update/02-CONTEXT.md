# Phase 2: scip-swift Toolchain Update - Context

**Gathered:** 2026-08-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Move jarvis onto a working scip-swift release and adapt every jarvis-side surface to the new binary's contract: setup.sh install (latest-resolution + version floor + checksum), the release cut itself (jarvis-intelligence/scip-swift is ours — tagging main and publishing assets + `.sha256` sidecars is in-scope work here), `--cache-dir` plumbing to `~/.jarvis/cache/scip-swift/<slug>/`, watch self-trigger prevention for Swift artifacts, and CI-smoke verification of `_swift_indexer_cmd` xcodebuild dispatch. No fallback behavior changes (Phase 3), no signature capture (Phase 4), no registry/reporting changes beyond what Phase 1 already landed.

</domain>

<decisions>
## Implementation Decisions

### Pin strategy under the external gate
- **D-01:** setup.sh resolves the **latest scip-swift release at install time** (gh api / redirect), NOT a hand-locked exact tag — user's explicit choice against the exact-tag pattern used for SCIP/zoekt. Deviates from ROADMAP wording "fixed release"; intent (a working binary) preserved via D-02 — **Reversibility:** costly — switching back to an exact-tag pin later rewrites the setup.sh resolution block and its smoke-CI guard
- **D-02:** Latest-resolution is guarded by a **minimum-version floor** (e.g. `> v0.2.1`): resolved latest below the floor = loud setup failure stating no good release exists yet. This is what keeps auto-roll off the broken v0.2.0/v0.2.1 releases — **Reversibility:** costly — the floor constant is load-bearing for install correctness on every future release
- **D-03:** **Cutting the release is in-scope work for this phase** — tag scip-swift main, publish release assets + `.sha256` sidecars. The external gate resolves on our schedule; the floor is set to the tag we cut
- **D-04:** `jarvis index` adds a **runtime scip-swift version floor check** (mirroring `MIN_SCIP_VERSION` for `scip`): too-old binary fails loudly with a re-run-setup.sh recovery hint. Necessary because installs now auto-roll; pairs with Phase 1's failure reporting for the error payload

### Cache directory
- **D-05:** scip-swift `--cache-dir` points at a **per-repo keyed** `~/.jarvis/cache/scip-swift/<slug>/` — deterministic isolation, no cross-repo IndexStore interference, clean lifecycle ownership
- **D-06:** `jarvis forget <slug>` **also deletes** that repo's cache dir — forgetting a repo removes everything jarvis stored for it

### Watch-ignore coverage
- **D-07:** Swift artifact ignores are added to the **existing ignore logic in `watch.py`** (`should_ignore_path()` + its ignore lists) — single ignore path for all languages, covered by the existing pure/fake-clock unit-test harness
- **D-08:** The ignore list is **broad — directory names AND suffix patterns, wherever they appear**: `.scip-cache`, `.build`, `DerivedData`, `.index-store`, `IndexStore`, `.swiftpm` build outputs. Belt-and-suspenders: the cache now lives outside the repo, but xcodebuild/SwiftPM configs can still drop artifacts in-tree

### Compatibility verification
- **D-09:** `_swift_indexer_cmd` compatibility is proven by **CI smoke only** — extend `.github/workflows/setup-smoke.yml` with a post-install `jarvis index` run on a Swift fixture. No local integration test (user's explicit choice against the fixture-integration-test pattern)
- **D-10:** The CI smoke indexes a **new small `.xcodeproj` fixture under `tests/fixtures/`** — it must exercise `_prefers_xcodebuild()` → `--build-tool xcodebuild` dispatch, which is precisely the v0.2.x regression; `mini_swift_repo` as-is would not. macOS runners have Xcode; non-macOS legs already skip Swift install

### Claude's Discretion
- Resolution mechanism details (gh api vs redirect fallback) and floor constant naming in setup.sh
- Exact fixture project contents (minimal .xcodeproj that xcodebuild can index on a GitHub macOS runner)
- Whether the version-floor check reuses a shared helper with the existing `scip` version gate
- Checksum sidecar handling details under latest-resolution (asset name discovery)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Planning documents
- `.planning/ROADMAP.md` §Phase 2 — goal, SWFT-01..03, 4 success criteria, and the jarvis-side vs upstream-gated notes (contingency if release isn't cut — now largely superseded by D-03)
- `.planning/REQUIREMENTS.md` §Swift Toolchain — SWFT-01/02/03 definitions; §Out of Scope (v0.2.0/v0.2.1 pinned never)
- `.planning/PROJECT.md` §Context — `setup.sh:48` pin location, setup-smoke.yml guard, `--cache-dir` requirement

### Codebase maps
- `.planning/codebase/INTEGRATIONS.md` §External Binaries (scip-swift entry: xcodebuild backend, `_prefers_xcodebuild()`, macOS arm64 constraint) and §CI/CD (setup-smoke.yml role)
- `.planning/codebase/STACK.md` §Configuration — `JARVIS_BIN_DIR`, `JARVIS_SETUP_SOURCED` setup.sh test seams

### Source surfaces this phase modifies
- `setup.sh` — `SCIP_SWIFT_VERSION` pin (~line 48) → latest-resolution + floor; asset naming; checksum verification (POSIX sh, dash-tested by `tests/test_setup_sh.py`)
- `src/jarvis/index_cli.py` — `_swift_indexer_cmd`, `_prefers_xcodebuild()`, version-gate pattern at `MIN_SCIP_VERSION`; `--cache-dir` argv plumbing; `forget` path for cache sweep
- `src/jarvis/watch.py` — `should_ignore_path()` + ignore lists (pure, unit-tested)
- `.github/workflows/setup-smoke.yml` — post-install Swift index smoke
- `tests/fixtures/` — new `.xcodeproj` fixture; `tests/test_setup_sh.py` — pin-consistency guards to adapt
- Upstream: `jarvis-intelligence/scip-swift` — release cut from main (D-03): tag + assets + `.sha256` sidecars

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `MIN_SCIP_VERSION` gate in `index_cli.py` — the exact pattern D-04's scip-swift floor check mirrors
- `should_ignore_path()` in `watch.py` — pure function, fake-clock testable; D-07/D-08 extend it
- `tests/test_setup_sh.py` — POSIX/dash test harness for setup.sh changes; pin-sync guards
- `tests/fixtures/mini_swift_repo` + integration-test skip-when-absent pattern — reference for fixture shape (but see D-10: a new .xcodeproj fixture is needed)
- `_run()` helper (env-merged subprocess) in `index_cli.py` — how the indexer argv (now with `--cache-dir`) is executed

### Established Patterns
- Pins live in setup.sh and are guarded by CI (ZOEKT_COMMIT/SCIP_COMMIT file↔script sync tests) — D-01 replaces the scip-swift pin with resolution+floor; the sync-test pattern may need adapting, not replicating
- setup.sh strictly POSIX sh; `JARVIS_SETUP_SOURCED` test seam
- scip-swift releases: macOS arm64 assets only; other platforms skip install (universal-binary gate noted in roadmap — treat arm64-only as current truth)

### Integration Points
- `_swift_indexer_cmd` argv construction (index_cli.py) — where `--cache-dir ~/.jarvis/cache/scip-swift/<slug>/` is injected
- `_cmd_forget` / registry removal path — where D-06's cache sweep hooks in
- Watch event pipeline (`index_cli.py` `_cmd_watch` → `watch.py`) — where ignored patterns filter events
- setup-smoke workflow — where D-09/D-10's post-install index lands

</code_context>

<specifics>
## Specific Ideas

Explicit user calls against recommended options (locked intent):
- Latest-at-install resolution instead of an exact locked tag (D-01) — user accepts auto-roll, guarded by the floor (D-02)
- CI-smoke-only verification instead of a local integration test (D-9/D-10)
- The upstream release is ours to cut — do it inside this phase (D-03), don't wait on anyone

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 2-scip-swift Toolchain Update*
*Context gathered: 2026-08-21*
