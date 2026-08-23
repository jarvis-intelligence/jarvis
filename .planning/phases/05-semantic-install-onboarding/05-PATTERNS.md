# Phase 5: Semantic Install Onboarding - Pattern Map

**Mapped:** 2026-08-23
**Files analyzed:** 4 (2 source + 2 test)
**Analogs found:** 4 / 4

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/jarvis/index_cli.py` | controller (CLI command) | request-response (interactive TTY) | itself — `_cmd_index`, `build_parser`, `_scip_version_output` seam | exact (same file) |
| `src/jarvis/registry.py` | model (persistence) | CRUD | itself — `fallback_enabled` column/setter pattern | exact (same file) |
| `tests/test_index_cli.py` | test | — | itself — CLI-layer mock + watch forwarding tests | exact (same file) |
| `tests/test_registry.py` | test | — | itself — `fallback_enabled` migration + tri-state tests | exact (same file) |

## Pattern Assignments

### `src/jarvis/index_cli.py` (controller, request-response)

All four change sites are in this file. Each maps to a specific existing pattern.

---

#### 1a. `build_parser` — `offer_semantic` argparse default

**Analog:** `build_parser` itself, index subparser `set_defaults` call

**Existing code** (line 1581):
```python
    index_parser.set_defaults(func=_cmd_index)
