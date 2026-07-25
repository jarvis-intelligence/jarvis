## Why

codeintel's language detection (`_LANGUAGE_INDEXERS` in `index_cli.py`) only recognizes `.ts`/`.tsx`/`.py`/`.java`/`.kt`, each mapped to an existing Sourcegraph SCIP indexer. Swift has no SCIP indexer anywhere in the Sourcegraph/community ecosystem — repos that are majority-Swift raise `UnsupportedLanguageError` and never get indexed, leaving `searchCode` (Zoekt lexical search) as the only navigation available. The 5 SCIP tools (`goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `documentSymbols`) and `blastRadius` stay permanently unavailable for Swift codebases. This is worth fixing now because Apple ships an official, high-fidelity indexing mechanism (IndexStoreDB) that can be converted to SCIP without inventing a new symbol-resolution scheme from scratch.

## What Changes

- Add a new `scip-swift` converter (external tool, not part of `codeintel`'s own package) that chains: `swift build -index-store-path <path>` (or `xcodebuild -index-store-path <path>` for Xcode-project-based apps that don't build via SwiftPM) → read the resulting IndexStore via `swiftlang/indexstore-db`'s `SymbolOccurrence` query API → emit SCIP protobuf (`Document`, `Occurrence`, `Symbol` messages).
- Register `.swift` in `_LANGUAGE_INDEXERS` (`index_cli.py`) mapped to `("swift", ["scip-swift", "index"])`, following the exact same dict-driven pattern already used for the other 4 languages — no change to the extension-majority detection algorithm or its tie-break order.
- No changes to `scip_decoder.py`, `query.py`, `server.py`, `graph.py`, or the publish/atomic-pointer-swap path — Swift repos flow through the existing `scip expt-convert` → SQLite → publish pipeline unchanged, exactly like every other language.
- Pin the Swift toolchain version used by the indexer container, matching the version-pinning discipline codeintel already applies to its vendored SCIP protobuf schema.

## Capabilities

### New Capabilities
- `swift-language-indexing`: Detecting Swift repos by extension, indexing them via a `scip-swift` (IndexStoreDB → SCIP) converter, and surfacing the same 5 SCIP nav tools plus `blastRadius` that other supported languages already get.

### Modified Capabilities
- None — no existing `openspec/specs/` entries exist yet in this repo; the existing language-indexing behavior for TS/Python/Java/Kotlin is unchanged, only extended with one more entry in the same table-driven map.

## Impact

- **Affected code**: `src/codeintel/index_cli.py` (`_LANGUAGE_INDEXERS`, `_EXT_PRIORITY`, `detect_language()` docstring/error message that lists supported extensions).
- **New external dependency**: a `scip-swift` binary must exist on `PATH` before `.swift` repos can be indexed (same requirement pattern as `scip-typescript`/`scip-python`/`scip-java`/`zoekt-index` today). This converter is new code — it does not exist in this codebase or upstream yet and is out of scope to build inside `codeintel` itself; `codeintel` only needs to invoke it.
- **Build/runtime footprint**: the Swift toolchain needed to run `swift build -index-store-path` is heavier (~1.2GB) than the TypeScript indexer's footprint (~400MB) — a cost to document, not a blocker.
- **Host requirement**: `codeintel index` for a `.swift` repo must be invoked on a macOS host (Xcode + `scip-swift` on `PATH`) whenever the repo imports Apple-platform-only frameworks (`UIKit`/`WatchKit`/`WidgetKit`) — Apple does not ship the iOS SDK for Linux, so this can't run on the Linux/GKE side. Publishing and query-serving are unaffected and continue to work anywhere (including Linux/GKE) that can read codeintel's local-first data directory — see design.md Decision 6.
- **No breaking changes**: existing supported-language behavior (TS/Python/Java/Kotlin) is untouched; unsupported repos still raise `UnsupportedLanguageError`, just with Swift no longer in that bucket.
