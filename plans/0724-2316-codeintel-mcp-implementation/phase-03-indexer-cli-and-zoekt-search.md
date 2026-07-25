---
phase: 3
title: "Indexer CLI and Zoekt Search"
status: completed
effort: "~4h"
priority: P1
dependencies: [2]
---

# Phase 3: Indexer CLI and Zoekt Search

`$SOURCE_REPO` = `<source-project>`

## Overview

Replace the manual index recipe with `codeintel` CLI (full pipeline incl. zoekt-index + atomic swap + registry), and add searchCode backed by an embedded zoekt-webserver.

## Requirements

- Functional: `codeintel index|list|status|reindex|forget`; searchCode via MCP returns Zoekt hits scoped to a repo.
- Non-functional: atomic swap (readers never see half-written index); zoekt-webserver lazy-started, no orphan processes.

## Architecture

- `registry.py`: simplified from `$SOURCE_REPO/.../repository/registry_store.py` (446 LOC) — keep `repos` table (path, slug, language, commit_sha, last_indexed, status); DROP registration state machine + hosted-git fields. Stdlib sqlite3, schema created in code.
- `index_cli.py` (argparse, entry point `codeintel`): detect language by extension scan (`.ts/.tsx`→scip-typescript, `.py`→scip-python, `.rs`→rust-analyzer, `.java/.kt`→scip-java) → run indexer binary → `scip expt-convert` → `zoekt-index -index ~/.codeintel/.zoekt <repo>` → write `index-{sha}.db`, flip `current` pointer, delete old, update registry. Assert `scip` CLI convert schema v0.7.0 (`scip version` check).
- `search.py`: port `$SOURCE_REPO/.../service/search_service.py` (225 LOC — httpx client to zoekt-webserver JSON API `{"Q": ...}`, verified 2026-07-12). Add lifecycle mgr: lazy-spawn `zoekt-webserver -index ~/.codeintel/.zoekt -listen :PORT` on first searchCode, pidfile in data_dir, health check, atexit kill.
- `server.py`: register searchCode (tool #7).

## Related Code Files

- Create: `src/codeintel/registry.py`, `src/codeintel/index_cli.py`, `src/codeintel/search.py`
- Create: `tests/test_registry.py`, `tests/test_search.py` ← port 5 tests from `$SOURCE_REPO/tests/test_api_search_code.py` (httpx MockTransport for zoekt responses), `tests/test_index_cli.py` (integration: tiny fixture repo in `tests/fixtures/mini_py_repo/`, real scip-python + scip convert; atomic-swap test = pointer flip under an open reader connection)
- Modify: `src/codeintel/server.py`, `pyproject.toml` (`[project.scripts] codeintel = ...`), `README.md`

## Implementation Steps (TDD order)

1. `tests/test_registry.py` (CRUD + status transitions). **Red** → `registry.py`. **Green.**
2. Port `tests/test_search.py` w/ MockTransport. **Red** → port `search.py` + lifecycle mgr (lifecycle unit-tested with a fake binary script). **Green.**
3. `tests/test_index_cli.py`: language detection unit tests + end-to-end on `mini_py_repo` fixture (real binaries; mark `@pytest.mark.integration`). **Red** → `index_cli.py`. **Green.**
4. Wire searchCode into server; extend `tests/test_server_tools.py` (7 tools).
5. Acceptance: `codeintel index` on a TS validation repo + the source project; `codeintel list/status` sane; searchCode from Claude Code returns expected hits; `codeintel reindex` while querying in parallel → no errors (atomic swap proof); no zoekt orphan after server exit (`pgrep zoekt-webserver`).

## Success Criteria

- [ ] `uv run pytest` green incl. integration marks
- [ ] Full CLI lifecycle on both target repos
- [ ] searchCode hits verified from Claude Code
- [ ] Reindex-under-load: zero query downtime; no orphan zoekt processes

## Risk Assessment

- Zoekt shard naming/scoping per repo → shard per repo dir (`zoekt-index` default names by repo); filter results by repo in search.py (source already filters).
- Binary paths differ per machine → config.py resolves from PATH with env overrides (`CODEINTEL_ZOEKT_BIN` etc.).
- scip convert schema drift → hard version assert with clear error.
