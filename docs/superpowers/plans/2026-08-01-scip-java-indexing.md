# scip-java Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Java/Kotlin repos indexable by installing the real `scip-java` launcher, and let repos that cannot get a SCIP index still get lexical + semantic search.

**Architecture:** `setup.sh` installs upstream's single-file JVM launcher into `~/.jarvis/bin`. `index_cli.py` serializes Gradle for Java (an upstream concurrency bug) and, when the indexer fails with a *recognized* signature, degrades to a search-only publish (Zoekt + semantic, no SCIP) and persists that decision so later runs skip the doomed build. The chunker's existing fixed-window fallback is opened to more languages so search-only repos get semantic coverage.

**Tech Stack:** POSIX sh (setup.sh, tested under `dash`), Python 3.12+, stdlib `sqlite3`, pytest, tree-sitter via `tree_sitter_language_pack`.

**Spec:** `docs/superpowers/specs/2026-08-01-scip-java-indexing-design.md`

## Global Constraints

- **Baseline:** branch `worktree-scip-java-indexing` off `origin/main` `ab561f7`. Unit baseline is green: 270 passed, 23 skipped, 7 deselected.
- **`setup.sh` is STRICTLY POSIX sh.** No arrays, no `[[ ]]`, no bashisms — `curl | sh` runs under dash. Tests source it with `JARVIS_SETUP_SOURCED=1` and run under `dash`.
- **Pinned versions:** `SCIP_JAVA_VERSION="v0.13.1"`, `SCIP_JAVA_REPO="scip-code/scip-java"`. scip-kotlinc inside it is built against Kotlin **exactly `2.2.0`**.
- **Do not bump `pyproject.toml` or `server.json` versions.** A CI guard fails the run when they drift; this change bumps neither.
- **Modern type hints only:** `str | None`, `list[T]`, `dict[K, V]`.
- **Result types are frozen dataclasses** (`@dataclass(frozen=True)`), never Pydantic.
- **Direct `sqlite3`, no ORM, always parameterized queries.**
- **Never mutate a published `index-<sha>.db`** — queries open it `mode=ro&immutable=1`.
- **Conventional commits, no AI references** in commit messages.
- Tests mirror source modules 1:1 (`test_index_cli.py` ↔ `index_cli.py`). The server test file is `tests/test_server_tools.py`.
- Run unit tests with `uv run pytest -m "not integration" -q`.

---

## File Structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `setup.sh` | `install_raw_binary` helper + real `install_scip_java` | 1 |
| `tests/test_setup_sh.py` | Replace docker-era scip-java tests | 1 |
| `src/jarvis/index_cli.py` | `_run` env param, Java `GRADLE_OPTS`, `--search-only`, signature fallback | 2, 4, 5 |
| `src/jarvis/registry.py` | `search_only` column + migration | 3 |
| `src/jarvis/chunker.py` | Extend `LANGUAGES` allowlist | 6 |
| `src/jarvis/server.py` | Search-only error text, `status` in `getIndexStatus` | 7 |
| `tests/fixtures/mini_java_repo/` | Gradle fixture for the integration test | 8 |
| `CLAUDE.md`, `CHANGELOG.md`, `.claude/skills/jarvis-setup/SKILL.md` | Docs | 9 |

---

### Task 1: Install the real scip-java launcher

Upstream ships `scip-java-v0.13.1` — a POSIX sh script with an embedded JAR (~86MB), plus a `.sha256` sidecar. It is a bare file, not a tarball, so `install_tarball_binary` does not fit.

**Files:**
- Modify: `setup.sh` (add pins near `SCIP_SWIFT_VERSION` ~line 27; add `install_raw_binary` after `install_tarball_binary` ~line 250; replace `install_scip_java` ~lines 411-449)
- Test: `tests/test_setup_sh.py` (replace the two docker-era tests ~lines 430-462)

**Interfaces:**
- Consumes: existing `download_to`, `verify_sha256`, `ensure_bin_dir`, `bin_dir`, `already_installed`, `have_cmd`, `log_info`, `log_warn`, `log_error`.
- Produces: shell function `install_raw_binary <url> <sha_url> <dest_name>`; a `scip-java` executable in `$(bin_dir)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_setup_sh.py`, immediately after `test_install_tarball_binary_refuses_on_checksum_mismatch`:

```python
def test_install_raw_binary_installs_and_marks_executable(tmp_path):
    """scip-java ships a bare launcher, not a tarball — no extraction step."""
    payload = tmp_path / "launcher"
    payload.write_text("#!/bin/sh\necho hello-from-launcher\n")
    sha_path = tmp_path / "launcher.sha256"
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    sha_path.write_text(f"{digest}  launcher\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'install_raw_binary file://{payload} file://{sha_path} mytool',
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode == 0, result.stderr
    installed = bin_path / "mytool"
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111, "must be executable"
    assert "hello-from-launcher" in installed.read_text()


def test_install_raw_binary_refuses_on_checksum_mismatch(tmp_path):
    payload = tmp_path / "launcher"
    payload.write_text("#!/bin/sh\ntrue\n")
    sha_path = tmp_path / "launcher.sha256"
    sha_path.write_text(f"{'0' * 64}  launcher\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'install_raw_binary file://{payload} file://{sha_path} mytool',
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode != 0
    assert not (bin_path / "mytool").exists(), "must not install an unverified binary"
```

Now **delete** `test_install_scip_java_reports_and_returns_zero_without_docker` and
`test_install_scip_java_never_pulls_without_confirmation` (they assert docker detect-only behavior
that no longer exists), and replace them with:

```python
def test_install_scip_java_warns_and_returns_zero_without_java(tmp_path):
    """The launcher is inert without a JVM, so a missing `java` is a soft skip."""
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\ninstall_scip_java'],
        capture_output=True,
        text=True,
        env={
            "JARVIS_SETUP_SOURCED": "1",
            "PATH": f"{empty_bin}",
            "JARVIS_BIN_DIR": str(tmp_path / "bin"),
        },
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "scip-java" in combined
    assert "java" in combined


def test_install_scip_java_never_mentions_docker(tmp_path):
    """Docker was never the install path — the image entrypoint is jshell."""
    assert "SCIP_JAVA_IMAGE" not in SETUP_SH.read_text()
    assert "docker pull" not in SETUP_SH.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -q -k "raw_binary or scip_java"`
