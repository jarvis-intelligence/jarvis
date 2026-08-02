# Maven javac bash shim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Maven-built Java repos indexable on macOS by giving scip-java's generated `javac` wrapper a bash >= 4.4 on `PATH`.

**Architecture:** scip-java writes a `javac` wrapper whose shebang is `#!/usr/bin/env bash`, so it resolves bash through `PATH`. `setup.sh` creates `~/.codeintel/shims/bash` symlinked to a modern bash when the system default is too old; `_java_indexer_env()` prepends that shim directory for the indexer subprocess only. If the shim is missing and the build fails on the known signature, the error is re-raised with the remedy rather than degraded to search-only.

**Tech Stack:** Python 3 (stdlib only for this change), POSIX `sh` (`setup.sh`), pytest, `dash` for shell tests.

Spec: `docs/superpowers/specs/2026-08-02-maven-javac-bash-shim-design.md`

## Global Constraints

- Minimum bash version is **4.4**. Below that, `set -u` + `"${empty[@]}"` is an error.
- The shim directory is `${CODEINTEL_DATA_DIR:-$HOME/.codeintel}/shims`. It is keyed to **`CODEINTEL_DATA_DIR`**, never `CODEINTEL_BIN_DIR` — `setup.sh` writes it and `index_cli.py` reads it, and only `CODEINTEL_DATA_DIR` is honoured by `config.data_dir()` on the reading side.
- The shim directory contains **only** a `bash` symlink. Never prepend `/opt/homebrew/bin` or any other general-purpose bin dir — that would shadow `java`, `mvn`, and `git` for the build.
- `setup.sh` must stay POSIX `sh`. No `[[ ]]`, no arrays, no `local`. Tests run it under `dash`, which rejects bashisms.
- `setup.sh` must never invoke `brew`. It advises; the user installs.
- Existing style: use `if cmd; then ...; fi` rather than `cmd && ...` for control flow, for unambiguous `set -e` semantics across dash and bash-posix.
- Python: modern type hints (`str | None`, `list[T]`), frozen dataclasses for result types, parameterized SQL. No new runtime dependencies.
- Conventional commits, no AI references in messages.

---

### Task 1: `config.shim_dir()`

**Files:**
- Modify: `src/codeintel/config.py` (add next to `lancedb_dir`, around line 28-30)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `config.data_dir(root: Path | None) -> Path` (existing).
- Produces: `config.shim_dir(root: Path | None = None) -> Path`, returning `data_dir(root) / "shims"`. Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_shim_dir_is_under_data_dir(tmp_path, monkeypatch):
    from codeintel import config

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    assert config.shim_dir() == tmp_path / "shims"


def test_shim_dir_honors_explicit_root(tmp_path):
    """The root override wins over the env var, matching data_dir()."""
    from codeintel import config

    assert config.shim_dir(tmp_path) == tmp_path / "shims"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k shim_dir -v`
Expected: FAIL with `AttributeError: module 'codeintel.config' has no attribute 'shim_dir'`

- [ ] **Step 3: Implement**

In `src/codeintel/config.py`, directly below `lancedb_dir`:

```python
def shim_dir(root: Path | None = None) -> Path:
    """Directory holding shims for system tools whose default version is too
    old for an indexer to use.

    Currently just `bash`: scip-java's generated javac wrapper is
    `#!/usr/bin/env bash` with `set -u` and an unguarded `"${LAUNCHER_ARGS[@]}"`,
    which errors on bash < 4.4 — the bash macOS ships. `setup.sh` writes the
    symlink here; `index_cli._java_indexer_env()` puts this directory first on
    PATH for the indexer subprocess.

    Deliberately NOT under `bin/`: that holds pinned binaries setup.sh
    downloaded and owns, this holds links to system tools it did not.
    """
    return data_dir(root) / "shims"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k shim_dir -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/config.py tests/test_config.py
git commit -m "feat(config): add shim_dir for indexer tool shims"
```

---

### Task 2: `_java_indexer_env()` prepends the shim directory

**Files:**
- Modify: `src/codeintel/index_cli.py:185-194` (`_java_indexer_env`)
- Test: `tests/test_index_cli.py` (next to the existing `_java_indexer_env` tests at lines 304-318)

**Interfaces:**
- Consumes: `config.shim_dir()` from Task 1. `config` is already imported in `index_cli.py`.
- Produces: `_java_indexer_env() -> dict[str, str]` now returns a `"PATH"` key **only when** `shim_dir() / "bash"` exists. `GRADLE_OPTS` behaviour is unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_index_cli.py` after `test_java_indexer_env_appends_to_existing_gradle_opts` (line 318):

