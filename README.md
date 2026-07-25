# codeintel

Personal, local-first code intelligence MCP server. SCIP-backed navigation
(go-to-definition, find-references, call/type hierarchy, document symbols) +
Zoekt-backed lexical search, exposed as MCP tools to Claude Code, Cursor, or
any MCP client — over stdio, no server, no auth, no network.

Ported from `polaris-code-intelligence`'s query/search/graph logic; the
enterprise shell (FastAPI, Postgres, Bitbucket auth, Cloud Build) is dropped
in favor of a single stdio process reading local SQLite files.

## Status

Work in progress — implementing per `plans/0724-2316-codeintel-mcp-implementation/plan.md`.

## Install

```bash
uv sync
```

Requires on `PATH`: a SCIP indexer for your language(s) — `scip-typescript`,
`scip-python`, `rust-analyzer`, or `scip-java` — plus the `scip` CLI (for
`scip expt-convert`) and, for search, `zoekt-index` / `zoekt-webserver`.

## Indexing a repo

```bash
codeintel index /path/to/your/repo            # slug defaults to the directory name
codeintel index /path/to/your/repo --slug foo # or pick one explicitly
codeintel list
codeintel status foo
codeintel reindex foo
codeintel forget foo
```

This runs the full pipeline: detect language (by extension count: `.ts`/
`.tsx` → scip-typescript, `.py` → scip-python, `.java`/`.kt` → scip-java) →
run that indexer → `scip expt-convert` → copy into
`~/.codeintel/scip/_/<slug>/_/index-<sha>.db` → atomically flip the
`current` pointer (write-temp-then-rename) → `zoekt-index` into
`~/.codeintel/.zoekt` → update the registry (`~/.codeintel/registry.db`).
Requires `scip-typescript`/`scip-python`/`scip-java`, the `scip` CLI, and
`zoekt-index` on `PATH`.

(The `scip/_/.../_/` path shape reuses `IndexConnectionCache`'s vendored
3-tuple layout with pinned constants — see `src/codeintel/config.py` — not a
user-facing contract; only the `<slug>` segment matters when calling tools.)

## Register with Claude Code

```bash
claude mcp add codeintel --scope user -- uv --directory /path/to/codeintel run codeintel-server
```

## MCP tools

`documentSymbols` · `goToDefinition` · `findReferences` · `callHierarchy` ·
`typeHierarchy` · `getIndexStatus` · `searchCode`

Phase 4 (planned): `blastRadius`.

Every nav tool takes `repo` (the slug from `codeintel index`) plus a
tool-specific `symbol` or `path`. `getIndexStatus` also takes an optional
`repo_path` (the repo's local git working directory) to compare the
published commit against `git rev-parse HEAD` — omitted, freshness is
reported without a staleness check. `searchCode` takes `query` and an
optional `repo` filter; on first call it lazy-spawns an embedded
`zoekt-webserver` (killed on process exit via `atexit`, pidfile'd so a
second codeintel process reuses it instead of spawning a duplicate).

**searchCode's `repo` filter matches Zoekt's own repository name** — the
basename of the directory you ran `codeintel index` against — which is
usually but not necessarily the same as codeintel's slug (`--slug` can
diverge from the directory name). If a `repo`-scoped search comes back
empty unexpectedly, try it unscoped first to confirm the name.

`typeHierarchy` returns empty on real-world indexes today (TS and Python
alike) — `scip expt-convert` v0.7.0 never populates `global_symbols.kind`/
`display_name`/`relationships` in practice, a converter limitation, not a
codeintel bug.

## Architecture

See `docs/assets/codeintel-system-architecture.png`.
