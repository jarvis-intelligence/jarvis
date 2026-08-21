# Phase 1: Registry Foundation & Degradation Reporting - Research

**Researched:** 2026-08-21
**Domain:** SQLite registry schema extension (additive migration) + failure/degradation state persistence and reporting across CLI (`jarvis status`/`list`) and MCP surfaces (`getIndexStatus`, nav-tool error payloads)
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Persisted failure record shape**
- **D-01:** Origin taxonomy is stored as string slugs in a TEXT column — `failed_hard`, `signature`, `manual` — with `degraded` reserved for Phase 3. Slugs are readable straight from the sqlite3 CLI and new origins are additive — **Reversibility:** costly — slugs get persisted in registry rows and MCP payloads; renaming one later requires a data migration plus payload-compat handling
- **D-02:** Persist the **complete, unbounded** indexer stderr on failure — no truncation. (User explicitly chose full fidelity over a capped tail; watch-loop overwrite churn was accepted.)
- **D-03:** Two cause columns: `status_reason` (one-line classified summary for status surfaces) + a second column (e.g. `status_stderr`) holding the full raw stderr verbatim
- **D-04:** Failure fields (`status_origin`/`status_reason`/stderr column) are NULLed on the next successful index — the registry always reflects the latest run

**Failed-run registry behavior**
- **D-05:** A hard-failed **first** index creates a registry row (`status_origin='failed_hard'` + cause + path/language recorded) so `jarvis status`, `getIndexStatus`, and `jarvis list` can explain the failure and `jarvis reindex` works. Today nothing is persisted (`registry.upsert()` runs only post-publish)
- **D-06:** A failed reindex of an already-registered repo **fully overwrites** the row — `commit_sha`/`last_indexed`/`status` all reflect the failed attempt plus failure fields. (User chose full overwrite over keeping last-good facts in the row.)
- **D-07:** Nav tools keep serving the last published index when the latest run failed (atomic publish leaves it live). Status surfaces report both facts: "last index run failed" AND "navigation available but stale (indexed at <commit>)" — capability truth comes from the `current` pointer on disk, not from the row's status
- **D-08:** `jarvis list` gains status markers (✗ failed / ◐ search-only / ✓ ok) with the reason one-liner for failed rows

**Recovery command mapping**
- **D-09:** Recovery commands are **derived from origin at read time** via a per-origin mapping in code — never persisted per-row. Phase 3 adds one mapping entry for `degraded`
- **D-10:** `manual` origin reports the real escape that exists today: `jarvis forget <slug>` then `jarvis index <path>` without `--search-only`. Un-setting `--search-only` in place remains out of scope
- **D-11:** `signature` origin reports one generic recovery — `jarvis reindex <slug>` after fixing the toolchain. The existing per-signature remedy texts (Kotlin 2.2.0, bash ≥4.4 shim) are NOT surfaced into status
- **D-12:** `failed_hard` origin reports the full original command: `jarvis index <path>`

**Capability field shape**
- **D-13:** `getIndexStatus` exposes nested per-capability fields: `capabilities: {navigation: {available, reason, recovery}, search: {…}, semantic: {…}}` — an MCP client branches on `capabilities.navigation.available` without parsing prose — **Reversibility:** one-way — additive MCP payload keys, but once clients branch on them removal breaks the published tool contract
- **D-14:** Nav-tool error payloads gain additive structured keys — `state` (origin slug), `cause`, `recovery` — alongside the existing prose `error` string. Existing `error` key keeps its meaning — **Reversibility:** one-way — same published-contract reasoning as D-13
- **D-15:** Run outcome and capability are orthogonal layers: top-level `last_index_run: {outcome, origin, reason, recovery}` reports the latest run; `capabilities.*` reports on-disk truth. A repo can be `outcome=failed` with `navigation.available=true, reason="stale — indexed at <commit>"` (see D-07). Phase 3's degraded state slots into the same shape

### Claude's Discretion
- Exact column names (`status_origin`/`status_reason`/`status_stderr` are suggestions, shapes are locked)
- Marker glyphs and layout of the `jarvis list` status column
- Exact key naming/casing within the locked payload shapes of D-13/D-14/D-15
- How `capabilities.semantic` reports when the `semantic` extra isn't installed

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STAT-01 | `jarvis status` / `getIndexStatus` report degradation: origin (signature/opt-in/manual), persisted cause, recovery command | New `status_origin`/`status_reason`/`status_stderr` columns (this research: exact schema, migration pattern, write hook points in `index_repo()`); `_cmd_status`/`_cmd_list` output formats; recovery-derivation mapping (D-09); `getIndexStatus` payload extension |
| STAT-02 | Nav tools' error payloads explain degraded/search-only state with cause + recovery | Single choke point `_error_payload(repo, exc)` in `server.py:136-164` already branches on `IndexNotFoundError` + registry status — extends additively with `state`/`cause`/`recovery` keys (D-14) |
| STAT-03 | `getIndexStatus` exposes machine-readable capability fields (navigation availability, reason, recovery) | D-13/D-15 payload shapes; nav-capability truth from `read_pointer()`/`current` pointer + metadata (D-07); search/semantic capability derivation options documented below |

**Success-criteria → requirement mapping** (ROADMAP.md §Phase 1): SC1+SC2 → STAT-01; SC3 → STAT-02; SC4 → STAT-03; SC5 (additive `_ensure_column` migration, legacy `search_only=1` semantics preserved) → constraint on all three.
</phase_requirements>

## Project Constraints (from CLAUDE.md)

Extracted from `./CLAUDE.md` (repo root) and `AGENTS.md`; the planner must verify task compliance against these:

- **No new runtime deps** — "Direct `sqlite3`, no ORM, always parameterized queries"; frozen dataclasses not Pydantic; `.planning/PROJECT.md` adds "no new runtime deps without discussion"
- **Modern type-hint syntax**: `T | None`, `list[T]`, `dict[K, V]` — never `Optional[T]`
- **Test files mirror source modules 1:1** (`test_registry.py` ↔ `registry.py`, `test_server_tools.py` covers `server.py`); unit tests mock subprocess/IO; SQLite operations are NEVER mocked — real `tmp_path` databases
- **MCP boundary rule**: every tool catches broadly and returns `{"error": "..."}` dicts, never raises — keeps the stdio server alive
- **Isolation seams**: never import `scip_pb2`/`zstandard` outside `scip_decoder.py`
- **Atomic publish invariant**: a failure anywhere leaves the previous index live; never mutate a published `index-<sha>.db`
- **Comments document why, not what**; naming `snake_case`/`PascalCase`/`UPPER_CASE`
- **Env vars `JARVIS_`-prefixed**; no logging framework — CLI prints to stdout/stderr
- **Optional extras use deferred imports** — status reporting must work on a base install (no `semantic`/`watch` imports at module level)
- **CI gate**: `uv run pytest -m "not integration" -rs` must stay green; Conventional Commits (`feat(scope):` etc.)
- Version-bearing files must register in `scripts/check_versions.py` — this phase adds none

