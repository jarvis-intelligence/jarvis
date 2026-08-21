# Stack Research

**Domain:** Local-first code-intelligence MCP server — indexing robustness & scip-swift update milestone
**Researched:** 2026-08-16
**Confidence:** HIGH (scip-swift facts verified against primary source via `gh api`; uv facts from official docs; TTY patterns corroborated)

## Headline Finding: Do NOT Pin scip-swift v0.2.0 or v0.2.1

The latest scip-swift release is **v0.2.1** (2026-08-15), but **both v0.2.0 and v0.2.1 carry a
regression that silently breaks the exact code path jarvis depends on**: the v0.2.0 refactor that
extracted `IndexCommand.indexOneRepo()` (commit "wire version field, indexOneRepo extraction,
IndexManyCommand...") dropped the `switch tool` dispatch. At v0.2.1, `--build-tool xcodebuild` and
`--scheme` are parsed but **never used** — every repo is built with `SwiftPMBuildRunner`
(`swift build`), so a `.xcodeproj`-only repo (the case `_prefers_xcodebuild()` exists for) fails
with `'swift build' failed with exit code ...` instead of building via xcodebuild. Verified by
reading `Sources/scip-swift/Commands/IndexCommand.swift` at both tags: v0.1.2 routes through
`XcodeProjectLocator` + `XcodebuildBuildRunner`; v0.2.1 constructs `SwiftPMBuildRunner`
unconditionally in both the cache and temp-dir branches. **Confidence: HIGH** (primary source).

**The fix already exists on `main` but is unreleased.** Phase 6 of scip-swift's v0.3.0 milestone
(commits after v0.2.1, pushed 2026-08-16) restored xcodebuild dispatch on both branches, added a
`--destination` CLI option threaded through to the xcodebuild runner, and added a new
`xcodebuildDestinationFailed` error with a `-showdestinations` hint. `Version.swift` on main still
reads `0.2.1` — no tag contains the fix yet.

**Recommendation:** the milestone's "update the pin" requirement has a prerequisite: **cut a new
scip-swift release from main** (v0.2.2 or v0.3.0, with `Version.swift` bumped) that contains the
phase-6 dispatch fix, then pin jarvis's `SCIP_SWIFT_VERSION` to that tag. Until that release
exists, v0.1.2 remains the only correct pin.

### scip-swift release history

| Tag | Date | Assets | State |
|-----|------|--------|-------|
| v0.1.2 | 2026-08-08 | `scip-swift-v0.1.2-macos-arm64.tar.gz` + `.sha256` sidecar | Current jarvis pin; xcodebuild routing correct; arm64-only |
| v0.2.0 | 2026-08-13 | `scip-swift-0.2.0.tar.gz` (no sidecar) | xcodebuild dispatch **broken** |
| v0.2.1 | 2026-08-15 | `scip-swift-0.2.1.tar.gz` (no sidecar) | Latest release; same regression; only change vs v0.2.0 is the version bump + tap fix |
| main (unreleased) | 2026-08-16 | — | Dispatch fixed, `--destination` added, `xcodebuildDestinationFailed` error added |

### What v0.2.x adds (why upgrading is worth it once fixed)

- **Symbol metadata enrichment** — `RelationshipMapping`, `enclosing_symbol`, expanded roles,
  signature documentation, `isSystem` classification. Relationships are what `scip expt-convert`
  turns into `global_symbols.relationships` — i.e. **Swift `typeHierarchy` starts returning real
  data** (v0.1.2 emitted no relationships).
- **Incremental indexing** — new optional `--cache-dir` and `--index-only` flags plus a
  `--configuration debug|release` option. Default behavior without these flags is unchanged
  (ephemeral temp scratch dir; no `.scip-cache/` dropped into repos unless requested).
- **`index-many` subcommand** — cross-repo indexing with `ScipIndexMerger`. Not needed by jarvis
  (one slug per repo), but harmless.
- **Universal binary** — release.yml now lipo-merges arm64 + x86_64 (`--triple
  arm64-apple-macosx14` / `x86_64-apple-macosx14`). The "macOS arm64 only" constraint in setup.sh
  can widen to "macOS only".
- **Runtime dylib guard** — `xcodeRequired` error when `libIndexStore.dylib` is missing, with
  xcode-select remediation steps.

### CLI compatibility with `_swift_indexer_cmd`

The invocation shape jarvis uses — `scip-swift index <repoPath> --output <path>
[--build-tool xcodebuild] [--scheme <name>]` — is **unchanged and additive** across v0.1.2 →
main: same `index` subcommand, same `@Option` long names, new flags all optional with compatible
defaults. `--version` reports `<ver> (swift 6.2.4)`. No jarvis-side invocation change is needed;
only the pin, once a fixed release exists. **Confidence: HIGH** (primary source).

### setup.sh breakage: release asset naming changed in v0.2.x

setup.sh builds the asset name as `scip-swift-${SCIP_SWIFT_VERSION}-macos-arm64.tar.gz` and
passes a `.sha256` sidecar URL to `install_tarball_binary` (setup.sh:503, 527). Since v0.2.0 the
release workflow publishes `scip-swift-<ver-without-v>.tar.gz` — no arch suffix, no `v` prefix in
the filename, and **no `.sha256` sidecar** (the checksum goes only into the Homebrew formula).
The tarball layout is compatible (single `scip-swift` binary at the root), but a naive pin bump
404s on both the asset and the sidecar.

Two options:

1. **Fix scip-swift's release.yml to also publish a `.sha256` sidecar** (and keep or restore a
   predictable name) — preferred, because setup.sh's checksum verification is a deliberate
   security property and the sidecar is one `shasum` + one extra asset in `gh release create`.
