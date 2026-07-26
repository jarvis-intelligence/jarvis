## Context

`openspec/specs/swift-language-indexing/spec.md` is already accepted and pins `detect_language()` to return `("swift", ["scip-swift", "index"])`, with a full scenario for "Successful Swift index build" via `index_repo()`. Nothing on the codeintel side needs to change to satisfy that spec — the gap is entirely upstream, in the sibling `scip-swift` repo (`/Users/ddphuong/Projects/scip-swift`, v0.1.0), whose CLI is currently a single root command (`scip-swift <repo-path> [--output ...]`) with no `index` subcommand at all. codeintel invokes `scip-swift index --output <path>` with `cwd=<repo>`; that invocation fails today because `index` isn't a recognized subcommand.

Because `scip-swift`'s v0.1.0 shape is already published (GitHub release binary, documented README, used by its own test suite), backward compatibility for the bare invocation is a hard constraint, not a nice-to-have.

## Goals / Non-Goals

**Goals:**
- Make `scip-swift index [<repo-path>] --output <path>` behave identically to today's `scip-swift [<repo-path>] --output <path>`.
- Preserve the existing bare-invocation shape exactly (same flags, same defaults, same output).
- Prove the full codeintel pipeline (detect → build → convert → publish → query) actually works end-to-end for a real Swift repo, not just in unit-level mocks.
- Do this without touching any codeintel source file, since the accepted spec already matches the target contract.

**Non-Goals:**
- No new `scip-swift` CLI flags, build backends, or output formats.
- No change to codeintel's `_LANGUAGE_INDEXERS` table, `_run()` invocation, or any query/graph/server code path.
- No change to the accepted `swift-language-indexing` spec's requirement text — this change delivers on it, it doesn't redefine it.

## Decisions

**1. Restructure `scip-swift` as a thin router + `IndexCommand`, not a second parallel implementation.**
Move the current root command's entire body verbatim into `Commands/IndexCommand.swift`, then replace `ScipSwiftCommand` with a router that declares `subcommands: [IndexCommand.self]` and `defaultSubcommand: IndexCommand.self`. swift-argument-parser's `defaultSubcommand` mechanism means a bare `scip-swift <repo-path>` is dispatched to `IndexCommand` automatically — no argument-parsing duplication, no behavioral drift between the two invocation shapes, because there is only one code path.
- *Alternative considered*: add a second, separate `index` subcommand that just calls into shared logic. Rejected — two ParsableCommand structs with overlapping `@Argument`/`@Option` definitions is exactly the kind of duplication `defaultSubcommand` avoids for free.

**2. Verify byte-for-byte equivalence between invocation shapes empirically, not just by code inspection.**
All three shapes (bare, `index`, and codeintel's exact `cd repo && scip-swift index --output <path>` form) were actually run this session and their `.scip` output diffed identical except for `Metadata.tool_info.arguments` (which legitimately differs — it records the literal argv). This is the acceptance bar for Task 1, not just "it compiles."

**3. Gate the new codeintel integration test on binary presence, following the file's existing pattern.**
`tests/test_index_cli.py` already skips `scip-python`/`zoekt-index`-gated tests when those binaries are missing from `PATH`, rather than failing. The new Swift test (`test_index_repo_end_to_end_for_swift_repo`) follows the identical `shutil.which(...)` + `pytest.mark.skipif` pattern for `scip-swift`/`scip`/`zoekt-index`, so CI/dev environments without the Swift toolchain installed get a clean `SKIPPED`, not a false failure.
- *Alternative considered*: mock `scip-swift`'s output. Rejected per this change's whole purpose — the point is a *real* end-to-end proof, and codeintel's existing integration tests for other languages already establish the real-binary pattern.

**4. Minimal spec delta: one added scenario, no changed requirement text.**
Confirmed by re-reading `swift-language-indexing/spec.md`: the "Swift extension detection" and "Swift indexing via scip-swift" requirements already specify exactly `["scip-swift", "index"]` and the `--output`-based invocation. This change doesn't alter that requirement text — it makes the already-accepted contract real and adds one new scenario to "Swift indexing via scip-swift" documenting that the invocation is now verified end-to-end by a real integration test, closing the gap between "accepted" and "actually works."

## Risks / Trade-offs

- **[Risk]** `swift build -c release` and a real SwiftPM-buildable fixture add a slow, platform-specific (macOS + Swift toolchain) dependency to the codeintel test suite. → **Mitigation**: the test is `@pytest.mark.integration` and `skipif`-gated on binary presence, exactly like the existing `scip-python`/`zoekt-index` gated tests, so it never blocks environments without the Swift toolchain.
- **[Risk]** `defaultSubcommand` swallowing the bare invocation could silently change help text or error messages users depend on. → **Mitigation**: Task 1 Step 4 diffs actual output across all three invocation shapes before considering the restructure done; Task 1 Step 5 re-runs scip-swift's full existing test suite (23 tests) to catch any regression.
- **[Trade-off]** The `scip-swift` restructure lives entirely outside this repo's `allowedEditRoots` (`/Users/ddphuong/Projects/scip-swift`), so this OpenSpec change can only document and track it, not enforce it via codeintel CI. Task 2's non-modification checklist is the only guardrail on the codeintel side.

## Migration Plan

1. Restructure and verify `scip-swift` in its own repo (Task 1); commit and push there independently of this change.
2. Confirm codeintel needs zero source changes (Task 2) — pure verification, no deploy step.
3. Add the new fixture + integration test to codeintel (Task 3); commit here.
4. No rollback concerns on the codeintel side — the new test is additive and skip-gated; if the `scip-swift` restructure ever needed reverting, the bare-invocation compatibility guarantee (Decision 1) means codeintel's own invocation would start failing loudly (binary not found / `index` not recognized) rather than silently misbehaving.

## Open Questions

None — every decision above was empirically verified in-session (build succeeded, all three invocation shapes diffed identical, existing test suites re-run) before being written down.
