---
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
---

# Coding Conventions

**Analysis Date:** 2026-09-08

Canonical style contract: `docs/code-standards.md` (~482 lines). This document
reflects what the tree at HEAD actually enforces. One known staleness: §9 of
`docs/code-standards.md` describes a 4-file/5-field version guard including the
Claude/Codex plugin manifests — the source of truth is `scripts/check_versions.py`,
which checks exactly three declarations (`pyproject.toml [project] version`,
`server.json version`, `server.json packages[0].version`) and deliberately
excludes the plugins (their truth moved to `jarvis-intelligence/jarvis-index`).

## Typing & Language Level

**Python:** `>=3.12,<3.15` (`pyproject.toml` `requires-python`); `.python-version` pins 3.12.

**Modern syntax only, everywhere:**
- `from __future__ import annotations` is the first import in every module (e.g. `src/jarvis/query.py`, `src/jarvis/server.py`, `tests/test_query.py`)
- `str | None`, never `Optional[str]`
- `list[T]`, `dict[K, V]`, `tuple[A, B]`, never `typing.List`/`Dict`
- `datetime.now(UTC)` with `from datetime import UTC` (`src/jarvis/query.py`)
- `StrEnum` (3.11+) for enums, never `str, Enum`:

```python
class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
```
(`src/jarvis/models.py`; also `DescriptorKind` in `src/jarvis/symbols.py`)

**Exception — registry status strings:** `src/jarvis/registry.py` uses module-level
plain-string constants (`SEARCH_ONLY_STATUS = "search-only"`, `DEGRADED_STATUS`,
`ORIGIN_FAILED_HARD`/`ORIGIN_SIGNATURE`/`ORIGIN_MANUAL`/`ORIGIN_FALLBACK`) rather
than a StrEnum, so origins stay inspectable via the raw sqlite3 CLI.

**Hints:** full annotations on all public functions; `-> None` on `main()`s;
`__all__` where the module is an API surface (`src/jarvis/query.py`,
`tests/fixtures/scip_encoder.py`).

## Data Models

