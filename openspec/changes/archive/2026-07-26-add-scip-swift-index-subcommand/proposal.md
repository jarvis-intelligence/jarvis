## Why

`openspec/specs/swift-language-indexing/spec.md` already locks in `detect_language()` returning `("swift", ["scip-swift", "index"])` and a full "Successful Swift index build" scenario, but no `scip-swift` binary on PATH actually understands the `index` subcommand yet — `scip-swift`'s current v0.1.0 shape is a bare root command (`scip-swift <repo-path> [--output ...]`), not `scip-swift index [<repo-path>] --output <path>`. Swift support in codeintel is therefore detection-only: real end-to-end indexing has never been exercised, and there's no regression test proving it works. This change closes that gap by making the external `scip-swift` binary satisfy the invocation contract codeintel already committed to, and by adding a real integration test that proves the whole pipeline (detect → build → convert → publish → query) works for a Swift repo.

## What Changes

- Restructure the sibling `scip-swift` CLI (`/Users/ddphuong/Projects/scip-swift`, outside this repo) from a single root command into a thin `ScipSwiftCommand` router with one `IndexCommand` subcommand set as `defaultSubcommand`, so both `scip-swift <repo-path>` (existing v0.1.0 shape) and `scip-swift index <repo-path> --output <path>` (codeintel's shape) behave identically.
- No behavior change on the codeintel side — `src/codeintel/index_cli.py`'s `_LANGUAGE_INDEXERS[".swift"]` entry and its `_run([*indexer_cmd, "--output", ...], cwd=repo_path, ...)` call already match the accepted spec exactly; this is confirmed, not modified.
- Add a real end-to-end integration test (`tests/test_index_cli.py::test_index_repo_end_to_end_for_swift_repo`) plus a minimal fixture SwiftPM package (`tests/fixtures/mini_swift_repo/`), gated by a `scip-swift`-presence `skipif` following the existing `scip-python`/`zoekt-index` gating pattern in the same file.

## Capabilities

### New Capabilities
(none — no new capability area is introduced)

### Modified Capabilities
- `swift-language-indexing`: existing requirement text is unchanged; adds one new scenario to "Requirement: Swift indexing via scip-swift" documenting that the `["scip-swift", "index"]` invocation is now verified end-to-end by a real integration test (previously accepted but unverified, since no `scip-swift` binary understood `index` yet)

## Impact

- **Affected repos**: `scip-swift` (sibling repo, restructured CLI — no codeintel source changes); `codeintel` (new test + fixture files only).
- **Affected code**: `tests/test_index_cli.py` (new test function, new skip-gate list), `tests/fixtures/mini_swift_repo/` (new fixture).
- **MCP tools/CLI**: none of codeintel's 8 MCP tools or CLI commands (`index`/`list`/`status`/`reindex`/`forget`/`watch`) change behavior or signature.
- **Architectural guarantees**: read-only runtime, atomic publish, and rebuild-not-accumulate package graph are all unaffected — the new test exercises the existing `index_repo()` path unchanged.
- **Dependencies**: requires a `scip-swift` v0.1.0+ binary (with the `index` subcommand) on `PATH` to run the new test un-skipped; no new Python dependencies.
