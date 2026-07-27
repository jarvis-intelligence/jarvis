# Swift xcodebuild Auto-Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `codeintel index` succeed on Swift repos that ship a checked-in `.xcodeproj`/`.xcworkspace` alongside `Package.swift` (e.g. UIKit-only iOS packages that can't build with plain `swift build`), by forcing `scip-swift`'s `--build-tool xcodebuild` in that case, with an explicit, persisted `--scheme` override for repos with more than one scheme.

**Architecture:** Two small, pure, unit-testable helpers in `index_cli.py` — `_prefers_xcodebuild()` (filesystem check) and `_swift_indexer_cmd()` (command-list builder) — wired into the existing `index_repo()` pipeline. A new nullable `scheme_override` column on `registry.py`'s `repos` table persists the `--scheme` value so `codeintel reindex`/`codeintel watch` don't need it repeated. No changes to `scip-swift` itself.

**Tech Stack:** Python 3.14, stdlib `sqlite3`, `argparse`, `pytest` (existing codeintel stack — no new dependencies).

## Global Constraints

- No ORM, no migration framework — schema changes use a guarded, idempotent `ALTER TABLE ... ADD COLUMN`, matching `registry.py`'s existing "plain stdlib CRUD, single-user, local-first" style.
- Modern type-hint syntax throughout: `str | None`, `list[str]`, etc. (existing convention).
- Never guess a scheme by name-matching heuristics — it's always either explicit (`--scheme`) or the existing `scip-swift` ambiguity error surfaces unchanged.
- Non-Swift languages, and Swift repos with no checked-in Xcode project, must produce byte-identical `indexer_cmd` to today — zero behavior change for the existing passing `test_index_repo_end_to_end_for_swift_repo` integration test.
- No new integration fixture (per approved design) — new coverage is unit-level only, exercised against real fixtures already in the repo.

---

### Task 1: `registry.py` — persist an optional `scheme_override` per repo

**Files:**
- Modify: `src/codeintel/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `RegisteredRepo.scheme_override: str | None`; `Registry.upsert(slug, path, language, commit_sha, status, scheme_override: str | None = None)` (new keyword-only-by-default trailing param — all 8 existing positional call sites in `index_cli.py` and all calls in `tests/test_registry.py` keep working unchanged).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_registry.py`:

```python
def test_upsert_persists_scheme_override(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="ios_theme_ui")
    repo = reg.get("my-repo")
    assert repo is not None
    assert repo.scheme_override == "ios_theme_ui"
    reg.close()


def test_upsert_defaults_scheme_override_to_none(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    assert reg.get("my-repo").scheme_override is None
    reg.close()


def test_scheme_override_survives_reopen_of_pre_existing_db(tmp_path: Path):
    """A registry.db written before this column existed must still open
    cleanly — the guarded ALTER TABLE has to be idempotent and safe against
    a database that predates the column."""
    db_path = tmp_path / "registry.db"
    Registry(db_path).upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    reopened = Registry(db_path)
    assert reopened.get("my-repo").scheme_override is None
    reopened.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed", scheme_override="foo")
    assert reopened.get("my-repo").scheme_override == "foo"
    reopened.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -k scheme_override -v`
Expected: FAIL — `TypeError: upsert() got an unexpected keyword argument 'scheme_override'` (or `AttributeError` on `repo.scheme_override`).

- [ ] **Step 3: Implement the schema + field + upsert param**

In `src/codeintel/registry.py`:

Change `_SCHEMA` to include the new column directly (so a brand-new database gets it from the start):

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    slug TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    commit_sha TEXT,
    last_indexed TEXT NOT NULL,
    status TEXT NOT NULL,
    scheme_override TEXT
)
"""
```

Add a migration helper and call it from `__init__`, right after the existing `CREATE TABLE IF NOT EXISTS` + commit:

```python
def _ensure_scheme_override_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration for databases created before this column
    existed. `ALTER TABLE ... ADD COLUMN` on a column that already exists
    raises `sqlite3.OperationalError` — caught and ignored, since that
    means a previous run (or a fresh `_SCHEMA` create) already added it."""
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN scheme_override TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
```

In `Registry.__init__`, after `self._conn.execute(_SCHEMA)` and its `self._conn.commit()`, add:

```python
        _ensure_scheme_override_column(self._conn)