## Summary

Phase 1 is almost entirely a brownfield codebase problem, not a library problem: every surface it touches already exists and has a verified shape. The registry (`src/jarvis/registry.py`) already runs an idempotent `_ensure_column` migration on every open, and the exact pattern this phase needs — nullable additive TEXT/INTEGER columns declared in BOTH `_SCHEMA` (fresh DBs) and `_ensure_column` (legacy DBs), with a `test_*_column_migrates_onto_an_existing_database` regression test — is proven four times over (`scheme_override`, `semantic_indexed_at`, `semantic_include`, `language_override`, `search_only`, `tracked_files`). The failure-write side has three verified hook points in `index_repo()`: the two search-only publish paths (manual `--search-only` at line 783, signature fallback at line 826) and the two hard-failure handlers (`mark_status(slug, "failed")` at lines 796 and 888) — plus the pre-pipeline raises (scip version gate, language detection) that today persist NOTHING and are the real target of D-05.

The critical facts the planner needs: (1) `mark_status()` is a bare `UPDATE` that silently no-ops when no row exists — a hard-failed first index that raises before the "indexing" upsert leaves no row, which is why `jarvis reindex` can't recover it today (D-05); (2) `upsert()`'s `ON CONFLICT DO UPDATE` column list is the load-bearing place where D-04's "NULL failure fields on success" must land — a column not listed there survives reindex with stale data (exactly why `tracked_files` is deliberately excluded, per its own docstring); (3) two truth sources must stay orthogonal (D-07/D-15): the registry row says what the last RUN did, the `current` pointer on disk says what navigation can do RIGHT NOW — `_publish_search_only` deletes the pointer (`_retire_scip_artifacts`), a hard failure leaves it live, so nav capability must be computed from the pointer, never from row status; (4) one genuine tension exists between locked D-11 and verified code — `jarvis reindex <slug>` does NOT retry the SCIP build for `search_only=1` rows because `_resolve_search_only()` resolves the persisted flag (flagged in Open Questions, needs user confirmation); (5) SQLite's official ALTER TABLE ADD COLUMN rules impose no obstacle for nullable TEXT columns, and unbounded stderr (D-02) is bounded only by SQLITE_MAX_LENGTH's 1-billion-byte default.

**Primary recommendation:** Implement as (a) three nullable TEXT columns added via the existing `_ensure_column` pattern, (b) a dedicated `Registry.record_failure(...)` write (INSERT…ON CONFLICT, not `mark_status`) plus explicit NULL-clearing in `upsert`'s conflict list, (c) origin-slug constants + a read-time recovery-mapping function living in `registry.py` beside `SEARCH_ONLY_STATUS` (both CLI and server already import it), (d) capability computation in `server.py` reading the `current` pointer via the existing `QueryService.get_index_status` machinery, and (e) `_error_payload` extended additively per D-14.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Failure/degradation persistence (write path) | CLI / Indexing Pipeline (`index_cli.py` `index_repo()`) | — | The pipeline is the only place the failure cause (exception + indexer stderr) exists; watch and reindex both funnel through `index_repo()` (verified: `index_cli.py:899`, `index_cli.py:1074`) |
| Schema + migration (`status_origin`/`status_reason`/`status_stderr`) | Data layer (`registry.py`) | — | Registry owns the `repos` table DDL and the `_ensure_column` migration idiom; every other layer goes through `Registry` |
| Origin taxonomy + recovery-command derivation (D-09) | Data layer (`registry.py`, read-time pure function) | CLI + MCP (both render it) | Both surfaces import `registry` already; `SEARCH_ONLY_STATUS` precedent lives there; single source of truth, never persisted per-row |
| Human status reporting (`jarvis status`, `jarvis list`) | CLI tier (`index_cli.py` `_cmd_status`/`_cmd_list`) | — | CLI owns print-to-stdout formatting; no logging framework |
| Machine-readable capability payload (D-13/D-15) | MCP Server tier (`server.py` `get_index_status`) | — | Published MCP tool contract; additive keys only |
| Nav-tool structured errors (D-14) | MCP Server tier (`server.py` `_error_payload`) | — | Single choke point all 5 nav tools already route through (verified at `server.py:180,194,213,232,260`) |
| Navigation-capability truth (D-07) | On-disk storage (`current` pointer + `*.metadata.json` via `index_reader.py`/`query.py`) | Registry row (run outcome only) | Pointer survives hard failure (atomic publish), is deleted by search-only publish (`_retire_scip_artifacts`) — it is the only correct availability source |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib `sqlite3` | 3.53.1 (local; via CPython ≥3.12) | Registry storage + additive migrations | Existing convention: raw sqlite3, no ORM, parameterized queries [VERIFIED: CLAUDE.md "Conventions worth knowing"] |
| FastMCP (`mcp[cli]>=1.2.0,<2.0.0`) | pinned in `pyproject.toml:57` | MCP stdio tool surface for `getIndexStatus` + nav tools | Existing server framework; capped <2.0.0 because "mcp 2.0.0 removed `mcp.server.fastmcp`" [VERIFIED: pyproject.toml:53-57] |
| pytest | 8.3.4 (local; `>=8.3` dev dep) | Test framework, TDD mode enabled in config | Existing convention, CI gate `pytest -m "not integration" -rs` [VERIFIED: pyproject.toml:109-111, .planning/config.json `tdd_mode: true`] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `dataclasses` (stdlib) | — | Frozen dataclass row shape (`RegisteredRepo`) + `asdict()` for payloads | Extending `RegisteredRepo` with the three new fields |
| `sqlite3` CLI | 3.53.1 local | Manual verification against the live `~/.jarvis/registry.db` | Proven usable this session (see Runtime State Inventory) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Three separate columns (`status_origin`+`status_reason`+`status_stderr`) | One JSON blob column | Rejected by locked D-03 (two cause columns) + D-01 (slugs readable from sqlite3 CLI); blob kills `WHERE status_origin = ?` queries and CLI readability |
| Persisting recovery command per row | Read-time derivation | Locked by D-09 — derivation stays consistent when Phase 3 adds `degraded`; per-row persistence would need a data migration on every wording change |
| `ALTER TABLE ... ADD COLUMN` per open | User_version-keyed migration framework | Overkill: the `_ensure_column` idiom is proven, idempotent, and lock-safe (re-raises non-duplicate errors); a framework adds weight with no benefit at 12 columns |

