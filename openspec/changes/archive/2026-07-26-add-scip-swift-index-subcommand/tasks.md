## 1. Restructure scip-swift into a router + `index` subcommand (sibling repo, tracked here only)

- [x] 1.1 Create `/Users/ddphuong/Projects/scip-swift/Sources/scip-swift/Commands/IndexCommand.swift` — move the current root command's full body into it verbatim (`repoPath`, `output`, `buildTool`, `configuration`, `scheme` args; `run()`; `produceIndexStore()`; `makeTemporaryDirectory()`), with `commandName: "index"`
- [x] 1.2 Replace `/Users/ddphuong/Projects/scip-swift/Sources/scip-swift/ScipSwiftCommand.swift` with a thin `@main` router: `subcommands: [IndexCommand.self]`, `defaultSubcommand: IndexCommand.self`
- [x] 1.3 `swift build` in the scip-swift repo — confirm clean build, no warnings/errors
- [x] 1.4 Run all three invocation shapes (bare `scip-swift <repo> --output <path>`, `scip-swift index <repo> --output <path>`, and codeintel's exact `cd <repo> && scip-swift index --output <path>`) against `Fixtures/MiniSwiftPackage` — confirm all exit 0 and produce byte-identical `.scip` output (aside from `Metadata.tool_info.arguments`)
- [x] 1.5 `swift test` in the scip-swift repo — confirm the existing 23-test suite still passes with no regressions
- [x] 1.6 Update scip-swift's `README.md` Usage section to document the `index` subcommand alongside the existing bare-invocation example
- [x] 1.7 Commit and push the scip-swift restructure (`Sources/scip-swift/ScipSwiftCommand.swift`, `Sources/scip-swift/Commands/IndexCommand.swift`, `README.md`)

## 2. Confirm codeintel needs zero source changes

- [x] 2.1 Re-read `openspec/specs/swift-language-indexing/spec.md`'s "Swift indexing via scip-swift" requirement and confirm it already matches the Task 1 contract exactly (no positional-path requirement, no extra flags)
- [x] 2.2 Run `uv run pytest tests/test_index_cli.py -v -m "not integration"` — confirm all non-integration tests pass unmodified, including the two tests asserting on `_LANGUAGE_INDEXERS[".swift"]`
- [x] 2.3 Verify `git status` in codeintel shows no unintended changes to `src/codeintel/index_cli.py`, `README.md`, or any existing test after Tasks 1 and 3 are done

## 3. Add a real end-to-end Swift indexing test in codeintel

- [x] 3.1 Create fixture SwiftPM package: `tests/fixtures/mini_swift_repo/Package.swift` and `tests/fixtures/mini_swift_repo/Sources/MiniSwiftRepo/Greeter.swift`
- [x] 3.2 Add `_SWIFT_REQUIRED_BINARIES`/`_missing_swift` skip-gate list to `tests/test_index_cli.py`, alongside the existing binary-presence checks
- [x] 3.3 Add `test_index_repo_end_to_end_for_swift_repo` to `tests/test_index_cli.py`, gated by `@pytest.mark.integration` + `@pytest.mark.skipif(_missing_swift, ...)`, asserting `entry.status == "indexed"`, `entry.language == "swift"`, and `COUNT(*) > 0` from `global_symbols`
- [x] 3.4 Run the new test with `scip-swift` absent from `PATH` — confirm it reports `SKIPPED`, not `FAILED` or an error
- [x] 3.5 Build/install the real `scip-swift` binary onto `PATH` (from Task 1's release build) and re-run the new test — confirm `PASSED`
- [x] 3.6 Run the full existing suite (`uv run pytest -v`) — confirm no regressions; new Swift test passes or skips cleanly depending on `scip-swift` availability
- [x] 3.7 Commit `tests/fixtures/mini_swift_repo` and `tests/test_index_cli.py` in codeintel