```

**Pattern:** Add one more `set_defaults` line beside it. No other subparser gains the attribute — reindex, watch, list, status, forget all lack it, so `getattr(args, "offer_semantic", False)` safely defaults to `False` for every non-`index` path.

---

#### 1b. `_cmd_index` — post-return offer block with Registry open/close

**Analog:** `_cmd_index` itself (lines 1269-1283) + `_cmd_list`/`_cmd_status` short-lived Registry pattern

**Existing `_cmd_index`** (lines 1269-1283):
```python
def _cmd_index(args: argparse.Namespace) -> int:
    raw_include = getattr(args, "semantic_include", None)
    try:
        slug = index_repo(
            Path(args.path), slug=args.slug, scheme=getattr(args, "scheme", None),
            semantic_include=tuple(raw_include) if raw_include is not None else None,
            language=getattr(args, "language", None),
            search_only=getattr(args, "search_only", None),
            fallback_search_only=getattr(args, "fallback_search_only", None),
        )
    except (UnsupportedLanguageError, NotAGitRepositoryError, IndexingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {slug}")
    return 0
```

**Existing `_cmd_list` short-lived Registry** (lines 1287-1295):
```python
def _cmd_list(args: argparse.Namespace) -> int:
    registry = Registry(config.data_dir() / "registry.db")
    try:
        for repo in registry.list():
            ...
        registry.close()
    return 0
```

**Pattern:** The offer block inserts between `print(f"indexed {slug}")` and `return 0`. It opens its own `Registry(...)` / `try` / `finally: registry.close()` — exactly like `_cmd_list`. The `getattr(args, "offer_semantic", False)` guard follows the established `getattr(args, "search_only", None)` / `getattr(args, "fallback_search_only", None)` pattern (lines 1270-1277) for optional attrs missing from synthetic Namespaces.

---

#### 1c. `_cmd_reindex` — synthetic Namespace (the trap to avoid)

**Analog:** `_cmd_reindex` itself (lines 1351-1369)

**Existing code** (lines 1365-1369):
```python
    return _cmd_index(argparse.Namespace(
        path=repo.path, slug=repo.slug, scheme=repo.scheme_override,
        semantic_include=list(repo.semantic_include),
        language=repo.language_override,
    ))
```

**Pattern:** The synthetic Namespace deliberately omits `search_only`, `fallback_search_only`, and will also omit `offer_semantic`. This is the structural gate — `_cmd_index`'s `getattr(..., False)` handles it. **Do not add `offer_semantic` to this Namespace.**

---

#### 1d. Detection/install helper seams (`_semantic_extra_missing`, `_at_interactive_tty`, `_install_semantic_extra`)

**Analog:** `_scip_version_output` (lines 486-492) — the "isolated for tests to monkeypatch" precedent

**Existing code** (lines 486-492):
```python
def _scip_version_output() -> str:
    """Isolated for tests to monkeypatch."""
    try:
        result = subprocess.run(["scip", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise IndexingError("scip not found on PATH — run setup.sh") from exc
    return f"{result.stdout}\n{result.stderr}"
```

**Pattern:** Module-level helpers with docstrings naming their testability. Tests monkeypatch by full path (e.g., `monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: True)`). Note: `_install_semantic_extra` uses `shutil.which` + `subprocess.run(..., capture_output=True, text=True)` — do NOT reuse `_run()` (which raises `IndexingError`/`MissingBinaryError` on failure, the opposite of warn+continue).

---

#### 1e. `_run_semantic_stage` — re-invocation after install

**Analog:** `_run_semantic_stage` itself (lines 737-766)

**Existing code** (lines 737-766):
```python
def _run_semantic_stage(repo_path: Path, slug: str, root: Path | None,
                        include_prefixes: tuple[str, ...] = ()) -> bool:
    try:
        from jarvis import semantic
        from jarvis.embeddings import SemanticExtraMissingError
    except ImportError:
        print(
            "semantic indexing skipped — install jarvis-mcp[semantic] "
            "(uv tool install), or `uv sync --extra semantic` in a source checkout",
            file=sys.stderr,
        )
        return False
    try:
        report = semantic.index_semantic(repo_path, slug, root=root,
                                         include_prefixes=include_prefixes)
        _print_semantic_report(report)
    except SemanticExtraMissingError as exc:
        print(f"semantic indexing skipped — {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(
            f"warning: semantic indexing failed (SCIP/Zoekt index still published): {exc}",
            file=sys.stderr,
        )
        return False
    return True
```

**Pattern:** The offer block calls this function directly after `importlib.invalidate_caches()`. It accepts the same args (`Path(args.path)`, `slug`, `None`, `include_prefixes`). On success, the offer opens a Registry and calls `mark_semantic_indexed(slug)`.

---

### `src/jarvis/registry.py` (model, CRUD)

#### 2a. `_SCHEMA` — add `semantic_declined INTEGER`

**Analog:** `_SCHEMA` itself — `fallback_enabled` not in `_SCHEMA` either (it was added via `_ensure_column` migration). New `semantic_declined` column follows the same approach: NO `_SCHEMA` change needed; `_ensure_column` handles it.

---

#### 2b. `__init__` — `_ensure_column` call

**Analog:** `_ensure_column(self._conn, "fallback_enabled", "INTEGER")` (line 193)

**Existing code** (lines 192-193):
```python
    # Tri-state NULL/0/1 (FALL-02). NULL = never set, defer to the env
    # tier; written only via set_fallback_enabled with the explicit
    # CLI value, never by upsert (Pitfall 1).
    _ensure_column(self._conn, "fallback_enabled", "INTEGER")
```

**Pattern:** Add `_ensure_column(self._conn, "semantic_declined", "INTEGER")` on the next line, with a similar comment. Same nullable INTEGER type. NULL = never answered; 1 = declined.

---

#### 2c. `RegisteredRepo` — add field

**Analog:** `fallback_enabled: bool | None = None` (line 112)

**Existing code** (lines 111-112):
```python
    # Opt-in self-healing fallback (Phase 3): tri-state NULL/0/1. NULL =
    # never set, defer to the env tier; set only by `set_fallback_enabled`
    # with the explicit CLI value (FALL-02, Pitfall 1).
    fallback_enabled: bool | None = None
```

**Pattern:** Add `semantic_declined: bool = False` after `fallback_enabled`. Simpler than tri-state — no precedence chain, just "declined or not yet asked". NULL reads as `False` (offer again).

---

#### 2d. `_row_to_repo` — unpack new column

**Analog:** `fallback_enabled` unpacking (line 137)

**Existing code** (line 137):
```python
        fallback_enabled=bool(fallback_enabled) if fallback_enabled is not None else None,
```

**Pattern:** Append `semantic_declined` as the **last** element in the tuple unpack and the `RegisteredRepo()` constructor. Use `bool(semantic_declined) if semantic_declined is not None else False` — or simply `bool(semantic_declined or False)` since there's no tri-state meaning for NULL.

---

#### 2e. `get()` and `list()` — append to SELECT lists

**Analog:** `fallback_enabled` at end of SELECT (lines 302, 312)

**Existing code** (lines 301-303):
```python
    def get(self, slug: str) -> RegisteredRepo | None:
        row = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include, language_override, search_only, tracked_files, "
            "status_origin, status_reason, status_stderr, fallback_enabled "
            "FROM repos WHERE slug = ?",
```

**Pattern:** Append `, semantic_declined` after `fallback_enabled` in both `get()` and `list()` SELECT strings.

---

#### 2f. `set_semantic_declined` — new setter

**Analog:** `set_fallback_enabled` (lines 329-346)

**Existing code** (lines 341-346):
```python
        self._conn.execute(
            "UPDATE repos SET fallback_enabled = ? WHERE slug = ?",
            (int(value), slug),
        )
        self._conn.commit()
```

**Pattern:** Verbatim clone, changing column name:
```python
def set_semantic_declined(self, slug: str, value: bool) -> None:
    self._conn.execute(
        "UPDATE repos SET semantic_declined = ? WHERE slug = ?",
        (int(value), slug),
    )
    self._conn.commit()
```

---

#### 2g. `upsert` — NO changes (anti-pattern to avoid)

**Analog:** `tracked_files` docstring (line 317-323) and `set_fallback_enabled` docstring (lines 329-337)

**Existing `tracked_files` docstring** (lines 317-323):
```python
    def mark_tracked_files(self, slug: str, count: int) -> None:
        """Record how many git-tracked blobs the last successful index saw.

        Deliberately not a column on `upsert`: a reindex upserts `indexing`
        before the count is known, and `upsert`'s ON CONFLICT list would then
        reset it to NULL.
        """
```

**Pattern:** `semantic_declined` stays out of `upsert`'s INSERT and ON CONFLICT SET lists, exactly like `tracked_files` and `fallback_enabled`. Adding it would NULL-reset the decline on every transitional `indexing` write.

---

#### 2h. `forget` — NO changes needed

**Analog:** `forget` itself (lines 348-351)

**Existing code** (lines 348-351):
```python
    def forget(self, slug: str) -> bool:
        cursor = self._conn.execute("DELETE FROM repos WHERE slug = ?", (slug,))
        self._conn.commit()
        return cursor.rowcount > 0
```

**Pattern:** Row DELETE already kills the decline bit. Zero new wiring.

---

### `tests/test_index_cli.py` (test)

#### 3a. CLI-layer `_cmd_index` test with mock pipeline

**Analog:** `_mock_healthy_full_run` + `_cmd_index` Namespace pattern (lines 3686-3699, 3621-3624)

**Existing mock helper** (lines 3686-3699):
```python
def _mock_healthy_full_run(monkeypatch):
    """Mocks for a fully successful main-pipeline run: every subprocess
    succeeds, the convert step writes a navigable minimal index db, and the
    graph/semantic stages no-op (the two-phase pattern of the ordering
    test)."""
    def _successful_run(cmd, *, cwd, step, env=None):
        if step == "scip expt-convert":
            _make_index_db(Path(cmd[3]), chunks=1, mentions=14)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _successful_run)
    monkeypatch.setattr("jarvis.index_cli.populate_graph_for_repo", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)
```

**Existing Namespace construction** (lines 3621-3624, from `test_degraded_row_write_and_record_failure_both_locked`):
```python
    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug=None, scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=True,
    ))
