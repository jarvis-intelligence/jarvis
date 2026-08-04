# Swift indexing: prefer xcodebuild when a checked-in Xcode project exists — Design

## Problem

`jarvis index` fails on Swift repos that are UIKit-only SPM packages (`platforms: [.iOS(...)]`,
no macOS support) but ship a checked-in `.xcodeproj`/`.xcworkspace` specifically so they can be
built via `xcodebuild` against an iOS Simulator destination instead of plain `swift build`.

Root cause, traced against `epost-ios-theme-ui` (a real repo with this exact shape):

1. `index_cli.py`'s `_LANGUAGE_INDEXERS[".swift"]` invokes bare `scip-swift --output <path>`, with
   no `--build-tool`/`--scheme` flags.
2. `scip-swift`'s own `BuildBackendDetector.detect()` checks for `Package.swift` **first** and
   returns `.swiftpm` unconditionally when found — it never looks for `.xcodeproj`/`.xcworkspace`
   in that case, even if both exist side by side.
3. `.swiftpm` build tool runs plain `swift build`, which defaults to the macOS host destination.
   A UIKit-only package (or one of its dependencies) fails to compile there — `no such module
   'UIKit'`.
4. Manually confirmed the fix path: `scip-swift index . --build-tool xcodebuild --scheme
   ios_theme_ui` succeeds (275 documents). Without an explicit `--scheme`, it fails differently —
   `epost-ios-theme-ui` has two schemes (`ios_theme_ui`, `ios_theme_ui_tests`), so `scip-swift`'s
   own single-scheme auto-resolution can't pick one either.

Fix is scoped to `jarvis` only — `scip-swift` is a separate personal project and is not
modified by this design.

## Scope

In scope: `jarvis`'s Swift indexing path (`index_cli.py`, `registry.py`, the `index`/`reindex`/
`watch` CLI subcommands). Out of scope: `scip-swift`'s own detection logic, non-Swift languages,
and any change to how repos are indexed once a working `--build-tool`/`--scheme` combination is
known.

## Design

### 1. Build-tool auto-detection

Add a helper in `index_cli.py`:

```python
def _prefers_xcodebuild(repo_path: Path) -> bool:
    """True when repo_path has a checked-in .xcodeproj/.xcworkspace alongside
    Package.swift — scip-swift's own detector picks swiftpm whenever
    Package.swift exists, even when it can't build (e.g. a UIKit-only iOS
    package with no macOS platform support)."""
    return any(repo_path.glob("*.xcodeproj")) or any(repo_path.glob("*.xcworkspace"))
```

In `index_repo()`, when `language == "swift"` and `_prefers_xcodebuild(repo_path)` is true, the
Swift `indexer_cmd` gains `["--build-tool", "xcodebuild"]` (plus `["--scheme", scheme]` when a
scheme override is set — section 2). Non-Swift languages, and Swift repos with no checked-in
Xcode project, are unaffected — identical command to today.

### 2. Scheme override + persistence

`registry.py`:
- Add a nullable `scheme_override TEXT` column to `repos`, added via a guarded, idempotent
  `ALTER TABLE repos ADD COLUMN scheme_override TEXT` (caught `sqlite3.OperationalError` for
  DBs that already have it) run in `Registry.__init__` right after `CREATE TABLE IF NOT EXISTS`.
  No migration framework — matches the existing "plain stdlib CRUD, single-user, local-first"
  style documented in `registry.py`'s module docstring.
- `RegisteredRepo` gains `scheme_override: str | None`.
- `Registry.upsert()` gains `scheme_override: str | None = None`, persisted on every upsert.

`index_cli.py`:
- `index_repo()` gains `scheme: str | None = None`, forwarded into the `--scheme` flag (section 1)
  and into `registry.upsert(..., scheme_override=scheme)`.
- `jarvis index <path> --scheme <name>` (new optional CLI flag on the `index` subparser) sets
  it on first index.
- `jarvis reindex <slug>` reads `repo.scheme_override` back out of the registry and passes it
  through to `index_repo()` — no need to repeat `--scheme` on every reindex.
- `jarvis watch <path> --scheme <name>` (same new flag on the `watch` subparser) threads it
  into the `index_repo()` call inside `_reindex()`.
- If `xcodebuild` is selected and no scheme is set (and `scip-swift` can't auto-resolve because
  of >1 scheme), the existing `IndexingError` wraps `scip-swift`'s own message verbatim
  (`"xcodebuild requires --scheme <name>"`) — already tells the user exactly what to pass, no
  extra jarvis-level wrapping needed.

Nothing is guessed: which scheme to build is always either explicit (`--scheme`) or absent (an
error naming the ambiguity), never inferred by name-matching heuristics.

### 3. Testing

- `tests/test_index_cli.py` (unit, mirrors existing mock-subprocess style):
  - `_prefers_xcodebuild()` against `tmp_path` fixtures, with/without `.xcodeproj`/`.xcworkspace`.
  - `index_repo()` builds the expected `indexer_cmd` for: swift+xcodeproj+scheme,
    swift+xcodeproj+no-scheme, swift+no-xcodeproj (unchanged bare command). `_run` mocked, as
    existing tests already do.
- `tests/test_registry.py` (unit): `upsert()`/`get()` round-trip `scheme_override`; opening a
  pre-existing DB file created under the old schema (no column) still works after the guarded
  `ALTER TABLE` runs against it.
- No new integration fixture. Fabricating a synthetic UIKit + checked-in-Xcode-project fixture
  for CI is disproportionate to this fix's size; already manually verified end-to-end against the
  real `epost-ios-theme-ui` repo (`scip-swift index . --build-tool xcodebuild --scheme
  ios_theme_ui` → 275 documents).

### 4. Docs

- `CLAUDE.md`: one line in the `Commands` section for the new `--scheme` flag; one line in the
  spirit of the existing "Known gap" note describing the xcodeproj-presence heuristic and why it
  exists.
- `README.md`: update the CLI reference for `index`/`reindex`/`watch` to show `--scheme`.

## Follow-up (not part of this fix)

Once implemented, reindex `epost-ios-theme-ui`:
```bash
jarvis index /Users/ddphuong/Projects/epost-workspace/epost-app/epost-ios-theme-showcase/epost-ios-theme-ui \
  --slug epost-ios-theme-ui --scheme ios_theme_ui
```
