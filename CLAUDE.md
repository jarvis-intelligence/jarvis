# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Personal, local-first code intelligence MCP server. SCIP-backed navigation (go-to-definition,
find-references, call/type hierarchy, document symbols) + Zoekt-backed lexical search, exposed
as MCP tools over stdio — no HTTP server, no auth, no network.

## Commands

```bash
uv sync                              # install deps
uv sync --extra watch                # + watchdog, needed for `codeintel watch`
uv sync --extra semantic             # + lancedb/sentence-transformers/tree-sitter, needed for semanticSearch

uv run pytest                        # all tests
uv run pytest -m "not integration"   # unit only — no external binaries required
uv run pytest -m integration         # integration only — runs real scip-python/scip/zoekt-index
uv run pytest tests/test_query.py::test_go_to_definition_returns_location   # single test

uv run codeintel index /path/to/repo [--slug name] [--scheme name] [--language name] [--semantic-include path]
uv run codeintel list
uv run codeintel status <slug>
uv run codeintel reindex <slug>
uv run codeintel forget <slug>
uv run codeintel watch /path/to/repo [--debounce 5] [--scheme name] [--language name] [--semantic-include path]   # foreground, not a daemon

uv run codeintel-server              # MCP stdio entry point
claude mcp add codeintel --scope user -- uv --directory /path/to/codeintel run codeintel-server
```

Required on `PATH` for anything beyond unit tests: one language indexer per repo
(`scip-typescript` / `scip-python` / `scip-java` / `scip-swift`), `scip` (for `scip expt-convert`),
and `zoekt-index` / `zoekt-webserver`. Integration tests are gated on these and skip cleanly if absent.

## Architecture

Three engines sit behind the MCP server, each backed by its own storage:

- **Query** (`query.py` + `index_reader.py` + `scip_decoder.py`) — SCIP nav ops via raw SQL
  against the `scip expt-convert` SQLite schema (`documents`/`chunks`/`global_symbols`/`mentions`).
  `scip_decoder.py` is the *only* module importing `scip_pb2`/`zstandard` — an isolation seam so
  future SCIP proto version bumps localize to one file. `symbols.py` is the same kind of isolation
  seam for the SCIP *symbol string* grammar: it owns parsing (`parse_symbol()`) and bare-name
  resolution (`resolve()`, a two-rung ladder of exact match then dotted-suffix match) end to end, so
  `goToDefinition`/`findReferences`/`callHierarchy`/`typeHierarchy` accept a bare or qualified name
  instead of requiring the full, version-pinned SCIP string. Caveat: scip-swift emits clang USR
  strings (e.g. `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`) as its symbol names, so bare-name
  resolution has no practical value in Swift repos — it works for Python/TypeScript/Java/Kotlin.
- **Search** (`search.py`) — `searchCode` via a real `httpx` client to `zoekt-webserver`.
  `ZoektLifecycle` lazily spawns the webserver on first call (pidfile-tracked, killed at exit);
  never spawn it elsewhere.
- **Graph** (`graph.py` + `registry.py`) — package dependency graph (`packages`/`edges` in
  `registry.db`) driving `blastRadius` (2-hop BFS). `populate_graph_for_repo()` clears a repo's
  outgoing edges before recomputing — rebuild-not-accumulate, so retracted dependencies don't linger.
- **Semantic** (`chunker.py` + `embeddings.py` + `semantic.py`) — tree-sitter chunking →
  sentence-transformers embeddings → a per-repo LanceDB table under `~/.codeintel/lancedb/`.
  `semanticSearch` fuses vector hits with Zoekt hits via reciprocal rank fusion. Gated behind the
  optional `semantic` extra; the indexing stage is non-fatal in `codeintel index` (a failure there
  never blocks the SCIP/Zoekt publish). A LanceDB table only ever holds vectors from one
  model+revision — the model-identity rule.