**Installation:**
```bash
uv sync            # no new dependencies — this phase adds none
```

**Version verification:** `uv run pytest --version` → pytest 8.3.4; `uv run python -c "import sqlite3; print(sqlite3.sqlite_version)"` → 3.53.1 (run this session). No new packages, so no registry lookups were needed.

## Package Legitimacy Audit

**This phase installs zero external packages** (PROJECT.md constraint: "no new runtime deps without discussion"; everything needed is stdlib or already pinned). The Package Legitimacy Gate does not apply — no packages to check, none removed, none flagged. The planner must NOT introduce dependencies.

## Architecture Patterns

### System Architecture Diagram

```text
                        WRITE PATH (what the last RUN did)
┌──────────────────────────────────────────────────────────────────────────┐
│  jarvis index / reindex / watch  →  index_repo()   (src/jarvis/index_cli)│
│                                                                          │
│  pre-pipeline (today: NO row persisted — D-05 target)                    │
│    ├─ _git_head ✗ ─────────────────────────────┐                         │
│    ├─ check_scip_version ✗ ────────────────────┤                         │
│    └─ detect_language ✗ ───────────────────────┤                         │
│                                               ▼                         │
│  run starts: upsert(status="indexing", commit_sha=NULL)                   │
│                                               │                          │
│         ┌───────────────── indexer failure ───┴───────────────┐          │
│         ▼                                                    ▼          │
│  signature matches _SEARCH_ONLY_SIGNATURES?         no match / other step │
│         │ yes                                          │ fails          │
│         ▼                                               ▼               │
│  _publish_search_only()                        mark_status(slug,         │
│    + _retire_scip_artifacts()                    "failed")  ← today:      │
│      (DELETES current pointer)                   no cause saved          │
│         │                                               │               │
│         ▼            NEW: record origin='signature'      ▼               │
│                    NEW: record origin='failed_hard' + reason + stderr    │
│         │ success paths:                                     │          │
│         ▼                                               ▼               │
│  upsert(status='search-only', search_only=1)   upsert('indexed'/'partial')│
│                  NEW: NULL failure fields on every success upsert (D-04) │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                   ~/.jarvis/registry.db  `repos` table
                   (+ status_origin / status_reason / status_stderr,
                    added by _ensure_column on open — SC5)

                        READ PATH (what surfaces report)
┌──────────────────────────────────────────────────────────────────────────┐
│ registry row (run outcome: last_index_run)      current pointer on disk  │
│         │                                       (capability truth, D-07) │
│         ├─→ jarvis status <slug>  (cause + recovery lines)               │
│         ├─→ jarvis list        (✓ / ◐ / ✗ markers, D-08)                 │
│         ├─→ getIndexStatus     (capabilities.{navigation,search,semantic}│
│         │                        + last_index_run, D-13/D-15)            │
│         └─→ nav tool errors    (state/cause/recovery keys, D-14)         │
│                ↑ IndexNotFoundError ── read_pointer() raises when the     │
│                  pointer is absent (search-only / never indexed)          │
└──────────────────────────────────────────────────────────────────────────┘
```

A reader can trace the primary use case: an index run fails hard → row records `status_origin='failed_hard'` + reason + full stderr → `jarvis status`/`getIndexStatus`/`jarvis list` render cause + `jarvis index <path>` recovery → nav tools still answer from the untouched `current` pointer while status explains the staleness.

### Recommended Project Structure

No new files. The repo convention is one flat module per concern; this phase extends three existing modules plus their existing test files:

```
src/jarvis/
├── registry.py     # + status_origin/status_reason/status_stderr in _SCHEMA & _ensure_column;
│                   # + RegisteredRepo fields; + record_failure(); upsert() NULL-clears (D-04);
│                   # + origin constants + recovery-command derivation (D-09)
├── index_cli.py    # + failure writes at the 4 hook points; _cmd_status/_cmd_list reporting (D-08)
└── server.py       # + capabilities/last_index_run in getIndexStatus (D-13/D-15);
                    # + state/cause/recovery in _error_payload (D-14)
tests/
├── test_registry.py     # migration + persistence + recovery-mapping tests
├── test_index_cli.py    # failure-row write hooks, list/status output
└── test_server_tools.py # capability payload + structured nav-error tests
```

### Pattern 1: Additive column migration (extend `_SCHEMA` AND `_ensure_column` together)
**What:** Every historical column is declared twice: in `_SCHEMA` (so fresh DBs get it at CREATE) and in an `_ensure_column` call in `Registry.__init__` (so legacy DBs gain it at open).
**When to use:** For each of the three new columns.
**Example** — the existing, proven invocation [VERIFIED: src/jarvis/registry.py:118-123]:
```python
        _ensure_column(self._conn, "scheme_override", "TEXT")
        _ensure_column(self._conn, "semantic_indexed_at", "TEXT")
        _ensure_column(self._conn, "semantic_include", "TEXT")
        _ensure_column(self._conn, "language_override", "TEXT")
        _ensure_column(self._conn, "search_only", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(self._conn, "tracked_files", "INTEGER")
```
The guard itself [VERIFIED: src/jarvis/registry.py:41-57]: `ALTER TABLE repos ADD COLUMN {name} {decl}`, committing per column, swallowing only `"duplicate column name"` `OperationalError` and re-raising everything else (notably "database is locked" from a concurrent `jarvis watch` reindex).

**SQLite-side constraints on what `decl` may contain** [VERIFIED: sqlite.org/lang_altertable.html §4, fetched this session]:
> - The column may not have a PRIMARY KEY or UNIQUE constraint.
> - The column may not have a default value of CURRENT_TIME, CURRENT_DATE, CURRENT_TIMESTAMP, or an expression in parentheses.
> - If a NOT NULL constraint is specified, then the column must have a default value other than NULL.