Expected: FAIL — `install_raw_binary: not found`, and the docker assertions fail because `SCIP_JAVA_IMAGE` is still present.

- [ ] **Step 3: Add the version pins**

In `setup.sh`, after the `SCIP_SWIFT_REPO` line (~line 28):

```sh
# scip-java ships one self-contained launcher asset per release: a POSIX sh
# script with an embedded JAR. It runs on any JVM, so unlike scip-swift there
# is no os/arch gating.
#
# The scip-kotlinc plugin inside it is compiled against Kotlin 2.2.0 EXACTLY.
# Kotlin's compiler-plugin API is internal and unstable: 2.1.21 and 2.3.20 fail
# with AbstractMethodError, and even 2.2.20 fails with NoSuchMethodError. If
# this version is bumped, re-check which Kotlin the new release targets.
SCIP_JAVA_VERSION="v0.13.1"
SCIP_JAVA_REPO="scip-code/scip-java"
SCIP_JAVA_KOTLIN="2.2.0"
```

- [ ] **Step 4: Add `install_raw_binary`**

In `setup.sh`, immediately after the closing `}` of `install_tarball_binary`:

```sh
# Download a bare (non-archive) binary plus its .sha256 sidecar, verify it, and
# install it into bin_dir() under dest_name. Separate from
# install_tarball_binary rather than a refactor of it: the only difference is
# the missing extraction step, and four callers already depend on the tarball
# helper's behavior.
#
#   install_raw_binary <url> <sha_url> <dest_name>
install_raw_binary() {
	_url=$1
	_sha_url=$2
	_dest_name=$3

	_tmp=$(mktemp -d)
	# Clean up the temp dir on every exit path, including failure.
	# shellcheck disable=SC2064
	trap "rm -rf '$_tmp'" EXIT

	if ! download_to "$_url" "${_tmp}/binary"; then
		log_error "download failed: ${_url}"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	if ! download_to "$_sha_url" "${_tmp}/binary.sha256"; then
		log_error "checksum download failed: ${_sha_url}"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	# Sidecar format is "<digest>  <filename>"; take the first field.
	_expected=$(cut -d' ' -f1 <"${_tmp}/binary.sha256")
	if ! verify_sha256 "${_tmp}/binary" "$_expected"; then
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	ensure_bin_dir
	mv "${_tmp}/binary" "$(bin_dir)/${_dest_name}"
	chmod +x "$(bin_dir)/${_dest_name}"

	rm -rf "$_tmp"
	trap - EXIT
}
```

- [ ] **Step 5: Replace `install_scip_java`**

Delete the `SCIP_JAVA_IMAGE` line and the whole existing `install_scip_java` body (the detect-only
comment block, the docker/jvm probing, the `confirm` prompt, the `docker pull`). Replace with:

```sh
# Installs upstream's single-file launcher. Unattended, like scip/zoekt/
# scip-swift: the old confirm() prompt existed because the docker image is
# 6.75GB, and confirm() returns false without a TTY, which would make
# `curl | sh` silently skip scip-java.
install_scip_java() {
	if [ "${FORCE:-0}" != "1" ] && already_installed scip-java; then
		log_info "scip-java: already installed, skipping"
		return 0
	fi

	# The launcher is a JAR bootstrap: without a JVM it cannot run at all.
	# A soft skip with instructions, matching install_npm_indexer's missing-npm
	# branch — not a hard failure that would abort the whole setup run.
	if ! have_cmd java; then
		log_warn "scip-java: java not found — skipping. Install a JDK, then: ./setup.sh --only scip-java"
		return 0
	fi

	_asset="scip-java-${SCIP_JAVA_VERSION}"
	_base="https://github.com/${SCIP_JAVA_REPO}/releases/download/${SCIP_JAVA_VERSION}"

	log_info "scip-java: installing ${SCIP_JAVA_VERSION} (~86MB launcher)"
	if install_raw_binary "${_base}/${_asset}" "${_base}/${_asset}.sha256" scip-java; then
		log_info "scip-java: installed"
		log_info "scip-java: Kotlin repos must use Kotlin ${SCIP_JAVA_KOTLIN} exactly; Java is unrestricted"
	else
		log_error "scip-java: install failed — see https://github.com/${SCIP_JAVA_REPO}"
		return 1
	fi
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -q`
Expected: PASS, no failures.

- [ ] **Step 7: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): install the scip-java launcher instead of detecting docker"
```

---

### Task 2: Serialize Gradle for Java indexing

With `org.gradle.parallel=true`, concurrent `scipPrintDependencies` tasks across modules crash with `java.util.ConcurrentModificationException` inside scip-java's Gradle plugin. Setting `GRADLE_OPTS=-Dorg.gradle.parallel=false` avoids it.

**Files:**
- Modify: `src/jarvis/index_cli.py` (`_run` ~line 201; indexer invocation ~line 433)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Produces: `_run(cmd: list[str], *, cwd: Path, step: str, env: dict[str, str] | None = None) -> None` and `_java_indexer_env() -> dict[str, str]`. Task 5 calls `_run` for the indexer step.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_java_indexer_env_disables_gradle_parallelism(monkeypatch):
    from jarvis.index_cli import _java_indexer_env

    monkeypatch.delenv("GRADLE_OPTS", raising=False)
    assert _java_indexer_env()["GRADLE_OPTS"] == "-Dorg.gradle.parallel=false"


def test_java_indexer_env_appends_to_existing_gradle_opts(monkeypatch):
    """Clobbering GRADLE_OPTS would silently discard the user's heap settings."""
    from jarvis.index_cli import _java_indexer_env

    monkeypatch.setenv("GRADLE_OPTS", "-Xmx4g")
    value = _java_indexer_env()["GRADLE_OPTS"]
    assert "-Xmx4g" in value
    assert "-Dorg.gradle.parallel=false" in value


def test_run_merges_env_over_os_environ(tmp_path: Path, monkeypatch):
    """env= must extend os.environ, not replace it — PATH must survive."""
    from jarvis.index_cli import _run

    monkeypatch.setenv("JARVIS_MARKER", "from-parent")
    out = tmp_path / "out.txt"
    _run(
        ["sh", "-c", f'printf "%s|%s" "$JARVIS_MARKER" "$EXTRA" > {out}'],
        cwd=tmp_path,
        step="probe",
        env={"EXTRA": "from-arg"},
    )
    assert out.read_text() == "from-parent|from-arg"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -q -k "java_indexer_env or run_merges"`
