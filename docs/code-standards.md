# codeintel: Code Standards & Conventions

This document describes the patterns and conventions actually observed in the codeintel codebase, derived from the implementation and architectural decisions made across all 4 phases.

## Architecture & Design Patterns

### 1. Dataclass-First Type System

**Pattern:** All result types (nav/search results) are immutable frozen dataclasses, not Pydantic models.

**Why:** 
- codeintel has no HTTP API boundary that would benefit from Pydantic validation
- MCP tool functions return dicts built directly from dataclasses via `dataclasses.asdict()`
- Minimal validation overhead; focus on correctness at the source (database query builder)

**Examples:**
```python
@dataclass(frozen=True)
class Position:
    line: int
    character: int

@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    displayName: str | None = None
    kind: str | None = None
```

**Convention:** Use `| None` union syntax (Python 3.10+) instead of `Optional[T]`. All result dataclasses are frozen.

---

### 2. Isolation Seams: Protobuf & zstandard

**Pattern:** `scip_decoder.py` is the single module importing `scip_pb2` and `zstandard`.

**Why:**
- Future SCIP proto version bumps are localized to one file
- Tests can mock zstandard decompression without touching other modules
- Easier to track upstream API drift (scip.proto changes in sourcegraph/scip)

**Example isolation:**
```python
# scip_decoder.py (only imports scip_pb2)
import zstandard
from codeintel.scip_pb2 import Document

# query.py, search.py (NO scip_pb2 imports; delegate to scip_decoder)
from codeintel.scip_decoder import scip_range_to_positions
```

**Convention:** Isolation seams are explicitly commented in module docstrings.

---

### 3. Atomic Pointer-Swap Publishing

**Pattern:** Index publishing writes a new versioned `.db`, waits for graph/Zoekt completion, then atomically swaps the `current` pointer file via `os.replace()`.

**Why:**
- Queries reading the old index never see partial state
- A failure anywhere in the pipeline leaves the previously published index live
- Zero query downtime across reindex

**Implementation (index_cli.py):**
```python
# Write new index to temp file
new_db_path = index_dir / f"index-{commit_sha}.db"

# Run all expensive operations
populate_graph_for_repo(repo_slug, symbols)
zoekt_index(new_db_path)

# Only once all succeed, atomically swap
current_pointer = index_dir / "current"
os.replace(new_db_path, current_pointer)
```

**Convention:** Pointer files are small text files (1 line: the commit SHA or db name). Never write a pointer until *all* expensive operations complete.

---

### 4. Rebuild-Not-Accumulate Graph

**Pattern:** Each reindex clears that repo's outgoing package dependencies before recomputing them.

**Why:**
- Removed dependencies are properly retracted (not stale in the database)
- `blastRadius` always reflects each repo's *last* index run
- No accumulation bugs from partial re-runs or tool failures

**Implementation (graph.py):**
```python
def populate_graph_for_repo(repo_slug: str, symbols: list[str]) -> None:
    # DELETE old edges for this repo
    db.execute("DELETE FROM edges WHERE source_repo = ?", (repo_slug,))
    
    # INSERT new edges
    for pkg_name in extract_package_names(symbols):
        db.execute("INSERT INTO edges ...", (repo_slug, pkg_name, ...))
```

**Convention:** Always clear before rebuild in batch operations. Document the rebuild expectation in docstrings.

---

### 5. Direct sqlite3 Usage (No ORM)

**Pattern:** Raw SQL executed via stdlib `sqlite3` module; no SQLAlchemy, Tortoise, or GRDB.

**Why:**
- codeintel's schema is small (5-6 tables max): `repos`, `packages`, `edges` (registry.db); `documents`, `chunks`, `global_symbols`, `mentions`, `defn_enclosing_ranges` (index-*.db)
- SCIP schema is output of `scip expt-convert` — not a designed API, treated as moving target
- Zero external database dependency; easier to embed in single-user tool

**Exceptions:**
- `index_reader.py` (vendored from SCIP source) does use sqlite3 with some optimization flags (`mode=ro&immutable=1`)

**Convention:** Parameterized queries always; no string interpolation for user input (e.g., repo slugs).

---

### 6. Broad Exception Handling in MCP Server

**Pattern:** `server.py` catches all exceptions at the MCP tool boundary and returns uniform error payload: `{"error": "...message..."}`.