Plain nullable `TEXT` (the recommended decl for all three columns) is unrestricted; ADD COLUMN only rewrites schema text, so "execution time of such ALTER TABLE commands is independent of the amount of data in the table". Unbounded stderr storage is safe: "The maximum number of bytes in a string or BLOB in SQLite is defined by the preprocessor macro SQLITE_MAX_LENGTH. The default value of this macro is 1 billion" — and "the SQLITE_MAX_LENGTH parameter also determines the maximum number of bytes in a row" [VERIFIED: sqlite.org/limits.html §1, fetched this session].

### Pattern 2: Failure write as upsert, never bare UPDATE
**What:** `mark_status()` is `UPDATE repos SET status = ?, last_indexed = ? WHERE slug = ?` [VERIFIED: src/jarvis/registry.py:162-167] — an UPDATE that matches zero rows is a silent no-op. D-05's "failed first index creates a row" therefore requires an `INSERT ... ON CONFLICT(slug) DO UPDATE` shaped write (like `upsert()`), not a `mark_status()` variant.
**When to use:** The new failure-record method and every pre-pipeline hook.

### Pattern 3: `upsert()`'s ON CONFLICT list is where stale data goes to hide
**What:** `upsert()`'s conflict clause names exactly which columns a reindex overwrites [VERIFIED: src/jarvis/registry.py:143-149]:
```python
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status, "
            "scheme_override=excluded.scheme_override, "
            "semantic_include=excluded.semantic_include, "
            "language_override=excluded.language_override, "
            "search_only=excluded.search_only",
```
Any column absent from that list keeps its old value across a reindex — the mechanism `mark_tracked_files`' docstring documents deliberately ("a reindex upserts `indexing` before the count is known, and `upsert`'s ON CONFLICT list would then reset it to NULL" [VERIFIED: src/jarvis/registry.py:193-199]). D-04 inverts this deliberately: the success upsert MUST list the three failure columns and set them NULL.
**When to use:** Implementing D-04 — and its mirror image: the failure write (D-06) must overwrite `commit_sha`/`last_indexed`/`status` too. Note D-06's row-overwrite is already today's de-facto behavior for mid-pipeline failures: the pre-run upsert writes `commit_sha=None, status="indexing"` (`index_cli.py:802-803`) before the pipeline `try`, and `mark_status` then stamps `"failed"` — so the old commit SHA is already lost on a failed reindex; the phase's job is to also persist origin/reason/stderr.

### Pattern 4: Read-time recovery derivation (D-09)
**What:** A pure function `origin → recovery command`, parameterized by row data (slug, path). Recommended shape, colocated with the existing status constant `SEARCH_ONLY_STATUS = "search-only"` [VERIFIED: src/jarvis/registry.py:21]:
```python
# Suggested (discretion: exact names) — values from locked D-10/D-11/D-12:
ORIGIN_FAILED_HARD = "failed_hard"   # recovery: jarvis index <path>
ORIGIN_SIGNATURE   = "signature"     # recovery: jarvis reindex <slug>  (see Open Question 1)
ORIGIN_MANUAL      = "manual"        # recovery: jarvis forget <slug> && jarvis index <path>
# "degraded" reserved for Phase 3 (D-01/D-09)
```
**When to use:** `jarvis status`, `jarvis list` reason lines, `getIndexStatus` → `last_index_run.recovery`, nav-tool `recovery` key — all call the same function. Never persist the rendered command.