**Frozen dataclasses, not Pydantic.** All result shapes are
`@dataclass(frozen=True)` in `src/jarvis/models.py` (`Position`, `Range`,
`Location`, `SymbolInfo`, `DocumentSymbolEntry`, `CallHierarchyEntry`,
`TypeHierarchyEntry`) and per-module where local (`ZoektHit` in
`src/jarvis/search.py`, `FreshnessSnapshot` in `src/jarvis/query.py`,
`RegisteredRepo` in `src/jarvis/registry.py`). Rationale (recorded in
`src/jarvis/models.py`'s docstring): no HTTP boundary to validate; MCP tools
return dicts built via `dataclasses.asdict()`.

**Never introduce Pydantic** for internal result types — `server.py` is the only
serialization point and it uses `asdict()` plus `_json_safe()` (datetime → ISO).

## MCP Boundary (`src/jarvis/server.py`)

- Tool functions are **plain sync defs** (FastMCP handles transport); registered
  with a camelCase wire name distinct from the snake_case function:
  `@mcp.tool(name="goToDefinition")` over `def go_to_definition(...)`.
- Payload keys are camelCase for MCP clients: `displayName`, `resolvedSymbol`,
  `searchCoverage`, `candidateTotal` — even though the dataclasses/SQL columns
  are snake_case.
- Module-level singletons `_query_service`/`_zoekt_lifecycle`/`_graph_store`
  (lazily built by `_service()`/`_zoekt()`/`_graph()`); tests overwrite them via
  `monkeypatch.setattr(server, "_query_service", ...)`.
- Optional presence is expressed by key omission, not nulls: e.g.
  `_resolved_fields` returns `{}` when resolution changed nothing
  (`src/jarvis/server.py`).

## Error Handling

**Two-tier strategy — strict inner, forgiving boundary:**

1. **Typed exceptions everywhere internally.** One exception type per failure
   mode, each with a docstring stating when it fires:
   - `IndexingError`, `MissingBinaryError(IndexingError)`,
     `UnsupportedLanguageError`, `NotAGitRepositoryError` — `src/jarvis/index_cli.py`
   - `IndexNotFoundError` — `src/jarvis/index_reader.py`
   - `SymbolNotFoundError`, `AmbiguousSymbolError` (carries structured
     `candidates` + `total`) — `src/jarvis/symbols.py`
   - `ZoektUnavailableError` — `src/jarvis/search.py`
   - `OccurrenceDecodeError` (fail-closed: never partial data) — `src/jarvis/scip_decoder.py`
   - `SemanticExtraMissingError` — `src/jarvis/embeddings.py`
   - `SeedPackageNotFoundError` — `src/jarvis/graph.py`
   - `NoSemanticIndexError` — `src/jarvis/semantic.py`
   Subclassing for narrower catches is idiomatic: `MissingBinaryError`
   IS-A `IndexingError` "so every existing except-site keeps working"
   (`src/jarvis/index_cli.py:184`).

2. **Broad `except Exception` exists ONLY at the MCP tool boundary**, converting
   to a uniform `{"error": str(e)}` payload so the stdio server never dies
   (`src/jarvis/server.py`). The other sanctioned broad catch is the `watch`
   reindex loop, commented "Broad on purpose" with the rationale
   (`src/jarvis/index_cli.py` `_cmd_watch`). Any other broad catch requires a
   why-comment.

**Error message content:** user-facing (they surface in MCP payloads and CLI
stderr); always carry context — repo slug, path, symbol, step:
`f"Repo {repo} not indexed"`, `f"Symbol not found in {path} at {line}:{character}"`.
Failure text is persisted verbatim and unbounded; only *display* truncates
(`src/jarvis/index_cli.py`, D-02).

**Never fabricate on failure:** undecodable Zoekt lines degrade to `""`
(`_decode_line` in `src/jarvis/search.py`); blob decode errors raise rather than
return partial rows (`src/jarvis/scip_decoder.py`).

## CLI Conventions (`src/jarvis/index_cli.py`)

- **stdout is machine-parseable; everything else goes to stderr.** Success
  prints exactly `indexed <slug>`; warnings/notes/semantic reports all use
  `print(..., file=sys.stderr)`. `jarvis list` emits a 5-column TSV
  (slug, glyph-prefixed status, language, commit, path) with an optional 6th
  reason column for failed/degraded rows (D-08); `jarvis status` emits
  `key: value` lines. Do not add prose to stdout.
- Errors: `print(f"error: {exc}", file=sys.stderr)` then `return 1`; exit
  non-zero on every failure path.
- Interactive prompts (the SEMA-01 semantic-install offer) gate on
  `sys.stdin.isatty() and sys.stdout.isatty()` — automation must never block
  on stdin (SEMA-02), and run **after** publish so interaction cannot risk the
  index.

## Database Access

- Raw stdlib `sqlite3`, no ORM. Two databases: `registry.db`
  (`src/jarvis/registry.py`, `src/jarvis/graph.py`) and per-commit
  `index-<sha>.db` (queried via `src/jarvis/index_reader.py`).
- **Parameterized queries only** — `?` positional or `:named`; never f-string
  user input into SQL. Every query site in `src/jarvis/query.py` follows:
  `conn.execute("SELECT ... WHERE symbol = ?", (symbol,))`.
- Index connections go through the vendored `IndexConnectionCache`
  (`src/jarvis/index_reader.py`, read-only `mode=ro&immutable=1`); never open
  raw connections in query logic.
- Schema drift is handled by idempotent `_ensure_column` ALTERs
  (`src/jarvis/registry.py`) — additive columns, never destructive migrations.
- Batch rebuilds clear-before-insert (delete a repo's edges, then recompute —
  `populate_graph_for_repo` in `src/jarvis/graph.py`).

## Concurrency

**Fully synchronous query path — no async/await on the tool/service/query
layers.** Parallelism is the MCP client's job; jarvis is single-user. Don't add
async unless blocking I/O measurably hurts. (The only async code is in tests
driving the in-memory MCP client session, `tests/test_server_tools.py`.)

## Optional Extras & Lazy Imports

Extras live in `pyproject.toml` `[project.optional-dependencies]`:
`watch = ["watchdog>=4.0"]`, `semantic = ["lancedb>=0.20",
"sentence-transformers>=3.0", "tree-sitter>=0.25",
"tree-sitter-language-pack>=0.1"]`.

**Rule: a base install (no extras) must import every module in `src/jarvis/`
without error.** Therefore extra-only imports are deferred inside the function
that needs them, never at module top-level:

```python
# src/jarvis/semantic.py (inside the method)
try:
    import lancedb
except ImportError as exc:
    from jarvis.embeddings import SemanticExtraMissingError, _INSTALL_HINT
    raise SemanticExtraMissingError(_INSTALL_HINT) from exc
```
Same shape: `from sentence_transformers import SentenceTransformer` in
`src/jarvis/embeddings.py`, `import watchdog` in `_cmd_watch`
(`src/jarvis/index_cli.py`). Missing-extra failure must be a specific catchable
exception whose message names the install command, including the distribution
name: `jarvis-mcp[semantic]` (not just `uv sync` — unusable for PyPI installs).

## Environment Variables

- Always `JARVIS_`-prefixed: `JARVIS_DATA_DIR`, `JARVIS_FALLBACK_SEARCH_ONLY`,
  `JARVIS_ZOEKT_BIN`, `JARVIS_EMBEDDING_MODEL`,
  `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`,
  `JARVIS_EMBEDDING_DOC_PREFIX` (plus `JARVIS_BIN_DIR` / `JARVIS_SETUP_SOURCED`
  in `setup.sh`).
- Path vars pass through `.expanduser()`; default is `~/.jarvis`
  (`data_dir()` in `src/jarvis/config.py`).
- Boolean env parsing is a strict allowlist (`_FALLBACK_TRUTHY = frozenset({"1",
  "true", "yes", "on"})` in `src/jarvis/config.py`) — a typo'd value must warn
  and read as off, never silently enable.

## Naming Patterns

**Modules:** snake_case, single words where possible: `index_reader.py`,
`scip_decoder.py`, `symbol_search.py`.
**Classes/dataclasses:** PascalCase — `QueryService`, `ZoektLifecycle`,
`IndexConnectionCache`, `SemanticStore`.
**Functions/methods:** snake_case — `repo_slug()`, `index_repo()`,
`blast_radius()`, `reciprocal_rank_fusion()`.
**Constants:** UPPER_CASE — `PROJECT`, `BRANCH`, `DEFAULT_DATA_DIR`,
`CONTENT_FORMAT`.
**Private:** leading underscore — `_service()`, `_git_head()`, `_run()`,
`_json_safe()`, `_SLUG_UNSAFE`.

## Comments & Docstrings (rationale-first, decision-ID-anchored)

- **Comments explain why, never what.** The recurring house style anchors a
  decision to its design ID — `D-xx` (decisions), `FALL-xx` (fallback policy),
  `SEMA-xx` (semantic extras), `WR-xx` (watch/recovery), `T-xx-xx` (tasks) —
  and references the regression-pin test that guards it. Examples:
  `src/jarvis/index_cli.py:186` (`FALL-04`), `src/jarvis/registry.py:29`
  (`D-01`), `src/jarvis/server.py:145` (`D-15`), `src/jarvis/config.py:70`
  (`FALL-02`).
- **Module docstrings** state purpose, key exports, provenance ("Ported from an
  internal reference implementation's …", listing what was deliberately
  dropped), and known gaps/upstream issues. See `src/jarvis/search.py`,
  `src/jarvis/models.py`, `src/jarvis/scip_decoder.py`.
- **Function docstrings** are present-tense and cover edge cases and the bug
  they prevent (`repo_slug` in `src/jarvis/config.py` explaining `.`/`..`
  traversal rejection).
- Test docstrings follow the same discipline — they name the regression being
  pinned (see `tests/test_query.py`, `tests/test_index_cli.py`).

## Structural Patterns to Reuse

- **Isolation seam:** exactly one module may import a vendored heavy dep —
  `src/jarvis/scip_decoder.py` is the only importer of `scip_pb2` /
  `zstandard`; everything else goes through it.
- **Atomic pointer-swap publish:** write `index-<sha>.db`, run graph + Zoekt,
  then `os.replace()` the `current` pointer last (`src/jarvis/index_cli.py`).
  Never publish a pointer before all expensive steps succeed.
- **Injectable collaborators for testability:** optional keyword params with
  real defaults — `client: httpx.Client | None = None` in `search_zoekt()`
  (`src/jarvis/search.py`), clock-driven `Debouncer` (`src/jarvis/watch.py`),
  `model=` embedder injection in `index_semantic()` (`src/jarvis/semantic.py`).
- **Single-purpose functions:** normalize/validate/convert helpers
  (`repo_slug`, `should_ignore_path`, `hash_file`, `language_for`,
  `scip_range_to_positions`). Split anything growing past ~50 lines.
- **Version consistency is an invariant, not a todo:** any new file declaring
  the release version must be added to `scripts/check_versions.py` (guarded by
  `tests/test_check_versions.py` + CI).
- **Single-tenant constants stay pinned:** `PROJECT = "_"`, `BRANCH = "_"`
  in `src/jarvis/config.py` are intentional; don't make them configurable.

## Tooling & Formatting

- **No formatter/linter config exists** (no `[tool.ruff]`, no black config).
  `.github/workflows/test.yml` documents why there is no lint step and the
  precondition for adding one (configure ruff first, then gate). Until then,
  match surrounding style manually.
- Dependency ranges are capped below the next major throughout
  `pyproject.toml`, each with a comment stating the concrete break an uncapped
  range would cause. New dependencies need the same treatment.
- Comment-captured invariants accompany non-obvious build config (see the
  `requires-python`, `mcp<2.0.0`, and cibuildwheel notes in `pyproject.toml`).

## Import Organization

Order within a module:
1. `from __future__ import annotations`
2. stdlib (`import subprocess`, `from pathlib import Path`, …)
3. third-party (`import httpx`, `import pytest` — tests only)
4. local absolute imports (`from jarvis import config`,
   `from jarvis.query import QueryService`; `from tests.fixtures...` in tests)

No relative imports; no wildcard imports.

---

*Convention analysis: 2026-09-08*
