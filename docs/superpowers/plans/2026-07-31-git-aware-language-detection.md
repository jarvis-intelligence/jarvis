# Git-Aware Language Detection + `--language` Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide a repo's language from git-tracked files instead of a filesystem walk, so gitignored vendored checkouts can't flip detection to a language the repo doesn't use — and add a `--language` override for polyglot repos where file plurality isn't the language you want.

**Architecture:** `detect_language()` swaps its input set from `repo_path.rglob("*")` to `git -C <repo> ls-files -z`, keeping the existing `_IGNORED_DIRS` filter as a second pass (git doesn't help against *committed* build output) and the existing `_EXT_PRIORITY` tie-break. A non-git directory now raises `NotAGitRepositoryError` instead of silently walking the filesystem. Separately, a nullable `language_override` column on `repos` — following the existing `scheme_override` pattern exactly — lets `--language` bypass detection entirely and persist for `reindex`/`watch`.

**Tech Stack:** Python 3.12+, stdlib only for this change (`subprocess`, `sqlite3`, `argparse`, `pathlib`, `collections.Counter`). Tests: pytest. Package manager: `uv`.

**Spec:** `docs/superpowers/specs/2026-07-31-git-aware-language-detection-design.md`

## Global Constraints

- **Tracked files only.** Plain `git ls-files -z`. Never `--others` / `--exclude-standard` — measured on the real repo, the broader set added 6 leftover `.swift` files in `docs/` and `tests/` that nobody had gitignored. Noise, not signal.
- **`-z` is load-bearing, not stylistic.** With a newline separator git quotes non-ASCII filenames, corrupting suffix parsing. Always split on `"\0"`.
- **`_IGNORED_DIRS` second pass is retained.** Git does not exclude *committed* build output (a checked-in `dist/` or vendored `node_modules`).
- **One language per repo.** No multi-language merge. Unchanged.
- **An override skips detection entirely** — `detect_language()` is not called. An override means "do not guess", not "guess then correct".
- **The registry's `language` column keeps holding the *effective* language** so `codeintel list`/`status` show what was actually indexed. `language_override` records only that it was forced.
- **No validation that an override matches repo contents.** Forcing a language the repo lacks fails at the indexer with that indexer's own message — the honest failure.
- Result types stay frozen dataclasses (`@dataclass(frozen=True)`); direct `sqlite3`, no ORM, always parameterized queries; modern type hints (`str | None`, `list[T]`).
- All new tests are unit-level and must run under `uv run pytest -m "not integration"`. They shell out to `git`, which is already a hard requirement of the tool (`_git_head` needs it) and is already used by the existing `_init_git_repo` helper.
- Conventional commit messages. No AI references in commits.

---

### Task 1: Git subprocess wrappers fail loudly

Both deliverables are thin `git -C <path>` wrappers living side by side in `index_cli.py`; a reviewer would not accept one and reject the other.

**Files:**
- Modify: `src/codeintel/index_cli.py:66-71` (exception classes), `:118-122` (`_git_head`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `NotAGitRepositoryError(Exception)` — raised when a path is not a git working tree.
  - `_git_tracked_files(repo_path: Path) -> list[str]` — repo-relative paths of tracked files.
  - `_git_head(repo_path: Path) -> str` — unchanged signature, now raises `IndexingError` (not `subprocess.CalledProcessError`) on a repo with no commits.

- [ ] **Step 1: Move the existing `_init_git_repo` helper above its new first users**

It currently sits at `tests/test_index_cli.py:186`, below the detection tests that will start calling it in Task 2. Cut it from there and paste it immediately after the `_missing_swift` assignment (currently line 27), before the first test. The body is unchanged:

```python
def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
```

Python resolves module-level names at call time, so this is readability only — but Task 2's tests are its primary users and they read top-down.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_index_cli.py`, immediately after `_init_git_repo`:

```python
def test_git_tracked_files_lists_committed_paths(tmp_path: Path):
    from codeintel.index_cli import _git_tracked_files

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _init_git_repo(tmp_path)

    assert sorted(_git_tracked_files(tmp_path)) == ["a.py", "b.py"]


def test_git_tracked_files_handles_paths_with_spaces(tmp_path: Path):
    """`-z` is required: with a newline separator git quotes unusual names,
    which would corrupt suffix parsing in detect_language()."""
    from codeintel.index_cli import _git_tracked_files

    (tmp_path / "my module.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    assert _git_tracked_files(tmp_path) == ["my module.py"]


def test_git_tracked_files_raises_for_non_git_directory(tmp_path: Path):
    from codeintel.index_cli import NotAGitRepositoryError, _git_tracked_files

    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(NotAGitRepositoryError):
        _git_tracked_files(tmp_path)


def test_git_head_raises_indexing_error_for_repo_with_no_commits(tmp_path: Path):
    """A freshly `git init`-ed repo has no HEAD. Previously this surfaced as
    a bare CalledProcessError with no explanation of what was wrong."""
    from codeintel.index_cli import IndexingError, _git_head

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(IndexingError, match="no commits"):
        _git_head(tmp_path)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "git_tracked_files or git_head_raises" -v`

Expected: the three `_git_tracked_files` tests FAIL with `ImportError: cannot import name '_git_tracked_files'`; `test_git_head_raises_indexing_error_for_repo_with_no_commits` FAILS with `subprocess.CalledProcessError` instead of `IndexingError`.

- [ ] **Step 4: Add the exception class**

In `src/codeintel/index_cli.py`, after the existing `IndexingError` class (currently ending line 71):

```python
class NotAGitRepositoryError(Exception):
    """Raised when a repo path is not a git working tree.

    Indexing already required git -- `_git_head()` reads the commit SHA --
    so this is not a new restriction, just an early and explicit one.
    """
```

- [ ] **Step 5: Add `_git_tracked_files` and rewrite `_git_head`**

Replace the existing `_git_head` (currently `:118-122`) with both functions:

```python
def _git_tracked_files(repo_path: Path) -> list[str]:
    """Repo-relative paths of git-tracked files.

    Git is the source of truth for "what belongs to this repo". A
    filesystem walk also counts gitignored scratch directories -- vendored
    checkouts, sibling clones, worktrees -- which can outnumber the repo's
    own code and flip language detection to a language the repo does not
    actually use.

    `-z` (NUL-delimited) is required, not stylistic: with the default
    newline separator git quotes non-ASCII names, which would corrupt
    suffix parsing downstream.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-z"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NotAGitRepositoryError(
            f"{repo_path} is not a git repository (git ls-files: {result.stderr.strip()})"
        )
    return [name for name in result.stdout.split("\0") if name]


def _git_head(repo_path: Path) -> str:
    """Current commit SHA. A repo with no commits has no HEAD -- report
    that as an IndexingError naming the cause rather than letting a bare
    CalledProcessError escape."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise IndexingError(
            f"{repo_path} has no commits yet (git rev-parse HEAD: {result.stderr.strip()})"
        )
    return result.stdout.strip()
```

Note `check=True` is gone from `_git_head` — the returncode is now inspected explicitly.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "git_tracked_files or git_head_raises" -v`

Expected: 4 passed.

- [ ] **Step 7: Run the full unit suite for regressions**

Run: `uv run pytest -m "not integration"`

Expected: all pass. `_git_head` has no other callers in `index_cli.py`'s tests, and `query.py` has its own separate `_git_head` that this change does not touch.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: add _git_tracked_files and report missing commits clearly"
```

---

### Task 2: `detect_language()` reads git, not the filesystem

This is the core fix.

**Files:**
- Modify: `src/codeintel/index_cli.py:74-91` (`detect_language`)
- Test: `tests/test_index_cli.py:29-160` (rewrite 8 call sites), plus new tests

**Interfaces:**
- Consumes: `_git_tracked_files(repo_path) -> list[str]` and `NotAGitRepositoryError` from Task 1.
- Produces: `detect_language(repo_path: Path) -> tuple[str, list[str]]` — same signature, git-backed. Raises `NotAGitRepositoryError` for non-git paths, `UnsupportedLanguageError` when no tracked file has a supported suffix.

- [ ] **Step 1: Rewrite the 8 existing detection call sites to use real git repos**

Every one of these currently runs against a bare `tmp_path` with no `git init` — which is exactly why this bug survived: they exercised a code path production never takes. Replace `tests/test_index_cli.py:29-160`'s detection tests with these (keep any non-detection tests in that range untouched):

```python
def test_detect_language_picks_python_for_py_files(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _init_git_repo(tmp_path)
    language, cmd = detect_language(tmp_path)
    assert language == "python"
    assert cmd[0] == "scip-python"


def test_detect_language_picks_majority_extension(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"f{i}.ts").write_text("export const x = 1;\n")
    (tmp_path / "g.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    language, _ = detect_language(tmp_path)
    assert language == "typescript"


def test_detect_language_ignores_committed_node_modules(tmp_path: Path):
    """Git alone does not save us here -- these files ARE tracked. The
    retained _IGNORED_DIRS pass is what excludes them."""
    (tmp_path / "src.py").write_text("x = 1\n")
    ignored = tmp_path / "node_modules" / "pkg"
    ignored.mkdir(parents=True)
    for i in range(5):
        (ignored / f"f{i}.ts").write_text("export const x = 1;\n")
    _init_git_repo(tmp_path)
    language, _ = detect_language(tmp_path)
    assert language == "python"


def test_detect_language_ignores_committed_derived_data_and_dot_build(tmp_path: Path):
    """Same as above for Swift build output that a repo happens to commit."""
    (tmp_path / "src.swift").write_text("let x = 1\n")
    derived_data = tmp_path / "DerivedData" / "SourcePackages" / "checkouts" / "SomeDep"
    derived_data.mkdir(parents=True)
    dot_build = tmp_path / ".build" / "checkouts" / "SomeDep"
    dot_build.mkdir(parents=True)
    for i in range(5):
        (derived_data / f"f{i}.py").write_text("x = 1\n")
        (dot_build / f"g{i}.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    language, _ = detect_language(tmp_path)
    assert language == "swift"


def test_detect_language_picks_swift_for_swift_files(tmp_path: Path):
    (tmp_path / "a.swift").write_text("let x = 1\n")
    (tmp_path / "b.swift").write_text("let y = 2\n")
    _init_git_repo(tmp_path)
    language, cmd = detect_language(tmp_path)
    assert language == "swift"
    assert cmd[0] == "scip-swift"


def test_swift_invocation_omits_index_subcommand(tmp_path: Path):
    """The bare form is required for cross-version compatibility.

    scip-swift only gained its `index` subcommand after v0.1.0 shipped, so
    `scip-swift index --output ...` fails against that released binary -- it
    parses "index" as the repo path. The bare form works on every version.
    """
    (tmp_path / "a.swift").write_text("let x = 1\n")
    _init_git_repo(tmp_path)
    _, cmd = detect_language(tmp_path)
    assert cmd == ["scip-swift"], f"must stay bare for version tolerance, got {cmd}"


def test_detect_language_tie_break_prefers_earlier_priority_over_swift(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.swift").write_text("let x = 1\n")
    _init_git_repo(tmp_path)
    language, _ = detect_language(tmp_path)
    assert language == "python"


def test_detect_language_raises_for_no_supported_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("# hi\n")
    _init_git_repo(tmp_path)
    with pytest.raises(UnsupportedLanguageError):
        detect_language(tmp_path)
```

Two tests were renamed to say what they now prove: `..._ignores_node_modules_and_git` → `..._ignores_committed_node_modules`, and `..._ignores_derived_data_and_dot_build` → `..._ignores_committed_derived_data_and_dot_build`. Under git, an *un*committed `node_modules` is excluded for free; these tests are only meaningful for the committed case, which is what `_IGNORED_DIRS` still covers.

- [ ] **Step 2: Write the new failing tests**

Append after the rewritten tests above:

```python
def test_detect_language_ignores_gitignored_checkout_directory(tmp_path: Path):
    """Direct regression test for the polaris-code-intelligence failure: a
    gitignored `.local-checkouts/` of cloned sibling repos held 4782 .ts/.tsx
    files against the repo's own 81 tracked .py files, and detection picked
    typescript. Nothing in _IGNORED_DIRS covered it, and nothing could --
    the directory name is arbitrary and per-project."""
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text(".local-checkouts/\n")
    checkouts = tmp_path / ".local-checkouts" / "vendored-repo"
    checkouts.mkdir(parents=True)
    for i in range(50):
        (checkouts / f"f{i}.ts").write_text("export const x = 1;\n")

    _init_git_repo(tmp_path)  # `git add .` honors .gitignore

    language, _ = detect_language(tmp_path)
    assert language == "python"


def test_detect_language_ignores_untracked_files(tmp_path: Path):
    """Tracked-only by design. Uncommitted scratch files do not vote."""
    (tmp_path / "app.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    for i in range(50):
        (tmp_path / f"scratch{i}.ts").write_text("export const x = 1;\n")

    language, _ = detect_language(tmp_path)
    assert language == "python"


def test_detect_language_raises_for_non_git_directory(tmp_path: Path):
    from codeintel.index_cli import NotAGitRepositoryError

    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(NotAGitRepositoryError):
        detect_language(tmp_path)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k detect_language -v`

Expected: `test_detect_language_ignores_gitignored_checkout_directory` FAILS (asserts `python`, gets `typescript` — the bug, reproduced); `test_detect_language_ignores_untracked_files` FAILS the same way; `test_detect_language_raises_for_non_git_directory` FAILS (no exception raised — the filesystem walk succeeds). The rewritten tests pass already, since `git init` doesn't change what `rglob` sees.

That first failure is the one that matters. Confirm it reports `typescript` before continuing.

- [ ] **Step 4: Rewrite `detect_language`**

Replace `src/codeintel/index_cli.py:74-91` entirely:

```python
def detect_language(repo_path: Path) -> tuple[str, list[str]]:
    """Scan `repo_path`'s git-tracked files for supported source
    extensions; return `(language, indexer_command)` for whichever
    extension has the most files, ties broken by `_EXT_PRIORITY` order.

    Git-tracked, not a filesystem walk: a walk also counts gitignored
    vendored checkouts and sibling clones, which can outnumber the repo's
    own code and pick a language the repo does not use.

    `_IGNORED_DIRS` is still applied on top, because git does not exclude
    build output a repo happens to commit (a checked-in `dist/` or a
    vendored `node_modules`).
    """
    counts: Counter[str] = Counter()
    for name in _git_tracked_files(repo_path):
        path = Path(name)
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix in _LANGUAGE_INDEXERS:
            counts[path.suffix] += 1

    present = [ext for ext in _EXT_PRIORITY if counts[ext] > 0]
    if not present:
        raise UnsupportedLanguageError(
            f"no supported source files (.ts/.tsx/.py/.java/.kt/.swift) tracked under {repo_path}"
        )
    best_ext = max(present, key=lambda ext: (counts[ext], -_EXT_PRIORITY.index(ext)))
    return _LANGUAGE_INDEXERS[best_ext]
```

The `path.is_file()` check is gone — `git ls-files` only ever lists files. The error message gains "tracked" so a user who hits it knows which file set was consulted.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k detect_language -v`

Expected: all pass, including the gitignored-checkout regression.

- [ ] **Step 6: Run the full unit suite**

Run: `uv run pytest -m "not integration"`

Expected: all pass.

- [ ] **Step 7: Verify against the real repo that motivated this**

Run:

```bash
uv run python -c "
from pathlib import Path
from codeintel.index_cli import detect_language
p = Path('/Users/ddphuong/Projects/epost-workspace/polaris-ai-plaform/polaris-code-intelligence')
print(detect_language(p))
"
```

Expected: `('python', ['scip-python', 'index'])`. Before this task it returned `('typescript', ['scip-typescript', 'index'])`.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "fix: detect language from git-tracked files, not a filesystem walk"
```

---

### Task 3: Registry `language_override` column

Independent of Tasks 1–2; touches only `registry.py`.

**Files:**
- Modify: `src/codeintel/registry.py:18-31` (`_SCHEMA`), `:33-44` (after `_ensure_scheme_override_column`), `:95-105` (`RegisteredRepo`), `:108-121` (`_row_to_repo`), `:124-137` (`__init__`), `:139-167` (`upsert`), `:183-198` (`get`/`list`)
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `RegisteredRepo.language_override: str | None` (defaults `None`)
  - `Registry.upsert(..., language_override: str | None = None)`
  - `_ensure_language_override_column(conn: sqlite3.Connection) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registry.py`. The import line at the top of that file already pulls in `_ensure_scheme_override_column`; extend it to include `_ensure_language_override_column`.

```python
def test_upsert_persists_language_override(tmp_path: Path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    assert registry.get("my-repo").language_override == "python"
    registry.close()


def test_upsert_defaults_language_override_to_none(tmp_path: Path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    assert registry.get("my-repo").language_override is None
    registry.close()


def test_language_override_column_added_to_preexisting_db(tmp_path: Path):
    """A registry.db written before this column existed must still open
    cleanly -- the guarded ALTER TABLE has to be idempotent and safe
    against a database that predates the column."""
    db_path = tmp_path / "registry.db"
    Registry(db_path).upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    reopened = Registry(db_path)
    assert reopened.get("my-repo").language_override is None
    reopened.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="java")
    assert reopened.get("my-repo").language_override == "java"
    reopened.close()


def test_ensure_language_override_column_re_raises_non_duplicate_errors():
    """Only "duplicate column name" is idempotent-safe to swallow. A
    "database is locked" from a concurrent `codeintel watch` reindex must
    propagate -- swallowing it would leave the column missing while looking
    like a successful migration."""
    mock_conn = Mock(spec=sqlite3.Connection)
    mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")

    try:
        _ensure_language_override_column(mock_conn)
        assert False, "Expected OperationalError to be re-raised"
    except sqlite3.OperationalError as exc:
        assert str(exc) == "database is locked"


def test_ensure_language_override_column_swallows_duplicate_column_error():
    mock_conn = Mock(spec=sqlite3.Connection)
    mock_conn.execute.side_effect = sqlite3.OperationalError(
        "duplicate column name: language_override"
    )

    _ensure_language_override_column(mock_conn)  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -k language_override -v`

Expected: FAIL — `ImportError` for `_ensure_language_override_column`, and `AttributeError: 'RegisteredRepo' object has no attribute 'language_override'`.

- [ ] **Step 3: Add the column to `_SCHEMA`**

In `src/codeintel/registry.py`, add `language_override TEXT` as the final column of the `repos` table in `_SCHEMA`. Appending matches where `ALTER TABLE` puts it on an existing DB; column order is irrelevant to correctness because every `SELECT` lists columns explicitly by name.

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
    language_override TEXT
)
"""
```

- [ ] **Step 4: Add the guarded migration**

Immediately after `_ensure_semantic_include_column`:

```python
def _ensure_language_override_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration for databases created before this column
    existed. Same contract as `_ensure_scheme_override_column`: a
    "duplicate column name" error means a previous run (or a fresh
    `_SCHEMA` create) already added it, so it is ignored; any other
    `OperationalError` (e.g. "database is locked" from a concurrent
    `codeintel watch` reindex) is re-raised rather than swallowed."""
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN language_override TEXT")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise
```

Call it from `Registry.__init__`, after `_ensure_semantic_include_column(self._conn)`:

```python
        _ensure_language_override_column(self._conn)
```

- [ ] **Step 5: Thread the column through the dataclass, row mapper, and queries**

`RegisteredRepo` — add as the final field so existing positional construction is unaffected:

```python
    language_override: str | None = None
```

`_row_to_repo` — extend the unpacking and the constructor call:

```python
def _row_to_repo(row: tuple) -> RegisteredRepo:
    (slug, path, language, commit_sha, last_indexed, status,
     scheme_override, semantic_indexed_at, semantic_include, language_override) = row
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
    )