### Pattern 5: Two orthogonal truth sources (D-07/D-15)
**What:** `read_pointer()` raises `IndexNotFoundError` on missing/empty pointer [VERIFIED: src/jarvis/index_reader.py:66-72]:
```python
    pointer_path = _index_dir(filestore_root, project, repo, branch) / POINTER_FILENAME
    try:
        content = pointer_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise IndexNotFoundError(f"no published index for {project}/{repo}@{branch}") from exc
```
A hard failure never flips the pointer (atomic publish; failure handler at `index_cli.py:887-889` runs before `_publish_atomically`), so nav stays available from the old index. A search-only publish deletes the whole index dir first (`_retire_scip_artifacts`, `index_cli.py:615-645`, `shutil.rmtree(index_dir)`), so nav is genuinely unavailable. The stale commit comes from the pointer's sibling `index-<sha>.metadata.json` — `QueryService.get_index_status` already surfaces it as `FreshnessSnapshot(commit, generated_at, stale, freshness, checked_at)` [VERIFIED: src/jarvis/query.py:62-68].
**When to use:** `capabilities.navigation.available` = pointer readable; `reason` = stale commit when the row reports a failed/degraded run (D-15's `outcome=failed` + `navigation.available=true` combination). Never derive navigation availability from `repos.status`.

### Pattern 6: Additive MCP payload keys at a single choke point
**What:** All 5 nav tools already funnel exceptions through `_error_payload(repo, exc)` [VERIFIED: `server.py:180,194,213,232,260`], which today returns the prose-only search-only explanation:
```python
    if isinstance(exc, IndexNotFoundError) and _registry_status(repo) == SEARCH_ONLY_STATUS:
        return {
            "error": (
                f"{repo} is indexed search-only: it has no SCIP index, so navigation "
                "tools cannot answer. searchCode and semanticSearch do work on it. "
                "This happens when the language's indexer cannot build the repo — "
                "for example an Android/Gradle project."
            )
        }
```
[VERIFIED: src/jarvis/server.py:140-148]. `getIndexStatus` returns `{"repo", "indexed", "status", **freshness, **searchCoverage}` [VERIFIED: src/jarvis/server.py:295-296]. D-13/D-14 extend both additively — the repo's published pattern precedent is `searchCoverage` (added the same way) and `resolvedSymbol` ("appears only when resolution changed the input" — conditional keys are established practice [VERIFIED: server.py:167-170]).
**When to use:** STAT-02/STAT-03. The `AmbiguousSymbolError` branch shows the exact convention for structured non-prose keys: `"candidates": [...]` "A structured list, not prose inside `error`, so the caller can act on it without parsing English" [VERIFIED: server.py:156-158].

### Anti-Patterns to Avoid
- **Deriving navigation availability from `repos.status`** — contradicts D-07; the pointer on disk is the only truth (a `failed` row can still be fully navigable).
- **Persisting the recovery command or prose remedy text per row** — violates D-09/D-11 (remedy texts explicitly stay out of status).
- **A `NOT NULL` decl or default expression on the new columns** — SQLite ADD COLUMN forbids non-constant defaults; nullable TEXT is the only shape consistent with both SQLite rules and legacy-row NULL semantics.
- **Truncating stderr at capture time** — D-02 locks full fidelity; any truncation is display-only.
- **Mocking SQLite in tests** — repo convention: real `tmp_path` databases only.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Idempotent schema migration | version-table migration runner | `_ensure_column` (existing) | Proven across 6 columns; lock-safe (re-raises non-duplicate errors); ADD COLUMN is O(schema-text) regardless of row count [VERIFIED: sqlite.org/lang_altertable.html] |
| Nav availability probe | New filesystem checks in `server.py` | `read_pointer()` / `QueryService.get_index_status` | Already the canonical pointer read; already wired into `getIndexStatus`; already raises the `IndexNotFoundError` the error path keys on |
| Row → JSON shaping | Custom serializers | `RegisteredRepo` frozen dataclass + `_json_safe`/`asdict` | Established pattern (`FreshnessSnapshot` → `_freshness_fields`) |
| Freshness/staleness of the served index | New git comparison | Existing `FreshnessSnapshot` + `stale`/`commit` | `query.py:489-521` already compares pointer metadata commit vs `git rev-parse HEAD` |
| Legacy-DB regression coverage | New fixture machinery | The raw-`sqlite3` old-schema builder pattern | `test_search_only_column_migrates_onto_an_existing_database` builds a pre-column DB with `CREATE TABLE` + `INSERT` by hand [VERIFIED: tests/test_registry.py:234-261] |

**Key insight:** every sub-problem in this phase already has a proven in-repo implementation pattern; the work is wiring, not invention.

## Runtime State Inventory

> This phase ships a schema migration to live user state (`~/.jarvis/registry.db`), so the inventory is included. Audited this session against the developer's real registry (read-only `SELECT`).

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | **Live `~/.jarvis/registry.db`, 14 rows**: 3 rows `status='search-only', search_only=1` (`spm-cache`, `luz_epost_ios`, `ios-stress-app`) — legacy rows that will have `status_origin IS NULL` after migration; 3 rows `status='failed'` (`epost-ios-theme-ui`, `agent_runtime`, `cairn`) — exist but carry NO cause anywhere (the exact gap STAT-01 fixes); statuses `indexed`/`partial` on the rest [VERIFIED: `sqlite3 ~/.jarvis/registry.db "SELECT slug, status, search_only FROM repos"`, run this session] | Code edit only — additive columns; no data migration. Planner must define read-time behavior for NULL-origin rows (see Open Question 2) and must NOT rewrite `search_only` values (SC5) |
| Live service config | None — no external services store registry state; `registry.db` is the single store | — |
| OS-registered state | None — jarvis registers no OS-level state (no launchd/pm2/Task Scheduler; `zoekt-webserver` is pidfile-managed per-process and unrelated to registry state) | — |
| Secrets/env vars | None — no secrets; `JARVIS_DATA_DIR` is the only path-relevant env var and is unchanged | — |
| Build artifacts | None affected — compiled-wheel pipeline unchanged; no new version-bearing files (check_versions.py untouched) | — |

**Nothing found in category** is stated inline where empty. The migration risk concentrates entirely in "Stored data": the 3 legacy search-only rows and 3 cause-less failed rows ARE the SC5 acceptance population, available locally for manual verification.

## Common Pitfalls

### Pitfall 1: `mark_status()` silently no-ops on a missing row
**What goes wrong:** Writing D-05's failure row via `UPDATE ... WHERE slug = ?` affects 0 rows when the failure happened before the "indexing" upsert (scip version gate `index_cli.py:754`, `detect_language` raise at `:771`, invalid persisted language override at `:764`) — no row, no report, and `jarvis reindex <slug>` fails with "no such repo" (it requires a row: `_cmd_reindex` at `:954-955`).
**Why it happens:** Those raises predate any registry write today; the row only exists from `index_repo`'s upsert at `:802` onward.
**How to avoid:** Failure persistence must be an INSERT…ON CONFLICT write with a row-shaped API that tolerates being called with unknown language (`UNKNOWN_LANGUAGE = "unknown"` exists precisely because `registry.language` is NOT NULL [VERIFIED: index_cli.py:74-77]).
**Warning signs:** A test asserting a failed first index produces `jarvis status` output; if it passes only for post-upsert failures, D-05 is half-done.

### Pitfall 2: Forgetting `upsert`'s conflict-list NULL-clearing (D-04)
**What goes wrong:** Success reindex leaves `status_origin='failed_hard'` + old stderr on the row — status surfaces lie about the current run forever.
**Why it happens:** `upsert`'s `ON CONFLICT DO UPDATE` only touches listed columns (Pattern 3).
**How to avoid:** Add the three columns to the conflict SET list with NULL values on success paths (and to the INSERT column list), mirroring how `tracked_files` is deliberately EXCLUDED — the inverse decision, documented in its docstring.
**Warning signs:** `test_upsert_...` variants that fail→succeed→read and assert `status_origin is None`.

### Pitfall 3: `SELECT` column lists and positional unpack drift
**What goes wrong:** Columns added to `_SCHEMA`/`_ensure_column` but not to `get()`/`list()` SELECTs (or appended in a different order than `_row_to_repo`'s tuple unpack at `registry.py:88-90`) — silent `None`s or unpack errors.
**Why it happens:** The row mapper is positional, not name-based.
**How to avoid:** Extend both SELECTs (`registry.py:178-179`, `:187-188`) and `_row_to_repo` in the same change; a roundtrip test (`upsert with failure fields → get → assert`) catches it.

### Pitfall 4: Deriving capability from row status (D-07 violation)
**What goes wrong:** `capabilities.navigation.available=false` for a `failed` repo whose pointer is live — the client stops calling nav tools that would have answered.
**Why it happens:** Row status is the obvious lookup; but atomic publish guarantees the pointer outlives the failed run.
**How to avoid:** Compute `navigation.available` from `read_pointer`/`get_connection` success; enrich `reason` with the metadata commit ("stale — indexed at <sha>") exactly as D-15 specifies.
**Warning signs:** A repo with `outcome=failed` AND `navigation.available=true` in the same payload is CORRECT, not a bug — tests must assert that combination.

### Pitfall 5: D-11's recovery command doesn't retry the build (verified tension)
**What goes wrong:** Reporting `jarvis reindex <slug>` for `signature` rows advises a command that re-publishes search-only: `_resolve_search_only` resolves the persisted `search_only=1` when the flag isn't passed [VERIFIED: index_cli.py:537-545 — "This is what stops a repo that already proved un-indexable from re-running a doomed multi-minute build"], and `_cmd_reindex` never passes the flag (`:957-961` builds a Namespace without it). The CLI's own fallback note promises "this is remembered, so reindex/watch will not repeat the build" [VERIFIED: index_cli.py:820-825]. REQUIREMENTS.md's Out of Scope locks this: "Un-setting `--search-only` for existing manual/signature paths — Unchanged behavior".
**Why it happens:** The locked D-11 wording predates/accretes with the one-way `search_only=1` design.
**How to avoid:** Surface at plan time (Open Question 1): either the user accepts the wording ("after fixing the toolchain" — which today requires `jarvis forget` first), or D-11's recovery for this phase becomes `jarvis forget <slug>` + `jarvis index <path>`. Do NOT silently change reindex semantics — that's the locked out-of-scope.
**Warning signs:** Any plan task that "fixes" reindex to retry the build — scope violation.

### Pitfall 6: Legacy NULL-origin rows (SC5)
**What goes wrong:** Recovery derivation crashes or reports nothing for the 3 pre-migration `search_only=1` rows — `status_origin IS NULL` after `_ensure_column` migration.
**Why it happens:** Migration adds the column; it cannot know which path created the row (manual flag vs signature match are indistinguishable post-hoc — both wrote `status='search-only', search_only=1`).
**How to avoid:** Read-time fallback for `search_only=1 AND status_origin IS NULL` (recommended: treat as `manual` — its D-10 escape `jarvis forget` + `jarvis index <path>` is valid for both origins; see Open Question 2). Write-time origins apply only to NEW writes.
**Warning signs:** A migration test seeding a legacy search-only row (the live-registry trio is the model) asserting status output still explains + recovers.

### Pitfall 7: stderr size in MCP payloads
**What goes wrong:** Echoing `status_stderr` verbatim into `getIndexStatus`/nav-tool payloads bloats every status call (stderr can be MBs — scip-java Gradle dumps).
**Why it happens:** D-02's unbounded persistence is about the REGISTRY; payloads need only `status_reason` (D-03's one-liner) plus `cause`.
**How to avoid:** Payloads carry `status_reason`; full stderr surfaces via `jarvis status` (display truncation is fine — persistence stays unbounded). 
**Warning signs:** Any `asdict(entry)` dump of the full row into a payload.

### Pitfall 8: `jarvis list` tab-format consumers
**What goes wrong:** D-08 markers break scripts parsing the 5-column TSV (`{slug}\t{status}\t{language}\t{commit}\t{path}` [VERIFIED: index_cli.py:916]).
**Why it happens:** Glyphs inside the `status` column change its value.
**How to avoid:** Discretion item — cleanest option: prefix the status column with the glyph (`✓ indexed`) or add a separate leading marker column, keeping column count/order stable; decide in plan. Note the same line today doubles as machine-readable output and human summary.
**Warning signs:** Integration tests or user scripts piping `jarvis list` through `cut -f2`.

### Pitfall 9: `partial` status is a success, not a degradation
**What goes wrong:** Treating `PARTIAL_STATUS = "partial"` rows (`polaris` in the live registry) as failed/degraded in the new origin taxonomy.
**Why it happens:** "partial" means published with symbols but no navigable positions — a success variant [VERIFIED: index_cli.py:68-72 docstring].
**How to avoid:** `last_index_run.outcome` for partial stays a success-family outcome; no failure origin is recorded (NULL), nav capability already reflects reality via the pointer.
**Warning signs:** Mapping every non-"indexed" status to an origin.

## Code Examples

### Failure-row write hook (the load-bearing integration)
Current failure handlers, verbatim [VERIFIED: src/jarvis/index_cli.py:887-889]:
```python
    except Exception as exc:
        registry.mark_status(slug, "failed")
        raise IndexingError(str(exc)) from exc
```
And the search-only signature branch [VERIFIED: src/jarvis/index_cli.py:817-829]:
```python
                reason = _search_only_reason(str(exc))
                if reason is None:
                    raise
                print(
                    f"note: {slug} cannot be SCIP-indexed — {reason}. "
                    "Falling back to search-only; this is remembered, so reindex/watch "
                    "will not repeat the build.",
                    file=sys.stderr,
                )
                semantic_ok, tracked = _publish_search_only(repo_path, slug, root, semantic_include)
                registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS,
                                scheme_override=scheme, semantic_include=semantic_include,
                                language_override=language_override, search_only=True)
```
The `IndexingError` from `_run` already embeds the full subprocess stderr — verbatim [VERIFIED: src/jarvis/index_cli.py:349-350]:
```python
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")
```
So the pipeline's failure sites already hold everything D-02/D-03 want to persist: `str(exc)` is the classified/verbatim carrier; `status_reason` is the one-line classification (e.g. `f"{step} failed"`), `status_stderr` the raw tail. Planner decides the exact split; the data is available at every hook point without touching `_run`.

### Origin slugs and signature matcher (verbatim values the taxonomy describes)
[VERIFIED: src/jarvis/index_cli.py:87-104]:
```python
_SEARCH_ONLY_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("AbstractMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    ...
    (
        ("No SCIP shards found",),
        "the build produced no SCIP shards — for Android/AGP this is expected, because "
        "scip-java's Gradle plugin keys off standard source sets that AGP replaces with "
        "variants (upstream scip-java#177)",
    ),
)
```
Note D-11: these per-signature reason strings are what the row's `status_reason` may hold, but the per-signature REMEDY prose must NOT be surfaced in status; recovery is the generic per-origin mapping.

### Registry status values (verbatim, the taxonomy's neighbors)
[VERIFIED: src/jarvis/registry.py:78]: `status: str  # "indexed" | "indexing" | "failed" | "partial" | "search-only"`
[VERIFIED: src/jarvis/registry.py:21]: `SEARCH_ONLY_STATUS = "search-only"`

### Current status/list output (the surfaces STAT-01 extends)
[VERIFIED: src/jarvis/index_cli.py:936-939]:
```python
    print(f"slug: {repo.slug}\npath: {repo.path}\nlanguage: {repo.language}\nstatus: {repo.status}")
    print(f"commit: {repo.commit_sha or '-'}\nlast_indexed: {repo.last_indexed.isoformat()}")
    semantic = repo.semantic_indexed_at.isoformat() if repo.semantic_indexed_at else "-"
    print(f"semantic: {semantic}")
```

### Legacy-migration regression test (the pattern to copy for each new column)
[VERIFIED: tests/test_registry.py:234-261] — builds a pre-column DB with raw sqlite3, opens `Registry`, asserts the row survives with the default: shown in full in Pattern 1's source; the planner should replicate it once per new column (or parameterized over the three).

### MCP capability payload shape (locked by D-13/D-15 — key casing is discretion)
```python
# Suggested skeleton — shapes locked; exact casing discretionary (CONTEXT: Claude's Discretion)
def _capability_fields(repo: str) -> dict[str, Any]:
    entry = _registry_entry(repo)          # extends _registry_status to full row (best-effort)
    indexed, freshness = _service().get_index_status(repo)   # pointer truth + stale commit
    ...
    return {
        "last_index_run": {"outcome": ..., "origin": entry.status_origin, "reason": ...,
                           "recovery": recovery_for(entry)},
        "capabilities": {
            "navigation": {"available": indexed, "reason": ..., "recovery": ...},
            "search": {"available": ..., "reason": ...},
            "semantic": {"available": ..., "reason": ...},
        },
    }
```
All keys additive alongside the existing `{"repo", "indexed", "status", **freshness, **searchCoverage}` return [VERIFIED: server.py:295-296].

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `mark_status(slug, "failed")` — status string only, no cause | Failure record: origin + one-line reason + full stderr, row-shaped write | This phase | `jarvis status`/`getIndexStatus`/nav errors can explain and direct recovery |
| Search-only states indistinguishable post-hoc (manual vs signature both `search_only=1`) | `status_origin` slug distinguishes them at write time | This phase | Per-origin recovery commands (D-10/D-11); taxonomy ready for Phase 3 `degraded` |
| Nav-tool errors: prose-only explanation when `status == "search-only"` | Additive `state`/`cause`/`recovery` structured keys | This phase | MCP clients branch without parsing prose (mirrors existing `candidates` precedent) |

**Deprecated/outdated:** nothing in-repo is deprecated by this phase; all changes are additive (columns, payload keys, output lines).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | MCP clients tolerate additive keys in tool-result dicts without breaking (FastMCP serializes dicts to JSON as-is) | Architecture Patterns 6 | Low — precedent: `searchCoverage`, `resolvedSymbol`, `candidates` were all added additively to live clients `[ASSUMED]` (based on repo history, not external verification) |
| A2 | Glyphs `✓`/`◐`/`✗` render acceptably in the user's terminals (UTF-8 locales) | D-08 discretion | Low — cosmetic; ASCII fallback (`ok`/`so`/`FAIL`) trivial |
| A3 | Recovery-derivation lives in `registry.py` as a module-level pure function (suggested names `ORIGIN_*`, `recovery_for`) | Pattern 4 | None — placement/naming is planner discretion; alternative (new tiny module) violates flat-module economy |
| A4 | `jarvis status` displays full stored stderr (possibly with a display hint), since D-02 locks persistence not display | Pitfall 7 | Low — display truncation is compatible with D-02 either way; planner should pick explicitly |
| A5 | `capabilities.search.available` derives from row presence + zoekt shard existence (not a live webserver probe — status must never spawn zoekt, cf. `base_url_if_running` usage in CLAUDE.md) | STAT-03 | Medium — exact semantics not user-locked; recommend non-spawning derivation, planner confirms |
| A6 | `capabilities.semantic.available` = `semantic_indexed_at is not None` on the row (recorded at index time), reason text mentions the extra when absent — discretion item from CONTEXT | STAT-03 | Low — planner may refine (e.g. import probe), but must not import the `semantic` extra eagerly at module level (repo rule) |

**Note on external-source tags:** the two SQLite claims are tagged `[VERIFIED: sqlite.org/...]` with verbatim quotes fetched directly from the official docs this session. The gsd `classify-confidence` seam rates the generic `webfetch` provider LOW (it cannot see the URL); the tag definitions in this agent's source hierarchy (confirmed via tool AND authoritative source) govern, and sqlite.org is authoritative for SQLite. Flagged here for transparency.

## Open Questions

1. **D-11 recovery command vs. verified reindex semantics (highest priority)**
   - What we know: `jarvis reindex <slug>` on a `search_only=1` repo re-publishes search-only; `_resolve_search_only` (`index_cli.py:537-545`) resolves the persisted flag; `_cmd_reindex` (`:957-961`) never passes `--search-only`/`--no-search-only`; REQUIREMENTS.md Out of Scope locks un-setting the flag out of the milestone.
   - What's unclear: whether the user intends D-11's `jarvis reindex <slug>` literally (a command that succeeds but never retries the build), or expects it to actually restore navigation.
   - Recommendation: ask at plan time. If literal, ship as locked (the message can say "after fixing the toolchain"). If not, the correct recovery string for this phase is `jarvis forget <slug>` + `jarvis index <path>` — same as D-10 — and D-11's distinction collapses until Phase 3.

2. **Legacy `search_only=1` rows with NULL origin — what origin do they report?**
   - What we know: 3 live rows (`spm-cache`, `luz_epost_ios`, `ios-stress-app`); manual vs signature is unrecoverable post-hoc; SC5 requires their current semantics preserved.
   - What's unclear: which origin/recovery the status surfaces print for them.
   - Recommendation: treat as `manual` at read time (its escape hatch works for both origins); planner encodes this in the recovery function's NULL-origin branch and covers it with a migration test.

3. **`last_index_run.outcome` value set**
   - What we know: statuses today are `indexed`/`indexing`/`failed`/`partial`/`search-only` (verbatim comment, registry.py:78); `partial` is a success variant.
   - What's unclear: whether `outcome` mirrors `status` verbatim or is a new small enum (`success`/`failed`/`degraded`).
   - Recommendation: mirror the registry status string verbatim (zero new taxonomy to maintain; Phase 3 adds `degraded` naturally per D-15).

4. **Does `jarvis status` print the full stderr, a tail, or a pointer to it?**
   - What we know: D-02 locks persistence (unbounded); display is unconstrained.
   - Recommendation: print the one-line reason by default with full stderr available (e.g. full text after a blank line, or a hint like "full log: sqlite3 ~/.jarvis/registry.db ..."). Planner picks; tests assert persistence, not console width.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | ✓ | 3.14.3 local (project floor `>=3.12,<3.15` — `pyproject.toml:51`) | — |
| uv | dev workflow / test commands | ✓ | 0.11.19 | — |
| pytest | TDD + validation | ✓ | 8.3.4 (`>=8.3` pinned) | — |
| sqlite3 (lib) | registry + migrations | ✓ | 3.53.1 (far above any ADD COLUMN constraint era) | — |
| git | freshness comparison paths | ✓ | 2.50.1 | — |
| Live `~/.jarvis/registry.db` | SC5 manual verification | ✓ | 14 rows incl. 3 legacy search-only + 3 failed | Copy to `tmp_path` in tests; never mutate in tests |
| External binaries (scip-python/zoekt/…) | integration-marked e2e only | (not probed — not needed) | — | Integration tests self-skip when absent (`shutil.which` guards, TESTING.md) |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** none — the whole phase is unit-testable without external binaries (mock `_run`/subprocess per repo convention).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.4, custom marker `integration` |
| Config file | `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`) |
| Quick run command | `uv run pytest -m "not integration" -rs` |
| Full suite command | `uv run pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STAT-01 (CLI) | `jarvis status` shows origin/cause/recovery for failed + search-only rows; `jarvis list` shows markers | unit | `uv run pytest tests/test_index_cli.py -k "status or list" -q` | ✅ file exists (tests added to it) |
| STAT-01 (registry) | New columns persist/roundtrip; migrate onto pre-v1.0 DB; NULL-cleared on success (D-04); failure-row write creates row when absent (D-05) | unit | `uv run pytest tests/test_registry.py -q` | ✅ |
| STAT-01 (MCP) | `getIndexStatus` surfaces cause + recovery (prose/top-level fields) | unit | `uv run pytest tests/test_server_tools.py -k "index_status" -q` | ✅ |
| STAT-02 | Nav tool on search-only/failed repo returns `state`/`cause`/`recovery` keys alongside `error` | unit (anyio in-process MCP session) | `uv run pytest tests/test_server_tools.py -k "error_payload or missing_repo" -q` | ✅ |
| STAT-03 | `capabilities.navigation.available` branches correctly: pointer live + row failed → true+stale; search-only → false+recovery | unit | `uv run pytest tests/test_server_tools.py -k "capabilities or index_status" -q` | ✅ |
| SC5 | Pre-v1.0 DB opens, gains columns, `search_only=1` semantics unchanged | unit | `uv run pytest tests/test_registry.py -k "migrat" -q` | ✅ |

All commands are unit-scope (<30s) and require no external binaries.

### Sampling Rate
- **Per task commit:** `uv run pytest -m "not integration" -rs`
- **Per wave merge:** `uv run pytest` (includes integration, which self-skips without binaries)
- **Phase gate:** Full suite green before `/gsd-verify-work`; config enables `tdd_mode` — write failing tests first for each behavior above

### Wave 0 Gaps
None — existing test infrastructure (files, fixtures, in-process MCP client pattern via `create_connected_server_and_client_session`, raw-sqlite3 legacy-DB builder) covers every phase requirement. New tests belong in the three existing mirrored files.

## Security Domain

`security_enforcement: true`, ASVS level 1 (`.planning/config.json`). Local-first single-user tool: no auth, no session, no network surface, no crypto.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | None — no auth by design (ARCHITECTURE.md "Authentication: None") |
| V3 Session Management | no | None — stdio MCP, no sessions |
| V4 Access Control | no | Single-user local tool; registry.db is user-owned |
| V5 Input Validation | yes (narrow) | All SQL parameterized (repo rule); NEW surface: external-tool stderr persisted verbatim and re-displayed — validate nothing (D-02 locks verbatim), but do NOT interpolate it into SQL strings or shell commands; parameterized writes only |
| V6 Cryptography | no | No secrets stored |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via stderr content (indexer output persisted) | Tampering | Parameterized `INSERT`/`UPDATE` (existing rule); never f-string column VALUES [VERIFIED: registry.py upsert precedent] |
| Terminal escape injection (raw stderr printed by `jarvis status`) | Tampering | Low risk (local, user's own toolchain output); if displayed, print as-is per D-02 fidelity — note in plan; no shell interpolation of stderr anywhere |
| Registry poisoning via `status_reason` into MCP payloads | Tampering/Spoofing | Payloads carry reason as data (JSON-serialized), never executed; FastMCP JSON-encodes dict values |

## Sources

### Primary (HIGH confidence)
- `src/jarvis/registry.py` (read in full this session) — `_SCHEMA`, `_ensure_column`, `RegisteredRepo`, `upsert`, `mark_status`, `get`/`list` SELECTs
- `src/jarvis/index_cli.py:59-143, 325-403, 537-558, 615-715, 716-893, 896-1046` — signatures, `_run`, `_resolve_search_only`, `_retire_scip_artifacts`, `_publish_search_only`, `index_repo` failure paths, `_cmd_list`/`_cmd_status`/`_cmd_reindex`
- `src/jarvis/server.py` (read in full) — `_error_payload`, `_registry_status`, `_search_coverage_fields`, `get_index_status`, nav-tool choke points
- `src/jarvis/index_reader.py:59-73` — `read_pointer` / `IndexNotFoundError`
- `src/jarvis/query.py:62-95, 489-521` — `FreshnessSnapshot`, `get_index_status`
- `tests/test_registry.py:234-294` — legacy migration + idempotency test patterns
- `tests/test_server_tools.py` — error-payload/status/coverage test inventory
- `https://sqlite.org/lang_altertable.html` §4 — ADD COLUMN restrictions (fetched this session, quoted verbatim)
- `https://sqlite.org/limits.html` §1 — SQLITE_MAX_LENGTH default 1e9 (fetched this session, quoted verbatim)
- Live `~/.jarvis/registry.db` — read-only audit of real pre-migration state

### Secondary (MEDIUM confidence)
- `.planning/codebase/{ARCHITECTURE,INTEGRATIONS,TESTING}.md` — corroborating maps (cross-checked against source where cited)
- `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, `.planning/PROJECT.md` — scope and constraints

### Tertiary (LOW confidence)
- None — no training-data-only claims are load-bearing; A1/A2 in the Assumptions Log are the only unverified claims, both discretionary

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; everything verified against the repo and live environment
- Architecture: HIGH — all write/read hook points read this session with line references; payload shapes locked by CONTEXT.md
- Pitfalls: HIGH — each grounded in verbatim code; Pitfall 5 (D-11 tension) verified end-to-end through `_resolve_search_only` → `_cmd_reindex` → Out-of-Scope lock

**Research date:** 2026-08-21
**Valid until:** 2026-09-21 (stable internal-codebase domain; re-verify only if `registry.py`/`index_cli.py`/`server.py` change before planning)

---

*Phase: 1-Registry Foundation & Degradation Reporting*
