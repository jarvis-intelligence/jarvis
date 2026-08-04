# Code Review: jarvis Phase 2 + Phase 3 (server, query, index_cli, registry, search)

## Scope
- Files reviewed: `src/jarvis/{models,config,query,server,registry,search,index_cli,index_reader}.py`, `pyproject.toml`, `README.md`, tests.
- Tests: `uv run pytest -q` → **63 passed**, no failures, no sandbox hangs observed.
- Plan cross-check: `plans/0724-2316-jarvis-mcp-implementation/{plan,phase-02,phase-03}.md` — all listed success criteria appear met (7 tools registered, CRUD registry, atomic swap, lazy zoekt lifecycle). Marked "Completed" in plan.md; consistent with what's in the tree.

## Critical Issues

### 1. `--slug` bypasses `config.repo_slug()` sanitization → path traversal in `index_cli.py`
`index_repo()`:
```python
slug = slug or config.repo_slug(repo_path.name)
```
When `--slug` is supplied on the CLI, it is used **verbatim** — `config.repo_slug()` (the only place that strips `/`, collapses unsafe characters) is never applied to it. That raw string then flows into:
- `config.index_dir(slug, root)` → `target_dir = data_dir/scip/_/{slug}/_` used for `shutil.copy`, metadata write, and `_publish_atomically` (writes files).
- `_cmd_forget`: `index_dir = config.index_dir(args.slug)` (positional arg, also never sanitized) followed by `shutil.rmtree(index_dir)`.

Because `.` and `-` pass the allow-list regex unchanged, a value like `--slug ..` or `jarvis forget ..` produces a path component of `..`, letting `index_dir()` walk out of `~/.jarvis/scip/_/`. Combined with `forget`'s unconditional `shutil.rmtree`, a typo'd or copy-pasted slug (e.g. `jarvis forget ../../Documents`) can **recursively delete a directory outside jarvis's data dir**. `index` has the same issue for writes (arbitrary file creation via `shutil.copy`/pointer writes).
This is a personal single-user tool (no external attacker), but it is still a real correctness/safety gap the task asked me to check ("safe input handling") — a fat-fingered `--slug` is data-loss-capable, not merely cosmetic. No test exercises this path (`tests/test_index_cli.py` only tests well-formed slugs).

**Fix:** always route through `config.repo_slug()`: `slug = config.repo_slug(slug) if slug else config.repo_slug(repo_path.name)`, and sanitize `args.slug` in `_cmd_forget`/`_cmd_status`/`_cmd_reindex` the same way before it touches the filesystem.

## High Priority

### 2. Inconsistent tool error-shape across the 7 MCP tools (confirmed via source, not assumption)
`server.py`'s wrappers only catch `IndexNotFoundError` and return a *normal, isError=False* result `{"error": str(exc)}`. I checked `mcp.server.lowlevel.server.Server.call_tool`'s dispatch (installed package, `~/Library/Python/3.14/.../mcp/server/lowlevel/server.py`): any exception that escapes the tool function is caught one layer up and converted to `self._make_error_result(str(e))`, i.e. an **isError=True** `CallToolResult` with only unstructured text — no `{"error": ...}` dict, no freshness fields.

