---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# Codebase Concerns

**Analysis Date:** 2026-08-21

## Tech Debt

**No lint or type-check configuration committed:**
- Severity: **medium**
- Issue: No `[tool.ruff]`, `mypy`, `pyright`, `pylint`, `black`, or `isort` configuration exists anywhere in the repo. The CI workflow (``.github/workflows/test.yml``) explicitly documents why no lint step runs (pre-existing violations would block unrelated PRs). No static analysis runs on CI at all — only `pytest` and `check_versions.py`.
- Files: `pyproject.toml`, `.github/workflows/test.yml`
- Impact: Style inconsistencies and type errors accumulate undetected. New contributors have no automated style enforcement.
- Fix approach: Add `[tool.ruff]` with per-rule baseline exclusions, run `ruff check --fix`, then gate on `ruff check` in CI. Same for `pyright`/`mypy` if desired.

**Vendored protobuf gencode locked to fork SCIP commit:**
- Severity: **low**
- Issue: `src/jarvis/scip_pb2.py` is generated from a forked `phuongddx/scip` at a specific commit (not the upstream `scip-code/scip` tag). The fork exists because upstream through v0.9.0 never populates `global_symbols.relationships` (scip-code/scip#464). The gencode must be regenerated if the proto definition changes.
- Files: `src/jarvis/scip_pb2.py:1-33`, `SCIP_COMMIT`, `setup.sh:30-39`
- Impact: If upstream merges the fix and cuts a release, this fork dependency becomes unnecessary drift. The dual-maintenance burden is small but perpetual.
- Fix approach: Repoint `setup.sh` at upstream `scip-code/scip` once #465 ships in a release; delete `build-scip.yml` and `SCIP_COMMIT`.

**Fork-pinned `SCIP_COMMIT` / `ZOEKT_COMMIT` dual-file coupling:**
- Severity: **medium**
- Issue: `SCIP_COMMIT` and `ZOEKT_COMMIT` (repo-root files) must stay in sync with `SCIP_COMMIT_PIN` and `ZOEKT_COMMIT_PIN` inside `setup.sh`. Tests enforce both pairs (`tests/test_setup_sh.py`), but the coupling is fragile — editing one file without the other is a natural mistake.
- Files: `SCIP_COMMIT`, `ZOEKT_COMMIT`, `setup.sh:30-34`
- Impact: A desync means CI builds a different binary version than `setup.sh` installs for users, producing index schema mismatches.
- Fix approach: Derive the pin in `setup.sh` from the repo-root file at source time, or consolidate into a single source of truth.

**External-binary dependency chain on PATH:**
- Severity: **medium**
- Issue: Beyond the Python package, jarvis requires one language-specific SCIP indexer (`scip-typescript`, `scip-python`, `scip-java`, or `scip-swift`), `scip` (for `scip expt-convert`), `zoekt-index`, and `zoekt-webserver` — all installed to a `~/.jarvis/bin/` directory via `setup.sh` and appended to the user's shell RC. None are Python dependencies. A GUI-launched MCP server (Claude Desktop, Cursor) inherits a stripped environment where this directory may not be on PATH.
- Files: `setup.sh`, `src/jarvis/index_cli.py:101-119` (`_LANGUAGE_INDEXERS`), `src/jarvis/search.py:134-136` (binary resolution)
- Impact: Indexing or search silently fails with `FileNotFoundError`/`OSError` if binaries are missing from PATH. The error messages are descriptive but require the user to understand the bootstrap flow.
- Fix approach: The `config.py`/`index_cli.py` layer could resolve binaries from `JARVIS_BIN_DIR` explicitly (similar to how `_java_indexer_env` resolves the bash shim) rather than relying on PATH.

**Single-tenant hardcoding artifacts in path layout:**
- Severity: **low**
- Issue: `config.py` pins `PROJECT = "_"` and `BRANCH = "_"`, producing on-disk paths like `~/.jarvis/scip/_/{slug}/_/`. This is an artifact of reusing the vendored `IndexConnectionCache` path shape unchanged.
- Files: `src/jarvis/config.py:26-27`
- Impact: No functional problem, but the `scip/_/<slug>/_/` nesting is confusing for anyone inspecting the data directory. A future multi-tenant or multi-branch feature would require changing this.
- Fix approach: Cosmetic only; document or simplify the path layout when the vendored cache is replaced.

## Known Bugs

**Registry status reports `failed` when a valid SCIP index is actually live:**
- Severity: **high**
- Issue: In `index_repo()`, the publish ordering is: SCIP indexer → `scip expt-convert` → copy to versioned db → `_publish_atomically` (pointer flip) → `zoekt-index` → registry upsert. If `zoekt-index` fails, the `except Exception` handler at `src/jarvis/index_cli.py:887-889` sets registry status to `"failed"` — but the pointer has *already been flipped* to the new, fully valid SCIP index. Navigation tools (`goToDefinition`, `findReferences`, etc.) will silently serve the new, correct data even though `getIndexStatus` reports `status: "failed"`.
- Files: `src/jarvis/index_cli.py:825-890`
- Trigger: `zoekt-git-index` fails (e.g., out of disk, corrupt git object) after the SCIP publish succeeds.
- Workaround: Re-run `jarvis reindex <slug>`.
- Fix approach: Move `_publish_atomically` after `zoekt-index` succeeds (the SCIP db is only read through the pointer, so reordering is safe), or use a finer-grained status like `"indexed-search-stale"` so status reflects reality.

## Security Considerations

**`trust_remote_code=True` in embedding model loading:**
- Severity: **high**
- Issue: `src/jarvis/embeddings.py:106-107` loads `SentenceTransformer` with `trust_remote_code=True`, which executes arbitrary Python code from the model repository (Hugging Face Hub). If the model or its revision is compromised, arbitrary code runs at load time.
- Files: `src/jarvis/embeddings.py:106-107`
- Current mitigation: The default model (`BAAI/bge-m3`) is pinned to a specific revision (`5619a9f61b028005a4858fdac845db406aefb181`), reducing the attack surface to that specific revision. Custom models via `JARVIS_EMBEDDING_MODEL` bypass this pinning.
- Recommendations: Audit whether `trust_remote_code=True` is actually needed for the pinned model (many sentence-transformers models work without it). If not, remove it. If required, document the risk in README.

**`_ensure_column` uses f-string SQL (controlled inputs only):**
- Severity: **low**
- Issue: `src/jarvis/registry.py:53` uses `conn.execute(f"ALTER TABLE repos ADD COLUMN {name} {decl}")` — an f-string in SQL. Both `name` and `decl` are string literals passed only from `__init__` at `src/jarvis/registry.py:115-120`, never from user input.
- Files: `src/jarvis/registry.py:43-56`
- Current mitigation: Callers are exclusively hardcoded within the same file. No external input reaches these parameters.
- Recommendations: No immediate action needed. If `_ensure_column` is ever made public or called with dynamic input, switch to a parameterized approach or validate inputs.

**MCP stdio server has no authentication or input sanitization boundary:**
- Severity: **medium**
- Issue: `server.py` exposes 9 tools over stdio MCP. The `repo` and `symbol` parameters pass directly into SQL queries (parameterized) and file-system paths. A malicious MCP client can request any repo slug, which maps to a path under `~/.jarvis/`. The `repo_slug()` function in `config.py` rejects `.` and `..` but does not guard against absolute paths or path traversal in `repo_path` parameters passed to `getIndexStatus`.
- Files: `src/jarvis/server.py:175-394`, `src/jarvis/config.py:56-65`
- Current mitigation: Single-user, local-first design — the stdio server is spawned by the user's own MCP client. No network exposure.
- Recommendations: No action needed for the local-first use case. If the server is ever exposed over a network transport, add authentication and validate all path parameters.

**`slug` parameter in MCP tools is not validated against the registry:**
- Severity: **low**
- Issue: MCP tool functions accept `repo` (a slug string) and pass it directly to `QueryService` and `ZoektLifecycle`. A nonexistent slug raises `IndexNotFoundError` or returns an error dict — correct behavior, but the slug is not validated for safe characters before being used in path construction.
- Files: `src/jarvis/server.py:175-394`
- Current mitigation: `repo_slug()` in `config.py` sanitizes slug characters on write (index time), so slugs in the registry are already safe.
- Recommendations: Add a lightweight character validation on the read path for defense in depth.

## Performance Bottlenecks

**`symbols.py` global name-map cache is not thread-safe:**
- Severity: **medium**
- Issue: `src/jarvis/symbols.py:218-237` maintains a module-level `_name_maps: OrderedDict` cache accessed without any lock. `IndexConnectionCache` (the analogous cache in `index_reader.py`) correctly uses `threading.Lock()`, but the symbol name-map cache does not. The MCP server is single-threaded today (FastMCP stdio), so this is not currently exploitable, but `check_same_thread=False` on the SQLite connections in `index_reader.py:144` is explicitly for thread-safety, indicating threading is an anticipated concern.
- Files: `src/jarvis/symbols.py:218-237`, `src/jarvis/index_reader.py:144`
- Cause: The cache was modeled after `IndexConnectionCache` but the lock was omitted, likely because the server is currently single-threaded.
- Improvement path: Add a `threading.Lock()` around the `_name_maps` reads/writes, matching `IndexConnectionCache`'s pattern.

**`default_model()` in `embeddings.py` is not thread-safe:**
- Severity: **medium**
- Issue: `src/jarvis/embeddings.py:153-156` has a module-level `_default: EmbeddingModel | None = None` with a check-then-assign pattern (`if _default is None: _default = EmbeddingModel()`). Two concurrent calls could race and create two instances, though this is benign since `EmbeddingModel` is stateless until `_load()`.
- Files: `src/jarvis/embeddings.py:148-156`
- Cause: Simple lazy-init singleton without lock.
- Improvement path: Add a `threading.Lock()` or use `functools.cache`.

**LanceDB `to_arrow().to_pylist()` materializes entire table into memory:**
- Severity: **medium**
- Issue: `src/jarvis/semantic.py:200-202` calls `table.to_arrow().to_pylist()` in `rows_by_path()`, which loads every row of a repo's LanceDB table into memory as a list of dicts. For large repos this could be tens of thousands of rows with 768-dim vectors.
- Files: `src/jarvis/semantic.py:196-204`
- Cause: The carry-forward optimization needs to group rows by file path and match by `content_hash`, requiring full table access.
- Improvement path: Push the grouping and hash lookup into a LanceDB filter query rather than materializing the full table. For repos with thousands of chunks, this is a significant memory reduction.

**`index_cli.py` at 1189 lines — monolithic CLI module:**
- Severity: **low**
- Issue: `src/jarvis/index_cli.py` (1189 lines) contains the full indexing pipeline, all CLI subcommands, Zoekt shard management, scheme/language resolution, and the watch command orchestration. It is the largest file by 2.3× (next is `query.py` at 521 lines).
- Files: `src/jarvis/index_cli.py`
- Cause: Organic growth; all CLI-related logic accumulated in one module.
- Improvement path: Extract `_publish_search_only`, `_retire_scip_artifacts`, `_resolve_*` helpers, and `_cmd_*` subcommand functions into separate modules (e.g., `publish.py`, `commands.py`).

## Fragile Areas

**Zoekt webserver subprocess lifecycle (pidfile + SIGTERM + atexit):**
- Severity: **medium**
- Issue: `ZoektLifecycle` in `src/jarvis/search.py:113-231` manages an external `zoekt-webserver` process via pidfile. Risks: (a) The pidfile can become stale if the process is killed externally (OOM killer, `kill -9`) — `_pid_alive()` handles this, but the pidfile is not cleaned up. (b) `atexit.register(self.stop)` only fires for the process that spawned the webserver; a second MCP server process reusing the pidfile will never call `stop()`. (c) The `stop()` method unlinks the pidfile, so a graceful shutdown of one process leaves the pidfile missing for a concurrent process that was using the same webserver.
- Files: `src/jarvis/search.py:113-231`
- Why fragile: PID file management is inherently racy across process boundaries. The design is correct for the common single-server case but has edge cases under concurrent MCP server processes.
- Safe modification: Add a `try/except FileNotFoundError` around pidfile reads (already present). Consider making pidfile cleanup conditional on ownership (only delete if `self._own_process` is the one that created it — partially done, but `stop()` unlinks even for non-owned processes in some code paths).
- Test coverage: `tests/test_search.py` covers `ensure_running` and `stop` with mock subprocesses, but does not test concurrent-process pidfile races.

**Registry + GraphStore share one SQLite file with separate connections:**
- Severity: **medium**
- Issue: `Registry` and `GraphStore` both open independent connections to `~/.jarvis/registry.db`. Both set `PRAGMA busy_timeout = 5000` to handle concurrent writes (e.g., `jarvis watch` background reindex vs. manual `jarvis index`). However, `_retire_scip_artifacts` (`src/jarvis/index_cli.py:620-643`) opens a third `GraphStore` connection, performs edge deletions, and closes it — all while the caller may hold an open `Registry` connection. The 5-second busy timeout mitigates but does not eliminate the risk of `OperationalError: database is locked` under heavy contention.
- Files: `src/jarvis/graph.py:169-173`, `src/jarvis/registry.py:108-113`, `src/jarvis/index_cli.py:620-643`
- Why fragile: SQLite's write locking means concurrent writers serialize. With `busy_timeout = 5000`, a write that waits more than 5 seconds still raises.
- Safe modification: Reduce the number of short-lived connections (e.g., pass the `GraphStore` from the caller instead of opening a new one in `_retire_scip_artifacts`). Increase `busy_timeout` if needed.
- Test coverage: Unit tests mock SQLite, so this contention is not tested.

**Broad exception swallowing in MCP tool handlers:**
- Severity: **medium**
- Issue: Every MCP tool in `server.py` wraps its body in `except Exception as exc: return {"error": str(exc)}`. This is intentional (documented: "keeps every tool's error shape the same") and correct for stdio server stability — an unhandled exception would crash the server. However, it means `KeyboardInterrupt`, `SystemExit`, and programming errors (e.g., `TypeError` from a refactoring mistake) are all silently turned into `error` dicts with no log output.
- Files: `src/jarvis/server.py:177-180` (and 7 more identical patterns at lines 191, 210, 229, 257, 292, 308, 363, 377)
- Why fragile: A bug in query logic (e.g., wrong column name in SQL) produces `"error": "no such column: ..."` in the MCP response with no server-side log, making it invisible to the user unless they inspect the raw MCP response.
- Safe modification: Add `logging.error("tool error", exc_info=exc)` before returning the error dict. Catch `BaseException` subclasses like `KeyboardInterrupt` and re-raise them.
- Test coverage: `tests/test_server_tools.py` tests error paths but does not verify that unexpected exceptions are logged.

**Silent exception swallowing in hybrid search degradation:**
- Severity: **low**
- Issue: `semantic_search` in `src/jarvis/semantic.py:347-358` catches `ZoektUnavailableError` and bare `Exception` (for symbol search) silently, degrading to vector-only search. This is intentional (documented: "best-effort by contract") but means a persistent Zoekt failure goes unnoticed.
- Files: `src/jarvis/semantic.py:347-358`
- Why fragile: If Zoekt crashes and never comes back, every semantic search silently returns vector-only results with no indication to the user (the `sources` field in each hit reflects what was used, but users rarely inspect it).
- Safe modification: Log a warning on first Zoekt degradation within a session, then suppress.

## Scaling Limits

**Symbol name-map cache bounded at 64 entries — unbounded during `jarvis watch`:**
- Severity: **low**
- Issue: `src/jarvis/symbols.py:216-218` caps the name-map cache at 64 entries via an OrderedDict. The docstring acknowledges that `jarvis watch` republishes under a new SHA on every debounced change, so the key space grows unboundedly over time. The cap means old entries are evicted and rebuilt at ~20ms each — acceptable for 17K-symbol indexes.
- Files: `src/jarvis/symbols.py:216-237`
- Current capacity: 64 entries × ~5MB each = ~320MB worst case.
- Limit: Beyond 64 repos in a long-running server, cache thrashing causes every query to rebuild the name map.
- Scaling path: The current cap is adequate for single-user local use. A multi-repo future could increase the cap or use a TTL.

**LanceDB table_names() pagination with >10 repos:**
- Severity: **low**
- Issue: `src/jarvis/semantic.py:149-158` documents that LanceDB's `table_names()`/`list_tables()` paginates with a default page size of 10. The code avoids this by using `open_table()` directly (which raises `ValueError` for missing tables), but `rows_by_path()` calls `table.to_arrow().to_pylist()` which has its own scaling characteristics for large tables.
- Files: `src/jarvis/semantic.py:147-158, 196-204`
- Current capacity: No hard limit on repos; each repo gets one LanceDB table.
- Limit: Individual table size scales with repo LOC — a 100K-file repo produces tens of thousands of chunks, all materialized in `rows_by_path`.
- Scaling path: Push filtering into LanceDB queries rather than full-table materialization.

## Dependencies at Risk

**`mcp[cli]` capped below 2.0 — breaking change pending:**
- Severity: **high**
- Issue: `pyproject.toml` pins `mcp[cli]>=1.2.0,<2.0.0` because `mcp 2.0.0` removed `mcp.server.fastmcp`, which `server.py` imports. When `mcp` 2.x is the only available version, jarvis will not install.
- Files: `pyproject.toml:66-67`, `src/jarvis/server.py:22`
- Impact: Complete breakage of the MCP server on `mcp` 2.x.
- Migration plan: Follow the `mcp` 2.0 migration guide when ready. The import will need to change from `mcp.server.fastmcp` to the 2.x API surface. The `server.json` manifest may also need updates.

**`protobuf>=7.35.1,<8.0.0` — gencode/runtime version coupling:**
- Severity: **medium**
- Issue: `pyproject.toml` pins `protobuf>=7.35.1,<8.0.0` because `scip_pb2.py` is generated against `libprotoc 35.1`. The protobuf library validates at import time (`ValidateProtobufRuntimeVersion`) and refuses an older runtime.
- Files: `pyproject.toml:70-71`, `src/jarvis/scip_pb2.py:1-33`
- Impact: If protobuf 8.x changes the gencode surface, the vendored `scip_pb2.py` would need regeneration.
- Migration plan: Regenerate `scip_pb2.py` when upgrading protobuf, using the matching `protoc` version.

**Sentence-transformers and torch — heavy optional dependency:**
- Severity: **low**
- Issue: The `semantic` extra pulls in `sentence-transformers>=3.0`, which depends on `torch`. This is a multi-GB install that is required for semantic indexing and search but not for the core navigation/search functionality.
- Files: `pyproject.toml:76-80`
- Impact: Users who only need code navigation and lexical search pay no cost — the import is deferred. But CI must install it to avoid silent test skips.
- Migration plan: No action needed; the deferred-import discipline is correct. Monitor for lighter embedding runtimes.

## Test Coverage Gaps

**No integration test coverage in CI:**
- Severity: **medium**
- What's not tested: End-to-end indexing of a real repo with actual `scip`/`zoekt` binaries. The `integration` marker is excluded from CI.
- Files: `tests/test_index_cli.py` (integration tests), `.github/workflows/test.yml:39`
- Risk: A change that breaks real `scip expt-convert` output parsing, `zoekt-git-index` flag compatibility, or the full pipeline ordering would not be caught by CI. The `setup-smoke.yml` workflow tests binary installation but not jarvis's use of them.
- Priority: **Medium** — unit tests mock heavily and provide good coverage of logic, but the gap between mocked and real binary behavior is where regressions hide.

**`server.py` has no dedicated test file — covered only via `test_server_tools.py`:**
- Severity: **low**
- What's not tested: `server.py` module-level initialization (the `_service()`, `_zoekt()`, `_graph()` lazy singletons) and edge cases in `_json_safe`, `_freshness_fields`, `_search_coverage_fields`.
- Files: `src/jarvis/server.py`, `tests/test_server_tools.py`
- Risk: A bug in the lazy-init singletons (e.g., `GraphStore` failing to open `registry.db`) would surface only at runtime.
- Priority: **Low** — the server tools tests exercise the critical paths.

**Concurrent process pidfile race conditions untested:**
- Severity: **low**
- What's not tested: Two MCP server processes sharing a zoekt-webserver via pidfile; stale pidfile recovery after external kill.
- Files: `src/jarvis/search.py:113-231`, `tests/test_search.py`
- Risk: A race condition in pidfile management could cause a second server process to fail to find the running webserver or to kill it prematurely.
- Priority: **Low** — the common single-server case works correctly.

**GraphStore + Registry concurrent-write contention untested:**
- Severity: **low**
- What's not tested: Simultaneous writes to `registry.db` from `jarvis watch` and a manual `jarvis reindex`.
- Files: `src/jarvis/graph.py:169-173`, `src/jarvis/registry.py:108-113`
- Risk: Under heavy contention, the 5-second busy timeout might not be sufficient, but this is untested.
- Priority: **Low** — the busy_timeout is a standard SQLite contention mitigation.

---
*Concerns audit: 2026-08-21*
