## Context

codeintel indexes a repo by detecting its dominant language from file extensions (`_LANGUAGE_INDEXERS` in `index_cli.py`), running the matching SCIP indexer, converting with `scip expt-convert`, building a Zoekt shard, and publishing atomically. Today this table covers `.ts`/`.tsx` (`scip-typescript`), `.py` (`scip-python`), `.java`/`.kt` (`scip-java`) — all existing Sourcegraph-maintained indexers. No SCIP indexer for Swift exists anywhere in the Sourcegraph or wider community ecosystem (confirmed present for TypeScript, Java, Python, Rust, C/C++, Go, PHP — absent for Swift). Swift repos therefore hit `UnsupportedLanguageError` and never get indexed past Zoekt's lexical search.

Apple's own toolchain provides a path around this gap: `swift build -index-store-path <path>` (SwiftPM) or `xcodebuild -index-store-path <path>` (Xcode-project-based apps) produces an IndexStore — the same index Xcode's own "jump to definition" and SourceKit-LSP use. The `swiftlang/indexstore-db` library (Apache-2.0, actively maintained by Apple) reads this IndexStore and exposes a `SymbolOccurrence` query API with definition/reference/call roles. `indexstore-db` itself is confirmed buildable and runnable on Linux (its own README documents a manual `libdispatch` header search path for this: `swift build -Xcxx -I<toolchain>/usr/lib/swift -Xcxx -I<toolchain>/usr/lib/swift/Block`) — but that only helps for pure Swift-package code with no Apple-platform-only imports. Real iOS/watchOS/widget-extension code (`import UIKit`/`WatchKit`/`WidgetKit`) cannot be compiled at all without Apple's iOS SDK, which Apple does not ship for Linux — so producing the raw IndexStore for an actual iOS app repo requires a macOS host regardless of which build command is used (see Decision 6).

## Goals / Non-Goals