So:
- `IndexNotFoundError` → isError=False, body `{"error": "..."}` (a "successful" call by MCP's own isError flag).
- Any other exception — `OccurrenceDecodeError` (raised fail-closed by `scip_decoder.py` on a corrupt/mismatched-schema blob), `sqlite3.OperationalError` (e.g. querying a `.db` built by an incompatible/future `scip expt-convert` schema), or a bug — → isError=True, plain-text `str(e)` only.

A client that checks for a `"error"` key in the structured result (which is the pattern the code itself establishes for `IndexNotFoundError`) will silently miss every other failure mode, since those never populate structured content at all. This is exactly the "consistent error-shape" question raised in the task, and it does not hold across all 7 tools — only for the one exception type each wrapper explicitly catches. `getIndexStatus` is the only tool that never raises (query.py's `get_index_status` already catches `IndexNotFoundError` internally), so it's not exposed to this at all — but the other 6 tools are.

**Fix:** either (a) let all exceptions propagate and rely on the framework's isError path uniformly (drop the manual `except IndexNotFoundError` special-casing), or (b) wrap each tool body in `except Exception as exc: return {"error": str(exc)}` consistently so every failure mode gets the same structured shape. Pick one, not a mix.

### 3. `index_cli.py`: registry status can say "failed" while a fresh index is actually live
Order of operations in `index_repo()`: SCIP indexer → `scip expt-convert` → copy versioned db → write metadata → **`_publish_atomically` (pointer flip)** → `zoekt-index` → `registry.upsert(..., "indexed")`. If the `zoekt-index` step fails, the `except Exception` handler sets registry status to `"failed"` — but the pointer has *already* been flipped to the new, fully valid SCIP index. Nav tools (`goToDefinition`, `findReferences`, etc.) will silently serve the new, correct data even though `jarvis status <slug>` reports `failed` and `commit_sha` still shows the *previous* commit (the initial `registry.upsert(..., None, "indexing")` call is never updated with `sha` before the zoekt failure, since the `"indexed"` upsert with `sha` never runs). This misrepresents both the index freshness and the failure's actual blast radius (only search is stale, not navigation) — a user seeing "failed" has no way to know queries are fine.

**Fix:** either move the pointer swap after all steps succeed (search shard is not read on the pointer, so ordering can safely be indexer→convert→zoekt-index→swap→registry), or record commit_sha at the "indexing" stage and use a finer-grained status (e.g. `"indexed-search-stale"`) so `status` reflects reality.

## Medium Priority

### 4. `index_cli.py` writes/deletes working files (`index.scip`, `index.db`) directly inside the user's source repo
`index_repo()`'s `finally` unconditionally does `scip_path.unlink(missing_ok=True); db_path.unlink(missing_ok=True)` where both paths are `repo_path / "index.scip"` / `repo_path / "index.db"` — i.e., files inside the *target repo being indexed*, not a scratch/tmp directory. If the target repo happens to already track files with those exact names (unlikely but plausible, e.g. a repo about SCIP/indexing), they get silently overwritten and then deleted with no confirmation. Low likelihood, but worth using a `tempfile.TemporaryDirectory()` instead of writing into the repo being indexed, both for safety and to avoid polluting `git status` mid-run if the indexer crashes before the `finally` cleanup executes.

### 5. `ZoektLifecycle`'s health check validates reachability, not identity
`_is_healthy()` only checks `status_code < 500` on a bare GET to the base URL — it doesn't verify the responding process is actually `zoekt-webserver`. Combined with pidfile-based PID reuse (`_pid_alive` only checks `os.kill(pid, 0)` succeeds), if the OS reassigns a stale PID to an unrelated long-lived process that happens to also answer HTTP with a non-5xx on the same port, `ensure_running()` would treat it as "the" zoekt-webserver and forward real `/api/search` POSTs to it. Very low real-world likelihood for a personal single-user tool on a stable dev machine, and the PID-liveness + port-reachability combination does correctly cover the common case (stale pidfile pointing at a dead PID, or a genuinely dead port). Documenting as a known gap rather than a blocking defect — no fix required unless you want to add a lightweight identity probe (e.g. hit a zoekt-specific endpoint, not `/`).

### 6. `_publish_atomically`: harmless but real leak on crash between pointer flip and old-file cleanup
`os.replace(tmp_pointer, pointer_file)` (atomic) happens, then old versioned `.db`/`.metadata.json` are unlinked. If the process is killed between those two steps, the old version becomes a permanent orphan (never referenced by any future `old_pointer` comparison, since the *next* run's "old" will be the version that succeeded here). Not a correctness bug (readers only ever look at `current`), just an accumulating disk-space leak with no GC path. Worth a one-line note if you care about long-term disk usage; not blocking.

## Low Priority / Notes (verified, not bugs)
- `_publish_atomically`'s `old_pointer != versioned_name` guard correctly handles the "reindex produced the same sha" case (doesn't delete the file it just wrote to).
- `get_index_status`'s freshness branching (repo_path=None → UNKNOWN without staleness claim; unresolvable live HEAD → UNKNOWN, not FRESH/STALE; else compare) is logically sound and matches the docstring's "never stale=True without evidence" contract. Matches phase-02 plan intent.
- `query.py`'s bitwise role filtering (`m.role & role_bit != 0`) and call/type-hierarchy SQL are consistent with the documented v0.7.0 schema notes; I didn't find an introduced bug in the port (traversal logic, `local `-symbol filtering, and dedup via `seen` set all look correct).
- SQL throughout is parameterized (`?` placeholders) — no injection risk from `repo`/`symbol`/`path` args reaching sqlite.
- `searchCode`'s `f"r:{repo} {query}"` string-concat into the Zoekt query is a minor query-injection-into-search-syntax vector (a crafted `repo` value could add extra `r:`/other filters), but there's no authz boundary being crossed in a single-user tool — low severity, note only.
- StrEnum (`Freshness`) inside `asdict()` + `_json_safe` serializes correctly (str subclass); datetime handling in `_json_safe` is correct and applied consistently across all 7 tool wrappers.
- Registry's `ON CONFLICT...DO UPDATE` upsert and `mark_status` are straightforward and race-safe enough for a single local sqlite connection with `check_same_thread` default (single process/thread usage here).

## Fact-Check on Known Tradeoffs (from task's "do NOT re-flag" list)
All four listed items verified against source and confirmed as-described (config.py's pinned PROJECT/BRANCH constants; searchCode's `repo` vs slug divergence caveat is in README; typeHierarchy/kind emptiness is explained and matches `scip_decoder.py`'s documented v0.7.0 converter gap; no authz anywhere, confirmed, consistent with single-user design). Not re-flagged.

## Recommended Actions (priority order)
1. Sanitize `--slug` (and `forget`/`status`/`reindex`'s positional `slug`) through `config.repo_slug()` before any filesystem path is built — Critical, prevents accidental data loss.
2. Unify error-response shape across all 7 MCP tools (Issue 2) — pick catch-all-and-structure or let-framework-handle-uniformly, not both.
3. Reorder `index_repo()`'s pipeline so a zoekt-index failure doesn't leave a misleading "failed" status against an already-live, valid SCIP index (Issue 3).
4. Optional: move indexer scratch files (`index.scip`/`index.db`) to a tempdir instead of the target repo's working tree (Issue 4).

## Metrics
- Tests: 63/63 passing (`uv run pytest -q`), no skips observed, no long-running integration test appeared to hang in this sandbox.
- No lint/type-check config found to run (no `pyproject.toml` `[tool.ruff]`/`[tool.mypy]` sections encountered during this review — not verified further, out of scope for this pass).

## Unresolved Questions
- Is `--slug` intended to be a power-user escape hatch that's trusted not to contain path-traversal characters, or should it be a "friendly display name only" input? The fix differs slightly (reject invalid chars with an error vs. silently sanitize) depending on intended UX — flagging for your call rather than picking one.
- Do you want `jarvis forget`/`reindex`/`status` to also validate `args.slug` against the registry *before* touching any path (i.e., reject slugs that were never registered) as a second layer of defense, or is the `repo_slug()` sanitization alone sufficient given this is a personal tool?
