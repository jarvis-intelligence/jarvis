# Benchmark harness (`jarvis bench`) — Design

## Problem

`jarvis` has no way to measure how fast indexing or querying actually is, and no way to
notice when a future change makes either slower. This design adds a benchmark harness that
measures both, records history, and flags regressions — driven by the goal of catching
performance regressions over time, not just taking a one-off measurement.

## Scope

In scope: a new `jarvis bench` CLI subcommand, a new `src/jarvis/bench.py` module
(storage, comparison, drift-check, output formatting), a `~/.jarvis/bench.db` history store,
and unit tests for the harness's own logic. Out of scope: wiring this into CI (no CI test
workflow exists at all today — a separate gap), multi-repo scale curves, and anything that
mutates or checks out a real dev repo out from under the user.

## Design

### 1. Architecture

`jarvis bench` is a new argparse subcommand in `index_cli.py`, alongside `index`/`list`/
`status`/`reindex`/`forget`/`watch`. Running it:

1. Reads a small fixed, source-level config naming pinned repos: `(slug, repo_path, pinned_sha)`
   tuples. v1 has exactly one entry — see Section 3. This is a personal, local-first tool with
   no config-file layer anywhere else in the codebase, so a source constant matches existing
   style (comparable to `_LANGUAGE_INDEXERS`).
2. For each pinned repo, verifies `git rev-parse HEAD` at `repo_path` equals `pinned_sha`.
   **On mismatch, fails loudly and does nothing else** — never checks out, stashes, or otherwise
   mutates a real repo the user may be actively working in. This is a hard safety rule, not a
   convenience default.
3. Runs the full `index_repo()` pipeline once per pinned repo, timing it wall-clock
   end-to-end (indexer subprocess + `scip expt-convert` + `zoekt-index` + graph populate +
   atomic publish) — this is what the user actually experiences, so no sub-step breakdown for
   v1.
4. Times a fixed set of query calls (Section 3) directly against `query.py`/`search.py`'s
   underlying functions, opened against the pinned repo's just-published index — bypassing
   `server.py`'s MCP tool wrappers, since those are thin pass-throughs and going direct avoids
   needing a live MCP session inside the bench harness. This measures the query engine itself,
   not stdio/JSON-RPC transport.
5. Writes every measurement into `~/.jarvis/bench.db` (Section 2), then prints a comparison
   against the immediately-previous run for the same `(repo, operation)` pair (Section 4).
   Anything **>20% slower** is flagged as a regression, and the command exits non-zero if any
   regression is found — usable as a CI gate later even though nothing wires it in yet.

### 2. `bench.db` schema

```sql
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    run_at TEXT NOT NULL,           -- ISO timestamp
    jarvis_commit TEXT NOT NULL  -- git rev-parse HEAD of jarvis itself
);

CREATE TABLE measurements (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    repo TEXT NOT NULL,             -- slug, e.g. "epost-ios-theme-ui"
    operation TEXT NOT NULL,        -- "index_repo" | "documentSymbols" | "goToDefinition" | ...
    duration_ms REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
```

One `runs` row per `jarvis bench` invocation, recording which version of `jarvis` produced
the numbers — the whole point of tracking regressions over time. One `measurements` row per
timed operation within that run. Plain stdlib `sqlite3`, no ORM — matching `registry.py`'s
existing style exactly.

### 3. What gets measured, with real v1 inputs

**Pinned repo (v1, one repo — the config shape makes adding more later trivial):**
`epost-ios-theme-ui` @ `02ebe346eede5c5abee55913daa921882d0932a2`, path
`/Users/ddphuong/Projects/epost-workspace/epost-app/epost-ios-theme-showcase/epost-ios-theme-ui`.

**Operations timed, each against real data already present in this repo's published index:**

| operation | input |
|---|---|
| `index_repo` | full pipeline, this repo, `scheme="ios_theme_ui"` |
| `documentSymbols` | `path="ios_theme_ui/Classes/Libraries/SwiftTheme/Theming/Pickers/ThemeColorPicker.swift"` |
| `goToDefinition` | `` symbol="scip-swift xcodebuild ios_theme_ui . `c:@M@ios_theme_ui@objc(cs)ThemeColorPicker`." `` |
| `findReferences` | same symbol as above |
| `callHierarchy` | same symbol as above |
| `typeHierarchy` | same symbol as above |
| `searchCode` | `query="ThemeColorPicker"` |
| `blastRadius` | `symbol_or_package="xcodebuild:ios_theme_ui"` |

`index_repo` is the one operation that mutates state (it republishes the index) — since it reruns
on every bench invocation, later runs measure a realistic "reindex an existing repo" cost, not a
one-time cold-index cost.

### 4. CLI output

```
$ jarvis bench
Verifying pinned repos... epost-ios-theme-ui @ 02ebe346 ✓

Running epost-ios-theme-ui...
  index_repo         4812.3ms
  documentSymbols       2.1ms
  goToDefinition        1.4ms
  findReferences        3.8ms
  callHierarchy         2.9ms
  typeHierarchy         1.1ms
  searchCode            8.7ms
  blastRadius           0.6ms

Comparison vs previous run (2026-07-27T21:03:21, commit 6f3edbb):
  operation         this run    prev run    change
  index_repo         4812.3ms    4650.1ms    +3.5%
  documentSymbols        2.1ms       2.0ms    +5.0%
  goToDefinition         1.4ms       1.3ms    +7.7%
  findReferences         3.8ms       9.1ms   -58.2%
  callHierarchy          2.9ms       2.8ms    +3.6%
  typeHierarchy          1.1ms       1.0ms   +10.0%
  searchCode             8.7ms      21.4ms   -59.3%
  blastRadius            0.6ms       0.6ms    +0.0%

No regressions (threshold: >20% slower).
```

First-ever run (nothing to compare against) prints only the measurements and says so instead of a
comparison table. A regression line reads e.g. `⚠ REGRESSION: index_repo +34.2% (4812.3ms vs
3583.0ms)`.

### 5. Testing the bench harness itself

New module `src/jarvis/bench.py` holds the actual logic (storage, comparison/regression
logic, drift-check, output formatting) separately from the thin `_cmd_bench` CLI wiring added to
`index_cli.py` — with `tests/test_bench.py`, matching the project's existing 1:1
module-to-test-file convention.

- **Storage** (`record_run`, `read_previous`): real `sqlite3` against `tmp_path`, no mocks — same
  style as `test_registry.py`.
- **Comparison/regression logic**: a pure function, `(current_ms, previous_ms) -> (pct_change,
  is_regression)` against the 20% threshold — trivially unit-testable with plain numbers, no I/O.
- **Drift check**: reuses the existing `_init_git_repo()` test helper already in
  `test_index_cli.py` to build a real tmp git repo, asserting match/mismatch against a pinned SHA
  behaves correctly (no checkout attempted either way, matching the Section 1 safety rule).
- **Output formatting**: string-building, tested like any other pure function.

**Not covered by the automated pytest suite:** actually running `index_repo` plus the 8 queries
against the real `epost-ios-theme-ui` checkout. That path is specific to this machine, not
something a portable test suite should assert against (unlike `tests/fixtures/mini_swift_repo`,
which ships inside the repo). `jarvis bench` itself, run by hand, is that verification.

## Follow-ups (not part of this design)

- Wiring `jarvis bench` into CI once a CI test workflow exists at all (currently: no workflow
  runs pytest either).
- Adding more pinned repos, or synthetic scale-graduated fixtures, once real numbers exist to
  decide whether that's actually needed.