**Goals:**
- Let codeintel detect and index majority-Swift repos, producing SCIP output that flows through the existing (unmodified) `scip expt-convert` → publish pipeline.
- Give Swift repos the same 5 SCIP nav tools (`goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `documentSymbols`) and `blastRadius` that TS/Python/Java/Kotlin already get.
- Keep the design consistent with codeintel's existing conventions: table-driven language detection, external-binary-on-PATH indexer invocation, version-pinning discipline for compiler/toolchain dependencies.

**Non-Goals:**
- Building the `scip-swift` converter itself is out of scope for `codeintel`'s own package — it is a new, separate external tool that codeintel only needs to invoke via `PATH`, the same way it invokes `scip-typescript`/`scip-python`/`scip-java` today.
- Not designing a general-purpose SCIP-for-Swift symbol-mangling scheme from first principles in this document — the exact `Symbol.scip_symbol` string format derived from IndexStoreDB's USR (Unified Symbol Resolution) is an implementation detail to work out when the converter is actually built, not something to lock in here.
- Not changing `detect_language()`'s extension-majority algorithm or tie-break order — only adding one new table entry.
- Not addressing Objective-C indexing (`scip-clang` already exists for that; out of scope for this change).
- Making the compile/index-build step run on Linux/GKE for iOS-SDK-dependent code (anything importing `UIKit`/`WatchKit`/`WidgetKit`) — not possible, since Apple does not distribute the iOS SDK for Linux. Only pure Swift-package code without those imports can build on Linux; that is not the common case for an iOS app repo and is not this change's target scenario.

## Decisions

**Decision 1 — Build a new `scip-swift` converter around IndexStoreDB rather than alternatives.**
Considered and rejected:
- *Stay Zoekt-only (no SCIP for Swift)*: zero effort, but Swift repos remain permanently second-class (no nav tools, no blast radius). Acceptable as today's interim state, not as the permanent answer.
- *Use an existing unofficial/community scip-swift fork*: rejected — no tagged release, untested, unknown quality/provenance; doesn't meet the same trust bar as the vendored, pinned SCIP tooling codeintel already relies on.
- *Drive SourceKit-LSP in batch mode and convert its responses to SCIP*: rejected — LSP is built for interactive, stateful use, not batch conversion; would require fragile process-lifecycle management for something meant to run once per index build.
- *Use `scip-clang` for Objective-C portions only*: rejected/partial — clang cannot parse Swift at all, so this leaves the majority of a modern Swift-based codebase unindexed; not worth the complexity for partial coverage.
- **Chosen**: build a `scip-swift` converter chaining `swift build -index-store-path` (or `xcodebuild -index-store-path`) → `indexstore-db` query API → SCIP protobuf emission. Highest fidelity (same engine as Xcode itself); the `indexstore-db` reader library is portable to Linux, though producing the index for real iOS-SDK-dependent app code still needs a macOS build host (Decision 6); builds on an Apache-2.0 foundation; reuses 100% of codeintel's existing downstream pipeline.

**Decision 2 — Integrate via the existing table-driven `_LANGUAGE_INDEXERS` pattern, not a new detection mechanism.**
Add `".swift": ("swift", ["scip-swift", "index"])` to the existing dict. This keeps `detect_language()`'s extension-majority-scan-with-priority-tie-break logic untouched and treats `scip-swift` exactly like the other three indexer binaries: expected on `PATH`, invoked as a subprocess, failure surfaces as the existing `IndexingError`.

**Decision 3 — IndexStoreDB → SCIP concept mapping** (for the converter's design, referenced here so codeintel's expectations of its output are documented):
| IndexStoreDB | SCIP |
|---|---|
| `Symbol.name` | `Symbol.display_name` |
| `Symbol.usr` | `Symbol.scip_symbol` (mangled scheme — TBD) |
| occurrence role `.definition` | `Occurrence.symbol_roles: Definition` |
| occurrence role `.reference` | `Occurrence.symbol_roles: ReadAccess` |
| occurrence role `.call` | `Occurrence.symbol_roles: ForwardCall` |
| `Symbol.kind` | SCIP `Symbol.kind` enum |
| `SymbolOccurrence.location` | `Occurrence.range` |

**Decision 4 — Support both `swift build` and `xcodebuild` as the index-store-producing build command.**
Real-world iOS-style repos may not build via SwiftPM at all. Rather than assuming `swift build` universally works, the indexer should accept an alternate `xcodebuild -index-store-path` invocation so Xcode-project-based repos aren't silently unsupported.

**Decision 5 — Extend `_IGNORED_DIRS` to exclude Xcode/SwiftPM build-artifact directories.**
Real Xcode-project repos accumulate `DerivedData/` (often several differently-named snapshots for different build configurations) and SwiftPM's own `.build/` directory, each of which recursively contains full copies of every vendored SwiftPM dependency's source. Grounded against a real multi-target app (main app + watch companion + widget extension): the raw `.swift` file count went from ~410 (actual project source) to ~6296 once these directories were included in the scan — a ~15x inflation from vendored dependency code being walked repeatedly across multiple `DerivedData` snapshots. `_IGNORED_DIRS` currently only excludes `.git`/`node_modules`/`.venv`/`__pycache__`/`dist`/`build` — none of which catch this. Add `DerivedData` and `.build` to `_IGNORED_DIRS`. (A project-local cache directory name observed in the same grounding example was left out of this list since it isn't a standard Xcode/SwiftPM convention — only add directory names that are guaranteed to mean "build artifacts, not source" across any Xcode/SwiftPM repo.)

**Decision 6 — Swift indexing requires invoking `codeintel index` on a macOS build host; publishing/serving stays on Linux/GKE unchanged.**
Because iOS-SDK-dependent code can't be compiled on Linux at all (see Context, Non-Goals), the *build+index* step for a real iOS app repo can only happen on a machine with macOS + Xcode + the target's SDKs installed. This is an operational constraint on *where* `codeintel index <repo>` is invoked for `.swift` repos, not an architecture change: `scip-swift` is still invoked as a subprocess exactly per Decision 2, just the host running that subprocess must be macOS. Once indexed, the published artifacts (`index.db`, the Zoekt shard) are plain files under codeintel's existing local-first data directory (`~/.codeintel` / `CODEINTEL_DATA_DIR`) — no different from any other language's output — so a `codeintel-server` process serving queries from Linux/GKE only needs read access to that directory (e.g. a shared/synced volume), with zero code changes to the query/serving path. Provisioning the actual macOS build host (self-hosted Mac mini, cloud Mac CI runner, etc.) is an infra decision outside this repo's code scope (see Open Questions).

## Risks / Trade-offs

- **[Risk]** Swift's USR format may change between compiler versions, silently breaking symbol correlation. **[Mitigation]** Pin the Swift toolchain version in the indexer's build environment, matching the version-pinning discipline codeintel already applies to its vendored SCIP protobuf schema.
- **[Risk]** Some real iOS projects only build via `xcodebuild`, not `swift build`. **[Mitigation]** Support `xcodebuild -index-store-path` as an alternate build command (Decision 4).
- **[Risk]** iOS/watchOS/widget-extension Swift code (`import UIKit`/`WatchKit`/`WidgetKit`) cannot be compiled on Linux at all — Apple does not ship the iOS SDK for Linux, so this is a hard platform constraint, not something `scip-swift` can work around. **[Mitigation]** Run `codeintel index` for Swift repos on a macOS host (Decision 6); only the published index artifacts need to reach the Linux/GKE-hosted query-serving side, via codeintel's existing local-first, filesystem-based publish path.
- **[Risk]** Operational cost of provisioning and maintaining a macOS build host (self-hosted Mac, cloud Mac CI runner) instead of a Linux container, plus the Swift toolchain's footprint (~1.2GB) on that host vs. the TypeScript indexer's (~400MB) in a Linux container. **[Mitigation]** Document as an accepted cost of supporting Swift at all; not a blocker for repos that need it, and not something codeintel's own container footprint changes for other languages.
- **[Risk]** `Symbol.scip_symbol` mangling scheme is unspecified pending implementation. **[Mitigation]** Treat as an explicit open question (below) to resolve during `scip-swift` implementation, not during this design.
- **[Risk]** If Sourcegraph or the SCIP community later ships an official `scip-swift`, the custom converter becomes redundant maintenance. **[Mitigation]** Treat that as a future trigger to deprecate the custom converter in favor of the upstream one — not a reason to delay this change now.
- **[Risk]** `_IGNORED_DIRS` doesn't exclude `DerivedData`/`.build`, so `detect_language()`'s file walk (and a real indexing run) would churn through thousands of duplicate vendored-dependency files in any real Xcode-project repo, inflating both detection counts and indexing time/cost. **[Mitigation]** Extend `_IGNORED_DIRS` per Decision 5 — a small, low-risk change that also benefits any existing non-Swift repo that happens to have an Xcode subproject.

## Migration Plan

1. Build and validate the standalone `scip-swift` converter (outside `codeintel`'s package) against a real Swift repo, confirming it emits valid SCIP consumable by the existing `scip expt-convert` step.
2. Add the `.swift` entry to `_LANGUAGE_INDEXERS` in `index_cli.py`, extend `_IGNORED_DIRS` with `DerivedData` and `.build` (Decision 5), and update `detect_language()`'s error message / docstring that lists supported extensions.
3. Add a unit test asserting `.swift`-majority repos resolve to `("swift", ["scip-swift", "index"])`, mirroring the existing per-extension tests in `test_index_cli.py`; add a companion test confirming files under `DerivedData/`/`.build/` are excluded from the scan.
4. No changes needed to `scip_decoder.py`, `query.py`, `graph.py`, or `server.py` — verify with the existing integration-marked test path once a real `scip-swift` binary is available, the same way `.py` real-indexer integration tests work today.
5. Rollback is trivial: removing the `.swift` table entry reverts Swift repos to raising `UnsupportedLanguageError`, with zero impact on other languages.
6. Decide the macOS build-host topology (self-hosted Mac mini, cloud Mac CI runner, etc.) that will run `codeintel index`/`scip-swift` for Swift repos, and confirm the published index-artifact directory is reachable by whatever process serves queries (shared volume, sync job, etc.) — an infra decision, out of scope for this repo's code changes.

## Open Questions

- What exact string-mangling scheme should `Symbol.scip_symbol` use to encode a Swift USR? (Deferred to `scip-swift` implementation.)
- Should `scip-swift` auto-detect SwiftPM vs. Xcode-project repos, or should codeintel pass an explicit build-command hint? (Affects whether `_LANGUAGE_INDEXERS`' `["scip-swift", "index"]` command list needs a repo-specific argument.)
- Where should the `scip-swift` binary itself be built/distributed/versioned relative to codeintel's own repo — a separate project, or a subdirectory here? (Affects the "external dependency on PATH" assumption in the proposal's Impact section.)
- What's the concrete macOS build-host setup — self-hosted Mac mini, a cloud Mac CI runner (e.g. GitHub Actions macOS runners, MacStadium), or a developer's own machine? (Infra decision outside this repo's code scope; drives how "published index reaches the Linux/GKE query side" is actually wired — shared filesystem, object storage sync, etc.)