```

**Pattern for offer tests:** Same `_mock_healthy_full_run` base. Add `offer_semantic=True` to the Namespace. Monkeypatch `_semantic_extra_missing`, `_at_interactive_tty`, `_install_semantic_extra`, `builtins.input` (or the prompt seam) per test case.

---

#### 3b. Watch forwarding test (SEMA-02 structural pin)

**Analog:** `test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo` (lines 4106-4148)

**Existing code** (lines 4106-4148):
```python
def test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo(
    tmp_path: Path, monkeypatch
):
    """FALL-02 wiring: watch is just another reindex driver ..."""
    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = tmp_path / "not-a-repo"
    repo_dir.mkdir()

    captured: dict = {}

    def fake_index_repo(path, *, slug=None, root=None, scheme=None,
                        semantic_include=None, language=None, search_only=None,
                        fallback_search_only=_UNSET):
        captured["fallback_search_only"] = (
            "UNSET" if fallback_search_only is _UNSET else fallback_search_only
        )
        captured["slug"] = slug
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)
    ...
    rc = _drive_cmd_watch(
        cli, monkeypatch,
        {"path": str(repo_dir), "slug": None, "scheme": "myscheme",
         "debounce": 0.0, "language": "swift", "fallback_search_only": True},
        sleep_results=itertools.chain(itertools.repeat(None, 5), [KeyboardInterrupt()]),
    )
    assert rc == 0
    assert captured["fallback_search_only"] is True