```

`get()` and `list()` — append `language_override` to **both** SELECT column lists, keeping the order aligned with the unpacking above:

```python
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include, language_override "
```

`upsert()` — new keyword-only-in-practice parameter, one more placeholder, one more `ON CONFLICT` assignment, and the returned dataclass:

```python
    def upsert(
        self,
        slug: str,
        path: str,
        language: str,
        commit_sha: str | None,
        status: str,
        scheme_override: str | None = None,
        semantic_include: tuple[str, ...] = (),
        language_override: str | None = None,
    ) -> RegisteredRepo:
        last_indexed = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, "
            "scheme_override, semantic_indexed_at, semantic_include, language_override) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status, "
            "scheme_override=excluded.scheme_override, "
            "semantic_include=excluded.semantic_include, "
            "language_override=excluded.language_override",
            (slug, path, language, commit_sha, last_indexed.isoformat(), status,
             scheme_override, _join_include(semantic_include), language_override),
        )
        self._conn.commit()
        return RegisteredRepo(
            slug=slug, path=path, language=language, commit_sha=commit_sha,
            last_indexed=last_indexed, status=status, scheme_override=scheme_override,
            semantic_indexed_at=None, semantic_include=semantic_include,
            language_override=language_override,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -v`

Expected: all pass, including the pre-existing scheme/semantic tests.

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest -m "not integration"`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/registry.py tests/test_registry.py
git commit -m "feat: persist language_override on registered repos"
```

---

### Task 4: `_resolve_language` and `index_repo` wiring

**Files:**
- Modify: `src/codeintel/index_cli.py` — new `_INDEXER_BY_LANGUAGE` near `_LANGUAGE_INDEXERS` (`:33-48`), new `_resolve_language` after `_resolve_scheme` (`:213-221`), `index_repo` signature and opening (`:290-321`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `Registry.upsert(..., language_override=...)` and `RegisteredRepo.language_override` from Task 3; `detect_language` from Task 2.
- Produces:
  - `_INDEXER_BY_LANGUAGE: dict[str, list[str]]` — keys exactly `{"java", "python", "swift", "typescript"}`
  - `_resolve_language(registry: Registry, slug: str, language: str | None) -> str | None`
  - `index_repo(..., language: str | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_indexer_by_language_covers_every_supported_language():
    from codeintel.index_cli import _INDEXER_BY_LANGUAGE

    assert sorted(_INDEXER_BY_LANGUAGE) == ["java", "python", "swift", "typescript"]
    assert _INDEXER_BY_LANGUAGE["python"] == ["scip-python", "index"]
    assert _INDEXER_BY_LANGUAGE["swift"] == ["scip-swift"]


def test_resolve_language_preserves_stored_override_when_none_given(tmp_path: Path):
    from codeintel.index_cli import _resolve_language

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    assert _resolve_language(registry, "my-repo", language=None) == "python"
    registry.close()


def test_resolve_language_prefers_explicit_value_over_stored(tmp_path: Path):
    from codeintel.index_cli import _resolve_language

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    assert _resolve_language(registry, "my-repo", language="java") == "java"
    registry.close()


def test_resolve_language_returns_none_for_unknown_slug(tmp_path: Path):
    from codeintel.index_cli import _resolve_language

    registry = Registry(tmp_path / "registry.db")
    assert _resolve_language(registry, "nope", language=None) is None
    registry.close()


def test_index_repo_language_override_skips_detection(tmp_path: Path, monkeypatch):
    """An override means "do not guess" -- detect_language must not run at
    all, so a repo whose plurality says otherwise still gets the forced
    language, and the registry records both the effective language and the
    fact that it was forced."""
    import codeintel.index_cli as cli

    for i in range(5):
        (tmp_path / f"f{i}.ts").write_text("export const x = 1;\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    def boom(_repo_path):
        raise AssertionError("detect_language must not be called when overridden")

    monkeypatch.setattr(cli, "detect_language", boom)
    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    captured: dict = {}

    def fake_run(cmd, *, cwd, step):
        captured.setdefault("cmds", []).append(cmd)
        raise cli.IndexingError("stop after the indexer command is built")

    monkeypatch.setattr(cli, "_run", fake_run)

    data_root = tmp_path / "data"
    with pytest.raises(cli.IndexingError):
        cli.index_repo(tmp_path, slug="forced", root=data_root, language="python")

    assert captured["cmds"][0][0] == "scip-python"

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get("forced")
        assert entry is not None
        assert entry.language == "python"
        assert entry.language_override == "python"
    finally:
        registry.close()


def test_language_override_to_swift_still_gets_xcodebuild(tmp_path: Path, monkeypatch):
    """The Swift build-tool selection keys off the *effective* language, so
    it must fire when swift came from an override just as it does when swift
    came from detection."""
    import codeintel.index_cli as cli

    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "App.swift").write_text("let x = 1\n")
    (tmp_path / "App.xcodeproj").mkdir()
    (tmp_path / "App.xcodeproj" / "project.pbxproj").write_text("// stub\n")
    _init_git_repo(tmp_path)

    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    captured: dict = {}

    def fake_run(cmd, *, cwd, step):
        captured.setdefault("cmds", []).append(cmd)
        raise cli.IndexingError("stop after the indexer command is built")

    monkeypatch.setattr(cli, "_run", fake_run)

    with pytest.raises(cli.IndexingError):
        cli.index_repo(tmp_path, slug="forced-swift", root=tmp_path / "data",
                       language="swift", scheme="MyScheme")

    cmd = captured["cmds"][0]
    assert cmd[0] == "scip-swift"
    assert "--build-tool" in cmd and "xcodebuild" in cmd
    assert "--scheme" in cmd and "MyScheme" in cmd
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "indexer_by_language or resolve_language or language_override_skips or override_to_swift" -v`

Expected: FAIL — `ImportError` for `_INDEXER_BY_LANGUAGE` and `_resolve_language`; `index_repo() got an unexpected keyword argument 'language'`.

- [ ] **Step 3: Add the reverse map**

In `src/codeintel/index_cli.py`, immediately after the `_EXT_PRIORITY` assignment (currently line 48):

```python
# Reverse of `_LANGUAGE_INDEXERS`, derived from it so the two cannot drift.
# Several extensions share a language (.ts/.tsx, .java/.kt) and map to the
# same command, so the collapse is lossless. Its keys are the valid
# `--language` values.
_INDEXER_BY_LANGUAGE: dict[str, list[str]] = {
    language: cmd for language, cmd in _LANGUAGE_INDEXERS.values()
}
```

- [ ] **Step 4: Add the resolver**

Immediately after `_resolve_scheme` (currently ending line 221):

```python
def _resolve_language(registry: Registry, slug: str, language: str | None) -> str | None:
    """`language=None` means "leave the persisted override alone" (a
    `codeintel watch` reindex never repeats the flag) rather than "clear
    it" — the same contract as `_resolve_scheme`. Returning None means no
    override is in force and detection should run."""
    if language is not None:
        return language
    existing = registry.get(slug)
    return existing.language_override if existing is not None else None
```

- [ ] **Step 5: Wire it into `index_repo`**

Add `language: str | None = None` to the signature:

```python
def index_repo(
    repo_path: Path, *, slug: str | None = None, root: Path | None = None,
    scheme: str | None = None, semantic_include: tuple[str, ...] | None = None,
    language: str | None = None,
) -> str:
```

Then replace the opening block (currently `:307-321`) with:

```python
    repo_path = repo_path.resolve()
    slug = config.repo_slug(slug or repo_path.name)
    sha = _git_head(repo_path)
    check_scip_version()

    registry = Registry(config.data_dir(root) / "registry.db")
    language_override = _resolve_language(registry, slug, language)
    if language_override is not None:
        language, indexer_cmd = language_override, _INDEXER_BY_LANGUAGE[language_override]
    else:
        language, indexer_cmd = detect_language(repo_path)

    scheme = _resolve_scheme(registry, slug, scheme)
    semantic_include = _resolve_semantic_include(registry, slug, semantic_include)

    if language == "swift":
        indexer_cmd = _swift_indexer_cmd(indexer_cmd, repo_path, scheme)

    registry.upsert(slug, str(repo_path), language, None, "indexing", scheme_override=scheme,
                    semantic_include=semantic_include, language_override=language_override)
```

**`index_repo` has two `upsert` calls and both need the new argument.** The second one (currently line 370, on the success path, writing the final status and real commit SHA) must also pass it:

```python
        registry.upsert(slug, str(repo_path), language, sha, final_status, scheme_override=scheme,
                        semantic_include=semantic_include, language_override=language_override)
```

Missing this is silent and destructive: `upsert`'s `ON CONFLICT` clause does `language_override=excluded.language_override`, so the second call would reset the override to `NULL` on every *successful* index — the override would appear to work once and then vanish. Note that the unit tests in Step 1 cannot catch this, because they raise inside `_run` and never reach the second upsert. Step 5a below is the test that does.

Two things to note against the spec's §3 snippet, which showed `Registry(...)` before `_git_head`:

1. **`_git_head()` and `check_scip_version()` stay *above* the `Registry` construction.** Only `detect_language` had to move below it (the resolver needs the registry). Keeping the cheap validations first preserves today's behavior of failing before opening a DB connection.
2. The `language` parameter is reassigned to hold the effective language, so the rest of `index_repo` (the `swift` branch, the `upsert`, and everything downstream) is untouched. This is safe because `_resolve_language()` consumes the parameter on the line above, before the reassignment. `language_override` stays separate and is what gets persisted as the override.

Also add a line to the `index_repo` docstring, after the existing first paragraph:

```
    An explicit `language` (or one persisted from an earlier `--language`)
    bypasses `detect_language()` entirely -- an override means "do not
    guess", not "guess then correct". The registry's `language` column
    still records the effective language, so `list`/`status` show what was
    actually indexed.
```

- [ ] **Step 5a: Add the survives-a-successful-index test**

This is the only test that exercises the second `upsert`. It mirrors the existing `test_index_repo_preserves_scheme_override_when_not_repassed` and is integration-marked for the same reason — it needs a real successful pipeline run. Add it next to that test:

```python
@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_preserves_language_override_across_successful_index(tmp_path: Path):
    """The success path upserts a second time. If that call omits
    language_override, the override silently resets to NULL and a later
    reindex falls back to detection."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    slug = index_repo(repo_dir, root=data_root, language="python")

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status in ("indexed", PARTIAL_STATUS)
        assert entry.language_override == "python"
    finally:
        registry.close()
```

Add `PARTIAL_STATUS` to the `from codeintel.index_cli import ...` line at the top of the file if it isn't already imported.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "indexer_by_language or resolve_language or language_override_skips or override_to_swift" -v`

Expected: 6 passed.

Then, if `scip-python`, `scip`, and `zoekt-index` are on `PATH`:

Run: `uv run pytest tests/test_index_cli.py -k preserves_language_override -v`

Expected: 1 passed (or skipped, if binaries are absent — in which case flag it in the task report rather than assuming it would have passed).

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest -m "not integration"`

Expected: all pass. Watch specifically for `test_index_repo_preserves_scheme_override_when_not_repassed` and `test_reindex_forwards_stored_scheme_override` — the reordering must not disturb them.

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: bypass language detection when an override is set"
```

---

### Task 5: `--language` CLI flag

**Files:**
- Modify: `src/codeintel/index_cli.py:390-401` (`_cmd_index`), `:449-452` (`_cmd_reindex`), `:525` (watch's `_reindex`), `:572-585` (`index_parser`), `:602-609` (`watch_parser`)
- Test: `tests/test_index_cli.py` (new tests, plus one existing test's fake signature)

**Interfaces:**
- Consumes: `index_repo(..., language=...)` and `_INDEXER_BY_LANGUAGE` from Task 4; `RegisteredRepo.language_override` from Task 3; `NotAGitRepositoryError` from Task 1.
- Produces: `codeintel index <path> --language <lang>` and `codeintel watch <path> --language <lang>`; `reindex` reuses the persisted value with no flag.

- [ ] **Step 1: Update the existing reindex test's fake signature**

`tests/test_index_cli.py:531`'s `fake_index_repo` will start receiving a `language=` keyword once `_cmd_index` forwards one, and would fail with `TypeError`. Update it in place to accept and capture it:

```python
    def fake_index_repo(path, *, slug=None, root=None, scheme=None, semantic_include=None,
                        language=None):
        captured["path"] = path
        captured["slug"] = slug
        captured["scheme"] = scheme
        captured["semantic_include"] = semantic_include
        captured["language"] = language
        return slug
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_index_parser_accepts_language_flag():
    from codeintel.index_cli import build_parser

    args = build_parser().parse_args(["index", "/repos/x", "--language", "python"])
    assert args.language == "python"


def test_watch_parser_accepts_language_flag():
    from codeintel.index_cli import build_parser

    args = build_parser().parse_args(["watch", "/repos/x", "--language", "swift"])
    assert args.language == "swift"


def test_index_parser_rejects_unknown_language():
    from codeintel.index_cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["index", "/repos/x", "--language", "cobol"])


def test_reindex_forwards_stored_language_override(tmp_path: Path, monkeypatch):
    import argparse
    import codeintel.index_cli as cli

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    registry.close()

    captured: dict = {}

    def fake_index_repo(path, *, slug=None, root=None, scheme=None, semantic_include=None,
                        language=None):
        captured["language"] = language
        return slug

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = cli._cmd_reindex(argparse.Namespace(slug="my-repo"))
    assert rc == 0
    assert captured["language"] == "python"


def test_cmd_index_reports_non_git_directory_as_error(tmp_path: Path, monkeypatch, capsys):
    """NotAGitRepositoryError must be caught at the CLI boundary and printed,
    not escape as a traceback."""
    import argparse
    import codeintel.index_cli as cli

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "a.py").write_text("x = 1\n")

    rc = cli._cmd_index(argparse.Namespace(
        path=str(tmp_path), slug=None, scheme=None, semantic_include=None, language=None,
    ))

    assert rc == 1
    assert "not a git repository" in capsys.readouterr().err
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "language_flag or unknown_language or stored_language_override or non_git_directory_as_error" -v`

Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'language'` on the parser tests, and the non-git test errors with an uncaught `NotAGitRepositoryError`.

- [ ] **Step 4: Add the flag to both subparsers**

In `build_parser()`, after each parser's existing `--scheme` argument. For `index_parser`:

```python
    index_parser.add_argument(
        "--language",
        choices=sorted(_INDEXER_BY_LANGUAGE),
        help="force the indexer language instead of detecting it from git-tracked files "
             "(persisted and reused by reindex/watch)",
    )
```

For `watch_parser`, the identical block with `watch_parser.add_argument(`.

`choices` makes argparse reject a typo with a usage error naming the valid values, rather than failing later inside the indexer.

- [ ] **Step 5: Forward it through the command handlers**

`_cmd_index` — pass the flag through and catch the new exception:

```python
def _cmd_index(args: argparse.Namespace) -> int:
    raw_include = getattr(args, "semantic_include", None)
    try:
        slug = index_repo(
            Path(args.path), slug=args.slug, scheme=getattr(args, "scheme", None),
            semantic_include=tuple(raw_include) if raw_include is not None else None,
            language=getattr(args, "language", None),
        )
    except (UnsupportedLanguageError, NotAGitRepositoryError, IndexingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {slug}")
    return 0
```

`_cmd_reindex` — add the persisted value to the synthesized Namespace, mirroring how `scheme` is already handled:

```python
    return _cmd_index(argparse.Namespace(
        path=repo.path, slug=repo.slug, scheme=repo.scheme_override,
        semantic_include=list(repo.semantic_include),
        language=repo.language_override,
    ))
```

Watch's inner `_reindex` (currently line 525) — thread the flag through:

```python
            index_repo(repo_path, slug=slug, scheme=args.scheme, language=args.language)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "language_flag or unknown_language or stored_language_override or non_git_directory_as_error" -v`

Expected: 5 passed.

- [ ] **Step 7: Run the full suite, unit and integration**

Run: `uv run pytest -m "not integration"`

Expected: all pass.

Then, if the indexer binaries are on `PATH`:

Run: `uv run pytest -m integration`

Expected: all pass, or skip cleanly with "missing required binaries". The integration tests exercise `index_repo` end-to-end and are the real check on Task 4's reordering.

- [ ] **Step 8: Verify the CLI by hand**

Run: `uv run codeintel index --help`

Expected: `--language {java,python,swift,typescript}` appears with its help text.

- [ ] **Step 9: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat: add --language override to index and watch"
```

---

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (Commands block; the "Index pipeline" paragraph)
- Modify: `README.md` (CLI reference for `index`/`watch`)

**Interfaces:**
- Consumes: the finished behavior from Tasks 1–5.
- Produces: no code.

- [ ] **Step 1: Update the `CLAUDE.md` Commands block**

Add `--language` to the two usage lines:

```bash
uv run codeintel index /path/to/repo [--slug name] [--scheme name] [--language name] [--semantic-include path]
uv run codeintel watch /path/to/repo [--debounce 5] [--scheme name] [--language name] [--semantic-include path]
```

- [ ] **Step 2: Update the `CLAUDE.md` "Index pipeline" paragraph**

Replace the opening clause `detect language by file-extension plurality` with a description of the git-backed rule. The paragraph currently begins:

> **Index pipeline** (`index_cli.py`, `index_repo()`): detect language by file-extension plurality (ties broken by fixed priority `.ts→.tsx→.py→.java→.kt→.swift`; one language per repo, no multi-language merge) → …

Change that clause to:

> detect language by extension plurality **across git-tracked files** (ties broken by fixed priority `.ts→.tsx→.py→.java→.kt→.swift`; one language per repo, no multi-language merge)

Then add a paragraph after it, in the style of the existing "Swift build-tool selection" note:

```markdown
**Language detection reads git, not the filesystem:** `detect_language()` counts
extensions across `git ls-files`, not a `rglob` walk. A walk also counts gitignored
scratch directories — vendored checkouts, sibling clones, `.worktrees/` — which can
outnumber a repo's own code and pick a language it doesn't use. (Real case: a repo with
81 tracked `.py` files and a gitignored `.local-checkouts/` of 4782 `.ts`/`.tsx` files
was detected as TypeScript.) `IGNORED_DIRS` is still applied on top, because git does
not exclude build output a repo happens to commit. A non-git path raises
`NotAGitRepositoryError`. Pass `--language <name>` on the first `codeintel index` to
override detection for a polyglot repo — it's persisted in the registry, so
`reindex`/`watch` reuse it automatically.
```

- [ ] **Step 3: Update `README.md`**

Add one line to the `index` example block (currently ends at line 93, after the `--semantic-include` line):

```bash
codeintel index /path/to/your/repo --language python # force the language instead of detecting it from git-tracked files
```

And one to the `watch` example block (currently ends at line 140, after the `--scheme` line):

```bash
codeintel watch /path/to/your/repo --language python
```

README lines 106-107 already say of `--semantic-include`: *"Like `--scheme`, once set there is no flag to clear it; change it by re-running `codeintel index` with the new value(s)."* That statement is now true of `--language` as well — extend that sentence to name it, so the three persisted overrides are described together rather than leaving a reader to infer it.

- [ ] **Step 4: Verify the docs match the shipped behavior**

Run: `uv run codeintel index --help && uv run codeintel watch --help`

Confirm each documented flag exists with the documented choices, and that no flag was documented that doesn't exist.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: describe git-backed language detection and --language"
```

---

## Follow-up (not part of this plan)

After merge, reindex the repo that motivated the fix:

```bash
uv run codeintel reindex polaris-code-intelligence
```

Expected: detects `python` with no `--language` flag. Requires `scip-python` on `PATH`.

The other 20 registered repos were verified to detect identically before and after this change, so no other reindex is needed.
