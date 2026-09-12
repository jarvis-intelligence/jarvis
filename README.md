# jarvis

<!-- mcp-name: io.github.phuongddx/jarvis -->

[![CI](https://github.com/phuongddx/jarvis/actions/workflows/test.yml/badge.svg)](https://github.com/phuongddx/jarvis/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/jarvis-mcp.svg)](https://pypi.org/project/jarvis-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/jarvis-mcp.svg)](https://pypi.org/project/jarvis-mcp/)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%20%2F%20%20Linux-lightgrey)](https://pypi.org/project/jarvis-mcp/)
[![License: MIT](https://img.shields.io/pypi/l/jarvis-mcp.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-server-2e5aa8)](https://modelcontextprotocol.io)

**Local-first code intelligence for coding agents.** Precomputed SCIP navigation
(go-to-definition, find-references, call/type hierarchy, document symbols), Zoekt
lexical search, natural-language semantic search, and cross-repo blast radius —
exposed as ten MCP tools for Claude Code, Cursor, or any MCP client.

One indexing CLI writes up, one stdio runtime reads down — the storage seam in
`~/.jarvis` is the only contract between them. **No server, no auth, no network,
nothing leaves your machine.**

[What is it?](#what-is-it) · [How it works](#how-it-works) · [Quick start](#quick-start) · [MCP tools](#mcp-tools) · [Requirements and limits](#requirements-and-limits) · [Indexing](#indexing-a-repo) · [Configuration](#configuration) · [Documentation](#documentation)

## What is it?

**Without jarvis**, asking your agent *"where is `AuthService` used?"* means
grepping for the string, re-reading whole files to filter false positives, and
guessing at call sites — burning context window on **search** instead of
**reasoning**.

**With jarvis**, the agent calls `findReferences` and gets exact file-and-range
occurrences from a precomputed SCIP index, `callHierarchy` for the call graph,
and `semanticSearch` for questions like *"where is token refresh handled?"* in
plain language.

Think of it as `grep`, but matching **symbols, definitions, and references** —
indexed once per repo, answered in milliseconds.

- **Declaration-level navigation without any indexer** — a Tree-sitter syntax
  baseline (17 languages) is built on every `jarvis index` run from pip-installed
  grammars, no compiler or build system required — on top of a precomputed
  [SCIP](https://scip-code.org/) index for full precise navigation
  (TypeScript/TSX, Python, Java/Kotlin, Swift), Zoekt lexical search, and
  optional vector search, all from local SQLite/LanceDB files.
- **Read-only by design.** jarvis never edits code; it is the retrieval half.
  If you want an agent that performs semantic renames and refactors, you want
  [Serena](https://github.com/oraios/serena) — the two are complementary.

jarvis is deliberately narrow: one language per repo, macOS/Linux only, and
indexing is an explicit step — see [Requirements and limits](#requirements-and-limits)
before installing.

## How it works

<p align="center">
  <img
    src="https://raw.githubusercontent.com/phuongddx/jarvis/main/docs/assets/jarvis-architecture.png"
    width="880"
    alt="jarvis architecture: a writer CLI and an MCP reader inside the jarvis system boundary, both talking to four stores in the local data dir — the immutable SCIP index, Zoekt shards, LanceDB vectors, and the registry — plus the git repo and a lazily spawned zoekt-webserver">
1. **Index.** `jarvis index /repo` builds a Tree-sitter syntax baseline for every
   supported file first, then optionally runs the language's SCIP indexer and
   converts the result to SQLite, builds Zoekt shards (plus optional
   embeddings), and publishes everything **atomically** into `~/.jarvis` as one
   immutable snapshot selected by a single `current` pointer. SCIP tooling
   missing or failing degrades the run to exit-0 — the baseline still publishes.
2. **Serve.** `jarvis-server` speaks MCP over stdio and exposes ten tools,
   backed by lazy singletons; a `zoekt-webserver` is spawned on first search
   and shared across processes via pidfile.
3. **Ask.** Your agent calls tools. Every query opens the published
   `index-<sha>-<generation>.db` read-only (`mode=ro&immutable=1`) — the
   runtime path never writes.

**Storage is the seam.** The runtime half only ever reads down into it; the
indexing half only ever writes up into it; the two share no other contract.
Three load-bearing consequences:

- **The runtime path never writes.** Index files are never mutated in place.
- **Publishing is atomic.** A reindex writes a new versioned `.db`, populates
  the package graph, and runs `zoekt-index` — only once *all* of that succeeds
  does `os.replace` (POSIX `rename(2)`) flip the small `current` pointer. A
  query already reading the old file keeps working; there is no downtime
  window, and a failure anywhere leaves the previously published index live.
- **The package graph is rebuilt, not accumulated.** Each reindex clears that
  repo's own outgoing edges before recomputing them, so `blastRadius` always
  reflects each repo's *last* index run.

Layer-by-layer detail, the full index pipeline, and the semantic path are in
[`docs/system-architecture.md`](docs/system-architecture.md). Core query/search
logic is ported from an internal reference implementation; the enterprise shell
(FastAPI, Postgres, hosted-git auth, Cloud Build) is dropped in favor of a
single stdio process reading local SQLite files.

## Quick start

**1. Install the external indexer binaries** (only needed for optional SCIP
navigation and Zoekt search — the Tree-sitter syntax baseline ships inside the
pip package and needs no external binary): scip, zoekt, per-language indexers:

```bash
curl -fsSL https://raw.githubusercontent.com/jarvis-intelligence/jarvis-index/main/setup.sh | sh
```

**2. Install jarvis:**

```bash
uv tool install jarvis-mcp
```

**3. Index a repo** (slug defaults to the directory name):

```bash
jarvis index /path/to/your/repo
```

**4. Register the MCP server.** Using Claude Code, install the plugin and it
registers itself:

```
/plugin marketplace add jarvis-intelligence/jarvis-index
/plugin install jarvis@jarvis
```

Any other MCP client (or Claude Code without the plugin) registers manually:

```bash
claude mcp add jarvis --scope user -- jarvis-server
```

That's it — ask your agent *"find all references to `AuthService`"* and it will
call `findReferences` instead of grepping.

<details>
<summary>Other MCP clients (Cursor, Claude Desktop, any stdio client)</summary>

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "jarvis-server"
    }
  }
}
```

If your client can't find `jarvis-server` on `PATH` (GUI apps often don't
inherit your shell's), use the absolute path from `which jarvis-server`.
</details>

<details>
<summary>Running from a clone instead</summary>

```bash
git clone https://github.com/phuongddx/jarvis && cd jarvis
uv sync
claude mcp add jarvis --scope user -- uv --directory "$(pwd)" run jarvis-server
```
</details>

<details>
<summary>Optional extras</summary>

```bash
uv tool install "jarvis-mcp[watch]"      # + watchdog, for `jarvis watch`
uv tool install "jarvis-mcp[semantic]"   # + lancedb/sentence-transformers, for semanticSearch
```
</details>

## MCP tools

| `goToDefinition` | Resolve a symbol to its defining file and range — SCIP when the file has SCIP definition coverage, otherwise the syntax baseline's declaration; each location carries `source` (`"scip"` or `"tree-sitter"`) and `positionEncoding` |
| `findReferences` | Every occurrence of a symbol across the indexed repo — **SCIP-only**: without usable SCIP occurrence data it returns `requiredCapability`/`reason`/`recovery`, never an empty list |
| `callHierarchy` | Incoming/outgoing calls for a symbol — **SCIP-only** (same contract as `findReferences`) |
| `typeHierarchy` | Supertypes/subtypes — **SCIP-only**; needs an index built with the bundled `scip`, see [limitations](#known-upstream-limitations) |
| `documentSymbols` | Outline of every symbol defined in one file — routed per file: the SCIP outline when usable, otherwise Tree-sitter declarations; a syntax-served response carries a `coverage` object (parsed/partial/failed counts and reason) |
| `searchCode` | Zoekt lexical/regex search, optionally filtered to one repo |
| `semanticSearch` | Natural-language search — vector hits fused with Zoekt lexical hits and SCIP symbol-definition matches via reciprocal rank fusion |
| `blastRadius` | Which *other* indexed repos depend on a package, up to 2 hops |
| `getIndexStatus` | Published commit, freshness, staleness vs. a working tree; `capabilities.tools` reports per-tool providers, `capabilities.syntax` reports extraction counts, and freshness names the snapshot `generation` |
| `indexRepo` | Build an index for a git repo at `path` so the other tools have something to read. Returns immediately; poll `getIndexStatus`. `semantic` defaults to false. |

`documentSymbols`/`goToDefinition` are **per-file provider routed**: a file with
usable SCIP coverage is answered by SCIP (full identifiers, references,
hierarchies); a file without it is answered by the syntax baseline's real
Tree-sitter declarations, whose opaque `syntax:` identifiers round-trip through
`goToDefinition`. Bare or qualified names search both providers, so an
ambiguous name returns combined candidates from both.

Every nav tool takes `repo` (the slug from `jarvis index`) plus a
tool-specific `symbol` or `path`. All tools report failure the same way — a
`{"error": "..."}` payload rather than a transport-level error, so a query bug
never kills the stdio server.

## Requirements and limits

Read this before installing — jarvis is deliberately narrow.

- **macOS and Linux only.** Windows is not supported.
- **One language per repo.** Language is detected by extension plurality across
  git-tracked files; a polyglot monorepo gets indexed as whichever language has
  the most files. Multi-language merge is out of scope. Override with
  `--language`.
- **Build-free syntax baseline covers 17 languages** — Python, JavaScript,
  TypeScript/TSX, Java, Kotlin, Swift, Go, Ruby, Rust, C, C++, C#, PHP, Scala,
  Bash, and SQL — served by `documentSymbols`/`goToDefinition` as declaration
  outlines. Grammars are **pip-installed** dependencies of the package itself
  (no external binary, no download at index time); a repo with none of these
  still gets Zoekt search.
- **Precise SCIP navigation (`findReferences`, `callHierarchy`,
  `typeHierarchy`) covers four language families:** TypeScript/TSX, Python,
  Java/Kotlin, Swift. These tools require real SCIP data — without it they
  explain what is missing and how to retry rather than returning empty
  results.
- **Navigation and search only — jarvis never edits code.** If you want an
  agent that can perform semantic renames and refactors, you want
  [Serena](https://github.com/oraios/serena); the two are complementary.
- **Indexing is a separate, explicit step.** Nothing is live-analyzed. Run
  `jarvis index` (or `jarvis watch`) to publish an index before querying.
- **Optional SCIP/Zoekt enrichment requires external binaries** that `setup.sh`
  installs (the syntax baseline itself ships in the wheel):

  | Purpose | Binary | Source |
  |---------|--------|--------|
  | SCIP → SQLite conversion | `scip` | prebuilt, pinned `v0.9.0` (**minimum** — older versions silently drop occurrence ranges) |
  | Lexical search | `zoekt-git-index` · `zoekt-webserver` | cross-compiled by [our CI](.github/workflows/build-zoekt.yml) — upstream publishes no binaries |
  | Zoekt symbol queries (`sym:`) | `universal-ctags` | system package manager via setup.sh — without it `sym:` silently returns nothing |
  | TypeScript indexing | `scip-typescript` | `npm install -g` |
  | Python indexing | `scip-python` | `npm install -g` |
  | Swift indexing | `scip-swift` | prebuilt, **macOS arm64 only** |
  | Java/Kotlin indexing | `scip-java` | detect-only — Docker image, asks before pulling |

  Options: `--only <name>` to install one dependency, `--force` to reinstall,
  `--help` for usage. Re-running is safe: anything already present is skipped.

## Indexing a repo

```bash
jarvis index /path/to/your/repo            # slug defaults to the directory name
jarvis index /path/to/your/repo --slug foo # or pick one explicitly
jarvis index /path/to/your/repo --scheme MyScheme # Swift repo with an ambiguous Xcode scheme
jarvis index /path/to/your/repo --language python # force the language instead of detecting it from git-tracked files
jarvis index /path/to/your/repo --semantic-include vendor/generated # force-include a path the generated-file filter would otherwise skip
jarvis index /path/to/your/repo --no-scip   # skip optional SCIP enrichment; the syntax baseline + Zoekt still publish (exit 0)
jarvis index /path/to/your/repo --scip      # re-enable SCIP enrichment (both flags persist per repo)
jarvis list
jarvis status foo
jarvis reindex foo
jarvis forget foo
jarvis dashboard [--port N] [--no-open]    # localhost web console over ~/.jarvis
```

`status` (as shown by both `list` and `status`) is one of `indexing` (run in
progress), `indexed` (baseline published, SCIP usable/disabled/unsupported),
`partial` (published, but the snapshot has documented syntax/SCIP extraction
gaps), `degraded` (published, but the enabled SCIP stage failed, is missing, or
is watch-suppressed — exit 0, cause and `jarvis reindex <slug> --scip` recovery
recorded), or `failed` (a required stage/storage/publication failure — nothing
new published; the previous snapshot stays live).

`--semantic-include` is repeatable — pass it once per path prefix to
force-include several. Like `--scheme` and `--language`, once set there is no flag to clear
it; change it by re-running `jarvis index` with the new value(s).

**Language detection** counts source files by extension **across git-tracked files** and picks the winner —
one language per index:

| Extensions | Indexer |
|------------|---------|
| `.ts` `.tsx` | `scip-typescript` |
| `.py` | `scip-python` |
| `.java` `.kt` | `scip-java` |
| `.swift` | `scip-swift` |

Ties break by fixed priority (`.ts` → `.tsx` → `.py` → `.java` → `.kt` → `.swift`).
`.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, and `build` are
skipped. Reading git rather than walking the filesystem is deliberate: a walk
  also counts gitignored scratch directories, which can outnumber a repo's own
  code and pick a language it doesn't use.

The pipeline then runs in stages: capture tracked files and build the syntax
baseline (scratch) → optional SCIP indexer + `scip expt-convert` (failures
degrade to exit-0, never blocking the baseline) → `zoekt-index` into
`~/.jarvis/.zoekt` (its failure fails the run) → optional semantic embeddings →
graph edge update → publish everything as one immutable
`~/.jarvis/scip/_/<slug>/_/index-<sha>-<generation>.db` snapshot → atomic
`current` pointer flip → registry update → old snapshots retired.

> The `scip/_/<slug>/_/` path shape reuses the vendored `IndexConnectionCache`'s
> `(project, repo, branch)` 3-tuple layout with the outer two pinned to `_` (see
> [`src/jarvis/config.py`](src/jarvis/config.py)). It is not a user-facing
> contract — only `<slug>` matters when calling tools.

Swift indexing works end-to-end. It requires `scip >= v0.9.0`: older converters
cannot read scip.proto's `typed_range` oneof, which is the only range encoding
`scip-swift` emits, and silently produce an index with no navigable positions.
`jarvis index` refuses an older `scip` rather than publishing one.

Indexing a Swift repo with code-signed app-extension targets additionally requires
`scip-swift >= v0.1.2`: earlier versions pass no code-signing overrides to `xcodebuild`, which
then fails provisioning for every signed target before compiling anything. Because `setup.sh`
skips any dependency that is merely *present*, an existing install is **not** upgraded by
re-running it — use `sh ./setup.sh --only scip-swift --force`.

## Dashboard

```bash
jarvis dashboard          # serves http://127.0.0.1:6080 and opens a browser
```

A localhost web console over the same `~/.jarvis` data the CLI and MCP server
read — watch index runs and call the tools from a browser, no MCP client
involved:
<p align="center">
  <img src="docs/assets/dashboard-repos.png" width="860" alt="jarvis console, Repos view: two indexed repos with status chips, freshness, SCIP state, storage sizes, and reindex/forget actions">
</p>
<p align="center">
  <img src="docs/assets/dashboard-search.png" width="860" alt="jarvis console, Search: one query fanned to Zoekt lexical, semantic-vector, and SCIP-symbol results with term highlighting">
</p>
<p align="center">
  <img src="docs/assets/dashboard-detail.png" width="424" alt="jarvis console, repo detail: published snapshots, per-tool capabilities, recovery guidance, package-graph edges, storage footprint">&nbsp;
  <img src="docs/assets/dashboard-playground.png" width="424" alt="jarvis console, Playground: findReferences invoked with typed parameters, raw JSON response with error framing">
</p>

- **Repos** — every registered repo with status, freshness, and a live tail of
  its index log; index a path, reindex, or forget a repo (forget makes you
  type the slug to confirm).
- **Repo detail** — one repo's published snapshots, per-tool capabilities,
  recovery guidance, package-graph edges, and storage footprint.
- **Search** — one query answered three ways (Zoekt lexical, semantic vector,
  SCIP symbols), scoped to one repo or across all, with an in-browser source
  viewer for any hit.
- **Playground** — invoke any of the ten MCP tools with typed parameters and
  inspect the raw JSON response.

Index and reindex spawn the same detached `jarvis index` children as the
`indexRepo` MCP tool — same launch records, same per-slug build lock — and a
second run for a slug already in flight is rejected. Forget reuses the CLI's
own teardown. `--port` overrides the port; `JARVIS_DASHBOARD_PORT` does the
same via the environment (invalid values fall back to 6080 with a warning);
`--no-open` skips the browser. The server binds `127.0.0.1` only and rejects
non-localhost `Host` headers; like the MCP server it has no auth — a console
for your machine, not a network service. Details and troubleshooting:
[`docs/dashboard.md`](docs/dashboard.md).

## Watching a repo (auto-reindex)

```bash
jarvis watch /path/to/your/repo             # debounce defaults to 5s
jarvis watch /path/to/your/repo --debounce 3
jarvis watch /path/to/your/repo --scheme MyScheme
jarvis watch /path/to/your/repo --language python
jarvis watch /path/to/your/repo --no-scip   # persist SCIP-off for this repo
```

Each debounced reindex runs the same staged pipeline as `jarvis index`. When a
SCIP attempt already failed at the current commit, a watch run skips only that
SCIP retry (the syntax baseline still publishes); a new commit, an explicit
`jarvis reindex`, or an explicit `--scip` retries enrichment.

Runs in the foreground (not a daemon) using `watchdog` — install it with the
`watch` extra. A burst of file changes (e.g. an editor's atomic save touching
several files) coalesces into exactly **one** reindex. The reindex fires once
`--debounce` seconds (default 5) have passed since the *last* file change —
this prevents thrashing on rapid edits. `.git`, `node_modules`, `.venv`,
`__pycache__`, `dist`, and `build` are ignored.

## Tool details

- **`getIndexStatus`** takes an optional `repo_path` (the repo's local git
  working directory) to compare the published commit against
  `git rev-parse HEAD`. Omitted, freshness is reported without a staleness
  check — never `stale: true` without evidence.
- **`searchCode`** takes `query` plus an optional `repo` filter. On first call
  it lazy-spawns an embedded `zoekt-webserver` (pidfile'd so a second jarvis
  process reuses it instead of spawning a duplicate; killed on clean exit via
  `atexit`).
- **`blastRadius`** takes `repo` plus `symbol_or_package` (e.g. `"npm:@scope/
  name"`, the same `"{manager}:{name}"` string `jarvis index` derives from
  each repo's SCIP symbols). Returns every other indexed repo whose package
  depends on it, up to 2 hops, each tagged with its hop distance. The package
  graph has no per-node timestamp, so `freshness` is always `"unknown"` here —
  an honest limitation of the schema, not a bug. Cross-repo edges resolve by
  exact package name against whatever has *already* been indexed: index the
  dependency first, or re-run `jarvis index`/`reindex` after indexing it,
  for an edge to appear. Each reindex retracts that repo's own stale edges
  before recomputing them, so a removed dependency's edge disappears too —
  the graph always reflects each repo's *last* index run, not an
  accumulation of every run it's ever had.
- **`semanticSearch`** takes `repo` plus a natural-language `query`. Requires the
  optional `semantic` extra. Results fuse a LanceDB vector search over
  tree-sitter-chunked code with `searchCode`'s Zoekt hits via reciprocal rank
  fusion. Raises a clear error if the repo has never been indexed with the extra
  installed (`jarvis reindex <slug>` after installing it builds the missing
  table); indexing itself is non-fatal — a failure there never blocks the rest
  of `jarvis index`. Semantic indexing also respects `.gitignore` (on top of
  the hardcoded ignore-directory list) and skips any file over 1 MB, in addition
  to the existing generated-file banner/long-line detection —
  `--semantic-include` overrides all three.

### Known upstream limitations

These are real behaviors of the underlying SCIP tooling (`scip expt-convert`
as of v0.9.0, `scip-java`, `scip-kotlinc`), not jarvis bugs:

- **`typeHierarchy` returns an explicit `{"error": ...}`**, not empty arrays, on
  indexes built with an unpatched upstream `scip` — that converter declares
  `global_symbols.relationships` in its schema but never writes it. An empty
  result would wrongly assert "no supertypes"; the error says "cannot tell"
  instead. setup.sh installs a fork build carrying the fix, so a fresh
  `jarvis reindex <slug>` makes the tool work. Reported upstream:
  [scip-code/scip#464](https://github.com/scip-code/scip/issues/464), fix
  [scip-code/scip#465](https://github.com/scip-code/scip/pull/465) (open, CI green).
- **`displayName` / `kind` are backfilled from the symbol string.** The converter never populates
  `global_symbols.display_name`/`.kind`, so `query.py`'s `_display_and_kind` parses both from the
  SCIP symbol string whenever the database columns are empty (which they still normally are) —
  `documentSymbols` returns real values in practice; only a genuinely unparseable symbol falls
  through to `null`.
- **`searchCode`'s `repo` filter matches Zoekt's own repository name**, which
  `jarvis index` now names after the slug via `zoekt-index -meta` — so this
  no longer diverges for repos indexed with current code. Shards published by
  an older jarvis still carry their old directory-derived name until you
  `jarvis reindex <slug>`.
- **`scip-java` can't index Android/Gradle repos at all** — its Gradle plugin
  keys off Gradle's standard source sets, which AGP replaces with its variant
  model, so the build emits zero SCIP shards
  ([scip-java#177](https://github.com/scip-code/scip-java/issues/177)).
- **Kotlin indexing requires an exact Kotlin version match** — `scip-kotlinc`
  is compiled against one pinned Kotlin release (`SCIP_JAVA_KOTLIN` in
  `setup.sh`, currently `2.2.0`); its compiler-plugin API is internal and
  unstable even across patch releases, so any other version fails.
  Both cases are detected automatically from the indexer's own failure output
  and degrade to exit-0 `degraded` (SCIP skipped, syntax baseline still
  published) rather than failing outright.
- **Maven-built Java repos need bash >= 4.4 on macOS** — scip-java's generated
  `javac` wrapper (`#!/usr/bin/env bash`, `set -eu`) expands
  `"${LAUNCHER_ARGS[@]}"` unguarded, which errors on bash < 4.4; macOS ships
  only 3.2, so the build dies at `default-compile` with
  `LAUNCHER_ARGS[@]: unbound variable`. `setup.sh` works around it by linking
  first on `PATH` for the indexer. If no bash >= 4.4 is installed, indexing
  fails with the remedy rather than degrading — unlike the two cases above,
  this one is fixable (`brew install bash`).

## Configuration

**Data directory** (default `~/.jarvis`):
```bash
JARVIS_DATA_DIR=/custom/path jarvis index /path/to/repo
```

**Environment variables:**
- `JARVIS_DATA_DIR` — override default `~/.jarvis` for all indexes and registry
- `JARVIS_FALLBACK_SEARCH_ONLY` — **removed.** No longer read; jarvis prints a
  one-line note if your shell still exports it. Replaced by the reversible
  persisted `--scip`/`--no-scip` flags on `index`/`reindex`/`watch`.
- `JARVIS_EMBEDDING_QUERY_PREFIX` / `JARVIS_EMBEDDING_DOC_PREFIX` — override the
  query/document instruction prefix applied before embedding. Auto-detected for bge-m3,
  e5, and nomic-embed; set these if using a different model that needs one — `semanticSearch`
  warns when an unlisted model has no prefix configured.

`jarvis index --no-semantic` skips the semantic (vector) stage even when the
`semantic` extra is installed. The MCP `indexRepo` tool passes it by default,
so an agent tool call never implicitly downloads embedding weights.

## Agent skills

Three agent skills ship in the Claude Code plugin, under `plugin/skills/`:

- `jarvis-setup` — install, register, index, verify.
- `jarvis-use` — prefer jarvis for structural queries (find references, go-to-definition, hierarchy).
- `jarvis-issues` — file jarvis bugs/features via `gh`.

Install them, and register the MCP server, with:

```
/plugin marketplace add jarvis-intelligence/jarvis-index
/plugin install jarvis@jarvis
```

See [Quick start](#quick-start) above for the manual registration alternative.

## Standards

Blob decoding follows the [SCIP protocol](https://scip-code.org/docs.html):
`scip_pb2.py` is generated from `scip.proto` at `scip-code/scip` tag
**v0.9.0** (regenerated up from v0.7.0, which lacked the `typed_range` oneof
`scip-swift` requires), and occurrence/relationship blobs are decoded as real
`scip.Document` / `scip.SymbolInformation` messages.

The SQLite layer (`documents`, `chunks`, `global_symbols`, `mentions`,
`defn_enclosing_ranges`) is **not** part of that published spec — it is the
output shape of the experimental `scip expt-convert` sub-command, verified by
hand against a real index. Treat it as a moving target across `scip` releases.

## Tests

```bash
uv run pytest
```

Integration tests that shell out to the real `scip-python` / `scip` /
`zoekt-index` binaries are marked `integration`:

```bash
uv run pytest -m "not integration"   # unit only
uv run pytest -m integration         # real-binary pipeline
```

## Documentation

- [`docs/project-overview-pdr.md`](docs/project-overview-pdr.md) — scope, value prop, out-of-scope items
- [`docs/system-architecture.md`](docs/system-architecture.md) — architectural guarantees, storage layout, query paths
- [`docs/codebase-summary.md`](docs/codebase-summary.md) — module map, test coverage
- [`docs/code-standards.md`](docs/code-standards.md) — code patterns and conventions
- [`docs/project-roadmap.md`](docs/project-roadmap.md) — all phases complete, future ideas
- [`docs/dashboard.md`](docs/dashboard.md) — dashboard views, actions, security model, troubleshooting

All 4 planned phases are shipped — see
[`plans/0724-2316-jarvis-mcp-implementation/plan.md`](plans/0724-2316-jarvis-mcp-implementation/plan.md).

## License

[MIT](LICENSE)
