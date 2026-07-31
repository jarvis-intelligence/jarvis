# Swift indexing: index-safe code-signing defaults for scip-swift's xcodebuild backend — Design

## Problem

`codeintel index` fails on Swift repos that build via `xcodebuild` and contain app-extension
targets with provisioning profiles. The build never reaches the compiler — it dies during
`GatherProvisioningInputs`.

Root cause, traced against `luz_epost_ios` (a real iOS app with five signed targets):

1. `index_cli.py`'s `_swift_indexer_cmd()` correctly selects `--build-tool xcodebuild` and passes
   `--scheme` (see `2026-07-27-swift-xcodebuild-detection-design.md`). That part works.
2. `scip-swift`'s `XcodebuildBuildRunner.produceIndexStore()` builds its `xcodebuild` argument
   list with `-scheme`, `-configuration`, `-derivedDataPath`, `COMPILER_INDEX_STORE_ENABLE=YES`,
   and `build` — and **no destination and no signing settings**.
3. With no `-destination`, `xcodebuild` auto-selects the first matching destination. On an Apple
   Silicon Mac that is:
   ```
   { platform:macOS, arch:arm64, variant:Designed for [iPad,iPhone],
     id:00006030-000E69293AD0001C, name:My Mac }
   ```
4. Provisioning is then resolved against *this Mac's* device ID, which the iOS team provisioning
   profiles do not include. One error per signed target, before any compilation:
   ```
   error: Provisioning profile "iOS Team Provisioning Profile: ch.klara.epostdev.…"
   doesn't include the currently selected device "aavn-macbook025"
   (identifier 00006030-000E69293AD0001C). (in target 'luz_epost_siri_intent' …)
   ```
   Reproduced for all five: `ePostDev`, `notification_service`, `luz_epost_siri_intent`,
   `import_files_action`, `import_files_share`.
5. Neither `codeintel index` nor `scip-swift index` exposes a flag to pass `-destination` or to
   disable signing — confirmed against both `--help` outputs and `index_cli.py`.

**Verified fix:** re-running step 2's exact argument list with four added build settings produces
**zero** provisioning errors — the run clears `GatherProvisioningInputs` and proceeds to SwiftPM
package resolution:

```
CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= CODE_SIGN_ENTITLEMENTS=
```

### Why this reverses a prior scoping decision

`2026-07-27-swift-xcodebuild-detection-design.md` states: *"Fix is scoped to `codeintel` only —
`scip-swift` is a separate personal project and is not modified by this design."* That held for
build-tool *selection*, which is expressible in the flags `scip-swift` already accepts. It cannot
hold here: the failing arguments are constructed inside `scip-swift` and are unreachable from any
existing flag. `setup.sh:29` pins `SCIP_SWIFT_REPO="phuongddx/scip-swift"` — a fork under the same
ownership — so modifying it is a version bump, not a third-party dependency negotiation.

## Scope

In scope: `scip-swift`'s `XcodebuildBuildRunner` argument construction, its version constant, and
`codeintel`'s pin on the resulting release (`setup.sh`, one test, docs).

Out of scope: `SwiftPMBuildRunner` (no signing on that path), `BuildBackendDetector`,
`XcodeProjectLocator`, `IndexCommand`'s option set, and every `codeintel` Python module — this
design adds no CLI flag, no registry column, and no change to `_swift_indexer_cmd()`.

## Design

### 1. Signing settings are a hardcoded default, not a flag

A `scip-swift` build exists solely to populate `<DerivedData>/Index.noindex/DataStore`. The
product is never run, installed, notarized, or shipped. Code signing is therefore *always* dead
weight on this path — not a per-repo preference. Encoding it as a default rather than a flag keeps
the failure from recurring for the next signed repo, and keeps `codeintel`'s "detect and do the
right thing" posture (the same reasoning that made `_prefers_xcodebuild()` a heuristic rather than
a required flag).

A rejected alternative — a generic repeatable `--xcodebuild-arg` passthrough plumbed through
`codeintel` — would require a registry column, two CLI parsers, and would oblige the user to know
the correct `xcodebuild` incantation per repo. Deferred; no second use case exists yet.

### 2. `XcodebuildBuildRunner` changes

`Sources/scip-swift/Build/XcodebuildBuildRunner.swift`, two edits.

**Extract the argument list into a pure computed property.** Today it is built inline inside
`produceIndexStore()`, the same function that spawns the subprocess, so it cannot be asserted on
without running a real Xcode build — which is why the runner has no test coverage at all. The
extracted form mirrors `index_cli.py`'s `_swift_indexer_cmd()`, a pure function precisely so
`test_index_cli.py` can assert the constructed command without executing it.

```swift
/// The full `xcodebuild` argument list. Pure and separate from `produceIndexStore()` so it
/// can be asserted on without spawning Xcode.
var arguments: [String] {
  let xcodeConfiguration = configuration == .debug ? "Debug" : "Release"
  return projectArguments + [
    "-scheme", scheme,
    "-configuration", xcodeConfiguration,
    "-derivedDataPath", derivedDataPath,
    "COMPILER_INDEX_STORE_ENABLE=YES",
    // An index build never runs, installs, or ships the product — it exists only to write
    // Index.noindex/DataStore. Signing is pure overhead here, and with no -destination
    // xcodebuild targets "My Mac", whose device ID iOS provisioning profiles don't include,
    // so app-extension targets fail before compiling. Do not restore signing.
    "CODE_SIGNING_ALLOWED=NO",
    "CODE_SIGNING_REQUIRED=NO",
    "CODE_SIGN_IDENTITY=",
    "CODE_SIGN_ENTITLEMENTS=",
    "build",
  ]
}
```

