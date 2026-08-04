# Phase 4 Review — Graph / BlastRadius / Auto-Reindex

## Scope
- `src/jarvis/graph.py` (new), `src/jarvis/watch.py` (new), `src/jarvis/index_cli.py` (modified), `src/jarvis/server.py` (modified), `tests/test_graph.py`, `tests/test_watch.py`, `tests/test_server_tools.py`
- `uv run pytest -q` → 89 passed, 0 failed (no regressions observed; none of the 3 known-flaky Phase-3 tests were exercised this run).

## Critical Issues

None that risk data loss or a security boundary. The most serious finding (stale-edge accumulation) is a correctness bug, not a crash/security issue — see High.

## High Priority

**1. `populate_graph_for_repo` never retracts stale packages/edges — blastRadius can report dependents that no longer exist.**
`src/jarvis/graph.py:260-292`. Every call only *adds* rows (`upsert_package`, `add_edge`); there is no delete/reconcile step. Concretely:
- If repo A drops a dependency on package X and is reindexed, the edge `A -> X` is never removed. `blast_radius(X)` will keep listing A as a 1-hop dependent forever, even though the current code no longer references X.
- If a local package is renamed (or a repo is `jarvis forget`+re-added under the same slug with a different package name), the old `packages` row for the old name is orphaned permanently and stays traversable.

This directly matters for blastRadius's core promise ("which repos would this change affect") — after any dependency removal, the graph silently lies in the direction of over-reporting, and there is no way for a caller to detect this since freshness is always `UNKNOWN`. The user's own idempotency question ("shouldn't create duplicate edges") is satisfied, but idempotency for *growth* is not the same as correctness over a repo's lifetime — the current tests (`test_add_edge_is_idempotent`, `test_upsert_package_same_key_returns_same_id`) only check that re-adding the same edge/package is a no-op; nothing tests that removing a dependency and re-running `populate_graph_for_repo` retracts the edge (because nothing does).

