---
phase: 2
title: "MCP Server and SCIP Navigation"
status: completed
effort: "~6h"
priority: P1
dependencies: [1]
---

# Phase 2: MCP Server and SCIP Navigation

`$POLARIS_CI` = `~/Projects/epost-workspace/polaris-ai-plaform/polaris-code-intelligence`

## Overview

Port the query layer (5 SCIP nav tools + getIndexStatus) and wrap it in an MCP stdio server. Ends with codeintel registered user-scope in Claude Code and hand-verified answers on polaris-ui (TS) + polaris-ci (Python).

## Requirements

- Functional: documentSymbols, goToDefinition, findReferences, callHierarchy, typeHierarchy, getIndexStatus callable via MCP; results match hand-verified ground truth on both target repos.
- Non-functional: server start <1s; index lookup via `current` pointer; no network.

## Architecture

- `query.py` = port of `$POLARIS_CI/.../service/query_service.py` (522 LOC, sync sqlite3). PRESERVE the `mentions.role` bitwise-AND filters and v0.7.0 schema notes verbatim. Replace Bitbucket-based freshness with local git: index SHA (registry/pointer metadata) vs `git rev-parse HEAD`.
- `config.py`: `data_dir` (default `~/.codeintel`, env `CODEINTEL_DATA_DIR`), repo→index path resolution `repos/{repo_slug}/index-{sha}.db` + `current` pointer file (one line: filename).
- `server.py`: MCP python SDK (`mcp[cli]`), stdio transport; thin tool wrappers → query.py; tools return dicts (Location/Entry dataclasses serialized).
- Interim indexing for acceptance (CLI arrives Phase 3): document exact manual commands in README:
  `scip-typescript index` / `scip-python index .` → `scip expt-convert --format sqlite index.scip` → place as `~/.codeintel/repos/<slug>/index-<sha>.db` + write `current`.

## Related Code Files

- Create: `src/codeintel/query.py` (port), `src/codeintel/config.py`, `src/codeintel/server.py`
- Create: `tests/test_query.py` ← port query-level assertions from `$POLARIS_CI/tests/test_api_navigation.py` (11 tests; drop FastAPI client layer, call query fns directly against synthetic index)
- Create: `tests/test_index_status.py` (temp git repo: index at SHA A, commit B → stale)
- Create: `tests/test_server_tools.py` (MCP in-memory client session: list_tools returns 6; call documentSymbols roundtrip)
- Modify: `README.md` (manual index recipe, `claude mcp add` line)

## Implementation Steps (TDD order)

1. Port `tests/test_query.py` from `test_api_navigation.py` — target the NEW module API (`codeintel.query`). **Red.**
2. Port `query.py`; adapt imports + freshness; wire `index_reader` + `config` path resolution. **Green.**
3. Write `tests/test_index_status.py` (git fixture via `tmp_path`). **Red** → implement getIndexStatus. **Green.**
4. Write `tests/test_server_tools.py` using mcp SDK in-memory transport. **Red** → implement `server.py` (6 tools). **Green.**
5. Acceptance (manual, documented in README):
   - Index polaris-ui (verify it is TS first; fallback: any TS repo in epost workspace) and polaris-ci per interim recipe.
   - `claude mcp add codeintel --scope user -- uv --directory ~/Projects/codeintel run codeintel-server`
   - From Claude Code: goToDefinition + findReferences on 3 known symbols per repo; hand-verify locations. callHierarchy on 1 known function. typeHierarchy on Python class (TS expected empty — document).

## Success Criteria

- [ ] `uv run pytest` green (query, status, server suites)
- [ ] 6 tools listed via MCP; nav answers hand-verified on both repos
- [ ] getIndexStatus: fresh → commit → stale transition demonstrated
- [ ] Registered user-scope; works from a different project directory

## Risk Assessment

- query_service hidden deps on polaris models/middleware → trimmed `models.py` from Phase 1; if more surface, trim again — do NOT import FastAPI.
- typeHierarchy empty on TS (known converter limitation) → assert documented behavior, not failure.
- polaris-ui may not be TS → verify before acceptance; swap target if needed (note in plan.md if changed).
