---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# Coding Conventions

**Analysis Date:** 2026-08-21

## Naming Patterns

**Files:**
- Source modules: `snake_case.py` — one word, no prefixes (`config.py`, `query.py`, `graph.py`)
- Test files: `test_<module>.py` mirroring the source module 1:1 (`test_query.py` ↔ `query.py`)
- Multi-word names use underscores: `index_cli.py`, `index_reader.py`, `scip_decoder.py`
- The single exception is `scip_pb2.py` — a vendored gencode file whose name follows protobuf convention

**Functions:**
- `snake_case` throughout (`detect_language`, `populate_graph_for_repo`, `_run_semantic_stage`)
- Private/internal functions are `_`-prefixed (`_error_payload`, `_freshness_fields`, `_split_class`)
- CLI subcommand handlers follow `_cmd_<name>` (`_cmd_watch`, `_cmd_forget`)

**Variables:**
- `snake_case` for all locals and instance attributes (`_query_service`, `_own_process`)
- Module-level singletons use `_`-prefix: `_default: EmbeddingModel | None = None` in `src/jarvis/embeddings.py`

**Types:**
- Modern Python 3.12+ syntax throughout: `T | None` (never `Optional[T]`), `list[T]`, `dict[K, V]`, `tuple[str, ...]`
- Every source file opens with `from __future__ import annotations` to enable deferred evaluation of annotations
- Classes use `PascalCase`: `QueryService`, `GraphStore`, `ZoektLifecycle`, `EmbeddingModel`
- Constants use `UPPER_CASE`: `DEFAULT_MODEL`, `MAX_SEQ_LENGTH`, `IGNORED_DIRS`, `_MAX_HOPS`
- Enums use `PascalCase` with `StrEnum` base: `class Freshness(StrEnum): FRESH = "fresh"`

## Code Style

**Formatting:**
- No committed formatter/linter configuration (`[tool.ruff]` is absent — see `.github/workflows/test.yml` comment)
- Style is consistent with surrounding code; match what exists
- Double quotes for strings, single quotes acceptable in test parametrize data
- Trailing commas in multi-line collections and function signatures

**Linting:**
- No committed lint config. Keep style consistent with surrounding code
- Type-checking is not enforced in CI

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first)
2. Standard library imports (`os`, `sqlite3`, `subprocess`, `json`, `pathlib`)
3. Third-party imports (`httpx`, `mcp.server.fastmcp`)
4. Local imports (`from jarvis.config import ...`, `from jarvis.models import ...`)

**Isolation seam:**
- `scip_pb2` and `zstandard` imports are confined to `src/jarvis/scip_decoder.py` — never import them elsewhere
- `scip_pb2.py` is vendored gencode — never hand-edit

**Deferred imports for optional extras:**
- Optional-dependency imports happen inside the function that needs them, not at module level
- Pattern: `try: from sentence_transformers import SentenceTransformer except ImportError as exc: raise SemanticExtraMissingError(...) from exc`
- This means a base install (without `--extra semantic`/`--extra watch`) can import every module in `src/jarvis/` without ImportError
- `from typing import TYPE_CHECKING` guards type-only imports: `if TYPE_CHECKING: from jarvis.semantic import SemanticIndexReport` in `src/jarvis/index_cli.py`

**`__all__` exports:**
- Public API modules declare `__all__` explicitly (`src/jarvis/query.py`, `src/jarvis/graph.py`, `src/jarvis/scip_decoder.py`)
- Most modules omit `__all__` — they are internal, imported by `server.py` only

## Error Handling

**MCP tool boundary — broad catch to `{"error": ...}`:**
- Every `@mcp.tool` in `src/jarvis/server.py` wraps its body in `try/except Exception` and returns `{'error': str(exc)}`
- Comment on every catch: `# Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.`
- `_error_payload(repo, exc)` enriches certain error types (e.g. `IndexNotFoundError` + search-only status, `AmbiguousSymbolError`) with actionable context
- This is the ONLY place broad exception catching is acceptable — internal modules raise specific exceptions

**Internal modules — specific exceptions:**
- `IndexNotFoundError` for missing SCIP indices (`src/jarvis/index_reader.py`)
- `ZoektUnavailableError` for HTTP/transport failures (`src/jarvis/search.py`)
- `SemanticExtraMissingError` for missing `semantic` extra (`src/jarvis/embeddings.py`)
- `SeedPackageNotFoundError` for unknown graph seeds (`src/jarvis/graph.py`)
- `OccurrenceDecodeError` for corrupt SCIP data (`src/jarvis/scip_decoder.py`)
- `NotAGitRepositoryError`, `IndexingError`, `UnsupportedLanguageError` for CLI input validation (`src/jarvis/index_cli.py`)