```python
def test_java_indexer_env_prepends_shim_dir_when_bash_shim_exists(tmp_path: Path, monkeypatch):
    """The shim must come FIRST — the whole point is beating /bin/bash 3.2."""
    from codeintel.index_cli import _java_indexer_env

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "bash").write_text("#!/bin/sh\n")

    assert _java_indexer_env()["PATH"] == f"{shims}{os.pathsep}/usr/bin:/bin"


def test_java_indexer_env_omits_path_when_no_shim(tmp_path: Path, monkeypatch):
    """No shim on Linux or a modern-bash mac: the branch must stay inert."""
    from codeintel.index_cli import _java_indexer_env

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))

    assert "PATH" not in _java_indexer_env()
```

`os` and `Path` are already imported at the top of `tests/test_index_cli.py`. If `os` is not, add `import os`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k java_indexer_env -v`
Expected: the two new tests FAIL with `KeyError: 'PATH'`; the two existing ones still PASS.

- [ ] **Step 3: Implement**

Replace `_java_indexer_env` in `src/codeintel/index_cli.py`:

```python
def _java_indexer_env() -> dict[str, str]:
    """scip-java's Gradle plugin races against itself when Gradle runs tasks in
    parallel: two modules' `scipPrintDependencies` mutate shared state and the
    build dies with java.util.ConcurrentModificationException. Forcing
    single-threaded execution avoids it.

    Appended to any existing GRADLE_OPTS rather than replacing it, so a user's
    heap settings survive. Reported upstream.

    PATH gets the shim dir prepended when it holds a bash: scip-java's
    generated javac wrapper is `#!/usr/bin/env bash` (so bash comes from PATH)
    with `set -u` and an unguarded `"${LAUNCHER_ARGS[@]}"`, which is an error on
    bash < 4.4. macOS ships 3.2, so every Maven build fails at
    maven-compiler-plugin's version probe without this. Remove once the pinned
    scip-java emits a bash-3.2-safe wrapper.

    Only the shim dir, never a general bin dir: prepending e.g. Homebrew's bin
    would also shadow java/mvn/git for the build.
    """
    existing = os.environ.get("GRADLE_OPTS", "")
    env = {"GRADLE_OPTS": f"{existing} -Dorg.gradle.parallel=false".strip()}
    shims = config.shim_dir()
    if (shims / "bash").exists():
        env["PATH"] = f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"
    return env
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k java_indexer_env -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "fix(index): prepend bash shim to PATH for java indexing"
```

---

### Task 3: Actionable error instead of a search-only downgrade

**Files:**
- Modify: `src/codeintel/index_cli.py` (module constants near `_SEARCH_ONLY_SIGNATURES` at line 86; the `except IndexingError` branch at line 601)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `IndexingError` (existing), `_search_only_reason` (existing).
- Produces: `_bash_shim_failure(output: str) -> bool`. Checked **before** `_search_only_reason` in the `except IndexingError` branch, and re-raises — it must never reach `_publish_search_only`.

**Why not a `_SEARCH_ONLY_SIGNATURES` entry:** that path persists `search_only=1`, and `--search-only` is `action="store_true", default=None` — settable but not clearable, so the only escape is `codeintel forget` plus a full reindex. Right for Android/AGP (permanently unindexable), wrong here (one `brew install` fixes it). The outer `except Exception` at line 670 already marks the repo `failed`, which a plain reindex recovers from.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_index_cli.py`, next to the other signature tests (near line 1364):

