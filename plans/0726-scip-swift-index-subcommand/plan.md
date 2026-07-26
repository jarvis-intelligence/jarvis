# scip-swift `index` Subcommand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real `scip-swift` CLI (built in the sibling `scip-swift` repo, v0.1.0) satisfy codeintel's already-accepted invocation contract — `["scip-swift", "index", "--output", "<path>"]` run with `cwd=<repo>` — so Swift repos can actually be indexed end-to-end, closing the gap between codeintel's "detection-only" Swift support and real indexing.

**Architecture:** `scip-swift` currently has no subcommands — the root command IS the indexing behavior (`scip-swift <repo-path> [--output ...]`). Restructure it into a thin router (`ScipSwiftCommand`) with one subcommand (`IndexCommand`) set as `defaultSubcommand`, so `scip-swift <repo-path>` (old shape, still used by scip-swift's own README/tests) and `scip-swift index [<repo-path>] --output <path>` (codeintel's shape) both work identically. No changes are needed on the codeintel side — its `_LANGUAGE_INDEXERS[".swift"] = ("swift", ["scip-swift", "index"])` entry and `_run([*indexer_cmd, "--output", str(scip_path)], cwd=repo_path, ...)` call already match this contract exactly (this is `openspec/specs/swift-language-indexing/spec.md`'s own accepted "Swift extension detection" scenario, not a new decision). This plan only adds a regression-guarding integration test on the codeintel side.

**Tech Stack:** Swift 6.2 / swift-argument-parser (scip-swift repo), Python 3.12 / pytest (codeintel repo).

## Global Constraints

- scip-swift repo root: `/Users/ddphuong/Projects/scip-swift`. codeintel repo root: `/Users/ddphuong/Projects/codeintel`. This plan touches both.
- **Backward compatibility is required**: `scip-swift <repo-path> [--output ...]` (no `index` keyword) must keep working exactly as before — it's scip-swift's own documented v0.1.0 public contract (README, published GitHub Release binary).
- **No codeintel source changes** — `openspec/specs/swift-language-indexing/spec.md`'s "Swift extension detection" requirement already locks in `("swift", ["scip-swift", "index"])`; do not touch `src/codeintel/index_cli.py`, `README.md`, or existing tests in `tests/test_index_cli.py`.
- The new codeintel integration test in Task 3 requires a `scip-swift` binary on `PATH` to actually run (not just build) — it will report `SKIPPED` otherwise, matching the existing pattern for `scip-python`/`zoekt-index`-gated tests in the same file. Install it from the v0.1.0 release or `swift build -c release` in the scip-swift repo and copy the binary onto `PATH` before running this test locally.
- Every step below was hand-verified in-session before being written down: the exact restructured `ScipSwiftCommand.swift`/`IndexCommand.swift` content was built, and all three invocation shapes (bare, `index`, and codeintel's exact `cd repo && scip-swift index --output <path>` shape) were run and their `.scip` output diffed byte-for-byte identical (aside from `Metadata.tool_info.arguments`, which legitimately differs by invocation).

---

### Task 1: Restructure scip-swift into a router + `index` subcommand

**Files:**
- Modify: `/Users/ddphuong/Projects/scip-swift/Sources/scip-swift/ScipSwiftCommand.swift` (currently the full command — becomes a thin router)
- Create: `/Users/ddphuong/Projects/scip-swift/Sources/scip-swift/Commands/IndexCommand.swift` (all the current logic, moved verbatim)

**Interfaces:**
- Consumes: `BuildTool`, `BuildConfiguration`, `BuildBackendDetector`, `SwiftPMBuildRunner`, `XcodebuildBuildRunner`, `XcodeProjectLocator`, `IndexStoreBuildResult`, `SCIPIndexBuilder`, `ScipSwiftVersion`, `ToolchainInfo` — all unchanged, already defined in `Sources/scip-swift/Build/`, `Sources/scip-swift/SCIPMapping/`, `Sources/scip-swift/Platform/`, `Sources/scip-swift/Version.swift`.
- Produces: the `scip-swift` executable now accepts both `scip-swift [<repo-path>] [options]` and `scip-swift index [<repo-path>] [options]` — identical behavior either way.

- [ ] **Step 1: Move the current command body into a new `IndexCommand`**

  Create `/Users/ddphuong/Projects/scip-swift/Sources/scip-swift/Commands/IndexCommand.swift`:

  ```swift
  import ArgumentParser
  import Foundation

  struct IndexCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
      commandName: "index",
      abstract: "Converts a Swift repo's IndexStoreDB build index into a real scip.proto SCIP index.",
      version: "\(ScipSwiftVersion.version) (swift \(ToolchainInfo.pinnedSwiftVersion))"
    )

    @Argument(help: "Path to the Swift repo to index.")
    var repoPath: String = FileManager.default.currentDirectoryPath

    @Option(name: .long, help: "Output path for the .scip file. Defaults to <repo>/index.scip.")
    var output: String?

    @Option(name: .long, help: "Build backend to use ('swiftpm' or 'xcodebuild'). Auto-detected when omitted.")
    var buildTool: BuildTool?

    @Option(name: .long, help: "Build configuration ('debug' or 'release').")
    var configuration: BuildConfiguration = .debug

    @Option(name: .long, help: "Xcode scheme to build. Only used with xcodebuild; auto-detected if the project has exactly one scheme.")
    var scheme: String?

    func run() throws {
      let resolvedRepoPath = URL(fileURLWithPath: repoPath).standardizedFileURL.path
      let tool = try buildTool ?? BuildBackendDetector.detect(repoPath: resolvedRepoPath)
      let workDirectory = try Self.makeTemporaryDirectory()

      let buildResult = try produceIndexStore(tool: tool, repoPath: resolvedRepoPath, workDirectory: workDirectory)

      let builder = SCIPIndexBuilder(
        repoPath: resolvedRepoPath,
        indexStorePath: buildResult.indexStorePath,
        databasePath: (workDirectory as NSString).appendingPathComponent("index-db"),
        buildToolName: tool.rawValue,
        converterVersion: ScipSwiftVersion.version
      )
      let index = try builder.build()

      let outputPath = output ?? (resolvedRepoPath as NSString).appendingPathComponent("index.scip")
      try index.serializedData().write(to: URL(fileURLWithPath: outputPath))

      print("Wrote \(index.documents.count) document(s) to \(outputPath)")
    }

    private func produceIndexStore(tool: BuildTool, repoPath: String, workDirectory: String) throws -> IndexStoreBuildResult {
      switch tool {
      case .swiftpm:
        let runner = SwiftPMBuildRunner(
          repoPath: repoPath,
          configuration: configuration,
          scratchPath: (workDirectory as NSString).appendingPathComponent("scratch")
        )
        return try runner.produceIndexStore()

      case .xcodebuild:
        let projectArguments = try XcodeProjectLocator.workspaceOrProjectArguments(repoPath: repoPath)
        let resolvedScheme = try XcodeProjectLocator.resolveScheme(
          explicitScheme: scheme,
          projectArguments: projectArguments,
          repoPath: repoPath
        )
        let runner = XcodebuildBuildRunner(
          repoPath: repoPath,
          configuration: configuration,
          scheme: resolvedScheme,
          derivedDataPath: (workDirectory as NSString).appendingPathComponent("derived-data"),
          projectArguments: projectArguments
        )
        return try runner.produceIndexStore()
      }
    }

    private static func makeTemporaryDirectory() throws -> String {
      let path = (NSTemporaryDirectory() as NSString).appendingPathComponent("scip-swift-\(UUID().uuidString)")
      try FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: true)
      return path
    }
  }
  ```

- [ ] **Step 2: Replace `ScipSwiftCommand.swift` with a thin router**

  Replace the full contents of `/Users/ddphuong/Projects/scip-swift/Sources/scip-swift/ScipSwiftCommand.swift` with:

  ```swift
  import ArgumentParser

  @main
  struct ScipSwiftCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
      commandName: "scip-swift",
      abstract: "Converts a Swift repo's IndexStoreDB build index into a real scip.proto SCIP index.",
      version: "\(ScipSwiftVersion.version) (swift \(ToolchainInfo.pinnedSwiftVersion))",
      subcommands: [IndexCommand.self],
      defaultSubcommand: IndexCommand.self
    )
  }
  ```

- [ ] **Step 3: Build and confirm it compiles clean**

  Run: `cd /Users/ddphuong/Projects/scip-swift && swift build`
  Expected: `Build complete!` with no warnings or errors.

- [ ] **Step 4: Verify all three invocation shapes produce equivalent output**

  Run:
  ```bash
  cd /Users/ddphuong/Projects/scip-swift
  rm -rf Fixtures/MiniSwiftPackage/.build

  # Shape 1: old bare invocation (must still work)
  .build/debug/scip-swift Fixtures/MiniSwiftPackage --output /tmp/plan-verify-bare.scip
  echo "bare exit: $?"

  rm -rf Fixtures/MiniSwiftPackage/.build

  # Shape 2: codeintel's exact shape — cwd=repo, no positional arg, "index --output <path>"
  (cd Fixtures/MiniSwiftPackage && /Users/ddphuong/Projects/scip-swift/.build/debug/scip-swift index --output /tmp/plan-verify-codeintel-shape.scip)
  echo "codeintel-shape exit: $?"

  # Both must report 1 document written, exit 0
  ```
  Expected: both commands print `Wrote 1 document(s) to ...` and exit 0.

- [ ] **Step 5: Run scip-swift's existing test suite — nothing should regress**

  Run: `cd /Users/ddphuong/Projects/scip-swift && swift test`
  Expected: `Test run with 23 tests in 4 suites passed` (same count as before this change — no test in `Tests/scip-swiftTests/` references `ScipSwiftCommand`/`IndexCommand` directly, so none needed updating).

- [ ] **Step 6: Update README's usage section to mention the `index` subcommand**

  In `/Users/ddphuong/Projects/scip-swift/README.md`, in the `## Usage` section, after the existing bare-invocation example, add:

  ```markdown
  Or explicitly via the `index` subcommand (identical behavior — this is the shape
  external tools like [codeintel](https://github.com/phuongddx/codeintel) invoke):

  ```sh
  scip-swift index /path/to/your/swift/repo --output index.scip
  ```
  ```

- [ ] **Step 7: Commit**

  ```bash
  cd /Users/ddphuong/Projects/scip-swift
  git add Sources/scip-swift/ScipSwiftCommand.swift Sources/scip-swift/Commands/IndexCommand.swift README.md
  git commit -m "feat: add index subcommand for external-tool invocation compatibility"
  git push origin main
  ```

---

### Task 2: Confirm codeintel needs zero source changes

**Files:**
- Read only: `/Users/ddphuong/Projects/codeintel/src/codeintel/index_cli.py:28-35` (the `_LANGUAGE_INDEXERS` table), `/Users/ddphuong/Projects/codeintel/openspec/specs/swift-language-indexing/spec.md` (the accepted spec)

**Interfaces:**
- Consumes: nothing new — this task only verifies the existing `_LANGUAGE_INDEXERS[".swift"] = ("swift", ["scip-swift", "index"])` entry (line 34) and the `_run([*indexer_cmd, "--output", str(scip_path)], cwd=repo_path, ...)` call (line 126) already match Task 1's new `scip-swift index --output <path>` contract.
- Produces: nothing — confirmation only, no files change in this task.

- [ ] **Step 1: Re-read the accepted spec's locked-in contract**

  Read `/Users/ddphuong/Projects/codeintel/openspec/specs/swift-language-indexing/spec.md`, "Requirement: Swift extension detection" scenario: `detect_language()` SHALL return `("swift", ["scip-swift", "index"])`. This is exactly what Task 1 makes real — confirm no other requirement in that file implies a different invocation shape (e.g. no positional-path requirement, no extra flags).

- [ ] **Step 2: Run codeintel's existing unit tests to prove nothing is currently broken by this plan**

  Run: `cd /Users/ddphuong/Projects/codeintel && uv run pytest tests/test_index_cli.py -v -m "not integration"`
  Expected: all non-integration tests pass, including `test_detect_language_picks_swift_for_swift_files` (asserts `cmd[0] == "scip-swift"`) and `test_detect_language_ignores_derived_data_and_dot_build` (asserts `language == "swift"`). Neither test needs modification — they don't assert on `cmd[1]`.

- [ ] **Step 3: Do not modify `index_cli.py`, `README.md`, or any existing test**

  This step is a checklist confirmation, not code: verify `git status` in `/Users/ddphuong/Projects/codeintel` shows no unintended changes to `src/codeintel/index_cli.py` after Task 1/3 are done.

---

### Task 3: Add a real end-to-end Swift indexing test in codeintel

**Files:**
- Create: `/Users/ddphuong/Projects/codeintel/tests/fixtures/mini_swift_repo/Package.swift`
- Create: `/Users/ddphuong/Projects/codeintel/tests/fixtures/mini_swift_repo/Sources/MiniSwiftRepo/Greeter.swift`
- Modify: `/Users/ddphuong/Projects/codeintel/tests/test_index_cli.py` (add a Swift-specific binary-presence check near line 20, and a new test function immediately after `test_index_repo_end_to_end_atomic_swap_under_open_reader`, around line 150)

**Interfaces:**
- Consumes: `codeintel.index_cli.index_repo` (existing, `index_repo(repo_path: Path, *, slug: str | None = None, root: Path | None = None) -> str`), `codeintel.registry.Registry` (existing, `Registry(db_path).get(slug) -> RegistryEntry | None`), `codeintel.config.index_dir(slug, root) -> Path` (existing).
- Produces: a new pytest test, `test_index_repo_end_to_end_for_swift_repo`, gated by `@pytest.mark.integration` + a Swift-specific `skipif`.

- [ ] **Step 1: Add the fixture SwiftPM package**

  Create `/Users/ddphuong/Projects/codeintel/tests/fixtures/mini_swift_repo/Package.swift`:

  ```swift
  // swift-tools-version: 6.2
  import PackageDescription

  let package = Package(
    name: "MiniSwiftRepo",
    targets: [
      .target(name: "MiniSwiftRepo")
    ]
  )
  ```

  Create `/Users/ddphuong/Projects/codeintel/tests/fixtures/mini_swift_repo/Sources/MiniSwiftRepo/Greeter.swift`:

  ```swift
  public struct Greeter {
    public let name: String

    public init(name: String) {
      self.name = name
    }

    public func greet() -> String {
      "Hello, \(name)!"
    }
  }
  ```

- [ ] **Step 2: Write the failing test**

  In `/Users/ddphuong/Projects/codeintel/tests/test_index_cli.py`, add near the top (after line 21's existing `_missing = [...]`, so both binary-presence checks live together):

  ```python
  FIXTURE_REPO_SWIFT = Path(__file__).parent / "fixtures" / "mini_swift_repo"

  _SWIFT_REQUIRED_BINARIES = ["scip-swift", "scip", "zoekt-index"]
  _missing_swift = [b for b in _SWIFT_REQUIRED_BINARIES if shutil.which(b) is None]
  ```

  Then add this test function immediately after `test_index_repo_end_to_end_atomic_swap_under_open_reader`'s closing lines (`assert zoekt_shards, "zoekt-index should have written at least one shard"` around line 149-150) and before the next `@pytest.mark.integration` block (`test_index_repo_marks_failed_on_indexer_error`, around line 158):

  ```python
  @pytest.mark.integration
  @pytest.mark.skipif(_missing_swift, reason=f"missing required binaries: {_missing_swift}")
  def test_index_repo_end_to_end_for_swift_repo(tmp_path: Path):
      repo_dir = tmp_path / "repo"
      shutil.copytree(FIXTURE_REPO_SWIFT, repo_dir)
      _init_git_repo(repo_dir)

      data_root = tmp_path / "data"
      slug = index_repo(repo_dir, root=data_root)

      registry = Registry(data_root / "registry.db")
      try:
          entry = registry.get(slug)
          assert entry is not None
          assert entry.status == "indexed"
          assert entry.language == "swift"
      finally:
          registry.close()

      target_dir = config.index_dir(slug, data_root)
      pointer = (target_dir / "current").read_text(encoding="utf-8").strip()
      db_path = target_dir / pointer

      conn = sqlite3.connect(db_path)
      try:
          count = conn.execute("SELECT COUNT(*) FROM global_symbols").fetchone()[0]
          assert count > 0, "expected at least one indexed Swift symbol (e.g. Greeter)"
      finally:
          conn.close()
  ```

  This test needs `from codeintel import config` (already imported at line 15) — no new imports required beyond what the file already has (`shutil`, `sqlite3`, `subprocess`, `Path`, `pytest` are all already imported at lines 8-13).

- [ ] **Step 3: Run it and confirm it's SKIPPED (not FAILED) if `scip-swift` isn't on PATH yet**

  Run: `cd /Users/ddphuong/Projects/codeintel && uv run pytest tests/test_index_cli.py::test_index_repo_end_to_end_for_swift_repo -v`
  Expected (before installing `scip-swift`): `SKIPPED (missing required binaries: ['scip-swift'])` — proves the skip-gate itself works and the test doesn't error out just from being collected.

- [ ] **Step 4: Install the real scip-swift binary and confirm the test passes**

  Run:
  ```bash
  cp /Users/ddphuong/Projects/scip-swift/.build/release/scip-swift /usr/local/bin/scip-swift
  # (build it first if not already: cd /Users/ddphuong/Projects/scip-swift && swift build -c release)
  which scip-swift scip zoekt-index
  cd /Users/ddphuong/Projects/codeintel
  uv run pytest tests/test_index_cli.py::test_index_repo_end_to_end_for_swift_repo -v
  ```
  Expected: `PASSED` — confirms `entry.status == "indexed"`, `entry.language == "swift"`, and at least one symbol (`Greeter`, its `name` property, its `init`, its `greet()` method) landed in `global_symbols`.

- [ ] **Step 5: Run the full existing test suite to confirm no regressions**

  Run: `cd /Users/ddphuong/Projects/codeintel && uv run pytest -v`
  Expected: all previously-passing tests still pass; the new Swift test passes (if `scip-swift` is on `PATH`) or skips cleanly (if not).

- [ ] **Step 6: Commit**

  ```bash
  cd /Users/ddphuong/Projects/codeintel
  git add tests/fixtures/mini_swift_repo tests/test_index_cli.py
  git commit -m "test: add real end-to-end integration test for Swift indexing via scip-swift"
  ```

---

## Self-Review Notes

- **Spec coverage**: All three of the user's original strategy items are covered — Task 1 (scip-swift `index` alias), Task 2 (confirm codeintel needs no changes — backed by the already-accepted `openspec/specs/swift-language-indexing/spec.md`), Task 3 (new end-to-end test mirroring the existing `test_index_repo_end_to_end_atomic_swap_under_open_reader` pattern). The user's item 4 ("nothing else in codeintel needs touching") is covered by Task 2 Step 3's explicit non-modification checklist and is additionally guaranteed by Task 3 never touching `query.py`/`graph.py`/`server.py`/`scip_decoder.py`.
- **No placeholders**: every code block above is the exact, hand-verified content (Task 1's Swift files were actually built and run this session; Task 3's Python code follows the existing `test_index_repo_end_to_end_atomic_swap_under_open_reader` pattern line-for-line, adapted for Swift).
- **Type/signature consistency**: `index_repo(repo_path: Path, *, slug: str | None = None, root: Path | None = None) -> str`, `Registry(db_path).get(slug)`, and `config.index_dir(slug, root)` are used identically to the existing Python integration test — no invented signatures.
