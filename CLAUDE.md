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

uv run codeintel index /path/to/repo [--slug name] [--scheme name] [--semantic-include path]
uv run codeintel list
uv run codeintel status <slug>
uv run codeintel reindex <slug>
uv run codeintel forget <slug>
uv run codeintel watch /path/to/repo [--debounce 5] [--scheme name] [--semantic-include path]   # foreground, not a daemon

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
  future SCIP proto version bumps localize to one file.
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

**Index pipeline** (`index_cli.py`, `index_repo()`): detect language by file-extension plurality
(ties broken by fixed priority `.ts→.tsx→.py→.java→.kt→.swift`; one language per repo, no
multi-language merge) → run the matching indexer → `scip expt-convert` → populate the graph →
`zoekt-index` → **atomic publish**: write the new versioned `index-<sha>.db`, and only once graph +
Zoekt both succeed, flip the `current` pointer file via `os.replace()`. A query already reading the
old file is never interrupted; a failure anywhere leaves the previous index live. Never mutate a
published `index-<sha>.db` in place — queries always open it `mode=ro&immutable=1`.

**Single-tenant hardcoding:** `config.py` pins `PROJECT = "_"` and `BRANCH = "_"`. The on-disk
`scip/_/<slug>/_/` path shape is an artifact of reusing the vendored `IndexConnectionCache`'s
`(project, repo, branch)` 3-tuple key unchanged — not a real multi-tenancy feature. Only `<slug>`
is a real, user-facing identifier.

**Error handling at the MCP boundary:** `server.py` catches all exceptions per-tool and returns
`{"error": "..."}` rather than raising — this keeps the stdio server alive across query bugs. Every
nav tool takes `repo` (the slug from `codeintel index`) plus a tool-specific `symbol` or `path`.

**Known gap:** `scip expt-convert` (SCIP v0.7.0) never populates `relationships`, so `typeHierarchy`
returns empty results on real indexes — not a bug in codeintel's query logic.

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
- Env vars are prefixed `CODEINTEL_` (currently `CODEINTEL_DATA_DIR`).
