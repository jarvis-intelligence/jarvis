# codeintel

<!-- mcp-name: io.github.phuongddx/codeintel -->

**Local-first code intelligence for coding agents.** Precomputed SCIP navigation
(go-to-definition, find-references, call hierarchy, document symbols), Zoekt
lexical search, cross-repo blast radius, and semantic search — exposed as MCP
tools to Claude Code, Cursor, or any MCP client.

Runs as a single stdio process reading local SQLite files. **No server, no auth,
no network, nothing leaves your machine.**

```bash
# 1. External indexer binaries (scip, zoekt, per-language indexers)
curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh

# 2. codeintel itself
uv tool install codeintel-mcp

# 3. Index a repo (slug defaults to the directory name)
codeintel index /path/to/your/repo

# 4. Register with Claude Code
claude mcp add codeintel --scope user -- codeintel-server
```

That's it — ask your agent "find all references to `AuthService`" and it will
call `findReferences` instead of grepping.

<details>
<summary>Other MCP clients (Cursor, Claude Desktop, any stdio client)</summary>

```json
{
  "mcpServers": {
    "codeintel": {
      "command": "codeintel-server"
    }
  }
}
```

If your client can't find `codeintel-server` on `PATH` (GUI apps often don't
inherit your shell's), use the absolute path from `which codeintel-server`.
</details>

<details>
<summary>Running from a clone instead</summary>

```bash
git clone https://github.com/phuongddx/codeintel && cd codeintel
uv sync
claude mcp add codeintel --scope user -- uv --directory "$(pwd)" run codeintel-server
```
</details>

## MCP tools

