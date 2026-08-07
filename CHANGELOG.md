# Changelog

## [0.6.0] - 2026-08-07

Minor rather than patch: `typeHierarchy` works for the first time, and the
packaging model changes from readable pure-Python wheels to Cython-compiled
platform wheels. No breaking change to any MCP tool signature or response
shape.

### Added

- **`typeHierarchy` now returns real super/subtypes** (#29). Upstream
  `scip expt-convert` (through v0.9.0) declares `global_symbols.relationships`
  in its schema but never writes it (scip-code/scip#464), so the tool
  returned an explicit error on every index. The fix (scip-code/scip#465) is
  still unmerged upstream, so `setup.sh` now installs a build of the public
  fork `phuongddx/scip` carrying it: `build-scip.yml` cross-compiles the fork
  at the commit pinned in `SCIP_COMMIT` and publishes the binaries to
  jarvis-index releases — the same pattern zoekt already uses. **To activate:
  re-run setup.sh, then `jarvis reindex <slug>`** — the scip install is
  version-gated (an installed binary that doesn't stamp the pinned commit is
  replaced exactly once per pin bump), and indexes built with an unpatched
  scip keep returning the explicit error until reindexed.

### Changed

- **Releases ship Cython-compiled wheels; source is no longer readable on
  PyPI** (#28). A pure-Python wheel is a zip of readable `.py` files, so
  repo privacy protected the development process but not the source. The
  build backend is now setuptools + Cython, gated by `JARVIS_COMPILE=1` (set
  only in release CI — local dev and editable installs stay pure Python):
  every module compiles to a native `.so` except `__init__.py` and the
  generated `scip_pb2.py`. Wheels cover cp312–cp314 on
  {linux x86_64/aarch64, macOS arm64/x86_64}; **no sdist is published**, so
  platforms outside that matrix fail loudly instead of falling back to
  readable source. Consequences: wheels ≤ 0.5.1 remain readable on PyPI
  forever; user-reported tracebacks now show compiled frames; musl/Alpine
  and Windows are not installable targets.

No functional changes relative to 0.1.0 — this release exists purely to fix
version resolution on PyPI.

### Changed

- **Version fast-forwarded past the orphaned pre-reset `0.5.0`.** The 0.0.1
  clean-slate reset deleted the pre-reset tags and GitHub Releases, but
  `jarvis-mcp 0.5.0` was never yanked on PyPI and remained the highest
  non-yanked version there. Every unpinned install — `uvx --from jarvis-mcp`,
  `pip install jarvis-mcp`, and the plugin's `--from "jarvis-mcp>=0.0.1"`
  floor — therefore resolved to the stale pre-reset 0.5.0 instead of 0.0.1
  or 0.1.0. Jumping to 0.5.1 makes the current code the effective latest for
  all resolvers without requiring a yank. The intended post-reset numbering
  (0.0.x/0.1.x) is abandoned; versioning continues from 0.5.1.

## [0.1.0] - 2026-08-06

Minor rather than patch: `semanticSearch` gains a new capability — a third
retrieval signal — and the `symbols` module grows a public accessor surface.
No breaking change to any MCP tool signature or response shape.

### Added

- **SCIP symbol-definition signal in `semanticSearch`** (#24). Previously the
  tool fused two signals — LanceDB vector hits and Zoekt lexical hits — via
  reciprocal rank fusion, and neither knows what a *definition* is: a query
  naming an identifier ranked chunks that merely mention it on par with the
  definition site. A new `symbol_search` module now turns the query into
  ranked definition locations (token extraction with stopword filtering,
  adjacent-token bigram concatenation for identifiers written as separate
  words, dotted-suffix matching; ranking by matched-token count, then kind
  priority TYPE > METHOD > TERM, then shorter dotted path) by matching
  against the SCIP name map and resolving through `defn_enclosing_ranges`.
  The signal enters the existing RRF unweighted, and merges into a vector
  chunk when that chunk contains the definition line. `sources` on a result
  may now include `"symbol"`; a symbol-only hit carries `content: ""` (the
  SCIP db stores no source text) with `symbolName` set to the definition's
  dotted path.
- Public `symbols.name_map()` and `symbols.dotted_suffix_matches()` accessors
  — the latter generalizes the existing rung-2 matching rule with a
  `case_sensitive` flag (default preserves `resolve()`'s exact behavior).

### Fixed

- SCIP's `defn_enclosing_ranges` stores 0-based line numbers while chunker
  and Zoekt coordinates are 1-based; the symbol signal now converts at the
  `SymbolHit` seam. Without the conversion, a definition's symbol hit missed
  its own chunk's containment check by exactly one line — producing duplicate
  content-less results and off-by-one `startLine`/`endLine` — because
  def-derived chunks start precisely on the definition line.

### Notes

- The signal is strictly additive and best-effort: repos published
  `--search-only`, `partial` indexes, or any failure inside the signal
  degrade to the previous two-signal result, byte-identical.
- Swift repos gain nothing from this signal: scip-swift emits clang USR
  strings as symbol names, which natural-language tokens never match — the
  same caveat that already applies to bare-name resolution in the nav tools.

## [0.0.1] - 2026-08-05

Initial clean-slate release of `jarvis-mcp` after the repository was reset to a
single commit. This version exists to re-establish the release pipeline (PyPI,
MCP Registry, Claude Code plugin, Codex plugin) from a known-good baseline with
no prior history.

No code changes relative to the pre-reset state — every source file, test, and
piece of documentation is byte-identical to what shipped before. The version
number is intentionally reset to `0.0.1` so the release artifacts published from
this commit do not collide with the orphaned pre-reset tags (`v0.2.0`–`v0.5.0`,
now deleted from the repository and from GitHub Releases).

### Distribution

- PyPI: `jarvis-mcp` 0.0.1 published via trusted publishing.
- MCP Registry: `io.github.phuongddx/jarvis` 0.0.1.
- Claude Code plugin: `jarvis` 0.0.1 from `phuongddx/jarvis-dist`.
- Codex plugin: `jarvis` 0.0.1.


## 0.5.0

Renamed the project from `codeintel` to `jarvis`. This is a breaking rename with
no automatic migration path.

**What you must do**

- Reinstall: the PyPI distribution is now `jarvis-mcp` (was
  `codeintel-navigation-mcp`), and the CLIs are `jarvis` and `jarvis-server`
  (were `codeintel` and `codeintel-server`).
- Re-add the plugin: it is now `jarvis`, served from
  `phuongddx/jarvis-dist` (was `phuongddx/jarvis`).
- Re-register the MCP server: `claude mcp remove codeintel` then
  `claude mcp add jarvis --scope user -- uv --directory /path/to/jarvis run jarvis-server`.
- Re-index your repos. The data directory moved from `~/.codeintel` to
  `~/.jarvis` and starts empty; nothing is migrated. The old tree is left
  untouched, so `mv ~/.codeintel ~/.jarvis` recovers existing indexes if you
  prefer — published index files carry no absolute paths — but that is a manual
  step, not a supported code path.
- Rename any `CODEINTEL_*` environment variables to `JARVIS_*`. The old names
  are ignored, not honoured, so a stale `CODEINTEL_DATA_DIR` in a shell profile
  fails loudly rather than silently pointing at the abandoned tree.

**Distribution**

- `codeintel-navigation-mcp` has been deleted from PyPI. Nothing resolves it any
  more, so an existing pin fails at install time rather than quietly serving a
  stale version — switch to `jarvis-mcp`.
- The MCP Registry entry is now `io.github.phuongddx/jarvis`. The five older
  `io.github.phuongddx/codeintel` entries (0.2.1 through 0.4.0) remain
  published, but each points at `codeintel-navigation-mcp` and therefore no
  longer resolves to an installable package.

All notable changes to this project are documented in this file.

## [0.4.0] - 2026-08-04

Minor rather than patch: `getIndexStatus` gains a new field
(`searchCoverage`) that detects a class of failure the previous release
could not see at all, alongside the fix that caused it.

### Fixed

- `zoekt-index` walked the filesystem, not the git tree, so it indexed every
  gitignored path — `.venv/`, `node_modules/`, vendored checkouts. On real
  repos this inflated one index from 133 tracked files to 7353 documents /
  241 MB. Zoekt splits an oversized index into numbered shard files
  (`<slug>_v16.<NNNNN>.zoekt`); `<NNNNN>` is a shard ordinal, not a version,
  but that bloat made it look like accumulated stale versions. Deleting "old"
  shards on that mistaken premise destroyed 15 of 16 shards of a real
  repository's index, and `searchCode` kept answering queries afterward with
  no error, silently missing most of the repo's content.

  Indexing now runs through `zoekt-git-index`, which reads blobs directly out
  of the git tree, so gitignored content is excluded by construction with no
  denylist to maintain. This does mean `searchCode` now reflects git HEAD,
  not the working tree — an uncommitted edit or new untracked file is
  findable via `grep` but not `searchCode` until it's committed; SCIP
  navigation is unaffected and still reflects the working tree.

  `zoekt-git-index` has no `-meta` flag, so the per-repo search index name is
  now pinned via `git config zoekt.name <slug>` instead; without it, Zoekt
  falls back to naming the index after the `origin` remote URL, and
  `searchCode(repo=<slug>)`'s `r:<slug>` filter would silently match nothing.
  Because that key is one value per repo, `jarvis index` now refuses a
  second slug for an already-indexed repo path, naming the conflicting slug
  and the `jarvis forget` remedy.

### Added

- `getIndexStatus` reports `searchCoverage: {expected, indexed, complete}` —
  the count of git-tracked files at last index time compared against what
  Zoekt's live index actually holds for that repo. This is the check that
  would have caught the incident above: a search index missing shards after
  a successful publish now reports `complete: false` instead of silently
  answering with partial results. When it can't be computed (e.g.
  `zoekt-webserver` isn't running, or the repo predates this field),
  `searchCoverage` is `null` with a `searchCoverageReason` explaining why.

## [0.3.2] - 2026-08-04

### Added

- Bare-name symbol resolution for the SCIP navigation tools. `goToDefinition`,
  `findReferences`, `callHierarchy`, and `typeHierarchy` now accept a bare symbol
  name (e.g. `build_mcp_server`) in addition to the existing dotted SCIP
  identifier, resolving it against the index automatically. Callers no longer need
  to construct the full SCIP symbol string (`scheme manager package version descriptors`)
  before querying. Backed by
  the new `jarvis.symbols` module (`src/jarvis/symbols.py`).

### Changed

- `jarvis-use` skill and its `references/tool-roster.md` updated to document
  bare-name inputs and the resolved-symbol return shape.

## [0.3.1] - 2026-08-02

### Fixed

- Maven-built Java repos failed to index on macOS. scip-java's generated `javac` wrapper
  (`#!/usr/bin/env bash`, `set -eu`) expands `"${LAUNCHER_ARGS[@]}"` unguarded, which errors
  on bash < 4.4 — the only bash macOS ships (3.2.57) — so every Maven build died at
  `default-compile` with `LAUNCHER_ARGS[@]: unbound variable`. `setup.sh` now creates
  `~/.jarvis/shims/bash`, symlinked to a working bash >= 4.4 whenever one is findable,
  and `_java_indexer_env()` prepends that one directory to `PATH` for the indexer subprocess.
  If no bash >= 4.4 is available, indexing now fails with an actionable error naming the fix
  (`brew install bash`) instead of silently degrading to `--search-only`, which cannot be
  un-set short of `jarvis forget` and a full reindex. Filed upstream:
  [scip-code/scip-java#987](https://github.com/scip-code/scip-java/issues/987).

## [0.3.0] - 2026-08-01

Minor rather than patch: Java/Kotlin repos are indexable for the first time,
`--search-only` is a new mode, and ten more languages reach semantic search.

### Added

- `--search-only` on `jarvis index`: publishes Zoekt and semantic search without a SCIP index,
  for repos whose indexer cannot build them. Persisted, so `reindex`/`watch` reuse it. Navigation
  tools report the repo as search-only rather than "index not found".
- Automatic search-only fallback when the indexer fails with a recognized, unfixable signature —
  an Android/Gradle build that emits no SCIP shards, or a `scip-kotlinc` ABI mismatch. Any other
  failure is still a hard failure.
- Semantic indexing now covers Go, Ruby, Rust, C, C++, C#, PHP, Scala, shell, and SQL via the
  chunker's existing fixed-window fallback.
- `server.json` and a `publish-mcp-registry` workflow, listing jarvis in the
  official MCP Registry as `io.github.phuongddx/jarvis`. Authentication uses
  GitHub Actions OIDC, so releases do not block on anyone pasting a device code,
  and no token is stored. A guard fails the run when `server.json`'s versions
  drift from `pyproject.toml` — the registry cannot amend a published version,
  so a stale one is unrecoverable without a version bump.

### Fixed

- The `semantic` extra hints named a command that only works from a source
  checkout (`uv sync --extra semantic`). Anyone who installed from PyPI, or
  through the Claude Code plugin, had no clone to run it in. Both the
  `semanticSearch` error and the indexing warning now name the extra itself —
  `jarvis-mcp[semantic]` — and keep the `uv sync` form for
  checkouts. The plugin's own registration is unchanged and still omits the
  extra by design; `plugin/skills/jarvis-use/SKILL.md` documents the
  opt-in second-server path for anyone who needs `semanticSearch` there.

- Java and Kotlin repos were un-indexable: `setup.sh` only ever probed for Docker and never put a
  `scip-java` executable on `PATH`, so every index failed with
  `No such file or directory: 'scip-java'`. It now installs upstream's launcher into
  `~/.jarvis/bin`. Gradle also runs single-threaded for Java, working around a
  `ConcurrentModificationException` in scip-java's own Gradle plugin on multi-module builds.

## [0.2.1] - 2026-08-01

### Fixed

- `jarvis-server` could not start when installed from PyPI. The `mcp[cli]`
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

First release published to PyPI, as `jarvis-mcp`. Earlier versions existed
only as git tags' worth of history in this repo — there is no published 0.1.x.

### Added

- MIT `LICENSE`.
- PyPI packaging metadata: keywords, classifiers, project URLs, SPDX license
  expression, and the `mcp-name` marker the official MCP Registry uses to
  verify package ownership.
- `publish-pypi` workflow: publishes on a GitHub Release via PyPI trusted
  publishing (OIDC, no stored API token). Gates the upload on the unit suite,
  a release-tag/packaged-version match, a wheel that actually ships the
  `jarvis` import package, and the presence of the registry ownership
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

- `jarvis index` picked the wrong language for a repo whenever a gitignored
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

- `--language <name>` flag on `jarvis index` and `jarvis watch`, to
  force the indexer language instead of detecting it — for genuinely
  polyglot repos where file plurality isn't the language you want indexed.
  Persisted in the registry and reused automatically by `reindex`/`watch`,
  matching the existing `--scheme` override.

## [0.1.1] - 2026-07-30

### Fixed

- Swift repos with code-signed app-extension targets now index correctly.

## [0.1.0] - 2026-07-27

Initial versioned release.
