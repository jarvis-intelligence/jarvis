# Zoekt Git-Based Indexing Input Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `zoekt-index` (filesystem walk) with `zoekt-git-index` so gitignored content is excluded by construction, and make an incomplete search index detectable instead of silent.

**Architecture:** `zoekt-git-index` reads the git tree at HEAD rather than walking the working directory, so `.venv/`, `node_modules/`, and vendored checkouts never enter the index. Because it has no `-meta` flag, the Zoekt repository name is pinned with `git config zoekt.name <slug>` so `searchCode`'s `r:<slug>` filter keeps matching. A `tracked_files` count recorded at index time and compared against zoekt's live `Documents` at status time turns a truncated index from a silent wrong answer into a reported one.

**Tech Stack:** Python 3.12+, uv, pytest (`unit` default / `integration` marker), sqlite3 (no ORM), httpx, sourcegraph/zoekt pinned at commit `33f1f18af292`.

**Spec:** `docs/superpowers/specs/2026-08-03-zoekt-indexing-input-contract-design.md`

**Branch:** `feat/zoekt-git-index-input-contract` (already exists, spec committed at `e87ad7b`)

## Global Constraints

- Python `>=3.12`. Modern type-hint syntax only: `str | None`, `list[T]`, `dict[K, V]`.
- Result types are frozen dataclasses (`@dataclass(frozen=True)`), never Pydantic.
- Direct `sqlite3`, no ORM, always parameterized queries.
- Env vars are prefixed `CODEINTEL_`.
- Test files mirror source modules 1:1. Git-related unit tests build **real** git repos in `tmp_path` via the existing `_init_git_repo` helper — do not mock `subprocess` for git.
- Integration tests are marked `@pytest.mark.integration` and must skip cleanly when a binary is absent.
- `server.py` catches all exceptions per-tool and returns `{"error": "..."}` rather than raising.
- Zoekt commit pin is `ZOEKT_COMMIT_PIN="33f1f18af292"` in `setup.sh`. Do not change it.
- Conventional commit messages, no AI references.
- Never mutate a published `index-<sha>.db`; queries open it `mode=ro&immutable=1`.

---

### Task 1: Registry — generic column migration and `tracked_files`

**Files:**
- Modify: `src/codeintel/registry.py:40-117` (replace five `_ensure_*_column` functions), `:131-162` (`RegisteredRepo`, `_row_to_repo`), `:165-180` (`__init__`), `:233-248` (`get`, `list`)
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces:
  - `_ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None`
  - `Registry.mark_tracked_files(self, slug: str, count: int) -> None`
  - `RegisteredRepo.tracked_files: int | None = None`

**Deviation from the spec:** the spec specified a `Registry.find_by_path()`.
Dropped — Task 4 must compare *resolved* paths (rows written before this change
hold unresolved ones), so an exact-match SQL lookup cannot do the job and the
method would be dead code. Task 4 filters `Registry.list()` instead, which is
trivial at this scale.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registry.py`:

```python
def test_ensure_column_is_idempotent(tmp_path: Path):
    """Second call must not raise: "duplicate column name" means a previous
    run (or a fresh _SCHEMA create) already added it."""
    import sqlite3

    from codeintel.registry import _ensure_column

    conn = sqlite3.connect(tmp_path / "r.db")
    conn.execute("CREATE TABLE repos (slug TEXT PRIMARY KEY)")
    _ensure_column(conn, "extra_col", "TEXT")
    _ensure_column(conn, "extra_col", "TEXT")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(repos)")}
    conn.close()
    assert "extra_col" in cols


def test_ensure_column_reraises_non_duplicate_errors(tmp_path: Path):
    """A lock timeout must not be swallowed as "already exists" -- that
    would leave the column missing while looking like success."""
    import sqlite3

    import pytest

    from codeintel.registry import _ensure_column

    conn = sqlite3.connect(tmp_path / "r.db")
    with pytest.raises(sqlite3.OperationalError):
        _ensure_column(conn, "c", "TEXT")  # no `repos` table exists
    conn.close()


def test_tracked_files_defaults_to_none_and_round_trips(tmp_path: Path):
    from codeintel.registry import Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        assert registry.get("myslug").tracked_files is None
        registry.mark_tracked_files("myslug", 133)
        assert registry.get("myslug").tracked_files == 133
    finally:
        registry.close()