| Tool | What it does |
|------|--------------|
| `goToDefinition` | Resolve a symbol to its defining file and range |
| `findReferences` | Every occurrence of a symbol across the indexed repo |
| `callHierarchy` | Incoming/outgoing calls for a symbol |
| `documentSymbols` | Outline of every symbol defined in one file |
| `searchCode` | Zoekt lexical/regex search, optionally filtered to one repo |
| `semanticSearch` | Natural-language search — vector hits fused with Zoekt via reciprocal rank fusion |
| `blastRadius` | Which *other* indexed repos depend on a package, up to 2 hops |
| `getIndexStatus` | Published commit, freshness, staleness vs. a working tree |
| `typeHierarchy` | Supertypes/subtypes — **currently non-functional**, see [limitations](#known-upstream-limitations) |

Every nav tool takes `repo` (the slug from `codeintel index`) plus a
tool-specific `symbol` or `path`. All tools report failure the same way — a
`{"error": "..."}` payload rather than a transport-level error, so a query bug
never kills the stdio server.

## Requirements and limits

Read this before installing — codeintel is deliberately narrow.

- **macOS and Linux only.** Windows is not supported.
- **One language per repo.** Language is detected by extension plurality across
  git-tracked files; a polyglot monorepo gets indexed as whichever language has
  the most files. Multi-language merge is out of scope. Override with
  `--language`.
- **Four language families:** TypeScript/TSX, Python, Java/Kotlin, Swift. **Rust,
  Go, C/C++, C#, Ruby, and PHP are not supported.**
- **Navigation and search only — codeintel never edits code.** If you want an
  agent that can perform semantic renames and refactors, you want
  [Serena](https://github.com/oraios/serena); the two are complementary.
- **Indexing is a separate, explicit step.** Nothing is live-analyzed. Run
  `codeintel index` (or `codeintel watch`) to publish an index before querying.
- **Requires external binaries** that `setup.sh` installs:

  | Purpose | Binary | Source |
  |---------|--------|--------|
  | SCIP → SQLite conversion | `scip` | prebuilt, pinned `v0.9.0` (**minimum** — older versions silently drop occurrence ranges) |
  | Lexical search | `zoekt-index` · `zoekt-webserver` | cross-compiled by [our CI](.github/workflows/build-zoekt.yml) — upstream publishes no binaries |
  | TypeScript indexing | `scip-typescript` | `npm install -g` |
  | Python indexing | `scip-python` | `npm install -g` |
  | Swift indexing | `scip-swift` | prebuilt, **macOS arm64 only** |
  | Java/Kotlin indexing | `scip-java` | detect-only — Docker image, asks before pulling |

  Options: `--only <name>` to install one dependency, `--force` to reinstall,
  `--help` for usage. Re-running is safe: anything already present is skipped.

Optional extras:

```bash
uv tool install "codeintel-mcp[watch]"      # + watchdog, for `codeintel watch`
uv tool install "codeintel-mcp[semantic]"   # + lancedb/sentence-transformers/tree-sitter, for semanticSearch
```

## Why it's built this way

**Overview** — client, server, the 3 engines (Query / Search / Graph), and storage:

![codeintel overview](docs/assets/codeintel-architecture.png)

**Index pipeline & package graph** — how `codeintel index` builds and publishes
an index, how the package dependency graph feeds `blastRadius`, and how
`codeintel watch` debounces a burst of edits into one reindex:

![codeintel index pipeline and package graph](docs/assets/codeintel-system-architecture.png)

Three things worth reading the diagrams for:

- **The runtime path never writes.** Queries open a published `index-<sha>.db`
  read-only (`mode=ro&immutable=1`). Index files are never mutated in place.
- **Publishing is atomic.** A reindex writes a new versioned `.db`, populates
  the package graph, and runs `zoekt-index` — only once *all* of that succeeds
  does `os.replace` (POSIX `rename(2)`) flip the small `current` pointer. A
  query already reading the old file keeps working; there is no downtime
  window, and a failure anywhere leaves the previously published index live.
- **The package graph is rebuilt, not accumulated.** Each reindex clears that
  repo's own outgoing edges before recomputing them, so a removed dependency's
  edge is retracted — `blastRadius` always reflects each repo's *last* index
  run.

Editable sources:
[`docs/assets/codeintel-architecture.excalidraw`](docs/assets/codeintel-architecture.excalidraw) ·
[`docs/assets/codeintel-system-architecture.excalidraw`](docs/assets/codeintel-system-architecture.excalidraw)

Core query/search logic is ported from an internal reference implementation;
the enterprise shell (FastAPI, Postgres, hosted-git auth, Cloud Build) is
dropped in favor of a single stdio process reading local SQLite files.

## Indexing a repo

```bash
codeintel index /path/to/your/repo            # slug defaults to the directory name
codeintel index /path/to/your/repo --slug foo # or pick one explicitly
codeintel index /path/to/your/repo --scheme MyScheme # Swift repo with an ambiguous Xcode scheme
codeintel index /path/to/your/repo --language python # force the language instead of detecting it from git-tracked files
codeintel index /path/to/your/repo --semantic-include vendor/generated # force-include a path the generated-file filter would otherwise skip
codeintel list
codeintel status foo
codeintel reindex foo
codeintel forget foo
```

`status` (as shown by both `list` and `status`) is usually `indexed` or
`failed`, but can also be `partial`: the index published real symbols but no
navigable positions (an indexer/converter bug) — check the stderr warning
from `codeintel index` for details.

`--semantic-include` is repeatable — pass it once per path prefix to
force-include several. Like `--scheme` and `--language`, once set there is no flag to clear
it; change it by re-running `codeintel index` with the new value(s).

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

The pipeline then runs: chosen indexer → `scip expt-convert` → populate the
package dependency graph (`packages`/`edges` tables in `registry.db`) →
`zoekt-index` into `~/.codeintel/.zoekt` → copy to
`~/.codeintel/scip/_/<slug>/_/index-<sha>.db` → atomic `current` pointer flip →
registry update.

> The `scip/_/<slug>/_/` path shape reuses the vendored `IndexConnectionCache`'s
> `(project, repo, branch)` 3-tuple layout with the outer two pinned to `_` (see
> [`src/codeintel/config.py`](src/codeintel/config.py)). It is not a user-facing
> contract — only `<slug>` matters when calling tools.

Swift indexing works end-to-end. It requires `scip >= v0.9.0`: older converters
cannot read scip.proto's `typed_range` oneof, which is the only range encoding
`scip-swift` emits, and silently produce an index with no navigable positions.
`codeintel index` refuses an older `scip` rather than publishing one.

Indexing a Swift repo with code-signed app-extension targets additionally requires
`scip-swift >= v0.1.2`: earlier versions pass no code-signing overrides to `xcodebuild`, which
then fails provisioning for every signed target before compiling anything. Because `setup.sh`
skips any dependency that is merely *present*, an existing install is **not** upgraded by
re-running it — use `sh ./setup.sh --only scip-swift --force`.

## Watching a repo (auto-reindex)

```bash
codeintel watch /path/to/your/repo             # debounce defaults to 5s
codeintel watch /path/to/your/repo --debounce 3
codeintel watch /path/to/your/repo --scheme MyScheme
codeintel watch /path/to/your/repo --language python
```

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
  it lazy-spawns an embedded `zoekt-webserver` (pidfile'd so a second codeintel
  process reuses it instead of spawning a duplicate; killed on clean exit via
  `atexit`).
- **`blastRadius`** takes `repo` plus `symbol_or_package` (e.g. `"npm:@scope/
  name"`, the same `"{manager}:{name}"` string `codeintel index` derives from
  each repo's SCIP symbols). Returns every other indexed repo whose package
  depends on it, up to 2 hops, each tagged with its hop distance. The package
  graph has no per-node timestamp, so `freshness` is always `"unknown"` here —
  an honest limitation of the schema, not a bug. Cross-repo edges resolve by
  exact package name against whatever has *already* been indexed: index the
  dependency first, or re-run `codeintel index`/`reindex` after indexing it,
  for an edge to appear. Each reindex retracts that repo's own stale edges
  before recomputing them, so a removed dependency's edge disappears too —
  the graph always reflects each repo's *last* index run, not an
  accumulation of every run it's ever had.
- **`semanticSearch`** takes `repo` plus a natural-language `query`. Requires the
  optional `semantic` extra. Results fuse a LanceDB vector search over
  tree-sitter-chunked code with `searchCode`'s Zoekt hits via reciprocal rank
  fusion. Raises a clear error if the repo has never been indexed with the extra
  installed (`codeintel reindex <slug>` after installing it builds the missing
  table); indexing itself is non-fatal — a failure there never blocks the rest
  of `codeintel index`. Semantic indexing also respects `.gitignore` (on top of
  the hardcoded ignore-directory list) and skips any file over 1 MB, in addition
  to the existing generated-file banner/long-line detection —
  `--semantic-include` overrides all three.

### Known upstream limitations

These are real behaviors of `scip expt-convert` (as of v0.9.0), not codeintel bugs:

- **`typeHierarchy` returns an explicit `{"error": ...}`**, not empty arrays, on
  every real-world index — the converter declares `global_symbols.relationships`
  in its schema but never writes it. An empty result would wrongly assert "no
  supertypes"; the error says "cannot tell" instead. Reported upstream:
  [scip-code/scip#464](https://github.com/scip-code/scip/issues/464), fixed by
  [scip-code/scip#465](https://github.com/scip-code/scip/pull/465) (open, CI green).
- **`displayName` / `kind` are often `null`** for symbols the converter only
  ever sees as bare occurrences (no defining `SymbolInformation` was indexed).
- **`searchCode`'s `repo` filter matches Zoekt's own repository name**, which
  `codeintel index` now names after the slug via `zoekt-index -meta` — so this
  no longer diverges for repos indexed with current code. Shards published by
  an older codeintel still carry their old directory-derived name until you
  `codeintel reindex <slug>`.

## Configuration

**Data directory** (default `~/.codeintel`):
```bash
CODEINTEL_DATA_DIR=/custom/path codeintel index /path/to/repo
```

**Environment variables:**
- `CODEINTEL_DATA_DIR` — override default `~/.codeintel` for all indexes and registry
- `CODEINTEL_EMBEDDING_QUERY_PREFIX` / `CODEINTEL_EMBEDDING_DOC_PREFIX` — override the
  query/document instruction prefix applied before embedding. Auto-detected for bge-m3,
  e5, and nomic-embed; set these if using a different model that needs one — `semanticSearch`
  warns when an unlisted model has no prefix configured.

## Agent skills

Three agent skills in `.claude/skills/` help onboard and use codeintel:

- `codeintel-setup` — install, register, index, verify.
- `codeintel-use` — prefer codeintel for structural queries (find references, go-to-definition, hierarchy).
- `codeintel-issues` — file codeintel bugs/features via `gh`.

To load them in a ZCode agent, link them once:

```bash
uv run python scripts/link_skills.py
```

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

All 4 planned phases are shipped — see
[`plans/0724-2316-codeintel-mcp-implementation/plan.md`](plans/0724-2316-codeintel-mcp-implementation/plan.md).

## License

[MIT](LICENSE)