```

Update `RegisteredRepo`:

```python
@dataclass(frozen=True)
class RegisteredRepo:
    slug: str
    path: str
    language: str
    commit_sha: str | None
    last_indexed: datetime
    status: str  # "indexed" | "indexing" | "failed" | "partial"
    scheme_override: str | None = None
```

Update `_row_to_repo`:

```python
def _row_to_repo(row: tuple) -> RegisteredRepo:
    slug, path, language, commit_sha, last_indexed, status, scheme_override = row
    return RegisteredRepo(
        slug=slug,
        path=path,
        language=language,
        commit_sha=commit_sha,
        last_indexed=datetime.fromisoformat(last_indexed),
        status=status,
        scheme_override=scheme_override,
    )
```

Update `Registry.upsert`:

```python
    def upsert(
        self,
        slug: str,
        path: str,
        language: str,
        commit_sha: str | None,
        status: str,
        scheme_override: str | None = None,
    ) -> RegisteredRepo:
        last_indexed = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, scheme_override) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status, "
            "scheme_override=excluded.scheme_override",
            (slug, path, language, commit_sha, last_indexed.isoformat(), status, scheme_override),
        )
        self._conn.commit()
        return RegisteredRepo(
            slug=slug, path=path, language=language, commit_sha=commit_sha,
            last_indexed=last_indexed, status=status, scheme_override=scheme_override,
        )
```

Update the two `SELECT` statements in `get()` and `list()` to also select `scheme_override`:

```python
    def get(self, slug: str) -> RegisteredRepo | None:
        row = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override "
            "FROM repos WHERE slug = ?",
            (slug,),
        ).fetchone()
        return _row_to_repo(row) if row is not None else None

    def list(self) -> list[RegisteredRepo]:
        rows = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override "
            "FROM repos ORDER BY slug"
        ).fetchall()
        return [_row_to_repo(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -v`
Expected: all PASS, including the 3 new tests and all pre-existing ones (they call `upsert()` with 5 positional args — still valid since `scheme_override` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
cd /Users/ddphuong/Projects/codeintel
git add src/codeintel/registry.py tests/test_registry.py
git commit -m "feat(registry): persist an optional per-repo scheme_override"
```

---

### Task 2: `index_cli.py` — detect a checked-in Xcode project

**Files:**
- Modify: `src/codeintel/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: nothing new (pure `pathlib.Path` check).
- Produces: `_prefers_xcodebuild(repo_path: Path) -> bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_index_cli.py` (near the other `detect_language`-style pure unit tests, e.g. after `test_detect_language_picks_swift_for_swift_files`):

```python
def test_prefers_xcodebuild_false_for_bare_spm_package(tmp_path: Path):
    from codeintel.index_cli import _prefers_xcodebuild

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    assert _prefers_xcodebuild(tmp_path) is False


def test_prefers_xcodebuild_true_when_xcodeproj_present(tmp_path: Path):
    from codeintel.index_cli import _prefers_xcodebuild

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _prefers_xcodebuild(tmp_path) is True


def test_prefers_xcodebuild_true_when_xcworkspace_present(tmp_path: Path):
    from codeintel.index_cli import _prefers_xcodebuild

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcworkspace").mkdir()
    assert _prefers_xcodebuild(tmp_path) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k prefers_xcodebuild -v`
Expected: FAIL with `ImportError: cannot import name '_prefers_xcodebuild'`.

- [ ] **Step 3: Implement `_prefers_xcodebuild`**

In `src/codeintel/index_cli.py`, add directly below `detect_language()` (after its closing `return _LANGUAGE_INDEXERS[best_ext]` at line 87):

```python
def _prefers_xcodebuild(repo_path: Path) -> bool:
    """True when `repo_path` has a checked-in `.xcodeproj`/`.xcworkspace`
    alongside `Package.swift`. `scip-swift`'s own `BuildBackendDetector`
    picks `swiftpm` whenever `Package.swift` exists, even when that can't
    build — e.g. a UIKit-only iOS package with no macOS platform support,
    where plain `swift build` fails with "no such module 'UIKit'" on the
    macOS host destination it defaults to."""
    return any(repo_path.glob("*.xcodeproj")) or any(repo_path.glob("*.xcworkspace"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k prefers_xcodebuild -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/ddphuong/Projects/codeintel
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): detect a checked-in Xcode project alongside Package.swift"
```

---

### Task 3: Wire `--build-tool xcodebuild` + `--scheme` into the Swift indexer invocation

**Files:**
- Modify: `src/codeintel/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `_prefers_xcodebuild(repo_path: Path) -> bool` (Task 2); `RegisteredRepo.scheme_override` / `Registry.upsert(..., scheme_override=...)` (Task 1).
- Produces: `_swift_indexer_cmd(base_cmd: list[str], repo_path: Path, scheme: str | None) -> list[str]`; `index_repo(repo_path, *, slug=None, root=None, scheme=None) -> str` (new keyword-only `scheme` param, default `None` — every existing call site keeps working unchanged).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_index_cli.py`, right after the `_prefers_xcodebuild` tests from Task 2:

```python
def test_swift_indexer_cmd_unchanged_without_xcodeproj(tmp_path: Path):
    from codeintel.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme=None) == ["scip-swift"]


def test_swift_indexer_cmd_adds_xcodebuild_when_xcodeproj_present(tmp_path: Path):
    from codeintel.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme=None) == [
        "scip-swift", "--build-tool", "xcodebuild",
    ]


def test_swift_indexer_cmd_adds_scheme_when_given(tmp_path: Path):
    from codeintel.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme="ios_theme_ui") == [
        "scip-swift", "--build-tool", "xcodebuild", "--scheme", "ios_theme_ui",
    ]


