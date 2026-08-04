# Brainstorm Report — jarvis Phase 0–3 Implementation

Date: 2026-07-24 | Mode: --research | Status: design approved by user

## Problem Statement

Implement `jarvis` per `<source-plan>`: personal, local-first, cloud-ready code intelligence MCP server. Greenfield repo (`~/Projects/jarvis` — only docs/assets exist). Vendor core IP from `the source project`.

## Scout Findings (load-bearing)

- **Source repo**: `<source-project>/src/the_source_package/`
  - Vendorable: `scip_pb2.py` (111 LOC), `service/scip_decoder.py` (279), `service/index_reader.py` (160), `service/query_service.py` (522), `service/search_service.py` (225), `repository/registry_store.py` (446), `service/graph_extraction.py` (209), `repository/graph_store.py` (303). Tests + fixtures in `tests/`.
- **Core uses stdlib `sqlite3` (sync) — NOT SQLAlchemy/aiosqlite.** Plan's dep list over-specified; drop both deps, port verbatim.
- `query_service.py` battle-tested: verified ground-truth notes (`mentions.role` bitmask AND-filter, scip expt-convert v0.7.0 schema).
- `search_service.py` already proxies zoekt-webserver JSON API (`{"Q": ...}`), verified against live server 2026-07-12.
- **Toolchain 100% installed**: uv, ripgrep, scip CLI (~/go/bin), zoekt + zoekt-index + zoekt-git-index + zoekt-webserver (~/go/bin), scip-typescript + scip-python (npm global), Python 3.13 (homebrew).
- Prior web research (this session): stdio-local/Streamable-HTTP-remote validated; SSE transport deprecated; Litestream over Postgres for cloud registry.

## Decisions (user-selected)

| Decision | Choice | Notes |
|---|---|---|
| Scope round 1 | **Phase 0–3** (full: nav + search + CLI + graph/blastRadius + watcher) | Against initial P0–2 recommendation; accepted — all portable code exists, phases ship incrementally |
| Search backend | **Zoekt embedded** | Initially recommended ripgrep; withdrawn after scout confirmed full zoekt toolchain installed + verified `search_service.py` port path |
| Validation targets | **a TS validation repo (TS) + the source project (Python)** | Proves both indexers + language auto-detection |
| MCP registration | **User scope** (`claude mcp add --scope user`) | Personal multi-repo tool |
| Deps | `mcp[cli]`, `protobuf`, `zstandard`, `httpx`, `watchdog` (P3) | No SQLAlchemy/aiosqlite; argparse CLI (stdlib) |

## Evaluated Approaches

1. **Phase 0+1 only (~8h)** — fastest agent value; rejected: manual indexing dance per reindex.
2. **Phase 0–2 (~12h)** — coherent MVP; initial recommendation; superseded by user choice.
3. **Phase 0–3 (~16h, CHOSEN)** — full plan incl. graph/blastRadius/watcher. Risk (graph before daily-use proof) accepted; mitigated by shippable phase boundaries.

Search: ripgrep (~50 LOC, no state) vs Zoekt (chosen: installed + verified port path) vs skip (rejected: Cursor benefit).

## Final Design

Module map (target ← source):

| `src/jarvis/` | Source | Change |
|---|---|---|
| `scip_pb2.py`, `scip_decoder.py`, `index_reader.py` | same | vendored unchanged |
| `query.py` | `query_service.py` | hosted-git freshness → `git rev-parse HEAD` |
| `search.py` | `search_service.py` | + zoekt-webserver subprocess lifecycle (lazy start, pidfile, health check) |
| `registry.py` | `registry_store.py` | drop state machine; keep repos table (path, language, SHA, last_indexed, status) |
| `graph.py` | `graph_extraction.py` + `graph_store.py` | port for blastRadius (2-hop BFS) |
| `server.py` | new | MCP stdio, 8 tools: searchCode, documentSymbols, goToDefinition, findReferences, callHierarchy, typeHierarchy, getIndexStatus, blastRadius |
| `config.py` | new | data_dir (~/.jarvis), zoekt port/paths, env overrides |
| `index_cli.py` | new | argparse: index/list/status/reindex/forget; pipeline: detect lang → scip-* → scip expt-convert → zoekt-index → atomic pointer swap → registry update |

Storage: `~/.jarvis/repos/{project}/{repo}/index-{sha}.db` + `current` pointer; `registry.db`; `.zoekt/` shards.

## Phases

- **P0 (~2h)**: repo scaffold, pyproject (uv, py3.12+), vendor 4 modules, decoder unit test green (port fixture from the source project tests).
- **P1 (~6h)**: server.py + query.py + getIndexStatus. Acceptance: manual-index a TS validation repo + the source project; goToDefinition/findReferences hand-verified on known symbols; registered user-scope in Claude Code.
- **P2 (~4h)**: index_cli + registry + search.py + zoekt pipeline. Acceptance: `jarvis index .` end-to-end both repos; searchCode returns expected hits; atomic swap (no downtime on reindex).
- **P3 (~4h)**: graph.py + blastRadius + watchdog auto-reindex (5s debounce). Acceptance: blastRadius 2-hop correct on known dep; edit→auto-reindex→fresh status.

## Risks

| Risk | Mitigation |
|---|---|
| typeHierarchy empty for TS | Known v0.7.0 converter limitation — document in README, not a bug |
| zoekt-webserver orphans | pidfile + startup health check + kill-on-exit |
| scip schema drift | pin/assert scip CLI version v0.7.0 in index_cli |
| Python 3.9 system default | uv-managed 3.12+ via pyproject requires-python |
| Monorepo multi-lang merge | defer detail to plan phase; per-language index + merge |

## Success Metrics

From Claude Code (user scope), on both target repos: all 8 tools return correct results (nav hand-verified); full CLI lifecycle; stale detection after new commit; reindex with zero query downtime.

## Next Steps

1. `/ck:plan` from this report (greenfield → default mode, not --tdd).
2. Implement per phases; each phase gate = acceptance criteria above.
3. Cloud (Phase 4) explicitly OUT of scope this round; revisit after daily use. Use Streamable HTTP (not SSE) + Litestream when it comes.

## Unresolved Questions

- a TS validation repo language mix unconfirmed (assumed TS) — verify at P1 acceptance; fallback: any TS repo in workspace.
- Zoekt shard scope: per-repo shards vs single shard dir — decide in plan (lean per-repo, matches index.db layout).