**Index pipeline** (`index_cli.py`, `index_repo()`): detect language by extension plurality
**across git-tracked files** (ties broken by fixed priority `.ts→.tsx→.py→.java→.kt→.swift`; one language per repo, no
multi-language merge) → run the matching indexer → `scip expt-convert` → populate the graph →
`zoekt-index` → **atomic publish**: write the new versioned `index-<sha>.db`, and only once graph +
Zoekt both succeed, flip the `current` pointer file via `os.replace()`. A query already reading the
old file is never interrupted; a failure anywhere leaves the previous index live. Never mutate a
published `index-<sha>.db` in place — queries always open it `mode=ro&immutable=1`.

**Language detection reads git, not the filesystem:** `detect_language()` counts extensions across
`git ls-files`, not a `rglob` walk. A walk also counts gitignored scratch directories — vendored
checkouts, sibling clones, `.worktrees/` — which can outnumber a repo's own code and pick a
language it doesn't use. (Real case: a repo with 81 tracked `.py` files and a gitignored
`.local-checkouts/` of 4782 `.ts`/`.tsx` files was detected as TypeScript.) `IGNORED_DIRS` is still
applied on top, because git does not exclude build output a repo happens to commit. A non-git path
raises `NotAGitRepositoryError`. This is a heuristic with known edge cases (git shows duplicate
entries for unmerged paths, sparse-checkout entries absent from disk still count, and repositories
with code entirely in git submodules won't be counted). Pass `--language <name>` on the first
`codeintel index` to override detection for a polyglot repo — it's persisted in the registry, so
`reindex`/`watch` reuse it automatically.

**Single-tenant hardcoding:** `config.py` pins `PROJECT = "_"` and `BRANCH = "_"`. The on-disk
`scip/_/<slug>/_/` path shape is an artifact of reusing the vendored `IndexConnectionCache`'s
`(project, repo, branch)` 3-tuple key unchanged — not a real multi-tenancy feature. Only `<slug>`
is a real, user-facing identifier.

**Error handling at the MCP boundary:** `server.py` catches all exceptions per-tool and returns
`{"error": "..."}` rather than raising — this keeps the stdio server alive across query bugs. Every
nav tool takes `repo` (the slug from `codeintel index`) plus a tool-specific `symbol` or `path`.

**Known gap:** `scip expt-convert` (through v0.9.0) declares `global_symbols.relationships` but never
populates it, so `typeHierarchy` returns an explicit `{"error": ...}` on real indexes — deliberately
*not* empty arrays, which would wrongly assert "no supertypes". Not a bug in codeintel's query logic;
reported upstream as [scip#464](https://github.com/scip-code/scip/issues/464) with fix PR
[scip#465](https://github.com/scip-code/scip/pull/465) open.

**Java/Kotlin reach is narrower than "supported" suggests.** `scip-java` indexes plain JVM
Gradle/Maven repos, and codeintel forces `-Dorg.gradle.parallel=false` for them because scip-java's
Gradle plugin races against itself across modules. Two cases cannot work at all and degrade to a
search-only publish instead:

- **Android/AGP** — scip-java's plugin keys off Gradle's standard `SourceSetContainer`, which AGP
  replaces with its variant model, so the build succeeds and emits zero SCIP shards
  ([scip-java#177](https://github.com/scip-code/scip-java/issues/177)).
- **Kotlin other than the pinned version** — `scip-kotlinc` is compiled against exactly one Kotlin
  release (`SCIP_JAVA_KOTLIN` in `setup.sh`, currently 2.2.0). Kotlin's compiler-plugin API is
  internal and unstable: 2.1.21 and 2.3.20 fail with `AbstractMethodError`, and even 2.2.20 fails
  with `NoSuchMethodError`. Java is unaffected — `scip-javac` uses javac's stable plugin API.

Both are detected from the indexer's own error output (`_SEARCH_ONLY_SIGNATURES` in
`index_cli.py`), never from parsing build files, and the decision is persisted so `reindex`/`watch`
skip the doomed build. `--search-only` requests the same publish up front: Zoekt and semantic search
work, navigation tools return an explanation. Only listed signatures trigger it — any other indexer
failure is still a hard failure.

**Maven Java repos need bash >= 4.4 on macOS.** scip-java generates a `javac`
wrapper (`#!/usr/bin/env bash`, `set -eu`) that expands `"${LAUNCHER_ARGS[@]}"`
unguarded. maven-compiler-plugin's version probe passes no `-J` flags, so the
array is empty — an error on bash < 4.4, and macOS ships only 3.2. Every
Maven-built Java repo fails at `default-compile` with
`LAUNCHER_ARGS[@]: unbound variable`.

Because the wrapper's shebang resolves bash through `PATH`, `setup.sh` creates
`~/.codeintel/shims/bash` pointing at a bash >= 4.4, and `_java_indexer_env()`
prepends that one directory for the indexer subprocess. Only the shim dir is
prepended, never a general bin dir — that would shadow `java`/`mvn`/`git` for
the build. Gradle is unaffected: it does not use the forked-javac wrapper.

Unlike the AGP case this is *fixable*, so it does not degrade to search-only —
`--search-only` cannot be un-set, which would strand a user who later installed
bash. It raises an error naming the remedy and leaves the repo `failed`.

**Swift build-tool selection:** `scip-swift`'s own `BuildBackendDetector` picks `swiftpm`
whenever `Package.swift` exists, even for repos that can't build that way (e.g. a UIKit-only
iOS package with no macOS platform support). `index_cli.py`'s `_prefers_xcodebuild()` overrides
this: a Swift repo with a checked-in `.xcodeproj`/`.xcworkspace` is indexed via `--build-tool
xcodebuild` instead. When such a repo has more than one scheme, pass `--scheme <name>` on the
first `codeintel index` — it's persisted in the registry, so `reindex`/`watch` reuse it
automatically.

## Testing conventions

Test files mirror source modules 1:1 (`test_query.py` ↔ `query.py`, etc.) — `models.py` and
`__init__.py` are the only modules without one. Unit tests mock subprocess/file I/O/HTTP; integration
tests (`@pytest.mark.integration`, concentrated in `test_index_cli.py`) call real binaries end-to-end
against fixtures in `tests/fixtures/`.

## Conventions worth knowing

- Result types are frozen dataclasses (`@dataclass(frozen=True)`), not Pydantic — there's no HTTP
  boundary to validate against, and `server.py` converts them via `dataclasses.asdict()`.
- Direct `sqlite3`, no ORM, always parameterized queries.
- Modern type-hint syntax throughout: `str | None`, `list[T]`, `dict[K, V]`.
- Env vars are prefixed `CODEINTEL_` (`CODEINTEL_DATA_DIR`, `CODEINTEL_EMBEDDING_MODEL`,
  `CODEINTEL_EMBEDDING_BATCH_SIZE`, `CODEINTEL_EMBEDDING_QUERY_PREFIX`,
  `CODEINTEL_EMBEDDING_DOC_PREFIX`, `CODEINTEL_ZOEKT_BIN`).

## Cutting a release

Use the `.claude/skills/codeintel-release` skill (project-scoped, maintainer-only — distinct from
`plugin/skills/`, which ships to end users installing codeintel). It captures the full pipeline
verified end-to-end while cutting v0.3.1: bump the version consistently across `pyproject.toml`,
`server.json` (two fields), `plugin/.claude-plugin/plugin.json`, and `uv.lock`; add a `CHANGELOG.md`
entry; open and merge a `chore/release-X.Y.Z` PR; tag and publish a GitHub Release; confirm
`publish-pypi.yml`/`publish-mcp-registry.yml` both succeed. `scripts/check_versions.py` (run in CI
by `test.yml`) enforces the same 4-file consistency, plus a 5th file the skill doesn't hand-bump:
`plugin/.mcp.json`'s `--from` floor, a `>=` compatibility minimum checked against the release
version, not synced to it.
