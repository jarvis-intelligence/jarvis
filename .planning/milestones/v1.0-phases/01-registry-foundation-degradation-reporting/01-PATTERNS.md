# Phase 1: Registry Foundation & Degradation Reporting - Pattern Map

**Mapped:** 2026-08-21
**Files analyzed:** 6 (3 source, 3 test)
**Analogs found:** 6 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/jarvis/registry.py` | model/service | CRUD | `src/jarvis/registry.py` (itself — extend existing) | exact |
| `src/jarvis/index_cli.py` | controller/hook | request-response | `src/jarvis/index_cli.py` (itself — extend existing) | exact |
| `src/jarvis/server.py` | controller | request-response | `src/jarvis/server.py` (itself — extend existing) | exact |
| `tests/test_registry.py` | test | CRUD | `tests/test_registry.py` (itself — extend existing) | exact |
| `tests/test_index_cli.py` | test | request-response | `tests/test_index_cli.py` (itself — extend existing) | exact |
| `tests/test_server_tools.py` | test | request-response | `tests/test_server_tools.py` (itself — extend existing) | exact |

All six files are modifications to existing modules — no new files. Each extends a proven pattern within its own file.

## Pattern Assignments

### `src/jarvis/registry.py` (model/service, CRUD)

**Analog:** itself — extending existing `_SCHEMA`, `_ensure_column`, `RegisteredRepo`, `_row_to_repo`, `upsert`, `get`, `list`

#### 1a. Module-level status constant (precedent for origin constants)

**Source:** `src/jarvis/registry.py:21`
```python
SEARCH_ONLY_STATUS = "search-only"
```

**New origin constants follow this pattern** — colocated module-level strings that both CLI and server already import from registry:
```python
ORIGIN_FAILED_HARD = "failed_hard"   # recovery: jarvis index <path>
ORIGIN_SIGNATURE   = "signature"     # recovery: jarvis reindex <slug>
ORIGIN_MANUAL      = "manual"        # recovery: jarvis forget <slug> && jarvis index <path>
# ORIGIN_DEGRADED  = "degraded"     # reserved for Phase 3
```

#### 1b. `_SCHEMA` additive column declaration

**Source:** `src/jarvis/registry.py:24-37`
```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    slug TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    commit_sha TEXT,
    last_indexed TEXT NOT NULL,
    status TEXT NOT NULL,
    scheme_override TEXT,
    semantic_indexed_at TEXT,
    semantic_include TEXT,
    language_override TEXT,
    search_only INTEGER NOT NULL DEFAULT 0,
    tracked_files INTEGER
)
"""
```

**Extend by appending three nullable TEXT columns** at end of column list:
- `status_origin TEXT` — origin taxonomy slug
- `status_reason TEXT` — one-line classified summary
- `status_stderr TEXT` — full raw indexer stderr

All three are nullable TEXT (NOT NULL with non-constant default is forbidden by SQLite ALTER TABLE; nullable TEXT is consistent with legacy-row NULL semantics per RESEARCH Pitfall 4).

#### 1c. `_ensure_column` migration calls

**Source:** `src/jarvis/registry.py:118-123`
```python
        _ensure_column(self._conn, "scheme_override", "TEXT")
        _ensure_column(self._conn, "semantic_indexed_at", "TEXT")
        _ensure_column(self._conn, "semantic_include", "TEXT")
        _ensure_column(self._conn, "language_override", "TEXT")
        _ensure_column(self._conn, "search_only", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(self._conn, "tracked_files", "INTEGER")
```

**Extend by adding three more calls** after `tracked_files`:
```python
        _ensure_column(self._conn, "status_origin", "TEXT")
        _ensure_column(self._conn, "status_reason", "TEXT")
        _ensure_column(self._conn, "status_stderr", "TEXT")
```

The `_ensure_column` guard itself (lines 41-57) swallows only `"duplicate column name"` and re-raises all other `OperationalError` — no changes needed there.

#### 1d. `RegisteredRepo` frozen dataclass — new fields

**Source:** `src/jarvis/registry.py:80-91`
```python
@dataclass(frozen=True)
class RegisteredRepo:
    slug: str
    path: str
    language: str
    commit_sha: str | None
    last_indexed: datetime
    status: str  # "indexed" | "indexing" | "failed" | "partial" | "search-only"
    scheme_override: str | None = None
    semantic_indexed_at: datetime | None = None
    semantic_include: tuple[str, ...] = ()
    language_override: str | None = None
    search_only: bool = False
    tracked_files: int | None = None
```

**Append three fields** after `tracked_files`:
```python
    status_origin: str | None = None
    status_reason: str | None = None
    status_stderr: str | None = None
```

All nullable, defaulting to `None`. Modern type-hint syntax: `str | None`, never `Optional[str]`.

#### 1e. `_row_to_repo` positional unpack

**Source:** `src/jarvis/registry.py:94-108`
```python
def _row_to_repo(row: tuple) -> RegisteredRepo:
    (slug, path, language, commit_sha, last_indexed, status,
     scheme_override, semantic_indexed_at, semantic_include, language_override,
     search_only, tracked_files) = row
    return RegisteredRepo(
        slug=slug,
        path=path,
        language=language,
        commit_sha=commit_sha,
        last_indexed=datetime.fromisoformat(last_indexed),
        status=status,
        scheme_override=scheme_override,
        semantic_indexed_at=datetime.fromisoformat(semantic_indexed_at) if semantic_indexed_at is not None else None,
        semantic_include=_split_include(semantic_include),
        language_override=language_override,
        search_only=bool(search_only),
        tracked_files=tracked_files,
    )
```

**Critical pitfall (RESEARCH Pitfall 3):** The unpack is positional. Add the three new columns to both the tuple destructuring AND the constructor call, in the same order as the SELECT.

#### 1f. `get()` and `list()` SELECT column lists

**Source:** `src/jarvis/registry.py:178-188`
```python
    def get(self, slug: str) -> RegisteredRepo | None:
        row = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include, language_override, search_only, tracked_files "
            "FROM repos WHERE slug = ?",
            (slug,),
        ).fetchone()
        return _row_to_repo(row) if row is not None else None

    def list(self) -> list[RegisteredRepo]:
        rows = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include, language_override, search_only, tracked_files "
            "FROM repos ORDER BY slug"
        ).fetchall()
        return [_row_to_repo(row) for row in rows]
```

**Append** `status_origin, status_reason, status_stderr` to both SELECT column lists. Must be in same order as `_row_to_repo` destructuring.

#### 1g. `upsert()` — ON CONFLICT SET list for D-04 NULL-clearing

**Source:** `src/jarvis/registry.py:133-155`
```python
    def upsert(self, slug, path, language, commit_sha, status, ..., search_only=False):
        ...
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, "
            "scheme_override, semantic_indexed_at, semantic_include, language_override, "
            "search_only) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status, "
            "scheme_override=excluded.scheme_override, "
            "semantic_include=excluded.semantic_include, "
            "language_override=excluded.language_override, "
            "search_only=excluded.search_only",
            ...
```

**D-04 (NULL failure fields on success):** Add the three failure columns to the ON CONFLICT SET list, setting them to NULL:
```sql
status_origin=excluded.status_origin, status_reason=excluded.status_reason, status_stderr=excluded.status_stderr
```
Since the success paths don't pass these values, they'll be NULL in the INSERT side too — which is the desired behavior. The key insight from RESEARCH Pattern 3: any column NOT in the ON CONFLICT list survives a reindex with stale data. The inverse of `tracked_files` (deliberately excluded per its docstring at line 193-199).

#### 1h. New `record_failure()` method — INSERT…ON CONFLICT DO UPDATE

**Why not `mark_status()`:** `mark_status()` is a bare `UPDATE ... WHERE slug = ?` that silently no-ops on zero rows (lines 162-167). D-05 requires creating a row when one doesn't exist (pre-pipeline failures before the "indexing" upsert). Must use `INSERT…ON CONFLICT(slug) DO UPDATE` shape, same as `upsert()`.

**Source pattern:** `upsert()` at lines 133-155 — the same INSERT…ON CONFLICT DO UPDATE skeleton, but with failure-specific columns.

**Required shape:**
```python
def record_failure(self, slug: str, path: str, language: str, origin: str,
                    reason: str, stderr: str) -> RegisteredRepo:
    last_indexed = datetime.now(UTC)
    self._conn.execute(
        "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, "
        "scheme_override, semantic_indexed_at, semantic_include, language_override, "
        "search_only, status_origin, status_reason, status_stderr) "
        "VALUES (?, ?, ?, NULL, ?, 'failed', NULL, NULL, NULL, NULL, 0, ?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET "
        "path=excluded.path, language=excluded.language, commit_sha=NULL, "
        "last_indexed=excluded.last_indexed, status='failed', "
        "scheme_override=excluded.scheme_override, "
        "semantic_include=excluded.semantic_include, "
        "language_override=excluded.language_override, "
        "search_only=excluded.search_only, "
        "status_origin=excluded.status_origin, "
        "status_reason=excluded.status_reason, "
        "status_stderr=excluded.status_stderr",
        (slug, path, language, last_indexed.isoformat(), origin, reason, stderr),
    )
    self._conn.commit()
    entry = self.get(slug)
    assert entry is not None
    return entry
```

**D-06 (full overwrite on reindex failure):** The ON CONFLICT SET list includes `commit_sha=NULL, status='failed'` — matching today's de-facto behavior (pre-run upsert writes `commit_sha=None, status="indexing"` before the try).

#### 1i. Recovery-derivation function (D-09)

**Source:** `SEARCH_ONLY_STATUS` constant at line 21 — colocated module-level pattern.

**Suggested shape** (colocated in registry.py, imported by both CLI and server):
```python
def recovery_for(entry: RegisteredRepo) -> str | None:
    """Derive a recovery command from the row's origin slug.

    Returns None when no origin is recorded (legacy rows, successful indexes).
    Pure function — never persisted.
    """
    origin = entry.status_origin
    if origin is None:
        # Legacy search_only=1 rows with NULL origin (Pitfall 6): treat as manual
        if entry.search_only:
            return f"jarvis forget {entry.slug} && jarvis index {entry.path}"
        return None
    if origin == ORIGIN_FAILED_HARD:
        return f"jarvis index {entry.path}"
    if origin == ORIGIN_SIGNATURE:
        return f"jarvis reindex {entry.slug}"  # see RESEARCH Open Question 1
    if origin == ORIGIN_MANUAL:
        return f"jarvis forget {entry.slug} && jarvis index {entry.path}"
    return None  # unknown origin — defensive
```

---

### `src/jarvis/index_cli.py` (controller/hook, request-response)

**Analog:** itself — extending existing `index_repo()` failure paths, `_cmd_status`, `_cmd_list`

#### 2a. Hard failure handler — where `record_failure()` hooks in

**Source:** `src/jarvis/index_cli.py:887-889`
```python
    except Exception as exc:
        registry.mark_status(slug, "failed")
        raise IndexingError(str(exc)) from exc
```

**Replace with** `record_failure()` call + classification of `str(exc)` into reason vs stderr. The `IndexingError` from `_run` already embeds full subprocess stderr (line 349-350):
```python
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")
```

The `str(exc)` carrier has the classified step + raw output. The split into `status_reason` (one-liner) and `status_stderr` (full text) is a planner discretion item.

#### 2b. Search-only signature fallback — where `signature` origin is recorded

**Source:** `src/jarvis/index_cli.py:817-833`
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

**After the successful `upsert`**, add an origin-stamping write or extend the upsert to include origin. The `reason` from `_search_only_reason()` is the per-signature human text — it goes into `status_reason`.

#### 2c. Manual `--search-only` path — where `manual` origin is recorded

**Source:** `src/jarvis/index_cli.py:802-812` (the pre-pipeline search-only branch at the top of `index_repo()`):
```python
    if search_only:
        registry.upsert(slug, str(repo_path), language, None, "indexing", ...)
        try:
            semantic_ok, tracked = _publish_search_only(repo_path, slug, root, semantic_include)
            registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS, ..., search_only=True)
            registry.mark_tracked_files(slug, tracked)
            if semantic_ok:
                registry.mark_semantic_indexed(slug)
        except Exception as exc:
            registry.mark_status(slug, "failed")
            raise IndexingError(str(exc)) from exc
```

The second `upsert` (success path) must also stamp `status_origin='manual'`.

#### 2d. `_cmd_list` — status markers (D-08)

**Source:** `src/jarvis/index_cli.py:917-919`
```python
def _cmd_list(args: argparse.Namespace) -> int:
    registry = Registry(config.data_dir() / "registry.db")
    try:
        for repo in registry.list():
            print(f"{repo.slug}\t{repo.status}\t{repo.language}\t{repo.commit_sha or '-'}\t{repo.path}")
    finally:
        registry.close()
    return 0
```

**Extend** with glyph markers in the status column. Per RESEARCH Pitfall 8, keep the 5-column TSV format stable — prefix status with the glyph or append the reason for failed rows. The `\t`-separated format must remain parseable by scripts.

#### 2e. `_cmd_status` — cause + recovery reporting (STAT-01)

**Source:** `src/jarvis/index_cli.py:921-930`
```python
def _cmd_status(args: argparse.Namespace) -> int:
    ...
    print(f"slug: {repo.slug}\npath: {repo.path}\nlanguage: {repo.language}\nstatus: {repo.status}")
    print(f"commit: {repo.commit_sha or '-'}\nlast_indexed: {repo.last_indexed.isoformat()}")
    semantic = repo.semantic_indexed_at.isoformat() if repo.semantic_indexed_at else "-"
    print(f"semantic: {semantic}")
    return 0
```

**Extend** with origin, reason, recovery lines when failure fields are non-NULL. Use `recovery_for()` from registry.

---

### `src/jarvis/server.py` (controller, request-response)

**Analog:** itself — extending existing `_error_payload`, `get_index_status`, `_registry_status`

#### 3a. Imports pattern

**Source:** `src/jarvis/server.py:10-19`
```python
from jarvis.index_reader import IndexNotFoundError
from jarvis.query import FreshnessSnapshot, QueryService
from jarvis.registry import SEARCH_ONLY_STATUS
```

**Extend** to import the new origin constants and `recovery_for` from registry:
```python
from jarvis.registry import SEARCH_ONLY_STATUS, ORIGIN_FAILED_HARD, ORIGIN_SIGNATURE, ORIGIN_MANUAL, recovery_for
```

**Important:** `recovery_for` takes a `RegisteredRepo`, so the import of `Registry` stays deferred (already is — inside `_registry_status` and `_search_coverage_fields`). The new `_registry_entry` helper (see 3b) follows the same deferred-import pattern.

#### 3b. `_registry_status` extend to `_registry_entry` (full row)

**Source:** `src/jarvis/server.py:91-104`
```python
def _registry_status(repo: str) -> str | None:
    """Best-effort registry lookup for error messaging only. Any failure
    returns None so a broken registry degrades the message rather than
    replacing one error with another."""
    try:
        from jarvis.registry import Registry

        registry = Registry(config.data_dir() / "registry.db")
        try:
            entry = registry.get(repo)
        finally:
            registry.close()
    except Exception:
        return None
    return entry.status if entry is not None else None
```

**Add a new helper** `_registry_entry(repo) -> RegisteredRepo | None` following the same try/except/deferred-import pattern, returning the full entry (or None). Both `_registry_status` and new capability/error code call this. Alternatively, refactor `_registry_status` to call `_registry_entry` internally.

#### 3c. `_error_payload` — additive structured keys (D-14)

**Source:** `src/jarvis/server.py:136-164`
```python
def _error_payload(repo: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, IndexNotFoundError) and _registry_status(repo) == SEARCH_ONLY_STATUS:
        return {
            "error": (
                f"{repo} is indexed search-only: it has no SCIP index, so navigation "
                "tools cannot answer. searchCode and semanticSearch do work on it. "
                "This happens when the language's indexer cannot build the repo — "
                "for example an Android/Gradle project."
            )
        }
    if isinstance(exc, AmbiguousSymbolError):
        hint = exc.candidates[0].dotted_path if exc.candidates else exc.query
        return {
            "error": (
                f"{exc.query!r} is ambiguous in {repo} ({exc.total} matches). "
                f"Retry with a qualifier, e.g. {hint!r}."
            ),
            "candidates": [
                {"symbol": c.symbol, "dottedPath": c.dotted_path, "kind": str(c.kind)}
                for c in exc.candidates
            ],
            "candidateTotal": exc.total,
        }
    return {"error": str(exc)}
```

**Extend** the `IndexNotFoundError` branch (and add a general failure branch) with additive structured keys per D-14:
```python
    if isinstance(exc, IndexNotFoundError):
        entry = _registry_entry(repo)
        if entry is not None:
            payload = {"error": str(exc)}
            if entry.status_origin:
                payload["state"] = entry.status_origin
            if entry.status_reason:
                payload["cause"] = entry.status_reason
            recovery = recovery_for(entry)
            if recovery:
                payload["recovery"] = recovery
            return payload
        return {"error": str(exc)}
```

**Key precedent:** `candidates` key at lines 155-158 — "A structured list, not prose inside `error`, so the caller can act on it without parsing English." The new `state`/`cause`/`recovery` keys follow the same convention.

#### 3d. `getIndexStatus` — capabilities + last_index_run (D-13/D-15)

**Source:** `src/jarvis/server.py:283-295`
```python
@mcp.tool(name="getIndexStatus")
def get_index_status(repo: str, repo_path: str | None = None) -> dict[str, Any]:
    try:
        indexed, freshness = _service().get_index_status(repo, repo_path)
    except Exception as exc:
        return {"error": str(exc)}
    return {"repo": repo, "indexed": indexed, "status": _registry_status(repo),
            **_freshness_fields(freshness), **_search_coverage_fields(repo)}
```

**Extend** the return dict with additive keys:
```python
    result = {"repo": repo, "indexed": indexed, "status": status,
              **_freshness_fields(freshness), **_search_coverage_fields(repo)}
    result["last_index_run"] = {"outcome": status, ...}
    result["capabilities"] = {"navigation": {...}, "search": {...}, "semantic": {...}}
    return result
```

**Navigation capability truth (D-07):** Derive from `read_pointer()` success (via `_service().get_index_status()` which already returns `indexed`). When `indexed=True` and the row reports failure, add `reason="stale — indexed at <commit>"` from `FreshnessSnapshot.commit`.

**Search capability truth (A5):** Derive from row presence + zoekt shard existence (not a live webserver probe). Follow `_search_coverage_fields` precedent.

**Semantic capability truth (A6):** `entry.semantic_indexed_at is not None`. Use deferred import — never import `semantic` extra at module level.

---

### `tests/test_registry.py` (test, CRUD)

**Analog:** itself — extending existing migration, persistence, and roundtrip test patterns.

#### 4a. Legacy migration regression test

**Source:** `tests/test_registry.py:234-261`
```python
def test_search_only_column_migrates_onto_an_existing_database(tmp_path):
    """A registry created before this column must gain it without data loss."""
    import sqlite3
    from jarvis.registry import Registry

    db = tmp_path / "registry.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE repos (slug TEXT PRIMARY KEY, path TEXT NOT NULL, "
        "language TEXT NOT NULL, commit_sha TEXT, last_indexed TEXT NOT NULL, "
        "status TEXT NOT NULL, scheme_override TEXT, semantic_indexed_at TEXT, "
        "semantic_include TEXT, language_override TEXT)"
    )
    conn.execute(
        "INSERT INTO repos VALUES ('old', '/p', 'python', NULL, "
        "'2026-01-01T00:00:00+00:00', 'indexed', NULL, NULL, NULL, NULL)"
    )
    conn.commit()
    conn.close()

    registry = Registry(db)
    try:
        entry = registry.get("old")
        assert entry is not None
        assert entry.search_only is False
    finally:
        registry.close()
```

**Replicate this pattern** for each new column (or parameterize over the three). The pre-migration CREATE TABLE must omit all three new columns. Assert the new fields default to None after migration.

#### 4b. Upsert roundtrip test

**Source:** `tests/test_registry.py:13-28` (`test_upsert_then_get_roundtrips`)
```python
def test_upsert_then_get_roundtrips(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("myslug", "/repos/mine", "python", "abc123", "indexed")
    repo = reg.get("myslug")
    assert repo.slug == "myslug"
    ...
    reg.close()
```

**Extend** with assertions that `status_origin`/`status_reason`/`status_stderr` are None after a normal upsert (D-04).

#### 4c. Failure-record tests

**New tests needed** (no existing analog — but following the same `tmp_path` + real-sqlite3 convention):
- `test_record_failure_creates_row_when_absent` — D-05
- `test_record_failure_overwrites_existing_row` — D-06
- `test_upsert_clears_failure_fields_on_success` — D-04
- `test_recovery_for_derives_correct_command_per_origin` — D-09/D-10/D-11/D-12
- `test_recovery_for_returns_none_for_successful_repo`
- `test_recovery_for_treats_legacy_search_only_as_manual` — Pitfall 6

#### 4d. Idempotency test

**Source:** `tests/test_registry.py:276-283` (`test_ensure_column_is_idempotent`)
```python
def test_ensure_column_is_idempotent(tmp_path: Path):
    import sqlite3
    from jarvis.registry import _ensure_column
    conn = sqlite3.connect(tmp_path / "r.db")
    conn.execute("CREATE TABLE repos (slug TEXT PRIMARY KEY)")
    _ensure_column(conn, "extra_col", "TEXT")
    _ensure_column(conn, "extra_col", "TEXT")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(repos)")}
    conn.close()
    assert "extra_col" in cols
```

Already covers idempotency generically — no new test needed for the migration guard itself.

---

### `tests/test_index_cli.py` (test, request-response)

**Analog:** itself — extending existing CLI test patterns.

#### 5a. Mocking `index_repo` with `monkeypatch`

**Source:** `tests/test_index_cli.py` (e.g. `test_search_only_publishes_zoekt_without_a_scip_pointer`)
```python
def test_search_only_publishes_zoekt_without_a_scip_pointer(tmp_path: Path, monkeypatch):
    from jarvis.index_cli import index_repo, SEARCH_ONLY_STATUS
    ...
    monkeypatch.setattr("jarvis.index_cli._run", lambda *a, **k: _fake_completed_process(a[0]))
    index_repo(repo, slug=slug, search_only=True, root=data_root)
    registry = Registry(config.data_dir(data_root) / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
    finally:
        registry.close()
```

**New tests** follow this same pattern: monkeypatch `_run`, call `index_repo`, assert registry state (including new origin/reason fields).

#### 5b. CLI output capture with `capsys`

**Source:** `tests/test_index_cli.py` (multiple tests use `capsys`)
```python
def test_cmd_index_reports_non_git_directory_as_error(tmp_path: Path, monkeypatch, capsys):
    ...
    assert "not a git repository" in capsys.readouterr().err
```

**New tests for `_cmd_status` and `_cmd_list`** use `capsys` to assert the origin/recovery/reason lines appear in output.

#### 5c. Integration test for failure recording

**Source:** `tests/test_index_cli.py` (`test_index_repo_marks_failed_on_indexer_error`, `@pytest.mark.integration`)
```python
@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=...)
def test_index_repo_marks_failed_on_indexer_error(tmp_path: Path, monkeypatch):
    ...
    with pytest.raises(IndexingError):
        index_repo(repo_dir, slug="fails", root=data_root)
    registry = Registry(config.data_dir(data_root) / "registry.db")
    try:
        repo = registry.get("fails")
        assert repo.status == "failed"
    finally:
        registry.close()
```

**Extend** this test (or add a unit-test variant) to assert `status_origin`, `status_reason`, `status_stderr` are populated.

---

### `tests/test_server_tools.py` (test, request-response)

**Analog:** itself — extending existing MCP test patterns.

#### 6a. In-process MCP session test fixture

**Source:** `tests/test_server_tools.py:24-35`
```python
@pytest.fixture(autouse=True)
def _wired_query_service(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    ...
    monkeypatch.setattr(server, "_query_service", None)
```

All tests use `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` to isolate from the real registry. New tests follow this same pattern.

#### 6b. `_error_payload` unit test pattern

**Source:** `tests/test_server_tools.py:308-326`
```python
def test_error_payload_explains_a_search_only_repo(tmp_path: Path, monkeypatch):
    from jarvis import config, server
    from jarvis.index_reader import IndexNotFoundError
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()

    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))
    assert "search-only" in payload["error"]
    assert "searchCode" in payload["error"]
```

**New tests** for structured keys follow this exact pattern: set up a registry row with origin/reason, call `_error_payload` with `IndexNotFoundError`, assert `state`/`cause`/`recovery` keys.

#### 6c. `get_index_status` test pattern

**Source:** `tests/test_server_tools.py:328-338`
```python
def test_get_index_status_reports_search_only_status():
    from jarvis.registry import Registry
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    finally:
        registry.close()
    result = server.get_index_status(repo="gorepo")
    assert result["status"] == "search-only"
    assert result["indexed"] is False
```

**New tests** assert `last_index_run` and `capabilities` fields in the returned dict.

#### 6d. Never-raises convention

**Source:** `tests/test_server_tools.py:451-458`
```python
def test_search_coverage_fields_never_raises(monkeypatch, tmp_path):
    """A coverage probe failure must not replace a working status response
    with an error."""
    from jarvis import config, server
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(server, "_zoekt_base_url_if_running", _boom)
    fields = server._search_coverage_fields("anything")
    assert fields["searchCoverage"] is None
```

The same never-raises convention applies to capability computation — any failure in deriving capabilities must not break the `getIndexStatus` response.

---

## Shared Patterns

### 1. Deferred imports for optional extras
**Source:** `src/jarvis/server.py:93-95` (inside `_registry_status`)
```python
    try:
        from jarvis.registry import Registry
        registry = Registry(config.data_dir() / "registry.db")
```
**Apply to:** Any new code in `server.py` that touches `Registry` — the deferred-import pattern ensures base installs work without optional extras. The `recovery_for` function itself can be imported at module level since it lives in `registry.py` (always available).

### 2. MCP boundary: catch broadly, return `{"error": ...}` dict
**Source:** `src/jarvis/server.py:136-164` (`_error_payload`) and `src/jarvis/server.py:291-292` (`get_index_status`)
```python
    except Exception as exc:
        return {"error": str(exc)}
```
**Apply to:** All new capability/error code in `server.py`. Every MCP tool catches broadly and returns error dict, never raises — keeps the stdio server alive. The `_search_coverage_fields` function (lines 113-142) is the gold standard: it never raises, returns `null` with a reason on any failure.

### 3. Best-effort registry lookup (degrades gracefully)
**Source:** `src/jarvis/server.py:91-104` (`_registry_status`)
```python
    """Best-effort registry lookup for error messaging only. Any failure
    returns None so a broken registry degrades the message rather than
    replacing one error with another."""
```
**Apply to:** New `_registry_entry()` helper and all capability derivation in `server.py`. A broken registry must not replace one error with another.

### 4. Real sqlite3, never mock
**Source:** All test files — `tmp_path` + real `Registry` instances
**Apply to:** All new tests. No `unittest.mock.patch` on sqlite3 operations.

### 5. Frozen dataclass `asdict()` for JSON payloads
**Source:** `src/jarvis/server.py:69-71`
```python
def _freshness_fields(snapshot: FreshnessSnapshot) -> dict[str, Any]:
    return _json_safe(asdict(snapshot))
```
**Apply to:** Capability payload construction. Do NOT use `asdict(entry)` for the full `RegisteredRepo` (RESEARCH Pitfall 7 — stderr can be megabytes). Build the payload dict manually, including only `status_reason` (one-liner), never `status_stderr`.

### 6. Print-to-stdout CLI output, no logging framework
**Source:** `src/jarvis/index_cli.py:917-930` (`_cmd_list`/`_cmd_status` use `print()`)
**Apply to:** All new CLI output for `jarvis status`/`jarvis list` extensions. Errors go to `file=sys.stderr`.

### 7. Modern type-hint syntax
**Source:** Throughout — `str | None`, `list[T]`, `dict[K, V]`
**Apply to:** All new code. Never `Optional[T]`, `Union[X, None]`, or `List[T]`.

## No Analog Found

All six files are modifications to existing modules with clear in-file analogs for every pattern needed. No file requires patterns from outside the codebase.

The closest thing to "no analog" is the `record_failure()` method — it's a new method, but it follows the exact same `INSERT…ON CONFLICT DO UPDATE` skeleton as the existing `upsert()`. And the recovery-derivation function is a new pure function, but it follows the same module-level-constant-then-function pattern as `SEARCH_ONLY_STATUS` + `_search_only_reason()`.

## Metadata

**Analog search scope:** `src/jarvis/registry.py`, `src/jarvis/index_cli.py`, `src/jarvis/server.py`, `src/jarvis/index_reader.py`, `src/jarvis/query.py`, `tests/test_registry.py`, `tests/test_index_cli.py`, `tests/test_server_tools.py`
**Files scanned:** 8
**Pattern extraction date:** 2026-08-21
