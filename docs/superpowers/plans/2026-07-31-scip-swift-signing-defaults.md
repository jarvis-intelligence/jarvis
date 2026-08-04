# scip-swift Index-Safe Code-Signing Defaults — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scip-swift`'s `xcodebuild` backend disable code signing so `jarvis index` stops failing on Swift repos with signed app-extension targets.

**Architecture:** Two repos. In `~/Projects/scip-swift` (a fork under the same ownership, pinned by `jarvis`'s `setup.sh`), extract `XcodebuildBuildRunner`'s argument list into a pure computed property so it becomes testable, then append four code-signing-disable build settings. Release that as `v0.1.2`. In `~/Projects/jarvis`, bump the version pin and one test string. No `jarvis` Python source changes, no new CLI flag, no registry migration.

**Tech Stack:** Swift 6.2 / SwiftPM, Swift Testing (`@Suite`/`@Test`/`#expect`), Python 3 / pytest, POSIX sh, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-07-31-scip-swift-signing-defaults-design.md`

## Global Constraints

- **The four signing settings, verbatim and in this order:** `CODE_SIGNING_ALLOWED=NO`, `CODE_SIGNING_REQUIRED=NO`, `CODE_SIGN_IDENTITY=`, `CODE_SIGN_ENTITLEMENTS=`. The last two intentionally have empty values — each is one `argv` element ending in `=`.
- **Never add `-destination`.** Spec §3: disabling signing alone was verified sufficient, and forcing an iOS destination would break macOS-only repos that `_prefers_xcodebuild()` also routes down this path.
- **Do not touch `SwiftPMBuildRunner`, `BuildBackendDetector`, `XcodeProjectLocator`, or `IndexCommand`'s option set.**
- **Do not add any CLI flag or registry column.** The settings are a hardcoded default, not a per-repo preference (spec §1).
- **`scip-swift` tests use Swift Testing, not XCTest:** `import Testing`, `@Suite`, `@Test`, `#expect(...)`, and `@testable import scip_swift` (underscore, not hyphen).
- **`scip-swift` canonical commands** (from `.github/workflows/ci.yml`): `swift build --configuration debug`, `swift test --configuration debug`.
- **Release tar layout:** the binary must be at the **tar root**, named exactly `scip-swift`. `setup.sh:244` runs `tar -xzf archive.tar.gz -C "$tmp" "scip-swift"`, which fails on any directory prefix.
- **Checksum sidecar is mandatory**, format `"<digest>  <filename>"` — plain `shasum -a 256` output. `setup.sh:229-234` fails the install outright if it 404s.
- **Version pin floor:** `tests/test_setup_sh.py:627` asserts `>= (0, 1, 1)`. New pin is `v0.1.2` in `setup.sh` (tag form, leading `v`) and `0.1.2` in `Version.swift` (no `v`).
- **`jarvis` commands:** `uv run pytest`, `uv run pytest -m "not integration"`.

## Preconditions

**Disk space blocks Tasks 1-3 and 5.** `/System/Volumes/Data` had ~206 MiB free of 460 GiB when this plan was written. `swift build` needs several GB, and Task 5's iOS app build needs far more. Freeing space is out of scope for this plan (spec Non-goals) but is a hard prerequisite.

- [ ] **Check free space before starting:** `df -h /System/Volumes/Data`
- [ ] If under ~20 GB free, stop and free space first. Tasks 1-2 cannot compile otherwise, and a mid-build `No space left on device` can leave a corrupt `.build` directory (remove with `rm -rf ~/Projects/scip-swift/.build` and rebuild).

## Task Overview

| Task | Repo | Deliverable |
| --- | --- | --- |
| 1 | scip-swift | `arguments` extracted as a pure property + first test for the runner (no behavior change) |
| 2 | scip-swift | Four signing settings added, test-driven |
| 3 | scip-swift | `v0.1.2` tagged and released with both assets |
| 4 | jarvis | Version pin bumped, asset-name test fixed, roadmap updated |
| 5 | both | Installed and verified end-to-end against `luz_epost_ios` |

---

### Task 1: Extract `arguments` into a pure, testable property

Pure refactor — the produced argument list must be byte-identical to today's. Splitting this from Task 2 means a reviewer can accept the testability change independently of the behavior change, and gives a clean bisect point if the signing settings ever regress.

**Files:**
- Modify: `~/Projects/scip-swift/Sources/scip-swift/Build/XcodebuildBuildRunner.swift:16-30`
- Create: `~/Projects/scip-swift/Tests/scip-swiftTests/XcodebuildBuildRunnerTests.swift`

**Interfaces:**
- Consumes: `XcodebuildBuildRunner(repoPath:configuration:scheme:derivedDataPath:projectArguments:)` — the existing memberwise initializer of an `internal struct` with five `let` properties. `BuildConfiguration` is `enum { case debug, release }`.
- Produces: `XcodebuildBuildRunner.arguments: [String]` — a pure computed property returning the full `xcodebuild` argument list. Task 2 extends it; Task 2's test asserts against it.

- [ ] **Step 1: Write the failing test**

Create `~/Projects/scip-swift/Tests/scip-swiftTests/XcodebuildBuildRunnerTests.swift`:

```swift
import Testing

@testable import scip_swift

@Suite("XcodebuildBuildRunner arguments")
struct XcodebuildBuildRunnerTests {
  private static let projectArguments = ["-project", "My.xcodeproj"]

  private func makeRunner(configuration: BuildConfiguration = .debug) -> XcodebuildBuildRunner {
    XcodebuildBuildRunner(
      repoPath: "/repo",
      configuration: configuration,
      scheme: "My Scheme",
      derivedDataPath: "/tmp/derived-data",
      projectArguments: Self.projectArguments
    )
  }

  /// The value xcodebuild would read for `flag`, i.e. the element right after it.
  private func value(after flag: String, in args: [String]) -> String? {
    guard let index = args.firstIndex(of: flag), args.indices.contains(index + 1) else {
      return nil
    }
    return args[index + 1]
  }

  @Test("project arguments lead the list")
  func projectArgumentsLead() {
    #expect(makeRunner().arguments.starts(with: Self.projectArguments))
  }

  @Test("scheme and derived-data path are passed through verbatim")
  func passesThroughSchemeAndDerivedData() {
    let args = makeRunner().arguments
    #expect(value(after: "-scheme", in: args) == "My Scheme")
    #expect(value(after: "-derivedDataPath", in: args) == "/tmp/derived-data")
  }

  @Test("debug and release map to Xcode's capitalized configuration names")
  func mapsConfigurationNames() {
    #expect(value(after: "-configuration", in: makeRunner(configuration: .debug).arguments) == "Debug")
    #expect(value(after: "-configuration", in: makeRunner(configuration: .release).arguments) == "Release")
  }

  @Test("index-store generation is enabled")
  func enablesIndexStore() {
    #expect(makeRunner().arguments.contains("COMPILER_INDEX_STORE_ENABLE=YES"))
  }

  @Test("the build action is the final argument")
  func buildActionIsLast() {
    #expect(makeRunner().arguments.last == "build")
  }
}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/Projects/scip-swift && swift test --configuration debug --filter XcodebuildBuildRunner
```

Expected: **compile error**, `value of type 'XcodebuildBuildRunner' has no member 'arguments'`. A compile failure is the correct "red" here — the property does not exist yet.

- [ ] **Step 3: Extract the property**

In `Sources/scip-swift/Build/XcodebuildBuildRunner.swift`, replace the body of `produceIndexStore()` (currently lines 16-30) so the argument list moves out. The full struct body becomes:

```swift
  /// The full `xcodebuild` argument list. Kept pure and separate from
  /// `produceIndexStore()` so it can be asserted on without spawning Xcode.
  var arguments: [String] {
    let xcodeConfiguration = configuration == .debug ? "Debug" : "Release"
    return projectArguments + [
      "-scheme", scheme,
      "-configuration", xcodeConfiguration,
      "-derivedDataPath", derivedDataPath,
      "COMPILER_INDEX_STORE_ENABLE=YES",
      "build",
    ]
  }

  func produceIndexStore() throws -> IndexStoreBuildResult {
    let xcodebuild = try SubprocessRunner.resolveExecutable(named: "xcodebuild")

    let result = try SubprocessRunner.run(
      executable: xcodebuild,
      arguments: arguments,
      currentDirectory: repoPath
    )
    guard result.exitCode == 0 else {
      throw BuildError.buildFailed(tool: "xcodebuild", exitCode: result.exitCode, output: result.combinedOutput)
    }

    let indexStorePath = (derivedDataPath as NSString)
      .appendingPathComponent("Index.noindex/DataStore")
    guard FileManager.default.fileExists(atPath: indexStorePath) else {
      throw BuildError.indexStoreNotProduced(expectedPath: indexStorePath)
    }
    return IndexStoreBuildResult(indexStorePath: indexStorePath)
  }
```

Leave the file's existing header doc comment (lines 1-8) and the struct's five `let` properties unchanged. The `let xcodeConfiguration` line moves out of `produceIndexStore()` — do not leave a duplicate behind.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ~/Projects/scip-swift && swift test --configuration debug --filter XcodebuildBuildRunner
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Run the full suite to confirm the refactor broke nothing**

```bash
cd ~/Projects/scip-swift && swift test --configuration debug
```

Expected: PASS. `BuildRunner` (`Sources/scip-swift/Build/IndexStoreBuildResult.swift:7`) only requires `produceIndexStore()`, so adding a property cannot break conformance.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/scip-swift
git add Sources/scip-swift/Build/XcodebuildBuildRunner.swift Tests/scip-swiftTests/XcodebuildBuildRunnerTests.swift
git commit -m "refactor(xcodebuild): extract argument list into a testable property"
```

---

### Task 2: Disable code signing on the xcodebuild path

**Files:**
- Modify: `~/Projects/scip-swift/Sources/scip-swift/Build/XcodebuildBuildRunner.swift` (the `arguments` property from Task 1)
- Modify: `~/Projects/scip-swift/Tests/scip-swiftTests/XcodebuildBuildRunnerTests.swift`

**Interfaces:**
- Consumes: `XcodebuildBuildRunner.arguments` and the `makeRunner()` / `value(after:in:)` helpers from Task 1's test suite.
- Produces: no new API. `arguments` gains four elements before the trailing `build`.

- [ ] **Step 1: Write the failing test**

Append these two tests inside the existing `XcodebuildBuildRunnerTests` struct in `Tests/scip-swiftTests/XcodebuildBuildRunnerTests.swift`, after `buildActionIsLast()`:

```swift
  @Test("code signing is fully disabled — an index build never ships a product")
  func disablesCodeSigning() {
    let args = makeRunner().arguments
    #expect(args.contains("CODE_SIGNING_ALLOWED=NO"))
    #expect(args.contains("CODE_SIGNING_REQUIRED=NO"))
    #expect(args.contains("CODE_SIGN_IDENTITY="))
    #expect(args.contains("CODE_SIGN_ENTITLEMENTS="))
  }

  @Test("signing settings precede the build action")
  func signingSettingsPrecedeBuildAction() {
    let args = makeRunner().arguments
    let lastSetting = args.lastIndex(where: { $0.hasPrefix("CODE_SIGN") })
    let buildAction = args.firstIndex(of: "build")
    #expect(lastSetting != nil)
    #expect(buildAction != nil)
    if let lastSetting, let buildAction {
      #expect(lastSetting < buildAction)
    }
  }
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/Projects/scip-swift && swift test --configuration debug --filter XcodebuildBuildRunner
```

Expected: FAIL — `disablesCodeSigning` and `signingSettingsPrecedeBuildAction` fail on the `#expect` assertions. The five Task 1 tests still pass.

- [ ] **Step 3: Add the settings**

In `Sources/scip-swift/Build/XcodebuildBuildRunner.swift`, insert the four settings into `arguments` between `COMPILER_INDEX_STORE_ENABLE=YES` and `build`:

```swift
      "COMPILER_INDEX_STORE_ENABLE=YES",
      // An index build never runs, installs, or ships the product — it exists only to write
      // Index.noindex/DataStore. Signing is pure overhead here, and because no -destination
      // is passed, xcodebuild targets "My Mac", whose device ID iOS provisioning profiles
      // don't include — so signed app-extension targets fail during
      // GatherProvisioningInputs, before anything compiles. Do not restore signing, and do
      // not "fix" this with -destination: repos with a checked-in .xcodeproj may be macOS
      // apps, which a forced iOS destination would break instead.
      "CODE_SIGNING_ALLOWED=NO",
      "CODE_SIGNING_REQUIRED=NO",
      "CODE_SIGN_IDENTITY=",
      "CODE_SIGN_ENTITLEMENTS=",
      "build",
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ~/Projects/scip-swift && swift test --configuration debug --filter XcodebuildBuildRunner
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

```bash
cd ~/Projects/scip-swift && swift test --configuration debug
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/scip-swift
git add Sources/scip-swift/Build/XcodebuildBuildRunner.swift Tests/scip-swiftTests/XcodebuildBuildRunnerTests.swift
git commit -m "fix(xcodebuild): disable code signing for index-only builds"
```

---

### Task 3: Release `v0.1.2`

**This task publishes to GitHub — an outward-facing, hard-to-reverse action. Confirm with the user before Step 5.**

**Files:**
- Modify: `~/Projects/scip-swift/Sources/scip-swift/Version.swift:5`

**Interfaces:**
- Consumes: nothing.
- Produces: GitHub release `v0.1.2` on `phuongddx/scip-swift` with assets `scip-swift-v0.1.2-macos-arm64.tar.gz` and `scip-swift-v0.1.2-macos-arm64.tar.gz.sha256`. Task 4 pins to this tag; Task 5 installs it.

- [ ] **Step 1: Bump the version constant**

In `Sources/scip-swift/Version.swift`, change line 5:

```swift
  static let version = "0.1.2"
```

No leading `v` — this string is embedded in `--version` output and in every SCIP symbol's `converterVersion`.

- [ ] **Step 2: Verify the whole suite still passes and commit**

```bash
cd ~/Projects/scip-swift
swift test --configuration debug
git add Sources/scip-swift/Version.swift
git commit -m "chore: bump version to 0.1.2"
```

- [ ] **Step 3: Build the release binary**

```bash
cd ~/Projects/scip-swift
swift build --configuration release
.build/release/scip-swift --version
```

Expected: version output contains `0.1.2`.

- [ ] **Step 4: Package the assets**

The binary must be at the tar root named exactly `scip-swift` — `-C .build/release` achieves this. A tarball with a directory prefix will fail `setup.sh:244`.

```bash
cd ~/Projects/scip-swift
ASSET=scip-swift-v0.1.2-macos-arm64.tar.gz
tar -czf "$ASSET" -C .build/release scip-swift
shasum -a 256 "$ASSET" > "$ASSET.sha256"

# Verify the layout is flat — expect exactly "scip-swift", no leading path.
tar -tzf "$ASSET"
cat "$ASSET.sha256"
```

- [ ] **Step 5: Tag and publish (confirm with the user first)**

```bash
cd ~/Projects/scip-swift
git tag v0.1.2
git push origin main --tags
gh release create v0.1.2 \
  --title "v0.1.2" \
  --notes "xcodebuild backend disables code signing for index-only builds, so repos with signed app-extension targets index without provisioning profiles." \
  scip-swift-v0.1.2-macos-arm64.tar.gz \
  scip-swift-v0.1.2-macos-arm64.tar.gz.sha256
```

- [ ] **Step 6: Verify both assets are downloadable**

```bash
gh release view v0.1.2 --repo phuongddx/scip-swift --json assets --jq '.assets[].name'
```

Expected: both the `.tar.gz` and the `.tar.gz.sha256`. If the checksum is missing, `setup.sh` will fail at `:229` — upload it before continuing.

- [ ] **Step 7: Clean up the local artifacts**

```bash
cd ~/Projects/scip-swift && rm -f scip-swift-v0.1.2-macos-arm64.tar.gz scip-swift-v0.1.2-macos-arm64.tar.gz.sha256
```

---

### Task 4: Pin `jarvis` to `v0.1.2`

**Files:**
- Modify: `~/Projects/jarvis/setup.sh:25-28`
- Modify: `~/Projects/jarvis/tests/test_setup_sh.py:624`
- Modify: `~/Projects/jarvis/docs/project-roadmap.md`

**Interfaces:**
- Consumes: the `v0.1.2` tag from Task 3. `setup.sh` derives both the download URL (`:363`) and the asset name (`:342`) from the single `SCIP_SWIFT_VERSION` constant, so only that one value changes.
- Produces: nothing consumed by later tasks except Task 5's install.

Work on branch `docs/scip-swift-signing-defaults-spec` (already checked out, already holds the spec commit). Note `docs/project-roadmap.md` has unrelated uncommitted edits in the working tree — stage only your own additions with `git add -p` if needed, and do not revert those edits.

- [ ] **Step 1: Update the failing test first**

In `tests/test_setup_sh.py:624`, change the expected asset name:

```python
    assert name == "scip-swift-v0.1.2-macos-arm64.tar.gz"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Projects/jarvis && uv run pytest tests/test_setup_sh.py::test_scip_swift_asset_name_uses_macos_not_darwin -v
```

Expected: FAIL — actual is still `scip-swift-v0.1.1-macos-arm64.tar.gz`, because `setup.sh` still pins `v0.1.1`.

- [ ] **Step 3: Bump the pin and extend its comment**

In `setup.sh`, replace lines 25-28 with:

```sh
# v0.1.1 is the first release whose binary supports `scip-swift index …`, the
# form index_cli.py invokes. v0.1.0 predates that subcommand and cannot be
# driven by jarvis at all. v0.1.2 is the first whose xcodebuild backend
# disables code signing, without which repos containing signed app-extension
# targets fail during GatherProvisioningInputs before compiling anything.
SCIP_SWIFT_VERSION="v0.1.2"
```

Keep the `v` prefix — the tag form. Leave `SCIP_SWIFT_REPO` on line 29 untouched.

- [ ] **Step 4: Run the setup tests to verify they pass**

```bash
cd ~/Projects/jarvis && uv run pytest tests/test_setup_sh.py -v
```

Expected: PASS, including `test_scip_swift_pin_is_at_least_v0_1_1`, which asserts `>= (0, 1, 1)` and needs no edit.

- [ ] **Step 5: Run the full unit suite**

```bash
cd ~/Projects/jarvis && uv run pytest -m "not integration"
```

Expected: PASS. No Python source changed, so this is a regression check only.

- [ ] **Step 6: Document it in the roadmap**

In `docs/project-roadmap.md`, insert a new subsection immediately before the `### Semantic/Vector Search (Landed, July 30)` heading — keeping it adjacent to the two existing Swift subsections (`### Post-Phase-4: Swift Detection` and `### Post-Phase-4: Swift xcodebuild Build-Tool Override`):

```markdown
### Post-Phase-4: Swift Index-Safe Code-Signing Defaults (Landed, July 31)

Swift repos containing signed app-extension targets could not be indexed: `scip-swift`'s
xcodebuild backend passed no `-destination`, so xcodebuild auto-selected `My Mac`, then failed
provisioning for every signed target during `GatherProvisioningInputs` — before compiling
anything. Reproduced against `luz_epost_ios` (5 failing targets: `ePostDev`,
`notification_service`, `luz_epost_siri_intent`, `import_files_action`, `import_files_share`).

Fixed upstream in `scip-swift` v0.1.2, not in jarvis: the failing arguments are constructed
inside `XcodebuildBuildRunner` and are unreachable from any existing flag. Its `xcodebuild`
invocation now always passes `CODE_SIGNING_ALLOWED=NO`, `CODE_SIGNING_REQUIRED=NO`,
`CODE_SIGN_IDENTITY=`, and `CODE_SIGN_ENTITLEMENTS=` — an index build never runs or ships the
product, so signing is always dead weight on that path.

jarvis's only change is the `SCIP_SWIFT_VERSION` pin in `setup.sh`. No new CLI flag, no
registry column, and `_swift_indexer_cmd()` is unchanged.

`-destination` was deliberately not added: disabling signing alone was verified sufficient, and
forcing an iOS destination would break macOS-only repos, which `_prefers_xcodebuild()` also
routes down the xcodebuild path.

**Known related gap:** `scip-swift` hardcodes `-configuration Debug`. Repos whose schemes use
custom configuration names (`luz_epost_ios` has `Debug Development`, `Debug TEST`, `Debug PROD`)
get `Debug` forced regardless of the scheme's own selection. Not yet addressed.

See `docs/superpowers/specs/2026-07-31-scip-swift-signing-defaults-design.md`.
```

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/jarvis
git add setup.sh tests/test_setup_sh.py docs/project-roadmap.md
git commit -m "fix(swift): pin scip-swift v0.1.2 for index-safe signing defaults"
```

If `git status` shows the four unrelated modified docs (`code-standards.md`, `codebase-summary.md`, `system-architecture.md`, and other hunks of `project-roadmap.md`) still uncommitted, that is correct — leave them.

---

### Task 5: Install and verify end-to-end

This is the acceptance gate that lets the roadmap claim "Verified:" rather than "should work". **Requires substantial free disk space** — a full iOS app build with ~30 SPM dependencies.

**Files:** none modified. Verification only.

**Interfaces:**
- Consumes: the `v0.1.2` release (Task 3) and the bumped pin (Task 4).
- Produces: a `luz-epost-ios` entry in the jarvis registry, and the verified/unverified verdict recorded in the roadmap.

- [ ] **Step 1: Confirm disk space**

```bash
df -h /System/Volumes/Data
```

If free space is under ~20 GB, stop here and report the plan as complete-but-unverified. Tasks 1-4 stand on their own; do not fake this gate.

- [ ] **Step 2: Install the new binary**

```bash
cd ~/Projects/jarvis && ./setup.sh --only scip-swift --force
scip-swift --version
```

Expected: `0.1.2`. If the version is still `0.1.1`, the `PATH` may resolve an older copy — check `which -a scip-swift` against `~/.jarvis/bin`.

- [ ] **Step 3: Index the repo that reproduced the failure**

```bash
cd ~/Projects/jarvis
uv run jarvis index /Users/ddphuong/Projects/epost-workspace/epost-app/luz_epost_ios \
  --slug luz-epost-ios --scheme "ePost Development"
```

Expected: completes without a provisioning error. If it fails, capture the error and classify it: a `Provisioning profile … doesn't include` message means the fix did not take (wrong binary installed); `No space left on device` is the disk precondition; a `Debug` configuration error is the known non-goal gap documented in Task 4 Step 6.

- [ ] **Step 4: Confirm the index published**

```bash
cd ~/Projects/jarvis && uv run jarvis status luz-epost-ios
```

Expected: `language: swift`, `status: indexed`. A status of `partial` means symbols published but occurrence ranges were dropped — record that verbatim rather than calling it success.

- [ ] **Step 5: Confirm the index is navigable, not just published**

Publishing and being queryable are different claims. Use the `jarvis` MCP tool `documentSymbols`
— the real user-facing interface — with:

- `repo`: `luz-epost-ios`
- `path`: `luz_epost_ios/AppDelegate/AppDelegate.swift` (verified to exist in the repo; paths are
  relative to the repo root)

Expected: a non-empty symbol list. Two failure modes to distinguish and report honestly:

- An empty list on a file known to contain a class means the index published but is **not
  navigable** — the `partial` fingerprint from Step 4. Do not call the gate passed.
- `{"error": ...}` means the query itself failed; `server.py` returns errors rather than raising,
  so capture the message verbatim.

If MCP tooling is unavailable in the execution context, do not hand-wire `QueryService` (it
requires an `IndexConnectionCache`); use `uv run jarvis status luz-epost-ios` as a weaker
substitute and record that the navigability check was skipped.

- [ ] **Step 6: Record the outcome**

Replace `(Landed, July 31)` in the roadmap subsection added in Task 4 Step 6 with a verification line reflecting what actually happened, e.g.:

```markdown
Verified end-to-end: `jarvis index` completes on `luz_epost_ios` (5 signed targets),
`jarvis status` reports `language: swift` / `indexed`, and `documentSymbols` returns results.
```

If the gate did not pass, state which step failed and why instead.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/jarvis
git add docs/project-roadmap.md
git commit -m "docs(swift): record end-to-end verification of signing-defaults fix"
```

---

## Definition of Done

- `swift test --configuration debug` passes in `scip-swift`, with 7 tests covering `XcodebuildBuildRunner.arguments` — a type that previously had none.
- `uv run pytest -m "not integration"` passes in `jarvis`.
- `scip-swift --version` reports `0.1.2` and both release assets are downloadable.
- No `jarvis` Python source file, CLI parser, or registry schema was modified — `git diff main --stat` on `jarvis` touches only `setup.sh`, `tests/test_setup_sh.py`, and `docs/`.
- Task 5's outcome is recorded truthfully in `docs/project-roadmap.md`, whether it passed or was blocked.