Expected: FAIL with `ImportError: cannot import name '_java_indexer_env'` and `_run() got an unexpected keyword argument 'env'`.

- [ ] **Step 3: Implement**

Replace `_run` in `src/jarvis/index_cli.py`:

```python
def _run(cmd: list[str], *, cwd: Path, step: str, env: dict[str, str] | None = None) -> None:
    """`env`, when given, is merged OVER a copy of `os.environ` rather than
    replacing it — a bare replacement would drop PATH and break the very
    subprocess lookup that finds the indexer."""
    merged = {**os.environ, **env} if env else None
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=merged)
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")
```

Add below `_swift_indexer_cmd`:

```python
def _java_indexer_env() -> dict[str, str]:
    """scip-java's Gradle plugin races against itself when Gradle runs tasks in
    parallel: two modules' `scipPrintDependencies` mutate shared state and the
    build dies with java.util.ConcurrentModificationException. Forcing
    single-threaded execution avoids it.

    Appended to any existing GRADLE_OPTS rather than replacing it, so a user's
    heap settings survive. Reported upstream."""
    existing = os.environ.get("GRADLE_OPTS", "")
    return {"GRADLE_OPTS": f"{existing} -Dorg.gradle.parallel=false".strip()}
```

- [ ] **Step 4: Wire it into the indexer call**

In `index_repo`, replace the indexer `_run` call (~line 433):

```python
            _run([*indexer_cmd, "--output", str(scip_path)], cwd=repo_path,
                 step=f"{indexer_cmd[0]} index",
                 env=_java_indexer_env() if language == "java" else None)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -q -m "not integration"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "fix(index): run Gradle single-threaded for Java to dodge a scip-java race"
```

---

### Task 3: Persist the search-only decision in the registry

**Files:**
- Modify: `src/jarvis/registry.py` (`_SCHEMA` ~line 19; migrations ~line 95; `RegisteredRepo` ~line 112; `_row_to_repo` ~line 125; `upsert` ~line 158; both `SELECT` statements at lines 207 and 216)
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `RegisteredRepo.search_only: bool` (default `False`) and `Registry.upsert(..., search_only: bool = False)`. Tasks 4, 5, and 7 read `RegisteredRepo.search_only`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registry.py`:

```python
def test_upsert_round_trips_search_only(tmp_path):
    from jarvis.registry import Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("r", "/p", "java", None, "search-only", search_only=True)
        entry = registry.get("r")
        assert entry is not None
        assert entry.search_only is True
    finally:
        registry.close()


def test_search_only_defaults_false(tmp_path):
    from jarvis.registry import Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("r", "/p", "python", None, "indexed")
        entry = registry.get("r")
        assert entry is not None
        assert entry.search_only is False
    finally:
        registry.close()


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -q -k search_only`
Expected: FAIL — `upsert() got an unexpected keyword argument 'search_only'`.

- [ ] **Step 3: Add the shared status constant**

`SEARCH_ONLY_STATUS` lives in `registry.py`, not `index_cli.py`, because both `index_cli.py` and
`server.py` need it and `server.py` does not import `index_cli` today — routing a constant through
the CLI module would drag `argparse`, `watch`, and `graph` into the MCP server for no reason.
(`PARTIAL_STATUS` sits in `index_cli.py`, but it has no consumers outside that module.)

At the top of `src/jarvis/registry.py`, below the imports:

```python
# Status for a repo published WITHOUT a SCIP index: Zoekt and the semantic
# table are live, navigation is not. Distinct from index_cli's PARTIAL_STATUS,
# which means a SCIP index exists but carries no occurrence ranges.
SEARCH_ONLY_STATUS = "search-only"
```

- [ ] **Step 4: Add the column to `_SCHEMA`**

In `src/jarvis/registry.py`, add a final column to the `_SCHEMA` `CREATE TABLE`:

```sql
    language_override TEXT,
    search_only INTEGER NOT NULL DEFAULT 0
```

- [ ] **Step 5: Add the migration helper**

After `_ensure_language_override_column`:

```python
def _ensure_search_only_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration for databases created before this column existed.
    Same contract as `_ensure_scheme_override_column`: a "duplicate column
    name" error means a previous run (or a fresh `_SCHEMA` create) already
    added it, so it is ignored; any other `OperationalError` (e.g. "database is
    locked" from a concurrent `jarvis watch` reindex) is re-raised."""
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN search_only INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise
```

Register it in `Registry.__init__`, after `_ensure_language_override_column(self._conn)`:

```python
        _ensure_search_only_column(self._conn)
```

- [ ] **Step 6: Thread it through the dataclass, row mapper, upsert, and SELECTs**

`RegisteredRepo` — add after `language_override`, and widen the status comment:

```python
    status: str  # "indexed" | "indexing" | "failed" | "partial" | "search-only"
```
```python
    language_override: str | None = None
    search_only: bool = False
```

`_row_to_repo` — unpack the extra column and coerce the SQLite integer to `bool`:

```python
    (slug, path, language, commit_sha, last_indexed, status,
     scheme_override, semantic_indexed_at, semantic_include, language_override,
     search_only) = row
```
```python
        language_override=language_override,
        search_only=bool(search_only),
    )