2. Adapt setup.sh to the new name and drop/replace checksum verification — worse: loses
   verification or hardcodes checksums into setup.sh (which is synced to jarvis-index on every
   release and would then need a bump per scip-swift release).

Either way, `tests/test_setup_sh.py` and `.github/workflows/setup-smoke.yml` (which guards that
the pin resolves) must be updated in the same change. **Confidence: HIGH** (primary source:
release.yml at v0.2.1 + setup.sh).

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| scip-swift | **next release cut from main** (v0.2.2/v0.3.0); v0.1.2 until then | Swift SCIP indexer | main has the xcodebuild dispatch fix, relationship metadata (Swift typeHierarchy), `--destination`, universal binary; v0.2.0/v0.2.1 are broken for `.xcodeproj` repos |
| Python stdlib (`sys`, `input`, `os.environ`, `shutil.which`, `subprocess`) | 3.12–3.14 | TTY detection + interactive prompt + uv invocation | Project constraint: no new runtime deps; the entire prompt feature is ~30 lines of stdlib |
| uv | user's installed uv | Programmatic install of the `semantic` extra | Already the project's package manager and the documented install path (`uv tool install`, `uv sync --extra`) |
| sqlite3 registry (`registry.db`) | stdlib | Persist fallback opt-in, degradation reason, and semantic-prompt decline per-repo | Existing pattern for per-repo persisted overrides (`--scheme`, `--language`, `search_only`); additive columns keep old registries working |

### scip-swift failure signatures (feature 3)

Exact `BuildError` description strings, **verified byte-identical between v0.1.2 and main** for
all shared cases — signatures written now match both the current pin and the future one. Matching
should use the existing `_SEARCH_ONLY_SIGNATURES` shape (tuple of required substrings, all must
match). **Confidence: HIGH** (primary source, both tags read).