```

**Pattern for SEMA-02 watch test:** Same `_drive_cmd_watch` + `fake_index_repo` + `KeyboardInterrupt` structure. Assert that `input` is never called (monkeypatch `builtins.input` to `assert False`). The offer code lives in `_cmd_index`, never reached by watch's `_reindex` closure which calls `index_repo` directly.

---

#### 3c. Semantic stage skip hint pin (SEMA-02 regression)

**Analog:** `test_semantic_stage_skips_cleanly_when_extra_missing` (lines 1024-1039)

**Existing code** (lines 1024-1039):
```python
def test_semantic_stage_skips_cleanly_when_extra_missing(monkeypatch, capsys):
    import sys

    import jarvis
    from jarvis.index_cli import _run_semantic_stage

    monkeypatch.setitem(sys.modules, "jarvis.semantic", None)
    monkeypatch.delattr(jarvis, "semantic", raising=False)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    assert "jarvis-mcp[semantic]" in capsys.readouterr().err
```

**Pattern:** This existing test pins the stderr hint behavior. It stays green untouched — the offer does not modify `_run_semantic_stage`. New offer tests should NOT conflict with it.

---

### `tests/test_registry.py` (test)

#### 4a. Column migration test

**Analog:** `test_fallback_enabled_column_migrates_onto_an_existing_database` (lines 577-607)

**Existing code** (lines 577-607):
```python
def test_fallback_enabled_column_migrates_onto_an_existing_database(tmp_path: Path):
    """FALL-02: a registry created before this column existed gains it on
    first open via `_ensure_column`; the legacy row reads NULL ..."""
    db = tmp_path / "registry.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE repos (slug TEXT PRIMARY KEY, path TEXT NOT NULL, "
        "language TEXT NOT NULL, commit_sha TEXT, last_indexed TEXT NOT NULL, "
        "status TEXT NOT NULL, scheme_override TEXT, semantic_indexed_at TEXT, "
        "semantic_include TEXT, language_override TEXT, "
        "search_only INTEGER NOT NULL DEFAULT 0, tracked_files INTEGER, "
        "status_origin TEXT, status_reason TEXT, status_stderr TEXT)"
    )
    conn.execute(
        "INSERT INTO repos VALUES ('old', '/p', 'python', 'abc', "
        "'2026-01-01T00:00:00+00:00', 'indexed', NULL, NULL, NULL, NULL, "
        "0, 42, NULL, NULL, NULL)"
    )
    conn.commit()
    conn.close()

    registry = Registry(db)
    try:
        entry = registry.get("old")
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.fallback_enabled is None  # NULL = never set
    finally:
        registry.close()

    probe = sqlite3.connect(str(db))
    try:
        cols = {row[1] for row in probe.execute("PRAGMA table_info(repos)")}
        assert "fallback_enabled" in cols
    finally:
        probe.close()