def test_mark_tracked_files_survives_a_later_upsert(tmp_path: Path):
    """upsert's ON CONFLICT list must not clobber tracked_files -- a reindex
    upserts status before the new count is known."""
    from codeintel.registry import Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
        registry.upsert("myslug", "/repos/mine", "python", "def", "indexing")
        assert registry.get("myslug").tracked_files == 133
    finally:
        registry.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -k "ensure_column or tracked_files" -v`
Expected: FAIL — `ImportError: cannot import name '_ensure_column'`, and `AttributeError: 'Registry' object has no attribute 'mark_tracked_files'`.

- [ ] **Step 3: Replace the five migration functions with one helper**

Delete `_ensure_scheme_override_column`, `_ensure_semantic_indexed_at_column`, `_ensure_semantic_include_column`, `_ensure_language_override_column`, and `_ensure_search_only_column` (`registry.py:40-117`). Replace with:

```python
def _ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Idempotent `ALTER TABLE repos ADD COLUMN` for databases created before
    `name` existed.

    A "duplicate column name" `OperationalError` means a previous run (or a
    fresh `_SCHEMA` create) already added it, so it is ignored. Any other
    `OperationalError` -- notably "database is locked" from a concurrent
    `codeintel watch` reindex -- is re-raised rather than swallowed: a lock
    timeout during migration would otherwise look identical to "already
    exists" while actually leaving the column missing.
    """
    try:
        conn.execute(f"ALTER TABLE repos ADD COLUMN {name} {decl}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise
```

`name` and `decl` are internal literals from the call site below, never user input, so the f-string carries no injection risk — SQLite does not accept parameters in DDL.

- [ ] **Step 4: Add `tracked_files` to the schema, dataclass, and row mapping**

In `_SCHEMA` (`registry.py:23-37`), add as the last column after `search_only`:

```
    search_only INTEGER NOT NULL DEFAULT 0,
    tracked_files INTEGER
```

In `RegisteredRepo`, add the last field:

```python
    tracked_files: int | None = None
```

In `_row_to_repo`, extend the unpack and the constructor:

```python
def _row_to_repo(row: tuple) -> RegisteredRepo:
    (slug, path, language, commit_sha, last_indexed, status,
     scheme_override, semantic_indexed_at, semantic_include, language_override,
     search_only, tracked_files) = row
```

and add `tracked_files=tracked_files,` to the `RegisteredRepo(...)` call.

- [ ] **Step 5: Update `__init__` to call the helper for all six columns**

Replace the five `_ensure_*_column(self._conn)` calls (`registry.py:176-180`) with:

```python
        _ensure_column(self._conn, "scheme_override", "TEXT")
        _ensure_column(self._conn, "semantic_indexed_at", "TEXT")
        _ensure_column(self._conn, "semantic_include", "TEXT")
        _ensure_column(self._conn, "language_override", "TEXT")
        _ensure_column(self._conn, "search_only", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(self._conn, "tracked_files", "INTEGER")
```

- [ ] **Step 6: Add `tracked_files` to both existing SELECTs and add `mark_tracked_files`**

`get` and `list` each select an explicit column list. Append `tracked_files` to both (after `search_only`). Then add, after `list`:

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

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -v`
Expected: PASS — all new tests plus every pre-existing registry test.

- [ ] **Step 8: Run the full unit suite to catch row-shape regressions**

Run: `uv run pytest -m "not integration" -q`
Expected: PASS. `_row_to_repo` now unpacks 12 values; any caller still building an 11-tuple fails here.

- [ ] **Step 9: Commit**

```bash
git add src/codeintel/registry.py tests/test_registry.py
git commit -m "feat: registry tracked_files column and generic column migration"
```

---

### Task 2: `_tracked_blob_count` — the coverage expectation

**Files:**
- Modify: `src/codeintel/index_cli.py` (add after `_git_tracked_files`, currently ending at `:257`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `_tracked_blob_count(repo_path: Path) -> int`

The spec calls this `_tracked_file_count`; renamed to `_tracked_blob_count`
because it counts git *blobs* and the distinction from gitlinks is the whole
point of the function. Use `_tracked_blob_count` everywhere.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_tracked_blob_count_counts_tracked_files(tmp_path: Path):
    from codeintel.index_cli import _tracked_blob_count

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _init_git_repo(tmp_path)

    assert _tracked_blob_count(tmp_path) == 2


def test_tracked_blob_count_ignores_untracked_files(tmp_path: Path):
    from codeintel.index_cli import _tracked_blob_count

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    (tmp_path / "untracked.py").write_text("z = 3\n")

    assert _tracked_blob_count(tmp_path) == 1


def test_tracked_blob_count_excludes_submodule_gitlinks(tmp_path: Path):
    """A submodule is one mode-160000 gitlink entry, not a file. Because
    zoekt-git-index runs with -submodules=false it never descends into it, so
    counting the gitlink would make the expectation permanently unreachable."""
    from codeintel.index_cli import _tracked_blob_count

    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "lib.py").write_text("v = 1\n")
    _init_git_repo(inner)

    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "a.py").write_text("x = 1\n")
    _init_git_repo(outer)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(inner), "inner"],
        cwd=outer, check=True, capture_output=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "add submodule"], cwd=outer, check=True)

    # a.py + .gitmodules == 2; the `inner` gitlink is excluded.
    assert _tracked_blob_count(outer) == 2


def test_tracked_blob_count_raises_for_non_git_directory(tmp_path: Path):
    from codeintel.index_cli import NotAGitRepositoryError, _tracked_blob_count

    with pytest.raises(NotAGitRepositoryError):
        _tracked_blob_count(tmp_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k tracked_blob_count -v`
Expected: FAIL — `ImportError: cannot import name '_tracked_blob_count'`.

- [ ] **Step 3: Implement it**

Add after `_git_tracked_files` in `index_cli.py`:

```python
_GITLINK_MODE = "160000"


def _tracked_blob_count(repo_path: Path) -> int:
    """How many git-tracked blobs exist at HEAD — the number of files
    `zoekt-git-index` should index, and so the expected search coverage.

    Not built on `_git_tracked_files`: that uses plain `ls-files -z`, which
    emits paths with no mode, and a submodule gitlink is indistinguishable
    from a file in that output. `-s` prefixes each entry with
    `<mode> <sha> <stage>\\t`, letting mode 160000 (gitlink) be dropped —
    required because `-submodules=false` means zoekt never descends into a
    submodule, so counting its gitlink would make the expectation unreachable.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-s", "-z"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NotAGitRepositoryError(
            f"{repo_path} is not a git repository (git ls-files -s: {result.stderr.strip()})"
        )
    count = 0
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        if not entry.startswith(f"{_GITLINK_MODE} "):
            count += 1
    return count
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k tracked_blob_count -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: count git-tracked blobs excluding submodule gitlinks"
```

---

### Task 3: Pin and unpin the Zoekt repository name

**Files:**
- Modify: `src/codeintel/index_cli.py` (add near `_remove_zoekt_shards`, currently `:787-804`; wire into `_cmd_forget` at `:824`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `_pin_zoekt_repo_name(repo_path: Path, slug: str) -> None`
  - `_unpin_zoekt_repo_name(repo_path: Path) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def _git_config_value(repo_path: Path, key: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "config", "--get", key],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def test_pin_zoekt_repo_name_sets_the_slug(tmp_path: Path):
    from codeintel.index_cli import _pin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    _pin_zoekt_repo_name(tmp_path, "myslug")

    assert _git_config_value(tmp_path, "zoekt.name") == "myslug"


def test_pin_zoekt_repo_name_is_idempotent(tmp_path: Path):
    from codeintel.index_cli import _pin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    _pin_zoekt_repo_name(tmp_path, "first")
    _pin_zoekt_repo_name(tmp_path, "second")

    assert _git_config_value(tmp_path, "zoekt.name") == "second"


def test_pin_zoekt_repo_name_raises_for_non_git_directory(tmp_path: Path):
    """Must fail loudly: an unpinned name makes zoekt derive one from the
    origin remote URL, and `r:<slug>` then returns zero hits with no error."""
    from codeintel.index_cli import IndexingError, _pin_zoekt_repo_name

    with pytest.raises(IndexingError):
        _pin_zoekt_repo_name(tmp_path, "myslug")


def test_unpin_zoekt_repo_name_removes_the_key(tmp_path: Path):
    from codeintel.index_cli import _pin_zoekt_repo_name, _unpin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    _pin_zoekt_repo_name(tmp_path, "myslug")

    _unpin_zoekt_repo_name(tmp_path)

    assert _git_config_value(tmp_path, "zoekt.name") is None


def test_unpin_zoekt_repo_name_tolerates_a_missing_key(tmp_path: Path):
    """git config --unset exits 5 when the key is absent. Repos indexed
    before this change have no zoekt.name, and `forget` must still succeed."""
    from codeintel.index_cli import _unpin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    _unpin_zoekt_repo_name(tmp_path)  # must not raise


def test_unpin_zoekt_repo_name_tolerates_a_missing_directory(tmp_path: Path):
    """`forget` must work after the user has deleted the repo from disk."""
    from codeintel.index_cli import _unpin_zoekt_repo_name

    _unpin_zoekt_repo_name(tmp_path / "gone")  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k zoekt_repo_name -v`
Expected: FAIL — `ImportError: cannot import name '_pin_zoekt_repo_name'`.

- [ ] **Step 3: Implement both functions**

Add above `_remove_zoekt_shards` in `index_cli.py`:

```python
def _pin_zoekt_repo_name(repo_path: Path, slug: str) -> None:
    """Pin the Zoekt repository name to `slug` via `git config zoekt.name`.

    `zoekt-git-index` has no `-meta` flag, so this replaces
    `_write_zoekt_meta`. Its name resolution order is: `zoekt.name` git
    config, else the `origin` remote URL url-escaped (e.g.
    `github.com%2Fowner%2Frepo`), else the directory basename. Every real repo
    has a remote, so without this `searchCode`'s `r:<slug>` filter matches
    nothing and the tool returns zero hits with no error — a silent wrong
    answer.

    `-shard_prefix_override` is NOT a substitute: it renames the shard file
    while leaving the indexed repository name untouched.

    Raises rather than warning: publishing an index whose name cannot be
    pinned produces exactly the silent failure this exists to prevent.
    """
    _run(["git", "-C", str(repo_path), "config", "zoekt.name", slug],
         cwd=repo_path, step="git config zoekt.name")