def test_swift_indexer_cmd_ignores_scheme_without_xcodeproj(tmp_path: Path):
    """A --scheme override is meaningless (and unsupported by scip-swift)
    under the swiftpm build tool, so it must not leak into the command
    when there's no checked-in Xcode project to justify xcodebuild."""
    from codeintel.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme="ios_theme_ui") == ["scip-swift"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k swift_indexer_cmd -v`
Expected: FAIL with `ImportError: cannot import name '_swift_indexer_cmd'`.

- [ ] **Step 3: Implement `_swift_indexer_cmd` and wire it into `index_repo`**

In `src/codeintel/index_cli.py`, add directly below `_prefers_xcodebuild` (from Task 2):

```python
def _swift_indexer_cmd(base_cmd: list[str], repo_path: Path, scheme: str | None) -> list[str]:
    """Extend `base_cmd` (`["scip-swift"]`) with `--build-tool xcodebuild`
    (and `--scheme`, if given) when `repo_path` has a checked-in Xcode
    project — see `_prefers_xcodebuild`. Non-Swift callers never reach
    this function; Swift repos without a checked-in Xcode project get
    `base_cmd` back unchanged, identical to today's behavior."""
    if not _prefers_xcodebuild(repo_path):
        return base_cmd
    cmd = [*base_cmd, "--build-tool", "xcodebuild"]
    if scheme:
        cmd += ["--scheme", scheme]
    return cmd
```

Now update `index_repo()`. Change its signature (currently at line 185):

```python
def index_repo(
    repo_path: Path, *, slug: str | None = None, root: Path | None = None, scheme: str | None = None
) -> str:
```

Right after the existing `language, indexer_cmd = detect_language(repo_path)` line (currently line 201), add:

```python
    if language == "swift":
        indexer_cmd = _swift_indexer_cmd(indexer_cmd, repo_path, scheme)
```

Update both `registry.upsert(...)` call sites (currently lines 206 and 253) to thread `scheme` through as `scheme_override`:

```python
    registry.upsert(slug, str(repo_path), language, None, "indexing", scheme_override=scheme)
```

and:

```python
        registry.upsert(slug, str(repo_path), language, sha, final_status, scheme_override=scheme)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -v`
Expected: all PASS — the 4 new tests, plus every pre-existing test in the file (including `test_index_repo_end_to_end_for_swift_repo`, which stays green because `mini_swift_repo` has no `.xcodeproj`, so `_swift_indexer_cmd` returns `indexer_cmd` unchanged).

- [ ] **Step 5: Run the full test suite as a regression check**

Run: `uv run pytest -m "not integration"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/ddphuong/Projects/codeintel
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): use xcodebuild + scheme for Swift repos with a checked-in Xcode project"
```

---

### Task 4: CLI `--scheme` flag on `index`/`watch`, and `reindex` forwarding the stored override

**Files:**
- Modify: `src/codeintel/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `index_repo(..., scheme=...)` (Task 3); `RegisteredRepo.scheme_override` (Task 1).
- Produces: `codeintel index <path> --scheme <name>`; `codeintel watch <path> --scheme <name>`; `codeintel reindex <slug>` now passes the stored `scheme_override` through automatically.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_index_cli.py`:

```python
def test_reindex_forwards_stored_scheme_override(tmp_path: Path, monkeypatch):
    import argparse
    import codeintel.index_cli as cli

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="ios_theme_ui")
    registry.close()

    captured: dict = {}

    def fake_index_repo(path, *, slug=None, root=None, scheme=None):
        captured["path"] = path
        captured["slug"] = slug
        captured["scheme"] = scheme
        return slug

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = cli._cmd_reindex(argparse.Namespace(slug="my-repo"))
    assert rc == 0
    assert captured["scheme"] == "ios_theme_ui"
    assert str(captured["path"]) == "/repos/my-repo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index_cli.py -k reindex_forwards_stored_scheme_override -v`