**Non-raising helpers:**
- `_search_coverage_fields()` and `_registry_status()` in `src/jarvis/server.py` catch all exceptions internally and return `None`/neutral values — documented: "Any failure returns None so a broken registry degrades the message rather than replacing one error with another."

## Logging

**Framework:** `print()` to stderr for CLI messages; no structured logging framework

**Patterns:**
- CLI subcommands write user-facing messages to stderr via `argparse`-wired output or direct `print(..., file=sys.stderr)`
- MCP server uses no logging — it communicates exclusively via tool return dicts
- Warnings (e.g. prefix mismatch from `EmbeddingModel.prefix_warning()`) are returned as strings for the caller to decide where to surface — the module itself never prints

## Comments

**When to Comment:**
- Comments document *why*, not *what* — the code itself says what
- Load-bearing design decisions, non-obvious invariants, and regression motivations get comments
- Example from `src/jarvis/search.py`: `# A list lets a caller (test doubles, mainly) prefix an explicit interpreter rather than relying on the target's own shebang.`

**Docstrings:**
- Every module has a docstring explaining its purpose and key design decisions
- Public functions and classes have docstrings
- Docstrings carry rationale, not just parameter descriptions
- Example from `src/jarvis/config.py`: explains why the `scip/_/<slug>/_/` path shape exists ("artifact of reusing the vendored cache's path shape unchanged, not a user-facing contract")
- Example from `src/jarvis/models.py`: explains why dataclasses instead of pydantic

**Inline comments:**
- Explain non-obvious constraints, e.g. bitwise role filter: `# mentions.role is the RAW SymbolRoles bitmask value — not a normalized 0/1 boolean. Every role filter below therefore uses a bitwise AND (m.role & ? != 0), never exact equality.`

## Function Design

**Size:**
- Functions stay focused on a single responsibility
- MCP tool functions in `server.py` are thin wrappers (5-15 lines) delegating to `QueryService` / `ZoektLifecycle` / `GraphStore`
- Long functions exist in `index_cli.py` (the full indexing pipeline) but are broken into private helpers

**Parameters:**
- Use keyword-only arguments (`*`) to separate configuration from positional data: `def __init__(self, ..., *, port: int = 6070, binary: str | list[str] | None = None)`
- Injectable dependencies for testability: `client: httpx.Client | None = None` in `search_zoekt()` and `zoekt_repo_documents()`
- `root: Path | None = None` override pattern for directing file operations to a temp dir in tests

**Return Values:**
- Return concrete types, never `None` unless absence is meaningful
- MCP tools always return `dict[str, Any]` — never raise
- Query methods return tuples: `(locations, freshness)` or `(incoming, outgoing, freshness)`

## Module Design

**Exports:**
- `__all__` declared on modules with a public API surface
- Internal modules rely on explicit imports from consumers

**Data transfer objects:**
- Frozen dataclasses only — no Pydantic
- `@dataclass(frozen=True)` for all result/value types: `Position`, `Range`, `Location`, `SymbolInfo`, `ZoektHit`, `Package`, `RegisteredRepo`, `FreshnessSnapshot`
- MCP boundary converts via `dataclasses.asdict()` in `server.py`
- `StrEnum` for fixed string sets (`Freshness` in `src/jarvis/models.py`)

**Database access:**
- Raw `sqlite3` only — no ORM
- Always use parameterized queries (`conn.execute("... WHERE symbol = ?", (symbol,))`)
- Connection caching in `IndexConnectionCache` (read-only, keyed on pointer content not mtime)
- Schema constants as module-level `_SCHEMA` strings
- `_ensure_column()` for idempotent migrations in `src/jarvis/registry.py`

**Environment variable overrides:**
- All runtime-overridable values use `JARVIS_`-prefixed env vars
- `JARVIS_DATA_DIR` — data directory location (`src/jarvis/config.py`)
- `JARVIS_ZOEKT_BIN` — zoekt-webserver binary path (`src/jarvis/search.py`)
- `JARVIS_EMBEDDING_MODEL` — model name override (`src/jarvis/embeddings.py`)
- `JARVIS_EMBEDDING_BATCH_SIZE` — batch size override (`src/jarvis/embeddings.py`)
- `JARVIS_EMBEDDING_QUERY_PREFIX` / `JARVIS_EMBEDDING_DOC_PREFIX` — instruction prefix overrides (`src/jarvis/embeddings.py`)
- Pattern: `os.environ.get("JARVIS_...", DEFAULT)` with `int()` conversion where needed

---

*Convention analysis: 2026-08-21*