```

**Pattern:** Clone this test. The pre-migration CREATE TABLE must include all columns **except** `semantic_declined` (and `fallback_enabled`). Assert `entry.semantic_declined is False` (NULL reads as False for the decline bit). Assert column exists via PRAGMA.

---

#### 4b. Tri-state roundtrip / upsert preservation test

**Analog:** `test_fallback_enabled_tri_state_roundtrip` (lines 616-634)

**Existing code** (lines 616-634):
```python
def test_fallback_enabled_tri_state_roundtrip(tmp_path: Path):
    """FALL-02 tri-state: NULL default, explicit True/False via the
    dedicated setter, and — critically — a plain upsert (the terminal-write
    shape, no fallback argument) must leave the stored value untouched
    (upsert's ON CONFLICT list never names the column)."""
    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("mine", "/repos/mine", "python", "abc", "indexing")
        assert registry.get("mine").fallback_enabled is None
        registry.set_fallback_enabled("mine", True)
        assert registry.get("mine").fallback_enabled is True
        registry.set_fallback_enabled("mine", False)
        assert registry.get("mine").fallback_enabled is False
        registry.set_fallback_enabled("mine", True)
        # The terminal-write shape: no fallback argument rides the upsert.
        registry.upsert("mine", "/repos/mine", "python", "def", "indexed")
        assert registry.get("mine").fallback_enabled is True
    finally:
        registry.close()
```

**Pattern:** Clone, adapting for `semantic_declined`. Simpler — only two states: `False` (default/NULL) and `True` (declined). Assert: default after upsert is `False`; setter writes `True`; plain upsert preserves `True`; `record_failure` preserves `True`.

---

## Shared Patterns

### Short-lived Registry connections in CLI commands
**Source:** `_cmd_list` (index_cli.py:1287-1295)
**Apply to:** The offer block in `_cmd_index`
```python
registry = Registry(config.data_dir() / "registry.db")
try:
    ...
finally:
    registry.close()
```

### `getattr(args, ...)` for optional Namespace attrs
**Source:** `_cmd_index` (index_cli.py:1270-1277)
**Apply to:** `offer_semantic` read in `_cmd_index`
```python
getattr(args, "search_only", None)
getattr(args, "fallback_search_only", None)
# New:
getattr(args, "offer_semantic", False)
```

### Dedicated UPDATE setter, never in upsert
**Source:** `set_fallback_enabled` (registry.py:329-346), `mark_tracked_files` (registry.py:314-323)
**Apply to:** `set_semantic_declined` — identical shape, different column name

### One stderr line per non-fatal condition
**Source:** `_run_semantic_stage` stderr prints (index_cli.py:747-749, 757-758, 762-764)
**Apply to:** Install failure warning — one `print(..., file=sys.stderr)` line naming the failed command

### Test seam isolation ("Isolated for tests to monkeypatch")
**Source:** `_scip_version_output` (index_cli.py:486-492)
**Apply to:** `_semantic_extra_missing`, `_at_interactive_tty`, `_install_semantic_extra` — all module-level helpers, monkeypatchable by full path

## No Analog Found

None — all four files have exact in-file analogs for every change site.

## Metadata

**Analog search scope:** `src/jarvis/index_cli.py`, `src/jarvis/registry.py`, `tests/test_index_cli.py`, `tests/test_registry.py`
**Files scanned:** 4 (all target files; no external files needed)
**Pattern extraction date:** 2026-08-23
