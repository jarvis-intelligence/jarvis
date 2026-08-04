# Changelog

All notable changes to this project are documented in this file.

## [0.3.2] - 2026-08-04

### Added

- Bare-name symbol resolution for the SCIP navigation tools. `goToDefinition`,
  `findReferences`, `callHierarchy`, and `typeHierarchy` now accept a bare symbol
  name (e.g. `build_mcp_server`) in addition to the existing dotted SCIP
  identifier, resolving it against the index automatically. Callers no longer need
  to construct the full SCIP symbol string (`scheme manager package version descriptors`)
  before querying. Backed by
  the new `codeintel.symbols` module (`src/codeintel/symbols.py`).

### Changed

- `codeintel-use` skill and its `references/tool-roster.md` updated to document
  bare-name inputs and the resolved-symbol return shape.

## [0.3.1] - 2026-08-02

### Fixed

- Maven-built Java repos failed to index on macOS. scip-java's generated `javac` wrapper
  (`#!/usr/bin/env bash`, `set -eu`) expands `"${LAUNCHER_ARGS[@]}"` unguarded, which errors
  on bash < 4.4 — the only bash macOS ships (3.2.57) — so every Maven build died at
  `default-compile` with `LAUNCHER_ARGS[@]: unbound variable`. `setup.sh` now creates
  `~/.codeintel/shims/bash`, symlinked to a working bash >= 4.4 whenever one is findable,
  and `_java_indexer_env()` prepends that one directory to `PATH` for the indexer subprocess.
  If no bash >= 4.4 is available, indexing now fails with an actionable error naming the fix
  (`brew install bash`) instead of silently degrading to `--search-only`, which cannot be
  un-set short of `codeintel forget` and a full reindex. Filed upstream:
  [scip-code/scip-java#987](https://github.com/scip-code/scip-java/issues/987).

## [0.3.0] - 2026-08-01

Minor rather than patch: Java/Kotlin repos are indexable for the first time,
`--search-only` is a new mode, and ten more languages reach semantic search.

### Added

- `--search-only` on `codeintel index`: publishes Zoekt and semantic search without a SCIP index,
  for repos whose indexer cannot build them. Persisted, so `reindex`/`watch` reuse it. Navigation
  tools report the repo as search-only rather than "index not found".
- Automatic search-only fallback when the indexer fails with a recognized, unfixable signature —
  an Android/Gradle build that emits no SCIP shards, or a `scip-kotlinc` ABI mismatch. Any other
  failure is still a hard failure.
- Semantic indexing now covers Go, Ruby, Rust, C, C++, C#, PHP, Scala, shell, and SQL via the
  chunker's existing fixed-window fallback.
- `server.json` and a `publish-mcp-registry` workflow, listing codeintel in the
  official MCP Registry as `io.github.phuongddx/codeintel`. Authentication uses
  GitHub Actions OIDC, so releases do not block on anyone pasting a device code,
  and no token is stored. A guard fails the run when `server.json`'s versions
  drift from `pyproject.toml` — the registry cannot amend a published version,
  so a stale one is unrecoverable without a version bump.

### Fixed

- The `semantic` extra hints named a command that only works from a source
  checkout (`uv sync --extra semantic`). Anyone who installed from PyPI, or
  through the Claude Code plugin, had no clone to run it in. Both the
  `semanticSearch` error and the indexing warning now name the extra itself —
  `codeintel-navigation-mcp[semantic]` — and keep the `uv sync` form for
  checkouts. The plugin's own registration is unchanged and still omits the
  extra by design; `plugin/skills/codeintel-use/SKILL.md` documents the
  opt-in second-server path for anyone who needs `semanticSearch` there.

- Java and Kotlin repos were un-indexable: `setup.sh` only ever probed for Docker and never put a
  `scip-java` executable on `PATH`, so every index failed with
  `No such file or directory: 'scip-java'`. It now installs upstream's launcher into
  `~/.codeintel/bin`. Gradle also runs single-threaded for Java, working around a
  `ConcurrentModificationException` in scip-java's own Gradle plugin on multi-module builds.

## [0.2.1] - 2026-08-01

### Fixed

- `codeintel-server` could not start when installed from PyPI. The `mcp[cli]`
  dependency had no upper bound, so a fresh install resolved mcp 2.0.0, which
  removed `mcp.server.fastmcp` — the module `server.py` imports — and the
  process died with `ModuleNotFoundError` before serving anything. Now capped
  at `<2.0.0`, matching the bounds already used for `protobuf` and `zstandard`.
  Development never saw this because `uv.lock` pinned mcp 1.x; only installing
  the published artifact surfaced it. **0.2.0 is broken for every consumer and
  should not be used.**

### Added

- The release workflow now installs the built wheel into a clean environment
  with no lockfile and requires the server to complete an MCP handshake and
  register all 9 tools before anything is uploaded. Every other check resolves
  from `uv.lock` and so cannot catch a dependency range that is broken for
  real users.

## [0.2.0] - 2026-08-01

First release published to PyPI, as `codeintel-navigation-mcp`. Earlier versions existed
only as git tags' worth of history in this repo — there is no published 0.1.x.

### Added

- MIT `LICENSE`.
- PyPI packaging metadata: keywords, classifiers, project URLs, SPDX license
  expression, and the `mcp-name` marker the official MCP Registry uses to
  verify package ownership.
- `publish-pypi` workflow: publishes on a GitHub Release via PyPI trusted
  publishing (OIDC, no stored API token). Gates the upload on the unit suite,
  a release-tag/packaged-version match, a wheel that actually ships the
  `codeintel` import package, and the presence of the registry ownership
  marker.

### Changed

- The PyPI distribution name is **`codeintel-navigation-mcp`** — the plain `codeintel`
  name is held by an unrelated, abandoned package (Komodo Edit CodeIntel, last
  released 2018). The import package, both CLIs (`codeintel`,
  `codeintel-server`), and the MCP server name are unchanged; only the name you
  `install` differs.
- README reordered install-first: value proposition, quick start, tool table,
  and supported-language/platform limits now precede the architecture material.

### Fixed

- `codeintel index` picked the wrong language for a repo whenever a gitignored
  scratch directory (vendored checkouts, sibling clones, `.worktrees/`) held
  more files than the repo's own tracked code — `detect_language()` walked the
  filesystem (`rglob`) and counted those files too. Detection now counts
  `git ls-files` output instead, so only the repo's own tracked files vote.
  `IGNORED_DIRS` filtering is still applied on top, since git alone doesn't
  exclude build output a repo happens to commit.
- A non-git directory now raises a clear `NotAGitRepositoryError` instead of
  silently walking the filesystem or failing with an unrelated message.
- A git repo with no commits now raises `IndexingError` naming the cause,
  instead of a raw, unhelpful `CalledProcessError`.

### Added

- `--language <name>` flag on `codeintel index` and `codeintel watch`, to
  force the indexer language instead of detecting it — for genuinely
  polyglot repos where file plurality isn't the language you want indexed.
  Persisted in the registry and reused automatically by `reindex`/`watch`,
  matching the existing `--scheme` override.

## [0.1.1] - 2026-07-30

### Fixed

- Swift repos with code-signed app-extension targets now index correctly.

## [0.1.0] - 2026-07-27

Initial versioned release.