def _unpin_zoekt_repo_name(repo_path: Path) -> None:
    """Remove the `zoekt.name` pin, so `forget` leaves no footprint in the
    user's repo.

    Best-effort by design: `git config --unset` exits 5 when the key is
    absent (a repo indexed before pinning existed) and non-zero when the
    directory is gone (the user deleted the repo). Neither should fail a
    `forget` whose real work — dropping the registry row, index, and shards —
    has nothing to do with this key.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "--unset", "zoekt.name"],
        capture_output=True, text=True, check=False,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k zoekt_repo_name -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Wire the unpin into `_cmd_forget`**

In `_cmd_forget`, the registry row is read before deletion — capture the path so the unpin can use it. Replace the body between the `existed` check and `_remove_zoekt_shards(slug)`:

```python
    registry = Registry(config.data_dir() / "registry.db")
    try:
        entry = registry.get(slug)
        existed = registry.forget(slug)
    finally:
        registry.close()
    if not existed:
        print(f"error: no such repo: {slug}", file=sys.stderr)
        return 1
    if entry is not None:
        _unpin_zoekt_repo_name(Path(entry.path))
    index_dir = config.index_dir(slug)
```

- [ ] **Step 6: Write the forget test**

```python
def test_forget_unpins_the_zoekt_repo_name(tmp_path: Path, monkeypatch, capsys):
    import argparse

    from codeintel import config
    from codeintel.index_cli import _cmd_forget, _pin_zoekt_repo_name
    from codeintel.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)
    _pin_zoekt_repo_name(repo, "myslug")

    data_dir = tmp_path / "data"
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(data_dir))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", str(repo), "python", "abc", "indexed")
    finally:
        registry.close()

    assert _cmd_forget(argparse.Namespace(slug="myslug")) == 0
    assert _git_config_value(repo, "zoekt.name") is None
```

- [ ] **Step 7: Run the forget test and the whole forget group**

Run: `uv run pytest tests/test_index_cli.py -k "forget or zoekt_repo_name" -v`
Expected: PASS, including the pre-existing `test_forget_removes_the_zoekt_shard`.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: pin zoekt repo name via git config, unpin on forget"
```

---

### Task 4: Enforce one slug per repo path

**Files:**
- Modify: `src/codeintel/index_cli.py:575-586` (`index_repo`, right after slug resolution)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `Registry.list() -> list[RegisteredRepo]` (pre-existing)
- Produces: `_reject_duplicate_slug_for_path(registry: Registry, slug: str, repo_path: Path) -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_index_repo_rejects_a_second_slug_for_the_same_path(tmp_path: Path, monkeypatch):
    """zoekt.name is one value per repo, so a second slug for one path would
    overwrite the first's name and silently break `r:<first-slug>`."""
    from codeintel import config
    from codeintel.index_cli import IndexingError, index_repo
    from codeintel.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("first", str(repo.resolve()), "python", "abc", "indexed")
    finally:
        registry.close()

    with pytest.raises(IndexingError, match="already indexed as 'first'"):
        index_repo(repo, slug="second")


def test_index_repo_allows_reindexing_the_same_slug(tmp_path: Path, monkeypatch):
    """The normal reindex/watch path: same slug, same path, must not trip."""
    from codeintel import config
    from codeintel.index_cli import _reject_duplicate_slug_for_path
    from codeintel.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("same", str(repo.resolve()), "python", "abc", "indexed")
        _reject_duplicate_slug_for_path(registry, "same", repo.resolve())  # must not raise
    finally:
        registry.close()


def test_reject_duplicate_slug_compares_resolved_paths(tmp_path: Path, monkeypatch):
    """Rows written before this change may hold unresolved paths; a trailing
    "/." or symlinked parent must still be recognised as the same repo."""
    from codeintel import config
    from codeintel.index_cli import IndexingError, _reject_duplicate_slug_for_path
    from codeintel.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("first", f"{repo}/.", "python", "abc", "indexed")
        with pytest.raises(IndexingError, match="already indexed as 'first'"):
            _reject_duplicate_slug_for_path(registry, "second", repo.resolve())
    finally:
        registry.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "duplicate_slug or same_slug" -v`
Expected: FAIL — `ImportError: cannot import name '_reject_duplicate_slug_for_path'`.

- [ ] **Step 3: Implement the check**

Add above `index_repo` in `index_cli.py`:

```python
def _reject_duplicate_slug_for_path(registry: Registry, slug: str, repo_path: Path) -> None:
    """One slug per repo path.

    `zoekt.name` lives in a repo's `.git/config` — one value per repo. Two
    slugs pointing at the same path cannot both be searchable: the second
    index overwrites the first's pinned name, and `r:<first-slug>` then
    returns zero hits with no error. Three slugs also meant paying for three
    near-identical shards of the same content.

    Compares resolved paths because rows written before this check existed
    may hold unresolved ones. Same slug at the same path is the normal
    reindex/watch case and passes.
    """
    for existing in registry.list():
        if existing.slug == slug:
            continue
        try:
            same = Path(existing.path).resolve() == repo_path
        except OSError:
            # A registered path that no longer exists cannot collide.
            continue
        if same:
            raise IndexingError(
                f"{repo_path} is already indexed as {existing.slug!r}. "
                f"One slug per repo — run `codeintel forget {existing.slug}` first, "
                f"or reindex that slug instead."
            )
```

`registry.list()` rather than a `WHERE path = ?` lookup: the stored path may be unresolved (`/repo/.`, a symlinked parent), so exact matching would miss the very collisions this exists to catch. Scanning is trivial at this scale — a personal registry holds tens of rows.

- [ ] **Step 4: Call it from `index_repo`**

In `index_repo`, immediately after `search_only = _resolve_search_only(...)` is **not** the right place — the check must precede any mutation. Insert directly after the `Registry(...)` construction and before `_resolve_search_only`:

```python
    registry = Registry(config.data_dir() / "registry.db")
    try:
        _reject_duplicate_slug_for_path(registry, slug, repo_path)
    except Exception:
        registry.close()
        raise
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "duplicate_slug or same_slug" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full unit suite**

Run: `uv run pytest -m "not integration" -q`
Expected: PASS. Any existing test that indexes two slugs at one path surfaces here — if one does, it was asserting the behavior now deliberately rejected; update it to use distinct paths.

- [ ] **Step 7: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: reject a second slug for an already-indexed repo path"
```

---

### Task 5: Swap `zoekt-index` for `zoekt-git-index`

**Files:**
- Modify: `src/codeintel/index_cli.py:292-299` (`_run` returns the process), `:371-381` (delete `_write_zoekt_meta`), `:533-541` (`_publish_search_only`), `:678-685` (`index_repo`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `_pin_zoekt_repo_name` (Task 3)
- Produces:
  - `_run(...) -> subprocess.CompletedProcess[str]` (was `-> None`)
  - `_zoekt_index_cmd(zoekt_dir: Path, repo_path: Path) -> list[str]`

**Pre-existing tests this task breaks** (fix them in Step 8, exact locations):
- `tests/test_index_cli.py:24,27,30` — `_REQUIRED_BINARIES`,
  `_SWIFT_REQUIRED_BINARIES`, `_missing_java` all list `zoekt-index`. Must
  become `zoekt-git-index` or every integration test skips forever.
- `tests/test_index_cli.py:586-591` — `test_write_zoekt_meta_contains_slug`
  must be **deleted**; the function no longer exists.
- `tests/test_index_cli.py:1198,1352` — monkeypatched `_run` doubles branch on
  `cmd[0] == "zoekt-index"`. Must become `"zoekt-git-index"`.
- `tests/test_index_cli.py:406,1509-1510` — assertion message and comment
  mentioning `zoekt-index`; cosmetic, update for accuracy.

- [ ] **Step 1: Write the failing tests**

```python
def test_zoekt_index_cmd_uses_git_index_with_pinned_flags(tmp_path: Path):
    """-incremental=false because the default would refuse to repair an
    already-published incomplete shard. -submodules=false because submodules
    are indexed as their own slugs, and including them here would both
    duplicate content and make the coverage expectation unreachable."""
    from codeintel.index_cli import _zoekt_index_cmd

    cmd = _zoekt_index_cmd(tmp_path / ".zoekt", tmp_path / "repo")

    assert cmd[0] == "zoekt-git-index"
    assert "-incremental=false" in cmd
    assert "-submodules=false" in cmd
    assert "-meta" not in cmd, "zoekt-git-index has no -meta flag"
    assert cmd[-1] == str(tmp_path / "repo")


def test_write_zoekt_meta_is_gone():
    """Replaced by _pin_zoekt_repo_name — zoekt-git-index takes no -meta."""
    import codeintel.index_cli as index_cli

    assert not hasattr(index_cli, "_write_zoekt_meta")


def test_run_returns_the_completed_process(tmp_path: Path):
    """Coverage parsing needs the indexer's stderr, which _run previously
    discarded on success."""
    from codeintel.index_cli import _run

    result = _run(["echo", "hello"], cwd=tmp_path, step="echo")

    assert result.stdout.strip() == "hello"


