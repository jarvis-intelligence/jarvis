# Phase 3: Opt-In Self-Healing Fallback - Pattern Map

**Mapped:** 2026-08-22
**Files analyzed:** 10 (4 source + 4 test modified, 1 source unchanged, 1 doc)
**Analogs found:** 10 / 10

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/jarvis/registry.py` | model/store | CRUD | itself (ORIGIN_*/_ensure_column/upsert/record_failure/mark_*/recovery_for) | exact (intra-file) |
| `src/jarvis/index_cli.py` | controller | request-response | itself (_resolve_*/_publish_search_only/except handler/_cmd_list/_cmd_watch) | exact (intra-file) |
| `src/jarvis/config.py` | config | request-response | `src/jarvis/config.py` (JARVIS_DATA_DIR env read, lines 62-66) | role-match |
| `src/jarvis/server.py` | controller | request-response | itself (_capability_fields/_error_payload) | exact (intra-file) |
| `src/jarvis/watch.py` | utility | event-driven | (unchanged) | n/a |
| `tests/test_registry.py` | test | CRUD | itself (column migration / recovery_for / record_failure tests) | exact (intra-file) |
| `tests/test_index_cli.py` | test | request-response | itself (signature fallback / pre-pipeline gate / search-only tests) | exact (intra-file) |
| `tests/test_server_tools.py` | test | request-response | itself (error_payload / capability_fields / last_index_run tests) | exact (intra-file) |
| `tests/test_watch.py` | test | event-driven | (unchanged) | n/a |

## Pattern Assignments

---

### `src/jarvis/registry.py` (model/store, CRUD)

**Analog:** itself — all patterns are intra-file extensions of existing slots.

**ORIGIN taxonomy constants** (lines 25-29):
```python
ORIGIN_FAILED_HARD = "failed_hard"
ORIGIN_SIGNATURE = "signature"
ORIGIN_MANUAL = "manual"
```
Phase 3 adds `DEGRADED_STATUS = "degraded"` beside `SEARCH_ONLY_STATUS` (line 21) and `ORIGIN_FALLBACK = "fallback"` beside the three existing origins (line 25-29). The comment at line 25-26 explicitly reserves the slot: "Additive -- Phase 3's `degraded` slots in as one new constant plus one recovery_for branch, with no schema or payload change."

**`_ensure_column` additive migration** (lines 52-68):
```python
def _ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    try:
        conn.execute(f"ALTER TABLE repos ADD COLUMN {name} {decl}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise
```
New column: `_ensure_column(self._conn, "fallback_enabled", "INTEGER")` — `INTEGER` (NULL/0/1), NULL default means "defer to env". Follows the exact pattern of the 9 existing `_ensure_column` calls in `__init__` (lines 158-167).

**`_SCHEMA` DDL** (lines 31-49):
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
    tracked_files INTEGER,
    status_origin TEXT,
    status_reason TEXT,
    status_stderr TEXT
)
"""
```
Do NOT add `fallback_enabled` to `_SCHEMA` — `_ensure_column` is the house pattern for additive columns; `_SCHEMA` only defines columns that existed from day one.

**`RegisteredRepo` frozen dataclass** (lines 89-122):
```python
@dataclass(frozen=True)
class RegisteredRepo:
    slug: str
    path: str
    language: str
    commit_sha: str | None
    last_indexed: datetime
    status: str
    scheme_override: str | None = None
    semantic_indexed_at: datetime | None = None
    semantic_include: tuple[str, ...] = ()
    language_override: str | None = None
    search_only: bool = False
    tracked_files: int | None = None
    status_origin: str | None = None
    status_reason: str | None = None
    status_stderr: str | None = None
```
Add `fallback_enabled: bool | None = None`. Then update `_row_to_repo` (lines 124-141) to unpack the new column from the SELECT tuple and pass it to the constructor. Update every SELECT column list in `get()` (lines 252-258) and `list()` (lines 261-268).

**`upsert` D-04 clearing** (lines 174-220):
```python
    def upsert(self, slug, path, language, commit_sha, status,
               scheme_override=None, semantic_include=(),
               language_override=None, search_only=False,
               status_origin=None, status_reason=None) -> RegisteredRepo:
        ...
        "INSERT INTO repos (..., status_origin, status_reason) VALUES (..., ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET "
        "... status_origin=excluded.status_origin, "
        "status_reason=excluded.status_reason, "
        "status_stderr=excluded.status_stderr",
        (slug, path, language, commit_sha, last_indexed.isoformat(), status,
         scheme_override, _join_include(semantic_include), language_override,
         int(search_only), status_origin, status_reason),
```
Two changes: (1) add `status_stderr` parameter (default None) so the degraded write can pass the full failure text; (2) do NOT add `fallback_enabled` to upsert's ON CONFLICT list — the tri-state persistence uses a dedicated setter (see mark_* precedent below).

**`recovery_for` read-time mapping** (lines 138-150):
```python
def recovery_for(entry: RegisteredRepo) -> str | None:
    origin = origin_of(entry)
    if origin == ORIGIN_FAILED_HARD:
        return f"jarvis index {entry.path}"
    if origin == ORIGIN_SIGNATURE:
        return f"jarvis reindex {entry.slug}"
    if origin == ORIGIN_MANUAL:
        return f"jarvis forget {entry.slug} && jarvis index {entry.path}"
    return None
```
Add `ORIGIN_FALLBACK` branch between `ORIGIN_SIGNATURE` and `ORIGIN_MANUAL`. Locked wording from CONTEXT: `f"fix the indexer failure, then \`jarvis reindex {entry.slug}\` (full build retries automatically)"`. The `return None` fallback at the end correctly handles unknown future origins.

**`mark_tracked_files` dedicated setter** (lines 293-303) — persistence precedent for the new `set_fallback_enabled`:
```python
    def mark_tracked_files(self, slug: str, count: int) -> None:
        """Record how many git-tracked blobs the last successful index saw.

        Deliberately not a column on `upsert`: a reindex upserts `indexing`
        before the count is known, and `upsert`'s ON CONFLICT list would then
        reset it to NULL.
        """
        self._conn.execute(
            "UPDATE repos SET tracked_files = ? WHERE slug = ?", (count, slug)
        )
        self._conn.commit()
```
New `set_fallback_enabled(slug, value: bool)` follows this exact shape: bare UPDATE, one parameterized SET, `WHERE slug = ?`. Needs INSERT..ON CONFLICT so it also works before the row exists (pre-pipeline failure case).

---

### `src/jarvis/index_cli.py` (controller, request-response)

**Analog:** itself — all patterns are intra-file extensions.

**Imports** (lines 1-33):
```python
from jarvis import config
from jarvis.graph import GraphStore, populate_graph_for_repo
from jarvis.registry import (
    ORIGIN_FAILED_HARD,
    ORIGIN_MANUAL,
    ORIGIN_SIGNATURE,
    SEARCH_ONLY_STATUS,
    Registry,
```
Add `DEGRADED_STATUS` and `ORIGIN_FALLBACK` to the registry import block. Add `from jarvis.config import fallback_search_only_from_env` (or call `config.fallback_search_only_from_env()`).

**`_resolve_*` family** (lines 570-612) — tri-state resolution pattern:
```python
def _resolve_scheme(registry: Registry, slug: str, scheme: str | None) -> str | None:
    if scheme is not None:
        return scheme
    existing = registry.get(slug)
    return existing.scheme_override if existing is not None else None
```
`_resolve_fallback` copies the shape but adds the env tier and returns bool. CRITICAL: do NOT copy the persistence idiom (resolved value written back on terminal upsert) — only the explicit CLI value may be persisted via the dedicated setter. Resolution order: CLI > persisted > env > off.

**`_run` missing-binary translation** (lines 366-370) — exclusion ladder input:
```python
    except FileNotFoundError as exc:
        raise IndexingError(
            f"{step} failed: {cmd[0]} not found on PATH — run setup.sh"
        ) from exc
```
For FALL-04: add a `MissingBinaryError(IndexingError)` subclass raised here instead of the plain `IndexingError`. The degrade gate checks `isinstance(exc, MissingBinaryError)` to stay hard-failure.

**`_bash_shim_failure` + `_BASH_SHIM_TOKENS`** (lines 141-152) — exclusion ladder input:
```python
_BASH_SHIM_TOKENS = ("LAUNCHER_ARGS[@]", "unbound variable")

def _bash_shim_failure(output: str) -> bool:
    return all(token in output for token in _BASH_SHIM_TOKENS)
```
The degrade gate re-checks `_bash_shim_failure(text)` at the except handler. This is a pure predicate — no new code needed, just a call at the degrade site.

**`_publish_search_only`** (lines 703-734) — corrected ordering:
```python
def _publish_search_only(repo_path, slug, root, semantic_include):
    _retire_scip_artifacts(slug, root)          # <-- MUST MOVE AFTER zoekt
    _pin_zoekt_repo_name(repo_path, slug)
    zoekt_dir = config.data_dir(root) / ".zoekt"
    zoekt_dir.mkdir(parents=True, exist_ok=True)
    tracked = _tracked_blob_count(repo_path)
    result = _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path,
                  step="zoekt-git-index")
    _warn_on_coverage_shortfall(slug, tracked, result.stderr)
    _sweep_zoekt_tmp_orphans(slug, root)
    semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)
    ...
```
Corrected order: `_pin_zoekt_repo_name` → `_run(zoekt)` → on success → `_retire_scip_artifacts` → `_run_semantic_stage`. If zoekt fails, old SCIP pointer survives (existing `test_get_index_status_failed_run_with_live_pointer_reports_stale_navigation` pattern).

**Main-pipeline except handler** (lines 985-994) — the degrade insertion point:
```python
    except Exception as exc:
        text = str(exc)
        reason = next((line for line in text.splitlines() if line.strip()),
                      exc.__class__.__name__)
        registry.record_failure(slug, str(repo_path), language, ORIGIN_FAILED_HARD,
                                reason, text)
        raise IndexingError(str(exc)) from exc
    finally:
        registry.close()
```
Insert the degrade gate between `reason = ...` and `record_failure(...)`. Guard order: bash-shim check → MissingBinaryError isinstance → fallback_enabled check. On degrade: call `_publish_search_only`, upsert with `DEGRADED_STATUS`/`ORIGIN_FALLBACK`/reason/stderr, mark_tracked_files, mark_semantic_indexed, stderr warning, return slug (exit 0). On fallback publish failure: fall through to existing `record_failure` + raise.

**Pre-pipeline wrap** (lines 816-870) — where `_resolve_fallback` is called:
```python
    try:
        check_scip_version()
        search_only = _resolve_search_only(registry, slug, search_only)
        language_override = _resolve_language(registry, slug, language)
        ...
        scheme = _resolve_scheme(registry, slug, scheme)
        semantic_include = _resolve_semantic_include(registry, slug, semantic_include)
        ...
    except Exception as exc:
        registry.record_failure(slug, str(repo_path), ..., ORIGIN_FAILED_HARD, reason, text)
        registry.close()
        raise
```
Add `fallback_enabled = _resolve_fallback(registry, slug, fallback_search_only)` alongside the other `_resolve_*` calls (line ~841). Persist the explicit CLI value via `set_fallback_enabled` right after the transitional `indexing` upsert (line ~873), only when `fallback_search_only is not None`.

**`_cmd_list` rendering** (lines 1020-1035) — glyph + 6th field:
```python
            if repo.status == "failed":
                marker = "✗"
            elif repo.status == SEARCH_ONLY_STATUS:
                marker = "◐"
            else:
                marker = "✓"
            line = (f"{repo.slug}\t{marker} {repo.status}\t{repo.language}"
                    f"\t{repo.commit_sha or '-'}\t{repo.path}")
            if repo.status == "failed":
                line += f"\t{repo.status_reason or repo.status}"
```
Add `elif repo.status == DEGRADED_STATUS: marker = "◐"` to the glyph block. Add 6th-field branch for degraded: `elif repo.status == DEGRADED_STATUS: line += f"\t{repo.status_reason or repo.status}"`.

**`_cmd_watch._reindex`** (lines 1210-1223) — sha-skip + flag pass-through:
```python
    def _reindex() -> None:
        print(f"[watch] change detected, reindexing {slug} ...")
        try:
            index_repo(repo_path, slug=slug, scheme=args.scheme, language=args.language)
            print(f"[watch] {slug} reindexed")
        except Exception as exc:
            print(f"[watch] reindex failed: {exc}", file=sys.stderr)
```
Before `index_repo(...)`: open a short-lived Registry, call `_watch_should_retry_full_build(entry, current_sha)` with `_git_head(repo_path)`. If False, print skip note to stderr and return without calling index_repo. Add `fallback_search_only=args.fallback_search_only` to the index_repo call (same pattern as `scheme=args.scheme`). The `_watch_should_retry_full_build` function is a pure module-level helper in index_cli.py.

**Parser flags** (lines 1254-1320) — BooleanOptionalAction precedent:
```python
    index_parser.add_argument(
        "--search-only",
        action="store_true",
        default=None,
        help="skip SCIP indexing and publish only Zoekt + semantic search "
             "(persisted and reused by reindex/watch)",
    )
```
For `--fallback-search-only`, use `BooleanOptionalAction` (stdlib, verified present on 3.12+) instead of the legacy `store_true`: `action=argparse.BooleanOptionalAction, default=None`. Add to both `index_parser` and `watch_parser`. This generates the `--fallback-search-only` / `--no-fallback-search-only` pair with None default.

**`_cmd_index`** (lines 1004-1019) — forward the new flag:
```python
    slug = index_repo(
        Path(args.path), slug=args.slug, scheme=getattr(args, "scheme", None),
        semantic_include=tuple(raw_include) if raw_include is not None else None,
        language=getattr(args, "language", None),
        search_only=getattr(args, "search_only", None),
    )
```
Add `fallback_search_only=getattr(args, "fallback_search_only", None)`.

---

### `src/jarvis/config.py` (config, request-response)

**Analog:** `src/jarvis/config.py` lines 62-66 — JARVIS_DATA_DIR env read:
```python
def data_dir(override: Path | None = None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("JARVIS_DATA_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_DATA_DIR
```
New `fallback_search_only_from_env()` function follows the house pattern: env reads live in config.py. Adds strict truthy set (`1/true/yes/on` case-insensitive), warn-once for non-truthy values, unset = off.

---

### `src/jarvis/server.py` (controller, request-response)

**Analog:** itself — intra-file branch additions.

**`_capability_fields` navigation-unavailable branch** (lines 183-190) — the nav_reason gap:
```python
        else:
            if entry is not None and entry.search_only:
                nav_reason = entry.status_reason or "indexed search-only — no SCIP index"
            elif entry is None:
                nav_reason = "no published index"
            else:
                nav_reason = None
            nav_recovery = recovery_for(entry) if entry is not None else None
```
Insert `elif entry is not None and entry.status == DEGRADED_STATUS:` between the `search_only` and `entry is None` branches: `nav_reason = entry.status_reason or "indexer failure — degraded to search-only"`. The `nav_recovery` line at the end automatically picks up the new `recovery_for(ORIGIN_FALLBACK)` verb.

**`_error_payload` IndexNotFoundError branch** (lines 233-268):
```python
    if isinstance(exc, IndexNotFoundError):
        entry = _registry_entry(repo)
        if entry is not None and entry.status == SEARCH_ONLY_STATUS:
            payload = {"error": f"{repo} is indexed search-only: ..."}
        else:
            payload = {"error": str(exc)}
        origin = origin_of(entry) if entry is not None else None
        if origin is not None:
            payload["state"] = origin
            if entry.status_reason:
                payload["cause"] = entry.status_reason
            recovery = recovery_for(entry)
            if recovery is not None:
                payload["recovery"] = recovery
        return payload
```
No change needed: a degraded row falls to the `else: payload = {"error": str(exc)}` branch (not `SEARCH_ONLY_STATUS`), then the origin/recovery keys are added automatically by the `origin is not None` block. The `state='fallback'` flows through verbatim — zero reshaping per the locked contract. The only server.py behavioral change is the `_capability_fields` nav_reason branch above.

---

### `tests/test_registry.py` (test, CRUD)

**Analog:** existing tests in same file.

**Column migration test** (lines 238-261) — `test_search_only_column_migrates_onto_an_existing_database`:
```python
def test_search_only_column_migrates_onto_an_existing_database(tmp_path):
    import sqlite3
    db = tmp_path / "registry.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE repos (slug TEXT PRIMARY KEY, path TEXT NOT NULL, ...)")
    conn.execute("INSERT INTO repos VALUES ('old', '/p', 'python', ...)")
    conn.commit(); conn.close()
    registry = Registry(db)
    entry = registry.get("old")
    assert entry.search_only is False  # NULL default
```
New test: same pattern but assert `fallback_enabled is None` (NULL default for new column on a pre-phase-3 DB).

**Tri-state roundtrip test** (lines 212-224) — `test_upsert_round_trips_search_only`:
```python
def test_upsert_round_trips_search_only(tmp_path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("r", "/p", "java", None, "search-only", search_only=True)
    entry = registry.get("r")
    assert entry.search_only is True
```
New test: roundtrip `fallback_enabled` through `set_fallback_enabled` + `get`, asserting None/True/False.

**`recovery_for` test** (lines 401-416) — `test_recovery_for_derives_per_origin_commands`:
```python
def test_recovery_for_derives_per_origin_commands():
    assert recovery_for(_entry(status_origin=ORIGIN_FAILED_HARD)) == "jarvis index /repos/mine"
    assert recovery_for(_entry(status_origin=ORIGIN_SIGNATURE)) == "jarvis reindex mine"
    assert recovery_for(_entry(status_origin=ORIGIN_MANUAL)) == "jarvis forget mine && jarvis index /repos/mine"
```
New assertion: `recovery_for(_entry(status_origin=ORIGIN_FALLBACK, status="degraded"))` returns the locked recovery wording.

**`_entry` helper** (lines 316-327):
```python
def _entry(**overrides):
    fields = dict(slug="mine", path="/repos/mine", language="python",
                   commit_sha=None, last_indexed=datetime.now(UTC), status="failed")
    fields.update(overrides)
    return RegisteredRepo(**fields)
```
Reuse directly for all new recovery_for tests.

**D-04 clearing test** — `test_record_failure_overwrites_existing_row_and_preserves_search_only` (lines 369-399):
```python
    before = registry.upsert("mine", ..., "indexed", search_only=True)
    registry.record_failure("mine", ..., ORIGIN_FAILED_HARD, ...)
    entry = registry.get("mine")
    assert entry.search_only is True  # preserved
```
New test: upsert a degraded row, then successful upsert → assert `status_origin`, `status_reason`, `status_stderr` are all None (D-04 clearing works for degraded rows too).

---

### `tests/test_index_cli.py` (test, request-response)

**Analog:** existing tests in same file.

**Test fixtures** (lines 33-44):
```python
def _fake_completed_process(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "m", "initial"], cwd=path, check=True)
```
Reuse directly. `FIXTURE_REPO` is the test Python fixture repo used by all pipeline tests.

**Signature fallback test** (lines 1796-1828) — closest analog for the degrade-branch test:
```python
def test_indexer_failure_with_known_signature_publishes_search_only(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"; shutil.copytree(FIXTURE_REPO, repo_dir); _init_git_repo(repo_dir)
    data_root = tmp_path / "data"
    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError("error: No SCIP shards found...")
        return _fake_completed_process(cmd)
    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)
    slug = index_repo(repo_dir, root=data_root)
    registry = Registry(data_root / "registry.db")
    entry = registry.get(slug)
    assert entry.status == SEARCH_ONLY_STATUS
    assert entry.search_only is True
```
New degrade test: identical mock structure, but `index_repo(repo_dir, root=data_root, fallback_search_only=True)`, assert `entry.status == DEGRADED_STATUS`, `entry.status_origin == ORIGIN_FALLBACK`, `entry.search_only is False`.

**Pre-pipeline gate test** (lines 2658-2693) — analog for FALL-04 boundary test:
```python
def test_pre_pipeline_version_gate_failure_creates_a_recoverable_row(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.index_cli.check_scip_version", _boom)
    with pytest.raises(RuntimeError, match="below the required floor"):
        index_repo(repo_dir, root=data_root)
    entry = registry.get(slug)
    assert entry.status == "failed"
    assert entry.status_origin == ORIGIN_FAILED_HARD
```
New FALL-04 test: same pre-pipeline failure with `fallback_search_only=True` → still `failed`/`failed_hard`, never `degraded`.

**Watch parser test** (lines 1414-1418) — analog for watch flag parser test:
```python
# tests/test_index_cli.py:1414-1418
```
New test: parse `watch --fallback-search-only` and `watch --no-fallback-search-only` → assert `args.fallback_search_only` is True/False; omit → assert None.

---

### `tests/test_server_tools.py` (test, request-response)

**Analog:** existing tests in same file.

**JARVIS_DATA_DIR isolation** (e.g., lines 304-327):
```python
def test_error_payload_explains_a_search_only_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    payload = server._error_payload("gorepo", IndexNotFoundError("no pointer"))
    assert "search-only" in payload["error"]
```
New test: upsert with `status=DEGRADED_STATUS, status_origin=ORIGIN_FALLBACK, status_reason=<cause>` → assert `_error_payload` carries `state='fallback'`, `cause`, `recovery`.

**`_capability_fields` test** (lines 479-496):
```python
def test_get_index_status_navigation_unavailable_for_search_only_explains_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry.upsert("gorepo", "/p", "unknown", "abc", "search-only", search_only=True)
    nav = server.get_index_status(repo="gorepo")["capabilities"]["navigation"]
    assert nav["available"] is False
    assert nav["reason"] == "indexed search-only — no SCIP index"
    assert nav["recovery"] == "jarvis forget gorepo && jarvis index /p"
```
New test: upsert with `status=DEGRADED_STATUS, status_origin=ORIGIN_FALLBACK, status_reason="indexer crashed"` → assert `nav["reason"] == "indexer crashed"` and `nav["recovery"]` carries the fallback recovery verb.

**`last_index_run` test** (lines 449-469):
```python
def test_get_index_status_reports_last_index_run_for_a_search_only_repo(tmp_path, monkeypatch):
    registry.upsert("gorepo", ..., "search-only", status_origin=ORIGIN_SIGNATURE, status_reason="...")
    result = server.get_index_status(repo="gorepo")
    assert result["last_index_run"] == {
        "outcome": "search-only", "origin": "signature",
        "reason": "...", "recovery": "jarvis reindex gorepo"
    }
```
New test: upsert with `status=DEGRADED_STATUS` → assert `outcome='degraded'`, `origin='fallback'`, reason/recovery populated.

## Shared Patterns

### Authentication
None — local-first single-user tool, no auth.

### Error Handling
**Source:** `src/jarvis/index_cli.py` lines 985-994 (main-pipeline except), lines 847-870 (pre-pipeline except)
**Apply to:** All degraded-path code in index_cli.py
```python
    except Exception as exc:
        text = str(exc)
        reason = next((line for line in text.splitlines() if line.strip()),
                      exc.__class__.__name__)
        # degrade gate inserts here
        registry.record_failure(slug, str(repo_path), language, ORIGIN_FAILED_HARD,
                                reason, text)
        raise IndexingError(str(exc)) from exc
    finally:
        registry.close()
```

### Registry Writes (transitional → terminal)
**Source:** `src/jarvis/index_cli.py` lines 873-875 (indexing upsert), 973-978 (terminal success upsert)
**Apply to:** All new upsert calls in the degrade branch
```python
    # Transitional (before pipeline runs):
    registry.upsert(slug, str(repo_path), language, None, "indexing", ...)
    # Terminal success:
    registry.upsert(slug, str(repo_path), language, sha, "indexed", ...)
    registry.mark_tracked_files(slug, tracked)
    if semantic_ok:
        registry.mark_semantic_indexed(slug)
```
The degraded terminal write follows the terminal-success pattern: upsert with the terminal status, then mark_tracked_files/mark_semantic_indexed. The `search_only=False` is explicit (NOT True — that's the self-heal trap).

### Additive Schema Migration
**Source:** `src/jarvis/registry.py` lines 52-68 (`_ensure_column`), lines 158-167 (init calls)
**Apply to:** New `fallback_enabled` column
```python
    _ensure_column(self._conn, "fallback_enabled", "INTEGER")
```

### Parameterized SQL
**Source:** All of `src/jarvis/registry.py` — every query uses `?` placeholders
**Apply to:** All new SQL in registry.py (set_fallback_enabled, any upsert changes)

### Test Isolation (JARVIS_DATA_DIR)
**Source:** `tests/test_server_tools.py` lines 304+ (monkeypatch.setenv pattern)
**Apply to:** All new server tests
```python
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
```

### Step-Keyed `_run` Mocking
**Source:** `tests/test_index_cli.py` lines 1796-1828 (signature fallback test)
**Apply to:** All new degrade-branch tests
```python
    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError("error: simulated post-build-start failure")
        return _fake_completed_process(cmd)
    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)
```

## No Analog Found

None — every file to be modified has a direct in-tree analog (most are intra-file extensions of existing patterns). `watch.py` is unchanged. All test patterns mirror existing test structure.

## Metadata

**Analog search scope:** `src/jarvis/registry.py`, `src/jarvis/index_cli.py`, `src/jarvis/config.py`, `src/jarvis/server.py`, `tests/test_registry.py`, `tests/test_index_cli.py`, `tests/test_server_tools.py`
**Files scanned:** 7 source + 3 test files (full reads of registry.py, config.py, watch.py, server.py:50-273; targeted reads of index_cli.py totaling ~900 of 1328 lines; targeted reads of test files)
**Pattern extraction date:** 2026-08-22