```python
def test_bash_shim_failure_detected():
    from codeintel.index_cli import _bash_shim_failure

    assert _bash_shim_failure(
        "Fatal error compiling: Could not retrieve version from "
        "/tmp/scip-java1/bin/javac. Exit code 1, Output: "
        "/tmp/scip-java1/bin/javac: line 38: LAUNCHER_ARGS[@]: unbound variable"
    )


def test_bash_shim_failure_ignores_other_output():
    from codeintel.index_cli import _bash_shim_failure

    assert not _bash_shim_failure("error: No SCIP shards found")


def test_bash_shim_failure_does_not_publish_search_only(tmp_path: Path, monkeypatch):
    """A fixable env problem must NOT persist search_only=1.

    --search-only is store_true/default=None: settable, never clearable. If this
    downgraded, a user who then installed bash would silently keep getting no
    navigation, escapable only via `codeintel forget`.
    """
    from codeintel.index_cli import IndexingError, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(
                "Fatal error compiling: Could not retrieve version from "
                "/tmp/scip-java1/bin/javac. Exit code 1, Output: "
                "/tmp/scip-java1/bin/javac: line 38: LAUNCHER_ARGS[@]: unbound variable"
            )
        return None

    monkeypatch.setattr("codeintel.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("codeintel.index_cli._run", _fake_run)
    monkeypatch.setattr("codeintel.index_cli._run_semantic_stage", lambda *a, **k: False)

    with pytest.raises(IndexingError, match="bash"):
        index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get("repo")
        assert entry is not None
        assert entry.status == "failed"
        assert entry.search_only is False, "a fixable env problem must stay recoverable"
    finally:
        registry.close()
```

`shutil`, `pytest`, `Path`, `Registry`, `FIXTURE_REPO`, and `_init_git_repo` are all already used by the neighbouring tests in this file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k bash_shim -v`
Expected: FAIL — `ImportError: cannot import name '_bash_shim_failure'`

- [ ] **Step 3: Implement**

In `src/codeintel/index_cli.py`, directly after `_search_only_reason` (line 112):

```python
# Not a _SEARCH_ONLY_SIGNATURES entry on purpose: that path persists
# search_only=1, and --search-only is store_true/default=None -- settable but
# never clearable, so the only escape is `codeintel forget` + reindex. Correct
# for Android/AGP, which is permanently unindexable; a trap for this, which one
# `brew install bash` fixes. Failing loudly with the remedy keeps the repo
# `failed` and recoverable by a plain reindex.
_BASH_SHIM_TOKENS = ("LAUNCHER_ARGS[@]", "unbound variable")

_BASH_SHIM_REMEDY = (
    "scip-java's generated javac wrapper requires bash >= 4.4, but this machine's "
    "default bash is older (macOS ships 3.2). Install a newer bash "
    "(`brew install bash`), re-run setup.sh to create the shim, then reindex."
)


def _bash_shim_failure(output: str) -> bool:
    """True when the indexer died on bash < 4.4 expanding an empty array."""
    return all(token in output for token in _BASH_SHIM_TOKENS)
```

Then in the `except IndexingError as exc:` branch (line 601), insert **before** the `reason = ...` line:

```python
            except IndexingError as exc:
                if _bash_shim_failure(str(exc)):
                    raise IndexingError(f"{_BASH_SHIM_REMEDY}\n\n{exc}") from exc
                reason = _search_only_reason(str(exc))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "bash_shim or search_only" -v`
Expected: PASS — the three new tests plus the existing search-only tests, confirming the AGP path is untouched.

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/index_cli.py tests/test_index_cli.py
git commit -m "fix(index): fail with a remedy on the bash 3.2 javac wrapper bug"
```

---

### Task 4: `setup.sh` creates the shim

**Files:**
- Modify: `setup.sh` (install-dir section ~line 117-130; installers section ~line 320; `usage` ~line 509; `main` ~line 588)
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `log_info`, `log_warn`, `record`, `should_run` (all existing in `setup.sh`).
- Produces: `shim_dir()`, `bash_at_least_44(path)`, `install_bash_shim(os)`. Writes `$(shim_dir)/bash`, which Task 2 reads.

**Note on `record`:** `install_bash_shim` calls `record` itself and always returns 0, rather than going through `run_one`. `run_one` can only record `ok` or `FAILED`; "no modern bash found" is neither — it is a skip that must not fail the whole setup for users who never index Java, but must not be reported as `ok` either.

**Version parsing** (verified under `dash` against real `bash --version` output for 5.3.15, 3.2.57, 4.4.0, and a garbage line):
`GNU bash, version 5.3.15(1)-release (...)` → strip through `version `, then strip from the first character that is not a digit or dot → `5.3.15`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
def _fake_bash(tmp_path, name: str, version_line: str) -> str:
    """A stand-in bash that answers --version and nothing else."""
    path = tmp_path / name
    path.write_text(f'#!/bin/sh\n[ "$1" = "--version" ] && echo "{version_line}"\n')
    path.chmod(0o755)
    return str(path)