`produceIndexStore()` then passes `arguments` to `SubprocessRunner.run`. No behavioral change
beyond the four added settings.

### 3. `-destination` is deliberately not added

The auto-selected `My Mac` destination is what *triggers* provisioning resolution, so forcing
`-destination generic/platform=iOS Simulator` is a plausible alternative fix. It is rejected:

- Unnecessary — disabling signing alone was verified sufficient (see Problem).
- Actively risky — `_prefers_xcodebuild()` routes *any* repo with a checked-in
  `.xcodeproj`/`.xcworkspace` down this path, including macOS-only apps. Forcing an iOS
  destination would break those, trading one class of failure for another.

Leaving the destination auto-selected keeps the fix platform-agnostic.

### 4. Version and release

`Sources/scip-swift/Version.swift`: `0.1.1` → `0.1.2`. Release `v0.1.2` must publish both
`scip-swift-v0.1.2-macos-arm64.tar.gz` and its `.sha256`. The checksum is not optional:
`install_tarball_binary` (`setup.sh:229-234`) fails the install outright when the sidecar 404s.
Its expected format is `"<digest>  <filename>"` — the first whitespace-delimited field is taken as
the digest (`setup.sh:237`), i.e. plain `shasum -a 256 <file>` output.

### 5. `codeintel` changes

| File | Change |
| --- | --- |
| `setup.sh:28` | `SCIP_SWIFT_VERSION="v0.1.1"` → `"v0.1.2"`. Both the download URL (`:363`) and the asset name (`:342`) derive from this one constant. |
| `setup.sh:25-27` | Extend the comment: it currently documents only the `index`-subcommand floor; add that v0.1.2 is the first release whose xcodebuild backend can build signed app-extension targets. |
| `tests/test_setup_sh.py:624` | Asset-name assertion hardcodes `v0.1.1`. |
| `docs/project-roadmap.md` | Note in the existing Swift section, alongside the bare-invocation and xcodebuild-selection entries. |

`tests/test_setup_sh.py:627` (`test_scip_swift_pin_is_at_least_v0_1_1`) asserts `>= (0, 1, 1)` and
passes untouched.

No changes to `index_cli.py`, `registry.py`, or any other Python module.

### 6. Testing

- **`scip-swift` (new).** Unit test on `XcodebuildBuildRunner.arguments`: the four signing
  settings are present; `-scheme`, `-configuration`, `-derivedDataPath`,
  `COMPILER_INDEX_STORE_ENABLE=YES` and the trailing `build` verb survive in order;
  `projectArguments` stay leading. Pure — no subprocess, no Xcode, no fixture project. This is the
  runner's first test.
- **`codeintel`.** `uv run pytest tests/test_setup_sh.py` after the pin bump.
- **End-to-end acceptance gate.** See Follow-up. This is what lets `project-roadmap.md` record
  "Verified:" rather than "should work", matching how the Swift work has been documented to date.

### 7. Sequence

1. `scip-swift`: extract `arguments`, add settings, add test, bump `Version.swift`, tag and
   release `v0.1.2` with both assets.
2. `codeintel`: bump the pin, fix the asset-name test, update `docs/project-roadmap.md`.
3. `./setup.sh --only scip-swift --force`, then run the acceptance gate.

## Risks

- **Disk space blocks verification.** `/System/Volumes/Data` has ~206 MiB free of 460 GiB. The
  reproduction build failed with `No space left on device` during SwiftPM checkout *after* the
  provisioning stage was cleared. This blocks both compiling `scip-swift` and the end-to-end gate.
  The signing fix is still correct — it just changes which error you hit.
- **Suppressed signing could mask a genuine misconfiguration** in a target. Accepted: nothing on
  this path produces a shippable artifact, and Xcode itself remains the place where signing is
  validated.
- **Indexing a simulator/unsigned build vs a device build** can differ inside
  `#if targetEnvironment(simulator)` branches. Unchanged by this design — destination selection is
  untouched (§3).

## Non-goals

- **`-configuration Debug` is hardcoded** (`IndexCommand.swift:21`). `luz_epost_ios` defines
  `Debug Development`, `Debug TEST`, `Debug PROD`, etc.; forcing `Debug` overrides whatever the
  scheme selects. A real latent bug of the same family, with different semantics (it needs either
  a flag or scheme-derived resolution). Flagged for a separate spec; it did not surface in the
  reproduction.
- The `--xcodebuild-arg` passthrough (§1).
- `--index-store-path` — skipping the build entirely and reusing Xcode's existing DerivedData
  index store. Independently valuable: it would sidestep signing, destination, configuration
  naming, SSH-authenticated SPM dependencies, *and* disk space, since Xcode has already built.
  But it makes index staleness the user's responsibility, which conflicts with the registry's
  commit-SHA-pinned model — a design question deserving its own brainstorm, not a workaround
  smuggled in here.
- Freeing disk space.

## Follow-up (not part of this fix)

Once `v0.1.2` is installed and disk allows, the acceptance gate:

```bash
codeintel index /Users/ddphuong/Projects/epost-workspace/epost-app/luz_epost_ios \
  --slug luz-epost-ios --scheme "ePost Development"
codeintel status luz-epost-ios     # expect: language swift, status indexed
```

Then one nav query (`documentSymbols` on a known file) to confirm the index is navigable, not just
published. Record the result in `docs/project-roadmap.md`.