```

`upsert` — add the parameter and persist it:

```python
        language_override: str | None = None,
        search_only: bool = False,
    ) -> RegisteredRepo:
```

In the `INSERT`, add `search_only` to the column list, one more `?` to `VALUES`, and
`search_only=excluded.search_only` to the `DO UPDATE SET` clause. Pass `int(search_only)` in the
parameter tuple, positioned to match the column order.

Both `SELECT` statements (lines 207 and 216) must gain `, search_only` at the end of their column
lists, matching `_row_to_repo`'s unpack order.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_registry.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/jarvis/registry.py tests/test_registry.py
git commit -m "feat(registry): persist a search_only flag per repo"
```

---

### Task 4: `--search-only` indexing path

Publishes Zoekt + semantic without SCIP. No `current` pointer is written, so `read_pointer` raises `IndexNotFoundError` and nav tools already fail safely.

**Files:**
- Modify: `src/jarvis/index_cli.py` (constants ~line 71; resolvers ~line 300; `index_repo` ~line 402; `_cmd_index` ~line 493; parser ~line 680)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `RegisteredRepo.search_only` and `Registry.upsert(..., search_only=)` from Task 3.
- Produces: `SEARCH_ONLY_STATUS = "search-only"`, `UNKNOWN_LANGUAGE = "unknown"`, `_resolve_search_only(registry: Registry, slug: str, search_only: bool | None) -> bool`, and `index_repo(..., search_only: bool | None = None)`. Task 5 reuses all of these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_search_only_publishes_zoekt_without_a_scip_pointer(tmp_path: Path, monkeypatch):
    """Search-only must skip the indexer entirely and write no `current` pointer."""
    from jarvis.index_cli import SEARCH_ONLY_STATUS, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _boom(*args, **kwargs):
        raise AssertionError("the SCIP indexer must not run in search-only mode")

    monkeypatch.setattr("jarvis.index_cli.detect_language", lambda p: ("python", ["nope"]))
    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", lambda cmd, **kw: None
                        if cmd[0] == "zoekt-index" else _boom())
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage",
                        lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root, search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
    finally:
        registry.close()

    assert not (config.index_dir(slug, data_root) / "current").exists()


def test_search_only_persists_so_reindex_reuses_it(tmp_path: Path):
    """`--search-only` follows the --language/--scheme contract: omitted means
    "leave the persisted value alone", not "clear it"."""
    from jarvis.index_cli import _resolve_search_only

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("r", "/p", "java", None, "search-only", search_only=True)
        assert _resolve_search_only(registry, "r", None) is True
        assert _resolve_search_only(registry, "r", False) is False
        assert _resolve_search_only(registry, "absent", None) is False
    finally:
        registry.close()


def test_search_only_tolerates_a_repo_with_no_indexable_language(tmp_path: Path, monkeypatch):
    """A Go/Ruby repo has no SCIP indexer; search-only must still index it."""
    from jarvis.index_cli import UNKNOWN_LANGUAGE, index_repo

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.go").write_text("package main\n\nfunc main() {}\n")
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", lambda cmd, **kw: None)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root, search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.language == UNKNOWN_LANGUAGE
    finally:
        registry.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -q -k search_only`
Expected: FAIL with `ImportError: cannot import name 'SEARCH_ONLY_STATUS'`.

- [ ] **Step 3: Add the constants and resolver**

In `src/jarvis/index_cli.py`, below `PARTIAL_STATUS`:

```python
# Recorded as the language when a search-only repo has no SCIP-indexable
# source at all (a Go or Ruby repo). `registry.language` is NOT NULL, so this
# has to be a value rather than NULL.
UNKNOWN_LANGUAGE = "unknown"
```

`SEARCH_ONLY_STATUS` comes from `registry.py` (Task 3). Extend the existing registry import:

```python
from jarvis.registry import SEARCH_ONLY_STATUS, Registry
```

Next to `_resolve_language`:

```python
def _resolve_search_only(registry: Registry, slug: str, search_only: bool | None) -> bool:
    """`search_only=None` means "leave the persisted value alone" (a `jarvis
    watch` reindex never repeats the flag) rather than "clear it" — the same
    contract as `_resolve_scheme`. This is what stops a repo that already
    proved un-indexable from re-running a doomed multi-minute build."""
    if search_only is not None:
        return search_only
    existing = registry.get(slug)
    return existing.search_only if existing is not None else False
```

- [ ] **Step 4: Add the search-only branch to `index_repo`**

Add `search_only: bool | None = None` to the signature. Resolve it **before** language detection,
not after — the `except UnsupportedLanguageError` clause below reads it, so it must already hold
the resolved `bool` (never the raw, possibly-`None` parameter) by the time that clause runs:

```python
    search_only = _resolve_search_only(registry, slug, search_only)
    language_override = _resolve_language(registry, slug, language)
```

(An earlier draft of this step placed the resolve call after `_resolve_semantic_include(...)` —
i.e. after language detection — which is buggy: a `search_only=None` reindex call would read the
unresolved `None` in the `except` clause below, `not None` is `True`, and the exception would
incorrectly propagate instead of falling through to `UNKNOWN_LANGUAGE`. Caught during Task 4's task
review; fixed by moving the resolve call earlier, as shown above.)

Language resolution must not be fatal in search-only mode. Replace the `else` branch of the
language-override block:

```python
    else:
        try:
            language, indexer_cmd = detect_language(repo_path)
        except UnsupportedLanguageError:
            if not search_only:
                raise
            language, indexer_cmd = UNKNOWN_LANGUAGE, []
```

Then, immediately before the `try:` that opens the temp dir, add the search-only short-circuit:

```python
    if search_only:
        registry.upsert(slug, str(repo_path), language, None, "indexing",
                        scheme_override=scheme, semantic_include=semantic_include,
                        language_override=language_override, search_only=True)
        try:
            semantic_ok = _publish_search_only(repo_path, slug, root, semantic_include)
            registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS,
                            scheme_override=scheme, semantic_include=semantic_include,
                            language_override=language_override, search_only=True)
            if semantic_ok:
                registry.mark_semantic_indexed(slug)
        except Exception as exc:
            registry.mark_status(slug, "failed")
            raise IndexingError(str(exc)) from exc
        finally:
            registry.close()
        return slug