def test_shim_dir_defaults_under_codeintel_home():
    result = run_func("shim_dir", env={"HOME": "/home/someone"})
    assert result.stdout.strip() == "/home/someone/.codeintel/shims"


def test_shim_dir_follows_data_dir_not_bin_dir(tmp_path):
    """Must key off CODEINTEL_DATA_DIR: config.shim_dir() reads that one, and a
    shim the Python side cannot find is worse than no shim."""
    result = run_func(
        "shim_dir",
        env={"HOME": "/home/someone",
             "CODEINTEL_DATA_DIR": str(tmp_path),
             "CODEINTEL_BIN_DIR": "/should/be/ignored"},
    )
    assert result.stdout.strip() == f"{tmp_path}/shims"


def test_bash_at_least_44_accepts_bash_5(tmp_path):
    fake = _fake_bash(tmp_path, "bash5", "GNU bash, version 5.3.15(1)-release (arm64-apple-darwin25)")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode == 0


def test_bash_at_least_44_accepts_exactly_44(tmp_path):
    fake = _fake_bash(tmp_path, "bash44", "GNU bash, version 4.4.0(1)-release")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode == 0


def test_bash_at_least_44_rejects_macos_bash_32(tmp_path):
    fake = _fake_bash(tmp_path, "bash32", "GNU bash, version 3.2.57(1)-release (arm64-apple-darwin25)")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode != 0


def test_bash_at_least_44_rejects_unparseable_version(tmp_path):
    fake = _fake_bash(tmp_path, "weird", "not a version string at all")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode != 0


def test_bash_at_least_44_rejects_missing_binary(tmp_path):
    assert run_func(f'bash_at_least_44 "{tmp_path}/nope"').returncode != 0