Expected: FAIL — `AssertionError: assert None == 'ios_theme_ui'` (the current `_cmd_reindex` builds a `Namespace` with no `scheme`, and `_cmd_index` doesn't forward one either).

- [ ] **Step 3: Implement the CLI wiring**

In `src/codeintel/index_cli.py`, update `_cmd_index` (currently):

```python
def _cmd_index(args: argparse.Namespace) -> int:
    try:
        slug = index_repo(Path(args.path), slug=args.slug)
    except (UnsupportedLanguageError, IndexingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {slug}")
    return 0
```

to:

```python
def _cmd_index(args: argparse.Namespace) -> int:
    try:
        slug = index_repo(Path(args.path), slug=args.slug, scheme=getattr(args, "scheme", None))
    except (UnsupportedLanguageError, IndexingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"indexed {slug}")
    return 0
```

(`getattr(args, "scheme", None)` keeps this safe against any `Namespace` built without a `scheme` attribute — including the `test_index_repo_rejects_dotdot_slug_before_touching_disk`-style Namespaces already in the test file. New callers below always set it explicitly.)

Update `_cmd_reindex` (currently ends with `return _cmd_index(argparse.Namespace(path=repo.path, slug=repo.slug))`):

```python
    return _cmd_index(argparse.Namespace(path=repo.path, slug=repo.slug, scheme=repo.scheme_override))
```

Update `_cmd_watch`'s inner `_reindex()` (currently `index_repo(repo_path, slug=slug)`):

```python
    def _reindex() -> None:
        print(f"[watch] change detected, reindexing {slug} ...")
        try:
            index_repo(repo_path, slug=slug, scheme=args.scheme)
            print(f"[watch] {slug} reindexed")
        except Exception as exc:
            print(f"[watch] reindex failed: {exc}", file=sys.stderr)
```

Update `build_parser()`'s `index_parser` and `watch_parser` (both currently only have `path`/`--slug`):

```python
    index_parser = subparsers.add_parser("index", help="index a repo")
    index_parser.add_argument("path", help="path to the repo to index")
    index_parser.add_argument("--slug", help="override the auto-derived slug")
    index_parser.add_argument(
        "--scheme", help="Xcode scheme to build (Swift repos using xcodebuild with more than one scheme)"
    )
    index_parser.set_defaults(func=_cmd_index)
```

```python
    watch_parser = subparsers.add_parser("watch", help="watch a repo and debounce-reindex on change")
    watch_parser.add_argument("path", help="path to the repo to watch")
    watch_parser.add_argument("--slug", help="override the auto-derived slug")
    watch_parser.add_argument(
        "--scheme", help="Xcode scheme to build (Swift repos using xcodebuild with more than one scheme)"
    )
    watch_parser.add_argument("--debounce", type=float, default=5.0, help="quiet-period seconds (default: 5.0)")
    watch_parser.set_defaults(func=_cmd_watch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_index_cli.py -k reindex_forwards_stored_scheme_override -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite as a regression check**

Run: `uv run pytest -m "not integration"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/ddphuong/Projects/codeintel
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "feat(cli): add --scheme flag to index/watch, forward it through reindex"
```

---

### Task 5: Update `CLAUDE.md` and `README.md`

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:** None (docs only).

- [ ] **Step 1: Update `CLAUDE.md`**

In the `## Commands` section, change:

```
uv run codeintel index /path/to/repo [--slug name]
```

to:

```
uv run codeintel index /path/to/repo [--slug name] [--scheme name]
```

and change:

```
uv run codeintel watch /path/to/repo [--debounce 5]   # foreground, not a daemon
```

to:

```
uv run codeintel watch /path/to/repo [--debounce 5] [--scheme name]   # foreground, not a daemon
```

In the `## Architecture` section, right after the existing **Known gap** paragraph (`... not a bug in codeintel's query logic.`), add a new paragraph:

```
**Swift build-tool selection:** `scip-swift`'s own `BuildBackendDetector` picks `swiftpm`
whenever `Package.swift` exists, even for repos that can't build that way (e.g. a UIKit-only
iOS package with no macOS platform support). `index_cli.py`'s `_prefers_xcodebuild()` overrides
this: a Swift repo with a checked-in `.xcodeproj`/`.xcworkspace` is indexed via `--build-tool
xcodebuild` instead. When such a repo has more than one scheme, pass `--scheme <name>` on the
first `codeintel index` — it's persisted in the registry, so `reindex`/`watch` reuse it
automatically.
```

- [ ] **Step 2: Update `README.md`**

In `## Indexing a repo`, change:

```
codeintel index /path/to/your/repo            # slug defaults to the directory name
codeintel index /path/to/your/repo --slug foo # or pick one explicitly
```

to:

```
codeintel index /path/to/your/repo            # slug defaults to the directory name
codeintel index /path/to/your/repo --slug foo # or pick one explicitly
codeintel index /path/to/your/repo --scheme MyScheme # Swift repo with an ambiguous Xcode scheme
```

In `## Watching a repo (auto-reindex)`, change:

```
codeintel watch /path/to/your/repo             # debounce defaults to 5s
codeintel watch /path/to/your/repo --debounce 3
```

to:

```
codeintel watch /path/to/your/repo             # debounce defaults to 5s
codeintel watch /path/to/your/repo --debounce 3
codeintel watch /path/to/your/repo --scheme MyScheme
```

- [ ] **Step 3: Commit**

```bash
cd /Users/ddphuong/Projects/codeintel
git add CLAUDE.md README.md
git commit -m "docs: document --scheme flag and xcodebuild auto-detection for Swift repos"
```

---

### Task 6: Reindex `epost-ios-theme-ui`

**Files:** None (no code changes — this is the follow-up verification the fix was built for).

- [ ] **Step 1: Run the index command with the new flag**

```bash
cd /Users/ddphuong/Projects/codeintel
uv run codeintel index \
  /Users/ddphuong/Projects/epost-workspace/epost-app/epost-ios-theme-showcase/epost-ios-theme-ui \
  --slug epost-ios-theme-ui --scheme ios_theme_ui
```

Expected: `indexed epost-ios-theme-ui` (no `error:` line).

- [ ] **Step 2: Verify via `codeintel status`**

```bash
uv run codeintel status epost-ios-theme-ui
```

Expected: `status: indexed`, `language: swift`, a non-empty `commit:`.

- [ ] **Step 3: Verify via the MCP `getIndexStatus` tool**

Call `mcp__codeintel__getIndexStatus` with `repo="epost-ios-theme-ui"` and
`repo_path="/Users/ddphuong/Projects/epost-workspace/epost-app/epost-ios-theme-showcase/epost-ios-theme-ui"`.

Expected: `"indexed": true`, `"stale": false`.

- [ ] **Step 4: Spot-check navigation actually works**

Call `mcp__codeintel__searchCode` with `query="ThemeColorPicker"` and `repo="epost-ios-theme-ui"`.

Expected: at least one hit from `ios_theme_ui/Classes/Theme/ColorSystem/`.

---

## Self-Review Notes

- **Spec coverage:** Section 1 (detection) → Task 2+3. Section 2 (scheme override + persistence + CLI flag) → Tasks 1, 3, 4. Section 3 (testing) → unit tests embedded in Tasks 1–4, explicit regression runs in Tasks 3 and 4; no new integration fixture, as agreed. Section 4 (docs) → Task 5. Follow-up (reindex) → Task 6.
- **Placeholder scan:** none found — every step has literal code/commands.
- **Type consistency:** `scheme: str | None` used identically across `index_repo()`, `_swift_indexer_cmd()`, `Registry.upsert()`/`scheme_override`, and the `--scheme` CLI flag; `_prefers_xcodebuild(repo_path: Path) -> bool` and `_swift_indexer_cmd(base_cmd: list[str], repo_path: Path, scheme: str | None) -> list[str]` signatures are used consistently between their Task 2/3 definitions and every call site in later tasks.