def test_run_turns_a_missing_binary_into_a_setup_remedy(tmp_path: Path):
    """A missing binary raised a bare FileNotFoundError, which says nothing
    about how to fix it. Matters most for zoekt-git-index: existing installs
    have zoekt-index and must re-run setup.sh."""
    from codeintel.index_cli import IndexingError, _run

    with pytest.raises(IndexingError, match="setup.sh"):
        _run(["definitely-not-a-real-binary"], cwd=tmp_path, step="fake step")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "zoekt_index_cmd or write_zoekt_meta_is_gone or run_returns" -v`
Expected: FAIL — `ImportError: cannot import name '_zoekt_index_cmd'`; `test_write_zoekt_meta_is_gone` fails because the attribute still exists.

- [ ] **Step 3: Make `_run` return the process**

Change the signature and add a return, leaving behavior otherwise identical (existing callers ignore the value):

```python
def _run(cmd: list[str], *, cwd: Path, step: str,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """`env`, when given, is merged OVER a copy of `os.environ` rather than
    replacing it — a bare replacement would drop PATH and break the very
    subprocess lookup that finds the indexer.

    Returns the completed process so callers can read output on success;
    `zoekt-git-index` reports its file count on stderr, which the search
    coverage check parses.

    A missing executable raises `FileNotFoundError`, not a non-zero exit, so
    it is translated into an `IndexingError` naming `setup.sh` — the same
    remedy `_scip_version_output` gives. This matters most for
    `zoekt-git-index`: every install predating the switch has `zoekt-index`
    instead, and there is deliberately no fallback to it, because falling back
    would silently reintroduce indexing of gitignored content.
    """
    merged = {**os.environ, **env} if env else None
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=merged)
    except FileNotFoundError as exc:
        raise IndexingError(
            f"{step} failed: {cmd[0]} not found on PATH — run setup.sh"
        ) from exc
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")
    return result
```

- [ ] **Step 4: Delete `_write_zoekt_meta` and add `_zoekt_index_cmd`**

Delete `_write_zoekt_meta` entirely (`index_cli.py:371-381`). Add in its place:

```python
def _zoekt_index_cmd(zoekt_dir: Path, repo_path: Path) -> list[str]:
    """The `zoekt-git-index` invocation shared by both publish paths.

    `zoekt-git-index`, not `zoekt-index`: it walks the git tree and reads
    blobs by SHA, so gitignored content — `.venv/`, `node_modules/`,
    vendored checkouts — is absent by construction rather than by a
    hand-maintained denylist. This is upstream's recommended tool for local
    git repos, and the same reasoning `detect_language()` already applies:
    read git, not the filesystem.

    Consequence: search reflects HEAD, while SCIP navigation reflects the
    working tree. Uncommitted edits are searchable only after a commit.

    `-incremental=false`: the default (true) skips indexing when the shard is
    newer than refs, which would refuse to repair an already-published
    incomplete shard. codeintel's registry owns the when-to-reindex decision.

    `-submodules=false`: submodules are indexed under their own slugs, so
    including them here would duplicate content across two indexes and make
    the coverage expectation from `_tracked_blob_count` unreachable.
    """
    return [
        "zoekt-git-index",
        "-index", str(zoekt_dir),
        "-incremental=false",
        "-submodules=false",
        str(repo_path),
    ]
```

- [ ] **Step 5: Update `_publish_search_only`**

The `tempfile.TemporaryDirectory` existed only to hold the meta file, so it goes away:

```python
    _retire_scip_artifacts(slug, root)
    _pin_zoekt_repo_name(repo_path, slug)
    zoekt_dir = config.data_dir(root) / ".zoekt"
    zoekt_dir.mkdir(parents=True, exist_ok=True)
    _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path, step="zoekt-git-index")
    semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)
```

- [ ] **Step 6: Update `index_repo`**

Replace the meta-file block (`index_cli.py:678-685`) with:

```python
            zoekt_dir = config.data_dir(root) / ".zoekt"
            zoekt_dir.mkdir(parents=True, exist_ok=True)
            _pin_zoekt_repo_name(repo_path, slug)
            _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path,
                 step="zoekt-git-index")
```

`index_repo`'s outer `with tempfile.TemporaryDirectory(...) as scratch:` stays — it still holds `index.scip` and `index.db`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "zoekt_index_cmd or write_zoekt_meta_is_gone or run_returns or missing_binary" -v`
Expected: PASS (4 tests).

- [ ] **Step 8: Fix the pre-existing tests listed in this task's Interfaces block**

Work through that list — the three binary-gate lists at `:24,27,30`, the
deletion of `test_write_zoekt_meta_contains_slug` at `:586-591`, the two `_run`
doubles at `:1198,1352`, and the two cosmetic mentions at `:406,1509`. Then:

Run: `uv run pytest -m "not integration" -q`
Expected: PASS. Confirm nothing else references the old command:
`grep -rn "zoekt-index" tests/ src/ | grep -v "zoekt-git-index"` should print nothing.

- [ ] **Step 9: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: index Zoekt shards from git via zoekt-git-index"
```

---

### Task 6: Coverage warning, `tracked_files` recording, `.tmp` orphan sweep

**Files:**
- Modify: `src/codeintel/index_cli.py` (add helpers near `_zoekt_index_cmd`; call from both publish paths)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `_tracked_blob_count` (Task 2), `_zoekt_index_cmd` and `_run`'s return (Task 5), `Registry.mark_tracked_files` (Task 1)
- Produces:
  - `_parse_indexed_file_count(output: str) -> int | None`
  - `_warn_on_coverage_shortfall(slug: str, expected: int, output: str) -> None`
  - `_sweep_zoekt_tmp_orphans(slug: str, root: Path | None) -> list[Path]`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_indexed_file_count_reads_the_indexer_log():
    from codeintel.index_cli import _parse_indexed_file_count

    output = (
        "2026/08/03 22:26:52 attempting to index 133 total files "
        "(0 via cat-file, 133 via go-git)\n"
        "2026/08/03 22:26:53 finished shard /x/codeintel_v16.00000.zoekt: "
        "6408345 index bytes (overhead 3.2), 133 files processed\n"
    )

    assert _parse_indexed_file_count(output) == 133


def test_parse_indexed_file_count_returns_none_on_unknown_format():
    """An upstream log change must degrade to "unknown", never fail a publish."""
    from codeintel.index_cli import _parse_indexed_file_count

    assert _parse_indexed_file_count("nothing recognisable here") is None


def test_warn_on_coverage_shortfall_warns(capsys):
    from codeintel.index_cli import _warn_on_coverage_shortfall

    _warn_on_coverage_shortfall("myslug", 133, "attempting to index 100 total files")

    assert "myslug" in capsys.readouterr().err


def test_warn_on_coverage_shortfall_is_quiet_when_complete(capsys):
    from codeintel.index_cli import _warn_on_coverage_shortfall

    _warn_on_coverage_shortfall("myslug", 133, "attempting to index 133 total files")

    assert capsys.readouterr().err == ""


def test_warn_on_coverage_shortfall_is_quiet_when_unparseable(capsys):
    from codeintel.index_cli import _warn_on_coverage_shortfall

    _warn_on_coverage_shortfall("myslug", 133, "unrecognised")

    assert capsys.readouterr().err == ""


def test_sweep_zoekt_tmp_orphans_removes_only_this_slugs_temp_files(tmp_path: Path):
    """A killed zoekt run leaves a .tmp that is never usable and never
    cleaned up; 545 MB of them accumulated once."""
    from codeintel.index_cli import _sweep_zoekt_tmp_orphans

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "myslug_v16.00000.zoekt").write_bytes(b"x")
    (zoekt_dir / "myslug_v16.00001.zoekt.12345.tmp").write_bytes(b"x")
    (zoekt_dir / "otherslug_v16.00000.zoekt.99.tmp").write_bytes(b"x")

    removed = _sweep_zoekt_tmp_orphans("myslug", root=tmp_path)

    assert len(removed) == 1
    assert (zoekt_dir / "myslug_v16.00000.zoekt").exists(), "must not touch real shards"
    assert not (zoekt_dir / "myslug_v16.00001.zoekt.12345.tmp").exists()
    assert (zoekt_dir / "otherslug_v16.00000.zoekt.99.tmp").exists(), "must not touch other repos"


def test_sweep_zoekt_tmp_orphans_is_safe_when_absent(tmp_path: Path):
    from codeintel.index_cli import _sweep_zoekt_tmp_orphans

    assert _sweep_zoekt_tmp_orphans("nothing-here", root=tmp_path) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "indexed_file_count or coverage_shortfall or tmp_orphans" -v`