def test_install_bash_shim_is_a_noop_on_linux(tmp_path):
    result = run_func(
        'install_bash_shim linux',
        env={"HOME": str(tmp_path), "CODEINTEL_DATA_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert not (tmp_path / "shims" / "bash").exists()


def test_install_bash_shim_skips_when_default_bash_is_modern(tmp_path):
    """A mac with a modern default bash needs no shim."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_bash(bindir, "bash", "GNU bash, version 5.3.15(1)-release")
    result = run_func(
        'install_bash_shim darwin',
        env={"HOME": str(tmp_path), "CODEINTEL_DATA_DIR": str(tmp_path),
             "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert not (tmp_path / "shims" / "bash").exists()


def test_install_bash_shim_links_candidate_when_default_is_old(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_bash(bindir, "bash", "GNU bash, version 3.2.57(1)-release")
    modern = _fake_bash(tmp_path, "modern-bash", "GNU bash, version 5.3.15(1)-release")
    result = run_func(
        f'BASH_SHIM_CANDIDATES="{modern}"\ninstall_bash_shim darwin',
        env={"HOME": str(tmp_path), "CODEINTEL_DATA_DIR": str(tmp_path),
             "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    link = tmp_path / "shims" / "bash"
    assert link.is_symlink()
    assert link.resolve() == Path(modern).resolve()


def test_install_bash_shim_advises_install_when_no_modern_bash(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_bash(bindir, "bash", "GNU bash, version 3.2.57(1)-release")
    result = run_func(
        f'BASH_SHIM_CANDIDATES="{tmp_path}/absent"\ninstall_bash_shim darwin',
        env={"HOME": str(tmp_path), "CODEINTEL_DATA_DIR": str(tmp_path),
             "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, "must not fail setup for users who never index Java"
    assert "brew install bash" in result.stdout + result.stderr
    assert not (tmp_path / "shims" / "bash").exists()
```

Add `from pathlib import Path` to the imports of `tests/test_setup_sh.py` if it is not already there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -k "shim or bash_at_least" -v`
Expected: FAIL — `shim_dir: not found`, `bash_at_least_44: not found`.

- [ ] **Step 3: Implement**

In `setup.sh`, after `ensure_bin_dir` (~line 130):

```sh
# Where bash shims go. Keyed to CODEINTEL_DATA_DIR, NOT CODEINTEL_BIN_DIR:
# index_cli.py resolves this same path via config.shim_dir() -> data_dir(),
# which only honours CODEINTEL_DATA_DIR. A shim the reader cannot find is
# worse than no shim at all.
#
# A sibling of bin_dir rather than a subdirectory: bin/ holds pinned binaries
# we downloaded and own, shims/ holds symlinks to system tools we did not.
shim_dir() {
	echo "${CODEINTEL_DATA_DIR:-${HOME}/.codeintel}/shims"
}
```

In the installers section (~line 320), before `install_scip`:

```sh
# Overridable so tests can point at fixtures instead of the real filesystem.
BASH_SHIM_CANDIDATES="${BASH_SHIM_CANDIDATES:-/opt/homebrew/bin/bash /usr/local/bin/bash}"

# True when $1 is a bash >= 4.4. Below that, `set -u` plus an empty
# "${arr[@]}" is an error -- which is exactly how scip-java's generated javac
# wrapper dies on macOS's stock bash 3.2.
#
# Parses `bash --version` rather than $BASH_VERSINFO so a test fixture can be a
# plain sh script. First line looks like:
#   GNU bash, version 5.3.15(1)-release (aarch64-apple-darwin25.4.0)
bash_at_least_44() {
	_bin=$1
	[ -n "$_bin" ] || return 1
	[ -x "$_bin" ] || return 1
	_line=$("$_bin" --version 2>/dev/null | head -n 1) || return 1
	_ver=${_line#*version }
	_ver=${_ver%%[!0-9.]*}
	_major=${_ver%%.*}
	_rest=${_ver#*.}
	_minor=${_rest%%.*}
	case "$_major" in '' | *[!0-9]*) return 1 ;; esac
	case "$_minor" in '' | *[!0-9]*) return 1 ;; esac
	if [ "$_major" -gt 4 ]; then return 0; fi
	if [ "$_major" -eq 4 ] && [ "$_minor" -ge 4 ]; then return 0; fi
	return 1
}

# scip-java's generated javac wrapper is `#!/usr/bin/env bash` with `set -u`
# and an unguarded "${LAUNCHER_ARGS[@]}", so it needs bash >= 4.4 on PATH.
# macOS ships only 3.2, which breaks every Maven-built Java repo. Linux ships
# >= 4.4, so this is a no-op there.
#
# Never runs `brew`: installing a shell is the user's call, and setup.sh
# otherwise only downloads pinned release binaries into its own bin dir.
install_bash_shim() {
	_os=$1
	if [ "$_os" != "darwin" ]; then
		record bash-shim "not needed"
		return 0
	fi

	_default=$(command -v bash 2>/dev/null) || _default=""
	if bash_at_least_44 "$_default"; then
		log_info "bash-shim: default bash is >= 4.4"
		record bash-shim "not needed"
		return 0
	fi

	# SC2086 intentional: BASH_SHIM_CANDIDATES is a space-separated list and
	# must word-split. POSIX sh has no arrays, which is why it is a string.
	# shellcheck disable=SC2086
	for _cand in $BASH_SHIM_CANDIDATES; do
		if bash_at_least_44 "$_cand"; then
			mkdir -p "$(shim_dir)"
			ln -sf "$_cand" "$(shim_dir)/bash"
			log_info "bash-shim: linked ${_cand}"
			record bash-shim "ok"
			return 0
		fi
	done

	log_warn "bash-shim: no bash >= 4.4 found. Maven-built Java repos cannot be SCIP-indexed until you run: brew install bash"
	record bash-shim "skipped (run: brew install bash)"
	return 0
}
```

In `main` (~line 607), after the `scip-java` line and before `ensure_on_path`:

```sh
	if should_run bash-shim; then install_bash_shim "$OS"; fi
```

In `usage` (~line 518), extend the `--only` list:

```
                  scip, zoekt, scip-swift, scip-typescript,
                  scip-python, scip-java, bash-shim
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS — the new tests plus every pre-existing `setup.sh` test.

- [ ] **Step 5: Verify against the real toolchain**

Run: `./setup.sh --only bash-shim`
Expected on macOS with Homebrew bash: `bash-shim  ok` in the summary, and `~/.codeintel/shims/bash` resolving to a bash >= 4.4:

```bash
ls -l ~/.codeintel/shims/bash && ~/.codeintel/shims/bash --version | head -1
```

- [ ] **Step 6: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): install a bash >= 4.4 shim for scip-java on macOS"
```

---

### Task 5: Documentation and upstream report

**Files:**
- Modify: `CLAUDE.md` (the Java/Kotlin reach section)
- Modify: `README.md:302` (append to the upstream-limitations list)

- [ ] **Step 1: Document the macOS bash requirement**

Add to the Java/Kotlin section of `CLAUDE.md`, after the AGP and Kotlin-version paragraphs:

```markdown
**Maven Java repos need bash >= 4.4 on macOS.** scip-java generates a `javac`
wrapper (`#!/usr/bin/env bash`, `set -eu`) that expands `"${LAUNCHER_ARGS[@]}"`
unguarded. maven-compiler-plugin's version probe passes no `-J` flags, so the
array is empty — an error on bash < 4.4, and macOS ships only 3.2. Every
Maven-built Java repo fails at `default-compile` with
`LAUNCHER_ARGS[@]: unbound variable`.

Because the wrapper's shebang resolves bash through `PATH`, `setup.sh` creates
`~/.codeintel/shims/bash` pointing at a bash >= 4.4, and `_java_indexer_env()`
prepends that one directory for the indexer subprocess. Only the shim dir is
prepended, never a general bin dir — that would shadow `java`/`mvn`/`git` for
the build. Gradle is unaffected: it does not use the forked-javac wrapper.

Unlike the AGP case this is *fixable*, so it does not degrade to search-only —
`--search-only` cannot be un-set, which would strand a user who later installed
bash. It raises an error naming the remedy and leaves the repo `failed`.
```

- [ ] **Step 2: Add the README limitation bullet**

Append to the upstream-limitations list in `README.md`, **after** the existing
"Both cases are detected automatically … degrade to `--search-only`" sentence
that closes the Kotlin bullet (line 301). Placing it before that sentence would
make "Both cases" read as covering three bullets, and this one deliberately does
*not* degrade to search-only:

```markdown
- **Maven-built Java repos need bash >= 4.4 on macOS** — scip-java's generated
  `javac` wrapper (`#!/usr/bin/env bash`, `set -eu`) expands
  `"${LAUNCHER_ARGS[@]}"` unguarded, which errors on bash < 4.4; macOS ships
  only 3.2, so the build dies at `default-compile` with
  `LAUNCHER_ARGS[@]: unbound variable`. `setup.sh` works around it by linking
  `~/.codeintel/shims/bash` to a newer bash and putting that one directory
  first on `PATH` for the indexer. If no bash >= 4.4 is installed, indexing
  fails with the remedy rather than degrading to `--search-only` — unlike the
  two cases above, this one is fixable (`brew install bash`), and a persisted
  `--search-only` cannot be un-set.
```

- [ ] **Step 3: Verify the docs match the code**

Run: `grep -n "shim" CLAUDE.md README.md src/codeintel/index_cli.py src/codeintel/config.py setup.sh | head -20`
Expected: the documented path `~/.codeintel/shims/bash` matches `shim_dir()` in both `config.py` and `setup.sh`.

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest -m "not integration"`
Expected: all pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: note the macOS bash >= 4.4 requirement for Maven Java repos"
```

- [ ] **Step 6: File the upstream issue**

Report against `scip-code/scip-java`: the generated javac wrapper is not
bash-3.2-safe. Include the reproduction from the spec and the one-line fix:

```bash
javac ${JAVAC_JVM_OPTIONS[@]+"${JAVAC_JVM_OPTIONS[@]}"} "@$NEW_JAVAC_OPTS" ${LAUNCHER_ARGS[@]+"${LAUNCHER_ARGS[@]}"}
```

Record the issue URL in a comment above `_BASH_SHIM_TOKENS` in `index_cli.py`,
matching how `scip#464` and `scip-java#177` are already cited, so the shim can
be removed once the pinned scip-java carries the fix.

---

## Post-implementation verification

Re-index the three affected repos and confirm they reach `indexed` rather than `failed`:

```bash
uv run codeintel index /Users/ddphuong/Projects/epost-workspace/polaris-ai-plaform/polaris-api --slug polaris-api
uv run codeintel index /Users/ddphuong/Projects/epost-workspace/polaris-ai-plaform/polaris-knowledge --slug polaris-knowledge
uv run codeintel list | grep polaris
```

Expected: all three (`polaris-api`, `polaris-directory`, `polaris-knowledge`) show `indexed` with a real commit SHA. `polaris-directory` was already brought to `indexed` during design verification; the other two are still `failed` and are the real test.

Then confirm navigation resolves on a Maven repo:

```
documentSymbols(repo="polaris-api", path="<some .java file>")
```

Expected: symbols with `scip-java maven maven/...` coordinates, not an error.