| Signature substring(s) | Cause | Recommended handling |
|------------------------|-------|----------------------|
| `Build succeeded but no IndexStore was produced` | Code can't compile on this host (Apple-platform-only imports, missing SDK) — build "succeeds" but emits nothing | **Automatic search-only** — direct analog of the AGP `No SCIP shards found` case: unfixable from jarvis |
| `Could not detect a build system at` (+ `no Package.swift`) | Swift repo with neither Package.swift nor .xcodeproj/.xcworkspace (Bazel, Tuist-without-generated-project) | **Automatic search-only** — jarvis already passes `--build-tool` when an Xcode project exists, so hitting this means the repo genuinely has no supported build system |
| `xcodebuild requires --scheme` | Multi-scheme project, no `--scheme` persisted | **Hard failure with remedy** (name `jarvis index --scheme <name>`), like the bash-shim pattern — user-fixable, must not be laundered into search-only |
| `libIndexStore.dylib was not found` | Xcode/CLT missing or not selected | **Hard failure with remedy** (error text already carries xcode-select steps) — fixable |
| `failed with exit code` + `for the requested destination` (main only) | Bad `--destination` specifier | **Hard failure with remedy** (`-showdestinations` hint is in the output) — only exists after the pin bump |
| `'xcodebuild' failed with exit code` / `'swift build' failed with exit code` | Generic build failure (signing, `no such module`, broken code) | **Not** a signature — this is exactly what the opt-in generic fallback (feature 2) covers |

Note: `buildFailed` output combines stdout+stderr (both `swift build` and `xcodebuild` print
compiler diagnostics to stdout), so signature matching against the exception string — the existing
`_search_only_reason(str(exc))` approach — sees the full build output.

### uv-based extra installation (feature 5)

| Mechanism | Command | When to Use |
|-----------|---------|-------------|
| Project sync | `uv sync --extra semantic` (run in the checkout root) | Dev checkout: `pyproject.toml` + `uv.lock` adjacent to the running package, `sys.prefix` under the project `.venv` |
| Tool receipt reinstall | `uv tool install --force 'jarvis-mcp[semantic]'` | uv tool env (`sys.prefix` under `$UV_TOOL_DIR`, default `~/.local/share/uv/tools/`): records the extra in the tool receipt, so `uv tool upgrade` **preserves it** (upgrade carries forward receipt requirements) |
| Direct env injection | `uv pip install --python <sys.executable> 'jarvis-mcp[semantic]==<installed version>'` | Universal fallback that works in any env kind; documented uv behavior. Pin the exact installed version (`importlib.metadata.version("jarvis-mcp")`) so the compiled wheel matches. Caveat: does NOT survive `uv tool upgrade`/`--reinstall` (receipt-only requirements are restored) |
| No install | print guidance only | uvx ephemeral env (`sys.prefix` under the uv cache, `~/.cache/uv/`): the env is cache-managed; installing into it is wasted work — suggest `uvx --from 'jarvis-mcp[semantic]' ...` or `uv tool install` instead |

Detection order: dev checkout (pyproject.toml next to source) → uv tool env (prefix path under
tools dir) → uvx cache env (prefix path under uv cache) → unknown (fall back to `uv pip install
--python sys.executable`). Guard everything with `shutil.which("uv")` — if uv is absent, print
the manual command and skip. **Confidence: MEDIUM** (context7/official uv docs for the
mechanisms; the prefix-path detection heuristic is convention, not documented API — verify the
exact tool-dir path in a unit test against `uv tool dir` output at implementation time).

### TTY prompt pattern (feature 5)

Stdlib-only, matching how pip/npm-class tools behave. **Confidence: MEDIUM** (stable, widely
corroborated patterns; no library needed).

- Prompt only when `sys.stdin.isatty() and sys.stderr.isatty()` — stdin alone is insufficient
  (output may be piped/captured); treat `isatty` as a hint, not a guarantee.
- Additionally suppress when `os.environ.get("CI")` is set — CI runners sometimes allocate PTYs.
- Write the prompt to **stderr** (jarvis already reserves stdout for data / stderr for
  diagnostics in `_run_semantic_stage`).
- Wrap `input()` in `try/except (EOFError, KeyboardInterrupt)` → treat as decline.
- Default **No** (`y/N`): empty input declines. Never block: the non-TTY paths (watch,
  MCP-triggered reindex) must keep today's silent-skip + stderr hint verbatim.