**Why:**
- Keeps the stdio server alive even on query bugs
- Consistent error format for MCP clients
- Separates internal exception details from user-facing messages

**Implementation (server.py):**
```python
@mcp.tool()
def goToDefinition(repo: str, path: str, line: int, character: int):
    try:
        result = _service().go_to_definition(repo, path, line, character)
        return {"location": asdict(result)} if result else {"location": None}
    except Exception as e:
        return {"error": str(e)}
```

**Convention:** No bare `except` without rationale. Broad catches are documented with "by design" comments.

---

### 7. Environment Variable Overrides

**Pattern:** Configuration via env vars with sensible defaults.

**Current overrides:**
- `CODEINTEL_DATA_DIR` → override `~/.codeintel` (default)
- Future: `CODEINTEL_ZOEKT_BIN` → override zoekt-webserver binary path

**Convention:** Prefix all env vars with `CODEINTEL_`. Use `.expanduser()` for path variables. Document in README and config.py docstring.

---

### 8. Single-Tenant Hardcoding

**Pattern:** `PROJECT = "_"` and `BRANCH = "_"` are pinned constants in `config.py`; the vendored `IndexConnectionCache` keys on `(project, repo, branch)`, but codeintel uses only `repo`.

**Why:**
- Single user, one repo per slug
- Reuse vendored cache code without modification
- Disk path `scip/_/<slug>/_/` is an artifact of the cache's path shape

**Convention:** These constants are intentionally hardcoded and not configurable. Document clearly in config.py docstring if ever tempted to make them dynamic.

---

## Code Organization

### Module Docstrings

Every module has a docstring explaining its purpose and key exports. Example:

```python
"""SCIP blob decoder (zstd+protobuf); isolation seam for protobuf dependency.

Decode zstd+protobuf `scip.Document` occurrences and `global_symbols.relationships`.
Also parses symbol packages and range→position conversions.

Known gap: SCIP v0.7.0 converter never populates `relationships`,
so `typeHierarchy` is empty on real indexes.
"""
```

**Convention:** Module docstrings should state purpose, key functions, and known gaps/limitations.

---

### Single-Purpose Functions

**Pattern:** Small, testable functions with clear contracts.

**Examples:**
- `repo_slug(name: str) -> str` — normalize user input, reject traversal attacks
- `scip_range_to_positions(range) -> (line, character)` — convert SCIP to LSP coordinates
- `should_ignore_path(path) -> bool` — centralized exclusion list

**Convention:** No "god functions" combining multiple concerns. If a function grows beyond ~50 lines, consider splitting.

---

### Error Messages

**Pattern:** Include context (repo slug, file path, query) in error messages; avoid generic "error occurred."

**Examples:**
- `f"Repo {repo} not indexed"`
- `f"Symbol not found in {path} at {line}:{character}"`
- `f"Failed to populate graph for {repo}: {e}"`

**Convention:** Error messages are user-facing (appear in MCP responses). Make them actionable.

---

## Testing Conventions

### Test File Organization

Each test file mirrors its source module:
- `test_models.py` → `models.py`
- `test_query.py` → `query.py`
- etc.

### Unit vs Integration Tests

**Unit tests:**
- Mock external dependencies (file I/O, subprocess calls, external HTTP)
- Use fixtures for synthetic SCIP blobs and SQLite schemas
- Mark with no special marker (run by default)

**Integration tests:**
- Call real binaries (scip-python, scip, zoekt-index, zoekt-webserver)
- Marked `@pytest.mark.integration`
- Concentrated in `test_index_cli.py` (the full pipeline)

**Run tests:**
```bash
uv run pytest                    # all tests
uv run pytest -m "not integration"  # unit only
uv run pytest -m integration     # real binaries only
```

**Convention:** Mark integration tests explicitly. Don't surprise developers with subprocess calls in unit tests.

---

### Fixtures

**Location:** `tests/fixtures/`

**Key fixtures:**
- `mini_py_repo/greeter.py` — Minimal Python file for scip-python indexing tests
- `scip_encoder.py` — Real zstd+protobuf SCIP blob builders (deterministic, repeatable)
- `synthetic_index.py` — Hand-copied real SQLite schema (documents/chunks/global_symbols tables with sample data)