```

Add the helper above `index_repo`. It does the publishing only and returns whether the semantic
stage succeeded — the caller owns the registry write, using the connection it already has open.
(An earlier draft opened a second `Registry` inside the helper and re-read the row to recover
`language`/`scheme`; that duplicated state the caller already holds, for no gain.)

```python
def _publish_search_only(repo_path: Path, slug: str, root: Path | None,
                         semantic_include: tuple[str, ...]) -> bool:
    """Zoekt + semantic only: no SCIP indexer, no `scip expt-convert`, no graph
    population, and deliberately no `current` pointer. Without a pointer,
    `read_pointer` raises IndexNotFoundError and every navigation tool fails
    safely — server.py turns that into an explanation.

    Returns whether the semantic stage succeeded, matching
    `_run_semantic_stage`'s contract; the caller records the registry row."""
    with tempfile.TemporaryDirectory(prefix="jarvis-index-") as scratch:
        zoekt_dir = config.data_dir(root) / ".zoekt"
        zoekt_dir.mkdir(parents=True, exist_ok=True)
        meta_path = _write_zoekt_meta(Path(scratch), slug)
        _run(
            ["zoekt-index", "-index", str(zoekt_dir), "-meta", str(meta_path), str(repo_path)],
            cwd=repo_path,
            step="zoekt-index",
        )
    semantic_ok = _run_semantic_stage(repo_path, slug, root, semantic_include)
    print(
        f"note: {slug} published search-only — searchCode and semanticSearch work, "
        "navigation tools do not (no SCIP index).",
        file=sys.stderr,
    )
    return semantic_ok
```

- [ ] **Step 5: Add the CLI flag**

In `_cmd_index`, pass it through:

```python
            language=getattr(args, "language", None),
            search_only=getattr(args, "search_only", None),
```

In `build_parser`, before `index_parser.set_defaults(...)`:

```python
    index_parser.add_argument(
        "--search-only",
        action="store_true",
        default=None,
        help="skip SCIP indexing and publish only Zoekt + semantic search "
             "(persisted and reused by reindex/watch)",
    )
```

`default=None` is load-bearing: it distinguishes "flag omitted, keep the persisted value" from
"explicitly false".

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py tests/test_registry.py -q -m "not integration"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): add --search-only for repos that cannot get a SCIP index"
```

---

### Task 5: Fall back to search-only on recognized indexer failures

**Files:**
- Modify: `src/jarvis/index_cli.py` (signature table near `SEARCH_ONLY_STATUS`; indexer call in `index_repo`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `_publish_search_only`, `SEARCH_ONLY_STATUS` (Task 4); `_run(..., env=)` (Task 2).
- Produces: `_search_only_reason(output: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
@pytest.mark.parametrize(
    "output",
    [
        "e: java.lang.AbstractMethodError: ... org.jetbrains.kotlin.fir.analysis.checkers ...",
        "NoSuchMethodError: 'org.jetbrains.kotlin.fir.declarations.FirFile ...getContainingFile()'",
        "error: No SCIP shards found. This typically means that `scip-java` is unable ...",
    ],
)
def test_recognized_failures_map_to_a_search_only_reason(output):
    from jarvis.index_cli import _search_only_reason

    assert _search_only_reason(output) is not None


@pytest.mark.parametrize(
    "output",
    [
        "error: Could not resolve all files for configuration ':app:debugCompileClasspath'",
        "AbstractMethodError: com.example.Whatever",   # not a Kotlin FIR crash
        "zsh: command not found: gradle",
    ],
)
def test_unrecognized_failures_do_not_trigger_the_fallback(output):
    from jarvis.index_cli import _search_only_reason

    assert _search_only_reason(output) is None


def test_indexer_failure_with_known_signature_publishes_search_only(tmp_path: Path, monkeypatch):
    from jarvis.index_cli import SEARCH_ONLY_STATUS, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    from jarvis.index_cli import IndexingError

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith("index"):
            raise IndexingError("error: No SCIP shards found. scip-java cannot index this")
        return None

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True, "must persist so reindex skips the doomed build"
    finally:
        registry.close()


def test_indexer_failure_without_known_signature_still_fails(tmp_path: Path, monkeypatch):
    """A transient build break must NOT be laundered into a success."""
    from jarvis.index_cli import IndexingError, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith("index"):
            raise IndexingError("error: could not resolve dependency com.example:thing:1.0")
        return None

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)

    with pytest.raises(IndexingError):
        index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
    finally:
        registry.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -q -k "search_only_reason or known_signature"`
Expected: FAIL with `ImportError: cannot import name '_search_only_reason'`.

- [ ] **Step 3: Add the signature table**

Below `UNKNOWN_LANGUAGE` in `src/jarvis/index_cli.py`:

```python
# Indexer failures that are known to be unfixable from here, and so degrade to
# a search-only publish instead of a hard failure. Each entry is
# (required substrings, human reason) — EVERY substring must be present, which
# is what keeps a generic AbstractMethodError from some unrelated library out.
#
# Deliberately narrow. This is not "fall back on any failure": a transient
# Gradle break or a missing binary must still fail loudly rather than be
# laundered into an apparent success.
_SEARCH_ONLY_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("AbstractMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    (
        ("NoSuchMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    (
        ("No SCIP shards found",),
        "the build produced no SCIP shards — for Android/AGP this is expected, because "
        "scip-java's Gradle plugin keys off standard source sets that AGP replaces with "
        "variants (upstream scip-java#177)",
    ),
)


def _search_only_reason(output: str) -> str | None:
    """Match indexer output against `_SEARCH_ONLY_SIGNATURES`; None means the
    failure is not recognized and must propagate."""
    for required, reason in _SEARCH_ONLY_SIGNATURES:
        if all(token in output for token in required):
            return reason
    return None
```

- [ ] **Step 4: Wrap the indexer call**

In `index_repo`, replace the indexer `_run` call from Task 2 with:

```python
            try:
                _run([*indexer_cmd, "--output", str(scip_path)], cwd=repo_path,
                     step=f"{indexer_cmd[0]} index",
                     env=_java_indexer_env() if language == "java" else None)
            except IndexingError as exc:
                reason = _search_only_reason(str(exc))
                if reason is None:
                    raise
                print(
                    f"note: {slug} cannot be SCIP-indexed — {reason}. "
                    "Falling back to search-only; this is remembered, so reindex/watch "
                    "will not repeat the build.",
                    file=sys.stderr,
                )
                semantic_ok = _publish_search_only(repo_path, slug, root, semantic_include)
                registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS,
                                scheme_override=scheme, semantic_include=semantic_include,
                                language_override=language_override, search_only=True)
                if semantic_ok:
                    registry.mark_semantic_indexed(slug)
                return slug
```

No explicit `registry.close()` here: this `return` unwinds through `index_repo`'s existing
`finally: registry.close()`, which already covers every exit path. Adding another would be a
harmless double-close, but relying on the one that is already there keeps a single owner.

Note this returns from inside the `with tempfile.TemporaryDirectory(...)` block, which is correct —
`_publish_search_only` creates its own scratch directory and never touches this one.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -q -m "not integration"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): degrade to search-only on known-unfixable indexer failures"
```

---

### Task 6: Widen the chunker allowlist

`chunk_file` already falls back to `_fixed_windows` when a parser is missing *or* when `_DEF_NODE_TYPES` has no entry — verified: `go`, `ruby`, and a nonsense `text` language all produce fixed-window chunks. The only gate is `iter_source_files`'s suffix filter.

**Files:**
- Modify: `src/jarvis/chunker.py` (`LANGUAGES` ~line 62)
- Test: `tests/test_chunker.py`

**Interfaces:**
- Produces: a wider `LANGUAGES` mapping. No signature changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chunker.py`:

```python
def test_newly_allowlisted_language_falls_back_to_fixed_windows():
    """Go has a tree-sitter parser but no _DEF_NODE_TYPES entry, so it must
    window rather than crash or return nothing."""
    from jarvis.chunker import chunk_file

    source = (
        "package main\n\n"
        "import \"fmt\"\n\n"
        "func Greet(name string) string {\n\treturn \"hi \" + name\n}\n\n"
        "func main() {\n\tfmt.Println(Greet(\"x\"))\n}\n"
    )
    chunks = chunk_file("demo.go", source, "filehash", "go")
    assert chunks
    assert all(c.symbol_name is None for c in chunks), "expected fixed-window chunks"


def test_go_and_ruby_are_allowlisted():
    from pathlib import Path

    from jarvis.chunker import language_for

    assert language_for(Path("main.go")) == "go"
    assert language_for(Path("app.rb")) == "ruby"


def test_existing_languages_still_chunk_by_symbol():
    """The wider allowlist must not regress symbol-aware chunking."""
    from jarvis.chunker import chunk_file

    source = "def greet(name):\n    return f'hi {name}'\n"
    chunks = chunk_file("demo.py", source, "filehash", "python")
    assert [c.symbol_name for c in chunks] == ["greet"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_chunker.py -q -k "allowlist or fixed_windows or still_chunk"`
Expected: FAIL — `language_for(Path("main.go"))` returns `None`.

- [ ] **Step 3: Extend `LANGUAGES`**

Replace the `LANGUAGES` dict in `src/jarvis/chunker.py`:

```python
# Extensions admitted to semantic indexing. Two tiers, by design:
#
#   * entries with a `_DEF_NODE_TYPES` mapping below get symbol-aware chunks
#   * everything else falls through `chunk_file`'s existing guard to
#     `_fixed_windows` — verified: a parser with no def-node mapping yields
#     windowed chunks, it does not error
#
# Adding a `_DEF_NODE_TYPES` entry for one of the second-tier languages
# upgrades it in place, with no change needed here.
#
# Kept as an ALLOWLIST rather than "everything that is not binary": this is
# what keeps images, lockfiles, and vendored blobs out of the embedding table.
LANGUAGES = {".py": "python", ".ts": "typescript", ".tsx": "tsx",
             ".java": "java", ".kt": "kotlin", ".swift": "swift",
             ".go": "go", ".rb": "ruby", ".rs": "rust",
             ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp",
             ".php": "php", ".scala": "scala", ".sh": "bash", ".sql": "sql"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_chunker.py tests/test_semantic.py -q -m "not integration"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/chunker.py tests/test_chunker.py
git commit -m "feat(chunker): admit common source languages via the fixed-window fallback"
```

---

### Task 7: Explain search-only at the MCP boundary

Without a `current` pointer, nav tools raise `IndexNotFoundError` and today return a bare "index not found" — confusing for a repo the user *did* index. `server.py` is the error-shaping boundary; `QueryService.__init__` takes only an `IndexConnectionCache`, so the registry lookup belongs here, not there.

**Files:**
- Modify: `src/jarvis/server.py` (error returns at lines 70, 81, 92, 103, 123; `get_index_status` ~line 144)
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `RegisteredRepo.search_only` (Task 3), `SEARCH_ONLY_STATUS` (Task 4).
- Produces: `_error_payload(repo: str, exc: Exception) -> dict[str, Any]` and a `status` key on `getIndexStatus`'s result.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_tools.py`:

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