Expected: FAIL — `ImportError: cannot import name '_parse_indexed_file_count'`.

- [ ] **Step 3: Implement the three helpers**

Add after `_zoekt_index_cmd`:

```python
_INDEXED_FILE_COUNT_RE = re.compile(r"attempting to index (\d+) total files")


def _parse_indexed_file_count(output: str) -> int | None:
    """How many files `zoekt-git-index` reported indexing, or None when the
    line is absent.

    Returns None rather than raising so an upstream log-format change
    degrades the coverage check to "unknown" instead of failing an otherwise
    healthy publish. The authoritative post-index count comes from zoekt's
    own `/api/list` at status time; this is the cheap index-time signal.
    """
    match = _INDEXED_FILE_COUNT_RE.search(output)
    return int(match.group(1)) if match else None


def _warn_on_coverage_shortfall(slug: str, expected: int, output: str) -> None:
    """Warn when the indexer saw fewer files than git tracks.

    Warns rather than failing: legitimate causes exist — zoekt skips files
    over its 2 MB `-file_limit`, files exceeding `-max_trigram_count`, and
    binaries. Mirrors how `index_has_navigation_data()` publishes a degraded
    index with a warning instead of refusing.
    """
    indexed = _parse_indexed_file_count(output)
    if indexed is None or indexed >= expected:
        return
    print(
        f"warning: {slug} indexed {indexed} of {expected} git-tracked files — "
        "searchCode results will be incomplete. Large files (>2MB) and binaries "
        "are skipped by design; a larger gap suggests a problem.",
        file=sys.stderr,
    )


def _sweep_zoekt_tmp_orphans(slug: str, root: Path | None = None) -> list[Path]:
    """Delete stranded `.tmp` shards for `slug`.

    `zoekt-git-index` writes `<name>.<n>.tmp` and renames on success, so a
    killed run (Ctrl-C, OOM) strands a temp file that is never usable and was
    never cleaned up — 545 MB of them accumulated once. A successful index is
    the natural moment to sweep this repo's leftovers.

    Slug-scoped like `_remove_zoekt_shards`: the `_v` in the glob stops "api"
    from matching "api-gateway"'s files.
    """
    zoekt_dir = config.data_dir(root) / ".zoekt"
    if not zoekt_dir.is_dir():
        return []
    removed: list[Path] = []
    for tmp in sorted(zoekt_dir.glob(f"{slug}_v*.zoekt*.tmp")):
        tmp.unlink(missing_ok=True)
        removed.append(tmp)
    return removed
```

`re` and `sys` are already imported in `index_cli.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "indexed_file_count or coverage_shortfall or tmp_orphans" -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Wire all three into `_publish_search_only`**

```python
    _retire_scip_artifacts(slug, root)
    _pin_zoekt_repo_name(repo_path, slug)
    zoekt_dir = config.data_dir(root) / ".zoekt"
    zoekt_dir.mkdir(parents=True, exist_ok=True)
    tracked = _tracked_blob_count(repo_path)
    result = _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path,
                  step="zoekt-git-index")
    _warn_on_coverage_shortfall(slug, tracked, result.stderr)
    _sweep_zoekt_tmp_orphans(slug, root)
    semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)
```

Change the signature to return the count alongside the semantic flag, since the caller records it:

```python
def _publish_search_only(repo_path: Path, slug: str, root: Path | None,
                         semantic_include: tuple[str, ...]) -> tuple[bool, int]:
```

and `return semantic_ok, tracked` at the end. Update both call sites in `index_repo` (`:614` and `:651`) to unpack `semantic_ok, tracked = _publish_search_only(...)` and to call `registry.mark_tracked_files(slug, tracked)` immediately after their `registry.upsert(..., SEARCH_ONLY_STATUS, ...)`.

- [ ] **Step 6: Wire all three into `index_repo`'s main path**

```python
            zoekt_dir = config.data_dir(root) / ".zoekt"
            zoekt_dir.mkdir(parents=True, exist_ok=True)
            _pin_zoekt_repo_name(repo_path, slug)
            tracked = _tracked_blob_count(repo_path)
            zoekt_result = _run(_zoekt_index_cmd(zoekt_dir, repo_path), cwd=repo_path,
                                step="zoekt-git-index")
            _warn_on_coverage_shortfall(slug, tracked, zoekt_result.stderr)
            _sweep_zoekt_tmp_orphans(slug, root)
```

Then after the existing `registry.upsert(slug, ..., final_status, ...)` call:

```python
        registry.mark_tracked_files(slug, tracked)
```

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest -m "not integration" -q`
Expected: PASS. Any test calling `_publish_search_only` and expecting a bare bool fails here — update it to unpack the tuple.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: record tracked-file coverage and sweep stranded zoekt temp shards"
```

---

### Task 7: `search.py` — non-spawning URL and repo document counts

**Files:**
- Modify: `src/codeintel/search.py` (add a method to `ZoektLifecycle` after `base_url` at `:121-122`; add a module function after `search_zoekt` at `:51`)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ZoektLifecycle.base_url_if_running(self) -> str | None`
  - `zoekt_repo_documents(base_url: str, repo: str, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> int | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_base_url_if_running_returns_none_without_a_pidfile(tmp_path: Path):
    """getIndexStatus must not spawn a webserver just to report coverage."""
    from codeintel.search import ZoektLifecycle

    lifecycle = ZoektLifecycle(index_dir=tmp_path / ".zoekt", data_dir=tmp_path)

    assert lifecycle.base_url_if_running() is None


def test_base_url_if_running_returns_none_for_a_dead_pid(tmp_path: Path):
    from codeintel.search import ZoektLifecycle

    (tmp_path / "zoekt-webserver.pid").write_text("999999999", encoding="utf-8")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / ".zoekt", data_dir=tmp_path)

    assert lifecycle.base_url_if_running() is None


def test_base_url_if_running_returns_url_when_healthy(tmp_path: Path, monkeypatch):
    import os

    from codeintel.search import ZoektLifecycle

    (tmp_path / "zoekt-webserver.pid").write_text(str(os.getpid()), encoding="utf-8")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / ".zoekt", data_dir=tmp_path)
    monkeypatch.setattr(lifecycle, "_is_healthy", lambda: True)

    assert lifecycle.base_url_if_running() == lifecycle.base_url()


def test_zoekt_repo_documents_reads_the_list_api():
    import httpx

    from codeintel.search import zoekt_repo_documents

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/list"
        return httpx.Response(200, json={
            "List": {"Repos": [{"Repository": {"Name": "myslug"}, "Stats": {"Documents": 133}}]}
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert zoekt_repo_documents("http://localhost:6070", "myslug", client=client) == 133


def test_zoekt_repo_documents_returns_none_when_repo_absent():
    import httpx

    from codeintel.search import zoekt_repo_documents

    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"List": {"Repos": []}})
    ))

    assert zoekt_repo_documents("http://localhost:6070", "myslug", client=client) is None


def test_zoekt_repo_documents_raises_on_transport_failure():
    import httpx
    import pytest

    from codeintel.search import ZoektUnavailableError, zoekt_repo_documents

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(ZoektUnavailableError):
        zoekt_repo_documents("http://localhost:6070", "myslug", client=client)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_search.py -k "base_url_if_running or repo_documents" -v`
