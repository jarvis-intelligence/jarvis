---
phase: 4
title: "Graph Blast Radius and Auto-Reindex"
status: completed
effort: "~4h"
priority: P2
dependencies: [3]
---

# Phase 4: Graph Blast Radius and Auto-Reindex

`$SOURCE_REPO` = `<source-project>`

## Overview

Port the package dependency graph + blastRadius (2-hop BFS), and add watchdog-based auto-reindex. Completes the 8-tool surface.

## Requirements

- Functional: blastRadius returns 2-hop dependents for a package/symbol; file change → debounced (5s) auto-reindex → fresh status.
- Non-functional: watcher optional (`jarvis watch [path]` command), not a daemon requirement; graph stored in registry.db (single RW database, keeps index.db immutable).

## Architecture

- `graph.py`: port `$SOURCE_REPO/.../service/graph_extraction.py` (209 LOC — extracts package deps during indexing) + `$SOURCE_REPO/.../repository/graph_store.py` (303 LOC — nodes/edges tables + 2-hop BFS query). Adapt storage to registry.db; strip Postgres/Alembic assumptions if any (source targets SQLAlchemy-free? verify — if graph_store uses SQLAlchemy, rewrite its ~10 queries on stdlib sqlite3 keeping SQL text).
- `index_cli.py`: pipeline gains graph-extraction step post-convert; `jarvis watch` subcommand — watchdog observer, 5s debounce per repo, triggers reindex.
- `server.py`: register blastRadius (tool #8).

## Related Code Files

- Create: `src/jarvis/graph.py`
- Create: `tests/test_graph.py` (port graph tests from `$SOURCE_REPO/tests/` if present — check `test_api_dashboard.py`/graph tests; else new: synthetic 3-package chain A→B→C, blastRadius(C) = {B 1-hop, A 2-hop}), `tests/test_watch.py` (debounce logic unit test — fake clock, no real watcher)
- Modify: `src/jarvis/index_cli.py`, `src/jarvis/server.py`, `pyproject.toml` (add `watchdog`), `README.md`

## Implementation Steps (TDD order)

1. Check source for existing graph tests; port or write `tests/test_graph.py` (extraction from synthetic index + BFS assertions). **Red** → port `graph.py`. **Green.**
2. `tests/test_watch.py` (debounce coalesces burst edits into one trigger). **Red** → watch subcommand. **Green.**
3. Wire blastRadius into server; extend `tests/test_server_tools.py` (8 tools).
4. Acceptance: blastRadius on the source project internal package with known dependents — hand-verify 2-hop set; `jarvis watch` + edit file → single reindex after ~5s → `getIndexStatus` fresh.

## Success Criteria

- [ ] `uv run pytest` green (full suite)
- [ ] blastRadius hand-verified on known dependency chain
- [ ] Burst of file edits → exactly one reindex; status returns fresh
- [ ] 8 tools listed via MCP from Claude Code

## Risk Assessment

- graph_store may use SQLAlchemy (unverified) → mitigation in Architecture: keep SQL text, swap execution to sqlite3; scope creep capped at ~10 queries.
- Watcher reliability across editors (atomic saves, temp files) → debounce + ignore patterns (`.git/`, `node_modules/`, dotfiles).
- BFS correctness depends on extraction coverage per language → acceptance uses Python repo (scip-python emits package info reliably); TS graph best-effort, documented.