**Convention:** Fixtures contain real, reproducible data (not random). Blob fixtures are compressed and can be inspected with zstandard tools.

---

## CLI Design

### Command Structure

All commands are under `codeintel`:
```bash
codeintel index <path> [--slug name]
codeintel list
codeintel status <slug>
codeintel reindex <slug>
codeintel forget <slug>
codeintel watch <path> [--debounce 5]
```

### Error Handling

CLI errors are printed to stderr with context (e.g., which repo failed, why).

```python
try:
    index_repo(path, slug)
except Exception as e:
    print(f"Error indexing {path}: {e}", file=sys.stderr)
    sys.exit(1)
```

**Convention:** Always exit with non-zero code on error. Include the repo/path in the error message.

---

## Documentation Standards

### Module Docstrings

Every module has a docstring (see above). Include:
- Purpose (1 sentence)
- Key exports / responsibilities
- Known gaps (upstream limitations, not implemented features)

### Function Docstrings

Functions with non-obvious behavior have docstrings:

```python
def repo_slug(name: str) -> str:
    """Normalize a user-chosen repo name into a directory-safe slug.

    Rejects `.`/`..` explicitly (not just `/`) — both survive the
    character-class substitution below unchanged since `.` is an allowed
    slug character, but either one alone is a path-traversal component.
    """
```

**Convention:** Use present tense ("normalizes", "validates"). Include edge cases (like `.` / `..` rejection).

### Comments

Comments explain *why*, not *what*. Code is readable; comments should justify decisions.

Example (good):
```python
# We use mode=ro&immutable=1 to enable WAL safety on NFS
# (vendored from source project's index_reader.py)
conn = sqlite3.connect(path, uri=True)
```

Example (bad):
```python
# Open the database
conn = sqlite3.connect(path, uri=True)
```

---

## Naming Conventions

### Modules
- Lowercase, snake_case: `index_reader.py`, `scip_decoder.py`

### Classes & Dataclasses
- PascalCase: `QueryService`, `SymbolInfo`, `IndexConnectionCache`

### Functions & Methods
- snake_case: `repo_slug()`, `index_repo()`, `blast_radius()`

### Constants
- UPPER_CASE: `PROJECT`, `BRANCH`, `DEFAULT_DATA_DIR`

### Private Functions & Attributes
- Prefix with `_`: `_service()`, `_query_service`, `_json_safe()`

### Environment Variables
- UPPER_CASE, prefixed with `CODEINTEL_`: `CODEINTEL_DATA_DIR`, `CODEINTEL_ZOEKT_BIN`

---

## Performance & Scalability

### Single-Threaded Query Path

The entire query path (from MCP tool → SQL → result) is synchronous and single-threaded. MCP clients are responsible for parallelization.

**Convention:** Don't add async/await unless blocking I/O becomes a bottleneck. codeintel is a single-user tool; no need for concurrent client handling.

### Connection Pooling

`IndexConnectionCache` maintains a bounded pool of SQLite connections per unique (project, repo, branch, pointer_content) tuple, garbage-collected on pointer changes.

**Convention:** Reuse the cache for all queries; never open raw sqlite3 connections in query logic.

---

## Type Hints

**Convention:** Use modern syntax (Python 3.10+):
- `str | None` instead of `Optional[str]`
- `list[T]` instead of `List[T]`
- `dict[K, V]` instead of `Dict[K, V]`

Full type hints on all public functions; private/internal functions may omit hints if obvious from context.

---

## Future-Proofing

### SCIP Version Pinning

SCIP proto is pinned to **v0.7.0** in `scip_pb2.py` (generated from sourcegraph/scip tag v0.7.0).

**Convention:** Document SCIP version in README and code. Any future version bump should be tracked in a plan, not a surprise refactor.

### SQLite Schema Versioning

The `scip expt-convert` output schema is not versioned. If scip releases change the schema, codeintel will need to adapt query logic.

**Convention:** Document which `scip` release was tested. Add comments to SQL queries if they depend on specific schema columns.

### Zoekt Server Lifespan

`ZoektLifecycle` manages the `zoekt-webserver` process (lazy-start, pidfile-tracked, killed on exit).

**Convention:** The lifecycle is encapsulated in `ZoektLifecycle`; don't spawn zoekt-webserver elsewhere. Always check if it's running before querying.