Fix direction: before/after each `populate_graph_for_repo` run, diff freshly-extracted `local_package_names`/`external_package_names` against what's already stored for that repo and delete edges/packages that are no longer present (e.g. `DELETE FROM edges WHERE from_package_id IN (this repo's local ids) AND to_package_id NOT IN (current targets)`).

**2. Unhandled exception classes can silently kill the `jarvis watch` background loop.**
`src/jarvis/index_cli.py:265-294`. `_reindex()` only catches `(UnsupportedLanguageError, IndexingError, ValueError)`. But several calls inside `index_repo()` execute **before** its own `try:` block (line 118) and are therefore never converted to `IndexingError`:
- `_git_head()` (line 113) runs `subprocess.run(..., check=True)` — a transient git failure (lock file, detached HEAD edge case, mid-rebase, etc.) raises `subprocess.CalledProcessError`, uncaught by `_reindex`.
- `registry.upsert(slug, ..., "indexing")` (line 116) — a `sqlite3.OperationalError` ("database is locked", see Finding 3) here is also outside the try, uncaught.

Either propagates straight through `Debouncer.poll()` → the `while True: ... debouncer.poll()` loop in `_cmd_watch` (line 286-288) → out of the function's `try/except KeyboardInterrupt` (which only catches `KeyboardInterrupt`) → crashes the entire `jarvis watch` process with no further reindexing, ever, until the user manually restarts it. This defeats the "not a daemon requirement but should be resilient while running" intent, and is a plausible real-world trigger (git operations racing with an editor's file-watch events on the same repo is not exotic). Not covered by any test — `test_watch.py` only unit-tests the pure `Debouncer`, never `_cmd_watch`/`_reindex`'s exception surface.

Fix direction: widen `_reindex`'s except clause to `Exception` (log-and-continue is the correct policy for a long-running watch loop — this is one of the few cases where a broad catch is actually justified, since the alternative is killing the whole background process), or move the pre-try calls in `index_repo` inside the try so they always surface as `IndexingError`.

**3. No `busy_timeout`/WAL configured on either SQLite connection sharing `registry.db` — lock contention between `Registry` and the new `GraphStore` is unhandled.**
`src/jarvis/registry.py:55` and `src/jarvis/graph.py:179` both `sqlite3.connect()` with no `PRAGMA busy_timeout` and no `PRAGMA journal_mode=WAL`. Default SQLite behavior returns `sqlite3.OperationalError: database is locked` immediately on writer contention (busy_timeout=0). Within a single `index_repo()` call this isn't an issue (writes are sequential, not concurrent), but Phase 4 specifically adds a scenario where concurrency becomes plausible: a `jarvis watch` background process and a manually-invoked `jarvis index`/`reindex` against the same registry.db, or two `watch` processes for different repos writing to the shared registry.db at the same moment. Given Finding 2, this failure mode is also unhandled by `_cmd_watch`. Low cost fix: `self._conn.execute("PRAGMA busy_timeout=5000")` (and ideally `PRAGMA journal_mode=WAL`) in both `Registry.__init__` and `GraphStore.__init__`.

## Medium Priority

**4. Leaked `sqlite3.Connection` in `index_repo`.**
`src/jarvis/index_cli.py:137`: `populate_graph_for_repo(graph_store, slug, sqlite3.connect(db_path))` — the connection is never closed (no `with`, no `.close()`). It's a short-lived scratch-dir file so this doesn't corrupt anything, but it leaves an open file descriptor/lock on `db_path` until GC finalizes the object, and `TemporaryDirectory.__exit__`'s cleanup races against that. On POSIX this reliably succeeds (unlink of an open file works), but it's an easy one-line fix (`with sqlite3.connect(db_path) as conn: populate_graph_for_repo(graph_store, slug, conn)`) and the pattern is a bad example to leave in code that also carefully closes `graph_store` via `finally`.

**5. `blastRadius`'s error message doesn't distinguish "repo never indexed" from "package name typo".**
`src/jarvis/graph.py:308-314`: `SeedPackageNotFoundError` is raised identically whether `repo` has zero packages ever registered or `name` just doesn't match any registered package for a real repo. The message (`no package {name!r} registered for {repo!r}`) is honest but not actionable — a caller has no way to tell "did you mean a different package name" from "you need to `jarvis index` this repo first." Given `getIndexStatus` already exists as tool #6, consider having the error at least hint at this (e.g. check `store.list_packages(repo)` and mention if it's empty). Not blocking — the current message is not wrong, just less useful than it could be.

## Low Priority

**6. Missing index on `edges.to_package_id`.**
`src/jarvis/graph.py:152-159` schema: `PRIMARY KEY (from_package_id, to_package_id)` only accelerates lookups keyed on `from_package_id` first. `get_dependents()` (line 248-254) filters `WHERE e.to_package_id = ?` and joins on it — for a personal, single-user, likely-small graph this is a non-issue today, but if `packages`/`edges` ever grow (e.g. one big monorepo indexing many packages), every `blast_radius` hop does a full table scan of `edges`. A one-line `CREATE INDEX IF NOT EXISTS edges_to_idx ON edges(to_package_id)` would future-proof this cheaply. Flagging as low since current expected scale doesn't warrant urgency.

## Verified Correct (per the user's specific "check carefully" list)

- **BFS cycle-safety and shortest-hop-count correctness** (`blast_radius`, `graph.py:308-333`): `visited_ids` is seeded with the seed's own id and grown monotonically as each hop is processed in order; a node already in `visited_ids` is skipped before being added to `next_frontier`, so a genuine cycle (A→B→A) cannot loop forever and the *first* discovery (i.e., shortest path, since hops are processed in ascending order 1..2) always wins. Confirmed correct by reading the loop; no test for an actual cycle exists (`test_graph.py` only has a linear 4-node chain) — worth adding `A->B->A` as a regression test given this was flagged as a specific risk, but the code itself is correct.
- **`upsert_package`/`add_edge` idempotency** for a *stable* dependency set (no removals) is correct — re-running `populate_graph_for_repo` with the same extraction result creates zero duplicate rows (get-or-create + existence check before insert). This is the "duplicate creation" half of the idempotency question; the "stale retraction" half is Finding 1 above.
- **DEFINITION-bit bitwise-AND semantics** (`extract_package_names`, `graph.py:102-142`): `(m.role & :definition_bit) != 0` correctly implements "any mention with the Definition bit set," and `referenced_only_symbols = all_symbols - defined_symbols` correctly captures symbols that are exclusively referenced (never defined) in this index — no bug found in the port.
- **`_cmd_watch` debounce wiring**: `Debouncer.notify()` on every non-ignored `on_any_event`, `poll()` on a 0.5s tick — matches the phase's "5s debounce, coalesce a burst into one reindex" requirement; the pure `Debouncer` unit tests (`test_watch.py`) correctly exercise burst-coalescing, re-fire-after-new-notify, and no-refire-without-new-notify. No bug in the debounce state machine itself.
- **Registered tool count / EXPECTED_TOOLS**: `server.py` registers exactly 8 tools including `blastRadius`; `test_server_tools.py`'s `EXPECTED_TOOLS` matches.

## Plan / Success-Criteria Status

`plans/0724-2316-jarvis-mcp-implementation/phase-04-graph-blast-radius-and-auto-reindex.md` success criteria:
- [x] `uv run pytest` green (89 passed)
- [x] blastRadius hand-verified on a known dependency chain (per session summary; cross-repo edge across two real indexed repos)
- [~] "Burst of file edits → exactly one reindex; status returns fresh" — manually smoke-tested per session summary, but the automated coverage stops at the pure `Debouncer`; the `_cmd_watch`/`_reindex` error-handling gap (Finding 2) means this guarantee is not resilient to the failure modes above. Recommend widening `_reindex`'s except clause before considering this criterion durably met, not just demo-verified.
- [x] 8 tools listed via MCP (verified via `EXPECTED_TOOLS` test)

## Recommended Actions (priority order)

1. Add a reconciliation/delete step to `populate_graph_for_repo` so stale local packages/edges are retracted on reindex (Finding 1).
2. Widen `_reindex`'s except clause in `_cmd_watch` to `Exception` (or move `index_repo`'s pre-try calls inside the try) so a transient git/sqlite failure doesn't kill the whole watch process (Finding 2).
3. Add `PRAGMA busy_timeout` (and consider WAL) to both `Registry` and `GraphStore` connections (Finding 3).
4. Close the leaked `sqlite3.Connection` in `index_repo` (Finding 4).
5. Optional: enrich `blastRadius`'s not-found error with repo-indexed-state context (Finding 5); add an `edges(to_package_id)` index (Finding 6); add an explicit cycle-graph regression test for `blast_radius`.

## Unresolved Questions

- Is there an expected operational pattern where `jarvis watch` and manual `jarvis reindex`/`index` run concurrently against the same repo in practice? If genuinely never expected (single interactive user, one terminal), Findings 2/3 are lower urgency (defense-in-depth) rather than blocking — but the `watch` subcommand's whole premise is "leave it running in the background," which makes an unhandled crash more likely to go unnoticed for a while.
- Is stale-edge retraction (Finding 1) in scope for this phase, or intentionally deferred (e.g. documented as a known limitation) given the plan's phase is marked `status: completed`? The plan text and phase file don't mention this limitation explicitly, unlike the `Freshness.UNKNOWN` and cross-repo-name-matching decisions, which *are* explicitly documented as accepted trade-offs.