def test_error_payload_passes_through_other_errors(tmp_path: Path, monkeypatch):
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    payload = server._error_payload("absent", RuntimeError("boom"))
    assert payload == {"error": "boom"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_tools.py -q -k error_payload`
Expected: FAIL with `AttributeError: module 'jarvis.server' has no attribute '_error_payload'`.

- [ ] **Step 3: Implement the helper**

Add to `src/jarvis/server.py`, above the first `@mcp.tool`:

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


def _error_payload(repo: str, exc: Exception) -> dict[str, Any]:
    """Turn a missing SCIP index into an explanation when the repo was
    deliberately published search-only. Every other error passes through
    unchanged, so this never hides a real fault."""
    if isinstance(exc, IndexNotFoundError) and _registry_status(repo) == SEARCH_ONLY_STATUS:
        return {
            "error": (
                f"{repo} is indexed search-only: it has no SCIP index, so navigation "
                "tools cannot answer. searchCode and semanticSearch do work on it. "
                "This happens when the language's indexer cannot build the repo — "
                "for example an Android/Gradle project."
            )
        }
    return {"error": str(exc)}
```

Add the imports near the existing ones:

```python
from jarvis.index_reader import IndexNotFoundError
from jarvis.registry import SEARCH_ONLY_STATUS
```

Import `SEARCH_ONLY_STATUS` from `registry`, never from `index_cli`: `server.py` does not import
the CLI module today, and adding that edge would pull `argparse`, `watch`, and `graph` into the
MCP server's import graph.

- [ ] **Step 4: Route the five SCIP nav tools through it**

At lines 70, 81, 92, 103, and 123 — the `except` blocks for `documentSymbols`, `goToDefinition`,
`findReferences`, `callHierarchy`, and `typeHierarchy` — replace:

```python
        return {"error": str(exc)}
```

with:

```python
        return _error_payload(repo, exc)
```

Leave lines 153 (`getIndexStatus`), 169 (`searchCode`), 202 (`semanticSearch`), and 217
(`blastRadius`) unchanged: the first three do not depend on the SCIP pointer, and `blastRadius`
reads the graph in `registry.db`, so none of them raises `IndexNotFoundError`.

- [ ] **Step 5: Add `status` to `getIndexStatus`**

Replace its return statement:

```python
    return {"repo": repo, "indexed": indexed, "status": _registry_status(repo),
            **_freshness_fields(freshness)}
```

`indexed` keeps its current meaning — whether a SCIP index is published — so it stays `False` for a
search-only repo. The new `status` field is what distinguishes "search-only" from "never indexed".

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_tools.py -q -m "not integration"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/server.py tests/test_server_tools.py
git commit -m "feat(server): explain search-only repos instead of reporting a missing index"
```

---

### Task 8: Integration test for Java indexing

Proves the launcher works end to end through `scip expt-convert`. Verified during design: this fixture yields `documents=1 chunks=1 global_symbols=8 mentions=10`.

**Files:**
- Create: `tests/fixtures/mini_java_repo/settings.gradle.kts`, `build.gradle.kts`, `src/main/java/demo/Greeter.java`, `gradlew`, `gradle/wrapper/gradle-wrapper.properties`, `gradle/wrapper/gradle-wrapper.jar`
- Modify: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `index_repo` with all Task 2/5 behavior in place.

- [ ] **Step 1: Create the fixture sources**

```bash
mkdir -p tests/fixtures/mini_java_repo/src/main/java/demo
```

`tests/fixtures/mini_java_repo/settings.gradle.kts`:

```kotlin
rootProject.name = "demo"
```

`tests/fixtures/mini_java_repo/build.gradle.kts`:

```kotlin
plugins { id("java") }
repositories { mavenCentral() }
```

`tests/fixtures/mini_java_repo/src/main/java/demo/Greeter.java`:

```java
package demo;

public class Greeter {
    public String greet(String name) {
        return "hi " + name;
    }

    public static void main(String[] args) {
        System.out.println(new Greeter().greet("x"));
    }
}
```

- [ ] **Step 2: Add the Gradle wrapper**

Fetch the wrapper from a pinned Gradle tag, so the fixture is reproducible on any machine and does
not depend on a `gradle` CLI being installed:

```bash
mkdir -p tests/fixtures/mini_java_repo/gradle/wrapper
BASE=https://raw.githubusercontent.com/gradle/gradle/v8.13.0
curl -fsSL -o tests/fixtures/mini_java_repo/gradlew "$BASE/gradlew"
curl -fsSL -o tests/fixtures/mini_java_repo/gradle/wrapper/gradle-wrapper.jar \
  "$BASE/gradle/wrapper/gradle-wrapper.jar"
chmod +x tests/fixtures/mini_java_repo/gradlew
```

Write `tests/fixtures/mini_java_repo/gradle/wrapper/gradle-wrapper.properties` by hand — the one in
the Gradle repo points at a snapshot build, which is not what a fixture wants:

```properties
distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\://services.gradle.org/distributions/gradle-8.13-bin.zip
networkTimeout=10000
validateDistributionUrl=true
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
```

Verify the wrapper actually runs before relying on it:

```bash
(cd tests/fixtures/mini_java_repo && ./gradlew --version)
```
Expected: prints `Gradle 8.13`. If `curl` fails or the jar is unusable, fall back to
`gradle wrapper --gradle-version 8.13` run inside the fixture directory.

`gradle-wrapper.jar` (~43KB) is committed deliberately, so the test is deterministic rather than
skipped on machines without a Gradle CLI.

- [ ] **Step 3: Write the integration test**

Append to `tests/test_index_cli.py`. Match the existing gating style used by
`test_index_repo_end_to_end_for_swift_repo`:

```python
_missing_java = [b for b in ("scip-java", "scip", "zoekt-index") if shutil.which(b) is None]
JAVA_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_java_repo"


@pytest.mark.integration
@pytest.mark.skipif(bool(_missing_java), reason=f"missing required binaries: {_missing_java}")
def test_index_repo_end_to_end_for_java_repo(tmp_path: Path):
    """A plain-JVM Gradle repo must produce real navigable symbols."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(JAVA_FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    slug = index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "indexed", f"expected a full index, got {entry.status}"
        assert entry.language == "java"
    finally:
        registry.close()

    target_dir = config.index_dir(slug, data_root)
    pointer = (target_dir / "current").read_text(encoding="utf-8").strip()
    conn = sqlite3.connect(f"file:{target_dir / pointer}?mode=ro", uri=True)
    try:
        symbols = conn.execute("SELECT COUNT(*) FROM global_symbols").fetchone()[0]
        mentions = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()

    assert symbols > 0, "no symbols — the indexer produced nothing navigable"
    assert chunks > 0 and mentions > 0, "symbols without occurrence ranges"
    assert any(
        "demo/Greeter#greet()" in row[0]
        for row in sqlite3.connect(
            f"file:{target_dir / pointer}?mode=ro", uri=True
        ).execute("SELECT symbol FROM global_symbols")
    )
```

- [ ] **Step 4: Run the integration test**

Run: `uv run pytest tests/test_index_cli.py -q -m integration -k java`
Expected: PASS if `scip-java` is installed (Task 1); SKIP otherwise. If it runs, expect roughly
`global_symbols=8`, `mentions=10`.

- [ ] **Step 5: Confirm the unit suite is unaffected**

Run: `uv run pytest -m "not integration" -q`
Expected: PASS, count at or above the 270 baseline.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/mini_java_repo tests/test_index_cli.py
git commit -m "test(index): add an end-to-end Java Gradle indexing fixture"
```

---

### Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md`, `CHANGELOG.md`, `.claude/skills/jarvis-setup/SKILL.md`

- [ ] **Step 1: Add the CLAUDE.md known-gap entry**

In `CLAUDE.md`, after the existing `**Known gap:**` paragraph about `typeHierarchy`:

```markdown
**Java/Kotlin reach is narrower than "supported" suggests.** `scip-java` indexes plain JVM
Gradle/Maven repos, and jarvis forces `-Dorg.gradle.parallel=false` for them because scip-java's
Gradle plugin races against itself across modules. Two cases cannot work at all and degrade to a
search-only publish instead:

- **Android/AGP** — scip-java's plugin keys off Gradle's standard `SourceSetContainer`, which AGP
  replaces with its variant model, so the build succeeds and emits zero SCIP shards
  ([scip-java#177](https://github.com/scip-code/scip-java/issues/177)).
- **Kotlin other than the pinned version** — `scip-kotlinc` is compiled against exactly one Kotlin
  release (`SCIP_JAVA_KOTLIN` in `setup.sh`, currently 2.2.0). Kotlin's compiler-plugin API is
  internal and unstable: 2.1.21 and 2.3.20 fail with `AbstractMethodError`, and even 2.2.20 fails
  with `NoSuchMethodError`. Java is unaffected — `scip-javac` uses javac's stable plugin API.

Both are detected from the indexer's own error output (`_SEARCH_ONLY_SIGNATURES` in
`index_cli.py`), never from parsing build files, and the decision is persisted so `reindex`/`watch`
skip the doomed build. `--search-only` requests the same publish up front: Zoekt and semantic search
work, navigation tools return an explanation. Only listed signatures trigger it — any other indexer
failure is still a hard failure.
```

- [ ] **Step 2: Update the setup skill**

In `.claude/skills/jarvis-setup/SKILL.md`, replace `scip-java` (detect-only)` in the installed-binaries sentence with:

```markdown
`scip-java` (a JVM launcher; needs `java` on `PATH`)
```

Then add after that paragraph:

```markdown
Java/Kotlin repos have real limits: Android/Gradle projects and Kotlin repos not on the pinned
Kotlin version cannot produce a SCIP index, and are published search-only instead (lexical and
semantic search work; navigation does not). See CLAUDE.md for the detail.
```

- [ ] **Step 3: Add the changelog entry**

In `CHANGELOG.md`, under the existing `## [Unreleased]` heading, extend `### Added` and add a
`### Fixed` section:

```markdown
### Added

- `--search-only` on `jarvis index`: publishes Zoekt and semantic search without a SCIP index,
  for repos whose indexer cannot build them. Persisted, so `reindex`/`watch` reuse it. Navigation
  tools report the repo as search-only rather than "index not found".
- Automatic search-only fallback when the indexer fails with a recognized, unfixable signature —
  an Android/Gradle build that emits no SCIP shards, or a `scip-kotlinc` ABI mismatch. Any other
  failure is still a hard failure.
- Semantic indexing now covers Go, Ruby, Rust, C, C++, C#, PHP, Scala, shell, and SQL via the
  chunker's existing fixed-window fallback.

### Fixed

- Java and Kotlin repos were un-indexable: `setup.sh` only ever probed for Docker and never put a
  `scip-java` executable on `PATH`, so every index failed with
  `No such file or directory: 'scip-java'`. It now installs upstream's launcher into
  `~/.jarvis/bin`. Gradle also runs single-threaded for Java, working around a
  `ConcurrentModificationException` in scip-java's own Gradle plugin on multi-module builds.
```

- [ ] **Step 4: Verify nothing else drifted**

Run: `uv run pytest -m "not integration" -q`
Expected: PASS.

Confirm the version guard is untouched:

```bash
git diff --name-only origin/main -- pyproject.toml server.json
```
Expected: empty output.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md CHANGELOG.md .claude/skills/jarvis-setup/SKILL.md
git commit -m "docs: record Java/Kotlin reach, search-only mode, and the Android gap"
```

---

## Follow-up (not in this plan)

File the `ConcurrentModificationException` upstream against `scip-code/scip-java` with a repro: any
multi-module Gradle build with `org.gradle.parallel=true`, two modules running
`scipPrintDependencies` concurrently. Not code, so it is not a task here — but it is the only way
Task 2's workaround ever gets removed.

## Decisions locked in during planning

Two questions the spec left open needed answers to write the tasks:

- **Sentinel language:** `UNKNOWN_LANGUAGE = "unknown"`, shown verbatim by `jarvis list` and
  `status`.
- **`getIndexStatus.indexed`:** keeps its current meaning — SCIP index present — so it reads `False`
  for a search-only repo. The new `status` field carries the distinction, which avoids changing an
  existing field's semantics for current consumers.
