# Changelog

All notable changes to this project are documented in this file.

## [0.2.0] - 2026-08-01

First release published to PyPI, as `codeintel-mcp`. Earlier versions existed
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

- The PyPI distribution name is **`codeintel-mcp`** — the plain `codeintel`
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