- Persist decline per-repo as an additive `registry.db` column (same pattern as
  `scheme_override`), so the prompt fires at most once per repo.
- Env-var override to force-disable the prompt entirely (e.g. `JARVIS_NO_PROMPT=1` or reuse of
  the fallback env var namespace) for scripted TTY use.

### Degradation state & reporting (features 2 and 4)

No new stack needed — this is registry schema + status plumbing:

- New additive per-repo fields distinct from the permanent `search_only` flag, e.g.
  `degraded` (0/1) + `degraded_reason` (the matched signature reason or truncated indexer error)
  + the fallback opt-in flag. Distinct state is what makes self-healing possible: reindex/watch
  retries the full build when only `degraded` is set, never when `search_only=1`.
- `getIndexStatus` / `jarvis status` surface `degraded_reason` plus a recovery hint. The existing
  `SEARCH_ONLY_STATUS` string and `searchCoverage` machinery are the model to extend.
- Env var for the global fallback default follows the existing `JARVIS_` prefix convention
  (e.g. `JARVIS_SEARCH_ONLY_FALLBACK=1`).

## Installation

```bash
# No new Python dependencies. Changes are:
# 1. setup.sh: SCIP_SWIFT_VERSION pin bump (after cutting a fixed scip-swift release)
#    + asset-name/sidecar handling for the new release.yml naming
# 2. scip-swift repo: release cut from main (xcodebuild dispatch fix) with .sha256 sidecar restored
# 3. registry.db: additive columns (degraded, degraded_reason, fallback opt-in, semantic declined)

# The semantic extra the prompt installs (existing, unchanged):
uv sync --extra semantic                                   # dev checkout
uv tool install --force 'jarvis-mcp[semantic]'             # tool install context
uv pip install --python "$(python -c 'import sys;print(sys.executable)')" 'jarvis-mcp[semantic]==X.Y.Z'  # generic
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Cut new scip-swift release from main, then bump pin | Pin v0.2.1 and carry a jarvis-side workaround | Never — the dispatch loss is silent (flags accepted, wrong builder used); no jarvis-side workaround exists |
| Fix release.yml to publish `.sha256` sidecar | Adapt setup.sh to formula-embedded checksums | If scip-swift's release pipeline can't be touched — but it's org-owned, so it can |
| stdlib `input()` + isatty | `click.confirm` / `rich.prompt` / `questionary` | Only if jarvis ever adopts a CLI framework wholesale; a dependency for one y/N violates the no-new-deps constraint |
| `uv tool install --force 'jarvis-mcp[semantic]'` in tool context | `uv pip install --python sys.executable` everywhere | Acceptable simpler v1 — works everywhere — but silently loses the extra on the user's next `uv tool upgrade`; the receipt path is durable |
| Signature matching on `BuildError` description strings | Parsing xcodebuild's own diagnostics (`error: Signing for ...`, exit code 65) | Don't — scip-swift's wrapper strings are the stable contract (verified identical v0.1.2 → main); raw xcodebuild output varies by Xcode version |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| scip-swift v0.2.0 / v0.2.1 | `--build-tool xcodebuild`/`--scheme` silently ignored — every repo swiftpm-built; breaks `.xcodeproj` repos | Next release cut from main; v0.1.2 until it exists |
| `uv tool upgrade` to add the extra | Upgrade preserves the existing receipt requirements — it cannot add an extra | `uv tool install --force 'jarvis-mcp[semantic]'` |
| `pip` / `pip._internal` programmatic API | Not the project's toolchain; pip may not exist in uv-managed envs; `pip._internal` is explicitly unsupported | `uv pip install --python ...` via subprocess |
| Reusing `search_only=1` for the generic fallback | One-way trap (only `jarvis forget` escapes); kills self-healing — already a project decision | New additive degraded-state fields |
| Prompting on `sys.stdin.isatty()` alone | Captured-output/orchestrator environments can hold a TTY stdin with non-TTY output; prompt garbage ends up in logs | stdin AND stderr isatty + `CI` env check |
| `scip-swift index-many` | Cross-repo merge is scip-swift's feature, not jarvis's model (one slug per repo path is enforced) | Keep per-repo `index` |

## Stack Patterns by Variant

**If the scip-swift release with the fix ships before this milestone's phase executes:**
- Bump `SCIP_SWIFT_VERSION`, handle new asset naming, widen the macOS-arm64 gate to macOS
  (universal binary), and add the `for the requested destination` signature to the hard-fail-with-remedy set.

**If it doesn't ship first:**
- Make "cut scip-swift release" an explicit early phase task (it's an org-owned repo; the fix is
  merged on main; `Version.swift` needs a bump from 0.2.1).

**If the user's uv is too old for a needed flag:**
- All flags used (`tool install --force`, `pip install --python`, `sync --extra`) have existed for
  a long time; no version gate needed beyond `shutil.which("uv")`.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| scip-swift (any) | Swift toolchain 6.2.4 | `ToolchainInfo.pinnedSwiftVersion = "6.2.4"`; USR mapping "only guaranteed stable" against it — softer than the Kotlin pin (warning-class, not a hard failure), but a candidate stderr note if `--version` toolchain ≠ host toolchain |
| scip-swift next release | jarvis `_swift_indexer_cmd` | Invocation shape verified unchanged (`index` subcommand, `--output`, `--build-tool`, `--scheme`); new flags additive |
| scip-swift v0.2.x SCIP output (relationships) | fork-built `scip expt-convert` (phuongddx/scip) | The fork's relationships fix is what persists them to `global_symbols.relationships`; both halves now exist for Swift typeHierarchy |
| `jarvis-mcp[semantic]` extra | exact installed jarvis-mcp version | Always pin `==importlib.metadata.version("jarvis-mcp")` when installing the extra — compiled wheels mean a version drift pulls a different binary build |
| registry.db (existing) | new degraded/decline columns | Must be additive (`ALTER TABLE ... ADD COLUMN` with defaults) — existing registries keep working per milestone constraint |

## Sources

- `gh release list/view --repo jarvis-intelligence/scip-swift` — release tags, dates, assets, notes (**HIGH**, primary)
- `gh api repos/jarvis-intelligence/scip-swift/...` at tags `v0.1.2`, `v0.2.1`, and `main` —
  `IndexCommand.swift` (dispatch regression + fix), `BuildError.swift` (exact signature strings, both tags),
  `SwiftPMBuildRunner.swift`, `XcodeIntegrationTests.swift`, `ToolchainInfo.swift`, `Version.swift`,
  `.github/workflows/release.yml` (asset naming, universal binary, no sidecar) (**HIGH**, primary)
- `/astral-sh/uv` (Context7, official docs/source) — `uv tool install --with`/extras + receipt,
  `uv tool upgrade` receipt preservation, `uv pip install --python`, `uv sync --extra`, `UV_TOOL_DIR` (**MEDIUM** per seam tiering; official-source content)
- Web search (TTY prompt patterns) — isatty-as-hint, non-interactive safe defaults, EOFError handling (**LOW** per seam tiering; corroborates stdlib conventions):
  [MindStick on sys.stdin.isatty](https://www.mindstick.com/forum/161329/how-does-sys-stdin-isatty-help-in-detecting-interactive-mode),
  [TheLinuxCode console-input guide](https://thelinuxcode.com/taking-input-from-the-console-in-python-a-practical-production-minded-guide/)
- `/Users/ddphuong/Projects/jarvis-ai/jarvis/setup.sh`, `src/jarvis/index_cli.py` — existing pin, asset-name construction, `_SEARCH_ONLY_SIGNATURES` shape, `_swift_indexer_cmd`, `_run_semantic_stage` (**HIGH**, primary)

---
*Stack research for: jarvis indexing robustness & scip-swift update milestone*
*Researched: 2026-08-16*