Expected: FAIL — `AttributeError: 'ZoektLifecycle' object has no attribute 'base_url_if_running'` and `ImportError` for `zoekt_repo_documents`.

- [ ] **Step 3: Add `base_url_if_running`**

Insert into `ZoektLifecycle` immediately after `base_url`:

```python
    def base_url_if_running(self) -> str | None:
        """The base URL of an already-healthy webserver, or None — never
        spawns one.

        `getIndexStatus` reports search coverage, which needs zoekt's
        `/api/list`, but a status call must stay cheap: spawning a webserver
        as a side effect of asking for status would be surprising. In
        practice the server is already up whenever searches are happening.
        """
        pid = self._read_pidfile()
        if pid is None or not self._pid_alive(pid) or not self._is_healthy():
            return None
        return self.base_url()
```

- [ ] **Step 4: Add `zoekt_repo_documents`**

Add after `search_zoekt`:

```python
def zoekt_repo_documents(
    base_url: str, repo: str, *, client: httpx.Client | None = None,
    timeout_seconds: float = 5.0,
) -> int | None:
    """How many documents zoekt currently holds for `repo`, or None when
    zoekt does not know that repo at all.

    Authoritative in a way the indexer's own log is not: it reflects the
    shards on disk *now*, so it detects shards deleted after a successful
    index — the failure mode that made a truncated index look healthy.

    `client` is injectable (a real `httpx.Client`, or one backed by
    `httpx.MockTransport` in tests); defaults to a short-lived real client.
    """
    client = client or httpx.Client()
    try:
        response = client.post(
            f"{base_url.rstrip('/')}/api/list",
            json={"Q": f"r:{repo}"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ZoektUnavailableError(f"zoekt-webserver /api/list failed: {exc}") from exc
    for entry in (response.json().get("List", {}).get("Repos") or []):
        if entry.get("Repository", {}).get("Name") == repo:
            return entry.get("Stats", {}).get("Documents")
    return None
```

The `r:<repo>` filter narrows the response, but the exact-name check is still required: `r:` is a regex match, so `r:api` also returns `api-gateway`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_search.py -v`
Expected: PASS — 6 new tests plus every pre-existing search test.

- [ ] **Step 6: Commit**

```bash
git add src/codeintel/search.py tests/test_search.py
git commit -m "feat: expose zoekt repo document counts without spawning a webserver"
```

---

### Task 8: `getIndexStatus` reports search coverage

**Files:**
- Modify: `src/codeintel/server.py:227-238` (`get_index_status`), plus a helper near `_registry_status` at `:66-80`
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `Registry.get(...).tracked_files` (Task 1), `ZoektLifecycle.base_url_if_running`, `zoekt_repo_documents` (Task 7)
- Produces: `_search_coverage_fields(repo: str) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

```python
def test_search_coverage_fields_reports_complete_when_counts_match(monkeypatch, tmp_path):
    from codeintel import config, server
    from codeintel.registry import Registry

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: "http://x")
    monkeypatch.setattr(server, "zoekt_repo_documents", lambda url, repo: 133)

    assert server._search_coverage_fields("myslug") == {
        "searchCoverage": {"expected": 133, "indexed": 133, "complete": True}
    }


def test_search_coverage_fields_reports_incomplete_after_shard_loss(monkeypatch, tmp_path):
    """The incident: shards deleted after a successful index. Search kept
    answering with partial results and nothing reported a problem."""
    from codeintel import config, server
    from codeintel.registry import Registry

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 1307)
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: "http://x")
    monkeypatch.setattr(server, "zoekt_repo_documents", lambda url, repo: 615)

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] == {"expected": 1307, "indexed": 615, "complete": False}


def test_search_coverage_fields_is_null_when_webserver_is_down(monkeypatch, tmp_path):
    from codeintel import config, server
    from codeintel.registry import Registry

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: None)

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] is None
    assert "not running" in fields["searchCoverageReason"]


def test_search_coverage_fields_is_null_when_never_recorded(monkeypatch, tmp_path):
    """Repos indexed before tracked_files existed have no expectation to
    compare against; report unknown rather than guessing."""
    from codeintel import config, server
    from codeintel.registry import Registry

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
    finally:
        registry.close()

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: "http://x")

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] is None
    assert "reindex" in fields["searchCoverageReason"]


def test_search_coverage_fields_never_raises(monkeypatch, tmp_path):
    """A coverage probe failure must not replace a working status response
    with an error."""
    from codeintel import config, server

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))

    def boom() -> str:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(server, "_zoekt_base_url_if_running", boom)

    fields = server._search_coverage_fields("myslug")

    assert fields["searchCoverage"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_server_tools.py -k search_coverage -v`
Expected: FAIL — `AttributeError: module 'codeintel.server' has no attribute '_search_coverage_fields'`.

- [ ] **Step 3: Add the import and the non-spawning URL helper**

In `server.py`, extend the search import:

```python
from codeintel.search import ZoektLifecycle, search_zoekt, zoekt_repo_documents
```

and add next to `_zoekt_base_url_or_none`:

```python
def _zoekt_base_url_if_running() -> str | None:
    """Module-level indirection so `_search_coverage_fields` is testable
    without a real webserver."""
    return _zoekt().base_url_if_running()
```

- [ ] **Step 4: Implement `_search_coverage_fields`**

Add after `_registry_status`:

```python
def _search_coverage_fields(repo: str) -> dict[str, Any]:
    """Whether Zoekt currently holds as many documents as git tracked at the
    last index.

    This is what makes a truncated index visible. The shards that were lost
    in the August 2026 incident were deleted *after* a successful index, so
    no index-time check could have caught it — only a comparison made when
    the index is consulted.

    Never raises: a coverage probe must not turn a working status response
    into an error. Any failure reports `null` with a reason.
    """
    try:
        from codeintel.registry import Registry

        registry = Registry(config.data_dir() / "registry.db")
        try:
            entry = registry.get(repo)
        finally:
            registry.close()
        if entry is None or entry.tracked_files is None:
            return {
                "searchCoverage": None,
                "searchCoverageReason": (
                    "no tracked-file count recorded — reindex this repo to enable "
                    "the coverage check"
                ),
            }
        base_url = _zoekt_base_url_if_running()
        if base_url is None:
            return {
                "searchCoverage": None,
                "searchCoverageReason": "zoekt-webserver not running",
            }
        indexed = zoekt_repo_documents(base_url, repo)
        if indexed is None:
            return {
                "searchCoverage": None,
                "searchCoverageReason": f"zoekt has no index for {repo}",
            }
        return {
            "searchCoverage": {
                "expected": entry.tracked_files,
                "indexed": indexed,
                # Greater-than is legitimate (multi-branch content), so this is
                # a floor check, not equality.
                "complete": indexed >= entry.tracked_files,
            }
        }
    except Exception as exc:
        return {"searchCoverage": None, "searchCoverageReason": str(exc)}
```

- [ ] **Step 5: Call it from `get_index_status`**

```python
    return {"repo": repo, "indexed": indexed, "status": _registry_status(repo),
            **_freshness_fields(freshness), **_search_coverage_fields(repo)}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server_tools.py -v`
Expected: PASS — 5 new tests plus every pre-existing server tool test.

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest -m "not integration" -q`
Expected: PASS. `tests/test_index_status.py` asserts `getIndexStatus`'s response shape; if it compares the dict exactly, add the two new keys there.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/server.py tests/test_server_tools.py tests/test_index_status.py
git commit -m "feat: report search coverage from getIndexStatus"
```

---

### Task 9: Build and install `zoekt-git-index`

**Files:**
- Modify: `.github/workflows/build-zoekt.yml:44-65` (build matrix), `:80-86` (smoke test), `:100` (release notes)
- Modify: `setup.sh:437-484` (`install_zoekt`)
- Test: `tests/test_setup_sh.py:555-610`

**Interfaces:**
- Consumes: nothing
- Produces: `zoekt-git-index` and `zoekt-webserver` on `PATH` after `setup.sh`

