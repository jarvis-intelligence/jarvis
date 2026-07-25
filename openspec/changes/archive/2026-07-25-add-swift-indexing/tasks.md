## 1. scip-swift converter (external prerequisite — BLOCKED, out of codeintel's package scope)

- [ ] 1.1 Build a standalone `scip-swift` CLI that runs `swift build -index-store-path <path>` (with an `xcodebuild -index-store-path <path>` fallback mode) and reads the resulting IndexStore via `swiftlang/indexstore-db`
- [ ] 1.2 Implement the IndexStoreDB → SCIP protobuf mapping (Symbol.name → display_name, Symbol.usr → scip_symbol, definition/reference/call roles → Definition/ReadAccess/ForwardCall, Symbol.kind → SCIP kind enum, location → range)
- [ ] 1.3 Resolve the open question of the exact `Symbol.scip_symbol` mangling scheme derived from Swift's USR format
- [ ] 1.4 Pin the Swift toolchain version used to build/run the converter
- [ ] 1.5 Validate `scip-swift`'s output is consumable by codeintel's existing `scip expt-convert` step against a real Swift repo

## 2. codeintel language-detection integration

- [x] 2.1 Add `".swift": ("swift", ["scip-swift", "index"])` to `_LANGUAGE_INDEXERS` in `src/codeintel/index_cli.py`
- [x] 2.2 Update `detect_language()`'s `UnsupportedLanguageError` message and any docstrings/comments that enumerate supported extensions to include `.swift`
- [x] 2.3 Confirm no changes are needed to `_EXT_PRIORITY` tie-break semantics beyond appending `.swift` in a deliberate position
- [x] 2.4 Add `DerivedData` and `.build` to `_IGNORED_DIRS` in `src/codeintel/index_cli.py` to exclude Xcode/SwiftPM build-artifact directories from the extension-majority scan (design.md Decision 5 — real Xcode-project repos otherwise inflate file counts ~15x with duplicated vendored-dependency source)

## 3. Tests

- [x] 3.1 Add a `test_index_cli.py` case asserting a majority-`.swift` repo resolves to `("swift", ["scip-swift", "index"])`
- [x] 3.2 Add a tie-break test covering `.swift` vs. another supported extension at equal file counts
- [ ] 3.3 Add an `@pytest.mark.integration` case exercising the real `scip-swift` binary end-to-end (indexer → `scip expt-convert` → publish), mirroring the existing real-indexer integration test for Python — BLOCKED on task group 1 (no real `scip-swift` binary or Swift fixture repo exists yet)
- [x] 3.4 Verify existing `test_query.py`/`test_server_tools.py`/`test_graph.py` suites pass unmodified against a Swift-sourced index (no Swift-specific code paths needed there)
- [x] 3.5 Add a `test_index_cli.py` case confirming files under `DerivedData/` and `.build/` are excluded from `detect_language()`'s extension counts (companion to 2.4)

## 4. Documentation

- [x] 4.1 Update `README.md`'s language-detection section to list Swift alongside the existing 4 languages
- [x] 4.2 Update `docs/codebase-summary.md`'s module map note on `_LANGUAGE_INDEXERS` to mention Swift
- [x] 4.3 Note the `scip-swift` external-binary requirement in the same place the other indexer binaries (`scip-typescript`, `scip-python`, `scip-java`, `zoekt-index`) are documented as required on `PATH`
