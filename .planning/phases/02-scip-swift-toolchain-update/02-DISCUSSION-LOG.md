# Phase 2: scip-swift Toolchain Update - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-21
**Phase:** 2-scip-swift Toolchain Update
**Areas discussed:** Pin strategy under gate, Cache-dir layout, Watch-ignore coverage, Compatibility verification

---

## Pin strategy under gate

### Pin resolution approach

| Option | Description | Selected |
|--------|-------------|----------|
| Exact tag lock | SCIP_SWIFT_VERSION stays an exact tag like SCIP/zoekt pins; smoke-CI guards resolution; reproducible | |
| Latest at install | setup.sh resolves latest release at install time (gh api/redirect); unblocks without vetted tag but auto-rolls every future release | ✓ |

**User's choice:** Latest at install
**Notes:** Explicit choice against the recommended exact-tag pattern; deviation from ROADMAP wording "fixed release" noted — intent preserved by the version floor below.

### Guarding latest resolution

| Option | Description | Selected |
|--------|-------------|----------|
| Version floor | Resolved latest must satisfy a minimum version (> v0.2.1); below floor = loud setup failure | ✓ |
| Bad-tag exclusion list | Skip known-bad tags (v0.2.0, v0.2.1) to newest good; list grows per broken release | |
| Unguarded latest | Trust that a good release is cut before shipping | |

**User's choice:** Version floor

### Release-cutting ownership

| Option | Description | Selected |
|--------|-------------|----------|
| Cut it in this phase | Phase includes tagging scip-swift main + publishing assets and .sha256 sidecars; gate on our schedule | ✓ |
| Wait for external cut | Proceed jarvis-side; verification waits on external event (roadmap contingency) | |

**User's choice:** Cut it in this phase
**Notes:** Supersedes the roadmap's external-gate contingency — jarvis-intelligence/scip-swift is effectively ours.

### Runtime version gate

| Option | Description | Selected |
|--------|-------------|----------|
| Add version floor check | index_cli gates on installed scip-swift version (like MIN_SCIP_VERSION); loud failure with re-run-setup.sh hint | ✓ |
| Setup-only guard | Install-time floor only; stale binaries produce confusing indexer failures | |

**User's choice:** Add version floor check

---

## Cache-dir layout

### Cache directory location

| Option | Description | Selected |
|--------|-------------|----------|
| Per-repo keyed | ~/.jarvis/cache/scip-swift/<slug>/ — isolation, no cross-repo interference, forget can sweep | ✓ |
| One flat shared dir | ~/.jarvis/cache/scip-swift/ managed by the tool; opaque lifecycle, unverified cross-repo behavior | |
| Inside index dir | Under ~/.jarvis/scip/_/<slug>/_/ next to published indexes; muddies atomic-publish discipline | |

**User's choice:** Per-repo keyed

### Cache lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| Forget sweeps cache | jarvis forget deletes the repo's cache dir; one mental model | ✓ |
| Cache survives forget | Re-index stays fast after forget/re-add; orphans accumulate | |
| Sweep + prune cmd | Forget sweep plus size-aware prune or cache subcommand; beyond phase criteria | |

**User's choice:** Forget sweeps cache

---

## Watch-ignore coverage

### Ignore mechanism placement

| Option | Description | Selected |
|--------|-------------|----------|
| Extend watch.py ignores | Patterns in should_ignore_path() alongside existing IGNORED_DIRS; single path, existing test harness | ✓ |
| Swift-only filter at CLI | Language-conditional filter in index_cli watch handler; splits ignore logic | |

**User's choice:** Extend watch.py ignores

### Ignore pattern scope

| Option | Description | Selected |
|--------|-------------|----------|
| Names + suffixes, broad | .scip-cache, .build, DerivedData, .index-store, IndexStore, .swiftpm outputs, wherever they appear | ✓ |
| Only observed artifacts | Exactly what the pinned binary produces in tests; one untested config from a watch loop | |

**User's choice:** Names + suffixes, broad

---

## Compatibility verification

### Verification mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Integration test fixture | Real-binary integration test on tests/fixtures, skip-when-absent, like other integration tests | |
| CI smoke only | Extend setup-smoke.yml with post-install jarvis index on a Swift fixture; no local test | ✓ |
| Manual checklist | Documented manual smoke; no automated guard | |

**User's choice:** CI smoke only
**Notes:** Explicit choice against the recommended integration-test pattern.

### CI smoke target

| Option | Description | Selected |
|--------|-------------|----------|
| New .xcodeproj fixture | Small .xcodeproj under tests/fixtures/ exercising _prefers_xcodebuild() → --build-tool xcodebuild dispatch (the v0.2.x regression) | ✓ |
| External repo clone | Real-world repo cloned in workflow; network-dependent CI | |
| Existing mini fixture | mini_swift_repo as-is; would not exercise xcodebuild dispatch — would pass on broken v0.2.1 | |

**User's choice:** New .xcodeproj fixture

---

## Claude's Discretion

- Resolution mechanism details (gh api vs redirect) and floor constant naming
- Fixture project contents (minimal xcodebuild-indexable .xcodeproj)
- Shared helper reuse between scip and scip-swift version gates
- Checksum sidecar/asset-name discovery under latest-resolution

## Deferred Ideas

None — discussion stayed within phase scope.