- [ ] **Step 1: Update the two existing zoekt setup tests to the new binary pair**

In `tests/test_setup_sh.py`, replace `"zoekt-index"` with `"zoekt-git-index"` in `test_install_zoekt_skips_when_both_binaries_present` and `test_install_zoekt_extracts_both_binaries` (both iterate `("zoekt-index", "zoekt-webserver")`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -k install_zoekt -v`
Expected: FAIL — `install_zoekt` still extracts `zoekt-index`, so the skip check never trips and the extraction asserts a missing file.

- [ ] **Step 3: Update `install_zoekt`**

In `setup.sh`, change all three `zoekt-index` occurrences to `zoekt-git-index` — the `already_installed` guard (`:444`), the `tar -xzf` member list (`:471`), and the install loop (`:477`) — and update the comment at `:437-439`:

```sh
# zoekt ships as one tarball containing both binaries. Upstream
# sourcegraph/zoekt publishes no releases at all, so these come from
# codeintel's own releases (see .github/workflows/build-zoekt.yml).
#
# zoekt-git-index, not zoekt-index: codeintel indexes from the git tree so
# gitignored content never enters the index. Nothing calls zoekt-index any
# more, and it is deliberately not installed as a fallback — falling back
# would silently reintroduce junk indexing.
```

- [ ] **Step 4: Run the setup tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS — all setup.sh tests.

- [ ] **Step 5: Update the build workflow**

In `.github/workflows/build-zoekt.yml`, the build loop becomes:

```yaml
          go mod init zoekt-build
          # Resolve the pinned commit to a module version and fetch it.
          GOFLAGS=-mod=mod go get "github.com/sourcegraph/zoekt@${ZOEKT_COMMIT}"
          # zoekt-git-index pulls deps zoekt-index does not (automaxprocs,
          # cloud.google.com/go/profiler). Without this the build fails with
          # "missing go.sum entry".
          GOFLAGS=-mod=mod go get "github.com/sourcegraph/zoekt/cmd/zoekt-git-index@${ZOEKT_COMMIT}"

          for target in darwin/arm64 darwin/amd64 linux/amd64 linux/arm64; do
            os=${target%/*}
            arch=${target#*/}
            stage="../dist/stage-${os}-${arch}"
            mkdir -p "$stage"
            for cmd in zoekt-git-index zoekt-webserver; do
              GOFLAGS=-mod=mod GOOS="$os" GOARCH="$arch" CGO_ENABLED=0 \
                go build -trimpath -o "${stage}/${cmd}" \
                "github.com/sourcegraph/zoekt/cmd/${cmd}"
            done
            tar -czf "../dist/zoekt-${os}-${arch}.tar.gz" -C "$stage" zoekt-git-index zoekt-webserver
            rm -rf "$stage"
          done
```

Update the smoke test to match:

```yaml
          smoke/zoekt-git-index -h >index-help.txt 2>&1 || true
          grep -q "zoekt-git-index" index-help.txt
          smoke/zoekt-webserver -version
```

And the release notes at `:100`:

```
--notes "zoekt-git-index and zoekt-webserver cross-compiled from sourcegraph/zoekt@${ZOEKT_COMMIT}. Consumed by setup.sh."
```

- [ ] **Step 6: Verify the workflow edits are internally consistent**

Run: `grep -n "zoekt-index" .github/workflows/build-zoekt.yml setup.sh src/codeintel/*.py`
Expected: only matches containing `zoekt-git-index`. A bare `zoekt-index` anywhere is a missed edit.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/build-zoekt.yml setup.sh tests/test_setup_sh.py
git commit -m "build: ship zoekt-git-index instead of zoekt-index"
```

---

### Task 10: Integration tests against the real binary

**Files:**
- Modify: `tests/test_index_cli.py` (integration section)
- Test: same file

**Interfaces:**
- Consumes: everything from Tasks 1-9
- Produces: nothing consumed by later tasks

**Prerequisite:** `zoekt-git-index` must be on `PATH`. Until the release in Task 9 lands, build it locally:

```bash
cd "$(mktemp -d)" && go mod init ztest && \
  GOFLAGS=-mod=mod go get github.com/sourcegraph/zoekt/cmd/zoekt-git-index@33f1f18af292 && \
  GOBIN="$HOME/.local/bin" go install github.com/sourcegraph/zoekt/cmd/zoekt-git-index
```

- [ ] **Step 1: Write the failing integration tests**

```python
@pytest.mark.integration
def test_zoekt_git_index_excludes_gitignored_content(tmp_path: Path):
    """The whole point: gitignored junk is absent by construction, with no
    denylist to maintain."""
    if shutil.which("zoekt-git-index") is None:
        pytest.skip("zoekt-git-index not on PATH")

    from codeintel.index_cli import (
        _pin_zoekt_repo_name, _tracked_blob_count, _zoekt_index_cmd,
    )

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "junkdir").mkdir()
    (repo / ".gitignore").write_text("junkdir/\n")
    (repo / "src" / "a.py").write_text("sourcetoken_alpha = 1\n")
    (repo / "junkdir" / "big.txt").write_text("junktoken_beta\n")
    _init_git_repo(repo)
    _pin_zoekt_repo_name(repo, "covslug")

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir()
    result = subprocess.run(
        _zoekt_index_cmd(zoekt_dir, repo), cwd=repo, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    # .gitignore + src/a.py == 2; junkdir/big.txt is untracked.
    assert _tracked_blob_count(repo) == 2
    assert "attempting to index 2 total files" in result.stderr
    assert list(zoekt_dir.glob("covslug_v*.zoekt")), "shard must be named after the slug"


@pytest.mark.integration
def test_get_index_status_reports_incomplete_after_a_shard_is_deleted(tmp_path: Path, monkeypatch):
    """The incident, reproduced: a successful index whose shards are then
    deleted must report complete: false instead of quietly answering with
    partial results.

    `-shard_limit 120` forces a multi-shard index on a tiny repo, so this is
    deterministic and fast rather than needing a 100 MB corpus.
    """
    if shutil.which("zoekt-git-index") is None:
        pytest.skip("zoekt-git-index not on PATH")
    if shutil.which("zoekt-webserver") is None:
        pytest.skip("zoekt-webserver not on PATH")

    from codeintel import config, server
    from codeintel.index_cli import _pin_zoekt_repo_name, _tracked_blob_count
    from codeintel.registry import Registry
    from codeintel.search import ZoektLifecycle

    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(6):
        (repo / f"f{i}.txt").write_text(f"token_{i:02d} padding padding padding padding\n")
    _init_git_repo(repo)
    _pin_zoekt_repo_name(repo, "incidentslug")

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir()
    subprocess.run(
        ["zoekt-git-index", "-index", str(zoekt_dir), "-incremental=false",
         "-submodules=false", "-shard_limit", "120", str(repo)],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    shards = sorted(zoekt_dir.glob("incidentslug_v*.zoekt"))
    assert len(shards) > 1, "need a multi-shard index to delete from"

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("incidentslug", str(repo), "python", "abc", "indexed")
        registry.mark_tracked_files("incidentslug", _tracked_blob_count(repo))
    finally:
        registry.close()

    shards[0].unlink()  # the deletion that caused the incident

    lifecycle = ZoektLifecycle(index_dir=zoekt_dir, data_dir=tmp_path, port=6079)
    try:
        base_url = lifecycle.ensure_running()
        monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: base_url)
        fields = server._search_coverage_fields("incidentslug")
    finally:
        lifecycle.stop()

    assert fields["searchCoverage"]["complete"] is False
    assert fields["searchCoverage"]["indexed"] < fields["searchCoverage"]["expected"]
```

`shutil` is already imported in `tests/test_index_cli.py`; add it if not.

- [ ] **Step 2: Run them to verify they fail meaningfully**

Run: `uv run pytest tests/test_index_cli.py -m integration -k "gitignored_content or shard_is_deleted" -v`
Expected: PASS if Tasks 1-9 are complete, or SKIP if `zoekt-git-index` is absent. A FAIL here means a real defect in the earlier tasks — investigate rather than adjusting the test.

- [ ] **Step 3: Run the whole suite, both markers**

Run: `uv run pytest -q`
Expected: PASS, with integration tests skipping cleanly where binaries are missing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_index_cli.py
git commit -m "test: integration coverage for git-based indexing and shard-loss detection"
```

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md` (Architecture → Search bullet, and the **Index pipeline** paragraph)
- Modify: `docs/project-roadmap.md:365-373` ("Garbage Collection for Old Indexes")

**Interfaces:**
- Consumes: nothing
- Produces: nothing

- [ ] **Step 1: Update the Search bullet in CLAUDE.md**

Replace the `**Search** (`search.py`)` bullet's body with:

```markdown
- **Search** (`search.py`) — `searchCode` via a real `httpx` client to `zoekt-webserver`.
  `ZoektLifecycle` lazily spawns the webserver on first call (pidfile-tracked, killed at exit);
  never spawn it elsewhere. `base_url_if_running()` is the non-spawning variant, used by
  `getIndexStatus`'s coverage check so a status call never starts a server as a side effect.
```

- [ ] **Step 2: Update the Index pipeline paragraph in CLAUDE.md**

The pipeline sentence names `zoekt-index`. Replace that step and append the new invariants:

```markdown
**Index pipeline** (`index_cli.py`, `index_repo()`): detect language by extension plurality
**across git-tracked files** (ties broken by fixed priority `.ts→.tsx→.py→.java→.kt→.swift`; one language per repo, no
multi-language merge) → run the matching indexer → `scip expt-convert` → populate the graph →
`zoekt-git-index` → **atomic publish**: write the new versioned `index-<sha>.db`, and only once graph +
Zoekt both succeed, flip the `current` pointer file via `os.replace()`. A query already reading the
old file is never interrupted; a failure anywhere leaves the previous index live. Never mutate a
published `index-<sha>.db` in place — queries always open it `mode=ro&immutable=1`.

**Search indexes git, not the filesystem — and indexes HEAD.** `zoekt-git-index` walks the git
tree and reads blobs by SHA, so gitignored content (`.venv/`, `node_modules/`, vendored
checkouts) is excluded by construction rather than by a denylist. This is the same
read-git-not-the-filesystem rule `detect_language()` follows, for the same reason: a filesystem
walk once made codeintel's own Zoekt index 241 MB / 7353 documents for a repo with 133 tracked
files. The trade-off is that **search reflects HEAD while SCIP navigation reflects the working
tree** — uncommitted edits are navigable but not searchable until committed.

`zoekt-git-index` has no `-meta` flag, so the Zoekt repository name is pinned with
`git config zoekt.name <slug>` (`_pin_zoekt_repo_name`); `forget` unsets it. Without the pin,
zoekt derives the name from the `origin` remote URL, url-escaped, and `searchCode`'s `r:<slug>`
filter silently matches nothing. `-shard_prefix_override` is not a substitute — it renames the
shard file only. Because that key is per-repo, **one slug per repo path** is enforced at index
time.

`<NNNNN>` in `<slug>_v16.<NNNNN>.zoekt` is a **shard ordinal, not a version** — a repo whose
corpus exceeds `-shard_limit` (100 MiB) is split across several shards, all current. Reindexing
overwrites shards in place and `zoekt-git-index` deletes its own surplus, so there is nothing to
garbage-collect; deleting all but the highest-numbered shard destroys most of a large repo's
index. `getIndexStatus`'s `searchCoverage` compares the `tracked_files` recorded at index time
against zoekt's live `Documents` precisely so that kind of loss is reported instead of silently
serving partial results.
```

- [ ] **Step 3: Update the roadmap GC section**

Replace the body of "Garbage Collection for Old Indexes" (`docs/project-roadmap.md:365-373`):

```markdown
### Garbage Collection for Old Indexes

Currently: `_publish_atomically` already deletes the superseded `index-<sha>.db` and its metadata
on every pointer flip. Zoekt shards are overwritten in place by `zoekt-git-index`, which also
removes its own surplus shards, and stranded `.tmp` files are swept after each successful index.
Since indexing moved to the git tree, a shard holds only tracked source, so there is no
accumulation to collect.

**Status:** Not planned, and no longer needed for disk reasons. A `codeintel gc` command would
have nothing to reclaim. Note that "keep only the newest shard" is actively harmful: `<NNNNN>` is
a shard ordinal, not a version.
```

- [ ] **Step 4: Verify no stale references remain**

Run: `grep -rn "zoekt-index" CLAUDE.md docs/ --include=*.md | grep -v "zoekt-git-index" | grep -v superpowers/specs | grep -v superpowers/plans`
Expected: no output. Matches inside `docs/superpowers/specs/` and `docs/superpowers/plans/` are historical records and stay.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/project-roadmap.md
git commit -m "docs: describe git-based Zoekt indexing and search coverage"
```

---

## Recovery Runbook

Not part of the code change — run once after Task 11 is merged and released.

- [ ] **Step 1: Reinstall the binaries**

Wait until the `build-zoekt` GitHub Actions workflow has republished the release assets under
its (unchanged) pin tag before running this — the release-asset tag does not change with this
PR, so `setup.sh` will fail with "archive did not contain both binaries" until that workflow
completes after merge.

```bash
./setup.sh
command -v zoekt-git-index
```

- [ ] **Step 2: Drop the duplicate slugs**

```bash
for slug in epost-comp-showcase-sdk post-shell-app ios-theme-ui luz-epost-ios; do
    uv run codeintel forget "$slug"
done
```

- [ ] **Step 3: Delete every existing shard**

Every shard was built by the old contract and is junk-polluted, including the single-shard ones.

```bash
rm -rf ~/.codeintel/.zoekt
```

- [ ] **Step 4: Reindex**

```bash
uv run codeintel list | awk -F'\t' '$2 == "indexed" || $2 == "partial" || $2 == "search-only" {print $1}' \
  | while read -r slug; do uv run codeintel reindex "$slug"; done
```

- [ ] **Step 5: Verify**

```bash
du -sh ~/.codeintel/.zoekt          # expect far below 2.2 GB
ls ~/.codeintel/.zoekt | wc -l      # expect roughly one shard per repo
find ~/.codeintel/.zoekt -name '*.tmp' | wc -l   # expect 0
```

Then, from an MCP client, call `getIndexStatus` on a few repos and confirm
`searchCoverage.complete` is `true`.

The four `failed` and three stuck `indexing` registry rows are out of scope — they fail for
unrelated indexer reasons (Kotlin ABI mismatch, Maven bash shim) and will surface again here.

---

## Verification Summary

Facts this plan depends on, all measured against zoekt `33f1f18af292`:

| Fact | Evidence |
|---|---|
| `zoekt-git-index` excludes gitignored content | gitignored `junkdir/` → 0 hits; 2 tracked files indexed |
| Origin URL becomes the repo name without a pin | `github.com%2Fsomeone%2Fothername`; `r:myslug` → 0 hits |
| `git config zoekt.name` fixes it | `r:myslug` → 1 hit, `Repository: myslug` |
| `-shard_prefix_override` is not a substitute | file renamed; `r:myslug` 0 hits, `r:myrepo` 1 hit |
| Coverage math is exact | codeintel: 133 tracked blobs → "attempting to index 133" → 133 processed |
| Size win | 241 MB / 7353 docs → 6.4 MB / 133 docs, 1 shard |
| Tiny `-shard_limit` forces multi-shard | `-shard_limit 120`, 6 files → 2 shards |
| `/api/list` returns per-repo `Documents` | codeintel: `Documents = 7353, Shards = 1` |
| `zoekt-git-index` needs an extra `go get` | build failed with "missing go.sum entry" for automaxprocs + cloud profiler |
| HEAD-only | unstaged edit and untracked file → 0 hits |
