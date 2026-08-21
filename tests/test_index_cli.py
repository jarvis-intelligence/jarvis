"""Unit tests for language detection, plus an end-to-end integration test
against `tests/fixtures/mini_py_repo/` using the real scip-python + scip
CLI binaries (marked `@pytest.mark.integration` — skipped if unavailable).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from jarvis import config
from jarvis.index_cli import PARTIAL_STATUS, UnsupportedLanguageError, detect_language, index_repo
from jarvis.registry import Registry

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_py_repo"
SWIFT_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_swift_repo"
JAVA_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_java_repo"

_REQUIRED_BINARIES = ["scip-python", "scip", "zoekt-git-index"]
_missing = [b for b in _REQUIRED_BINARIES if shutil.which(b) is None]

_SWIFT_REQUIRED_BINARIES = ["scip-swift", "scip", "zoekt-git-index"]
_missing_swift = [b for b in _SWIFT_REQUIRED_BINARIES if shutil.which(b) is None]

_missing_java = [b for b in ("scip-java", "scip", "zoekt-git-index") if shutil.which(b) is None]


def _fake_completed_process(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """A stand-in for `_run`'s real return value in tests that mock it out --
    `_publish_search_only`/`index_repo` read `.stderr` off it for the
    coverage-shortfall check, so a bare `None` return no longer suffices."""
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def test_git_tracked_files_lists_committed_paths(tmp_path: Path):
    from jarvis.index_cli import _git_tracked_files

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _init_git_repo(tmp_path)

    assert sorted(_git_tracked_files(tmp_path)) == ["a.py", "b.py"]


def test_git_tracked_files_handles_paths_with_spaces(tmp_path: Path):
    """`-z` is required: without it, git quotes/escapes non-ASCII names
    (e.g. as octal escapes like "caf\\303\\251.py") on the default
    newline-separated output, which would corrupt suffix parsing in
    detect_language(). A plain space would NOT reproduce this -- git
    does not quote plain-ASCII spaces even without `-z`."""
    from jarvis.index_cli import _git_tracked_files

    (tmp_path / "café.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    assert _git_tracked_files(tmp_path) == ["café.py"]


def test_git_tracked_files_raises_for_non_git_directory(tmp_path: Path):
    from jarvis.index_cli import NotAGitRepositoryError, _git_tracked_files

    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(NotAGitRepositoryError):
        _git_tracked_files(tmp_path)


def test_git_head_raises_indexing_error_for_repo_with_no_commits(tmp_path: Path):
    """A freshly `git init`-ed repo has no HEAD. Previously this surfaced as
    a bare CalledProcessError with no explanation of what was wrong."""
    from jarvis.index_cli import IndexingError, _git_head

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(IndexingError, match="no commits"):
        _git_head(tmp_path)


def test_git_head_raises_not_a_git_repository_for_non_git_directory(tmp_path: Path):
    """Distinct from the no-commits case above: a directory that isn't a
    git repository at all must not be misdiagnosed as "has no commits
    yet" -- `git rev-parse HEAD` fails identically in both cases, so
    `_git_head` must check `--is-inside-work-tree` first."""
    from jarvis.index_cli import NotAGitRepositoryError, _git_head

    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(NotAGitRepositoryError):
        _git_head(tmp_path)


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


def test_detect_language_ignores_gitignored_checkout_directory(tmp_path: Path):
    """Direct regression test for the sample-python-repo failure: a
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
    from jarvis.index_cli import NotAGitRepositoryError

    (tmp_path / "a.py").write_text("x = 1\n")

    with pytest.raises(NotAGitRepositoryError):
        detect_language(tmp_path)


def test_prefers_xcodebuild_false_for_bare_spm_package(tmp_path: Path):
    from jarvis.index_cli import _prefers_xcodebuild

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    assert _prefers_xcodebuild(tmp_path) is False


def test_prefers_xcodebuild_true_when_xcodeproj_present(tmp_path: Path):
    from jarvis.index_cli import _prefers_xcodebuild

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _prefers_xcodebuild(tmp_path) is True


def test_prefers_xcodebuild_true_when_xcworkspace_present(tmp_path: Path):
    from jarvis.index_cli import _prefers_xcodebuild

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcworkspace").mkdir()
    assert _prefers_xcodebuild(tmp_path) is True


def test_swift_indexer_cmd_unchanged_without_xcodeproj(tmp_path: Path):
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme=None) == ["scip-swift"]


def test_swift_indexer_cmd_adds_xcodebuild_when_xcodeproj_present(tmp_path: Path):
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme=None) == [
        "scip-swift", "--build-tool", "xcodebuild",
    ]


def test_swift_indexer_cmd_adds_scheme_when_given(tmp_path: Path):
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme="ios_theme_ui") == [
        "scip-swift", "--build-tool", "xcodebuild", "--scheme", "ios_theme_ui",
    ]


def test_swift_indexer_cmd_ignores_scheme_without_xcodeproj(tmp_path: Path):
    """A --scheme override is meaningless (and unsupported by scip-swift)
    under the swiftpm build tool, so it must not leak into the command
    when there's no checked-in Xcode project to justify xcodebuild."""
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, scheme="ios_theme_ui") == ["scip-swift"]


def test_detect_language_tie_break_prefers_java_over_swift(tmp_path: Path):
    (tmp_path / "a.java").write_text("class A {}\n")
    (tmp_path / "b.java").write_text("class B {}\n")
    (tmp_path / "a.swift").write_text("let x = 1\n")
    (tmp_path / "b.swift").write_text("let y = 2\n")
    _init_git_repo(tmp_path)
    language, _ = detect_language(tmp_path)
    assert language == "java"


def test_index_repo_rejects_dotdot_slug_before_touching_disk(tmp_path: Path):
    """A `--slug ..` (or any slug resolving to `.`/`..`) must be rejected
    before `config.index_dir()` ever builds a path from it — otherwise
    `jarvis forget ..` could `shutil.rmtree()` outside the data dir."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("x = 1\n")
    with pytest.raises(ValueError):
        index_repo(repo_dir, slug="..", root=tmp_path / "data")
    # No registry or scip dir should have been created for the rejected slug.
    assert not (tmp_path / "data").exists()


def test_index_repo_rejects_a_second_slug_for_the_same_path(tmp_path: Path, monkeypatch):
    """zoekt.name is one value per repo, so a second slug for one path would
    overwrite the first's name and silently break `r:<first-slug>`."""
    from jarvis import config
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("first", str(repo.resolve()), "python", "abc", "indexed")
    finally:
        registry.close()

    with pytest.raises(IndexingError, match="already indexed as 'first'"):
        index_repo(repo, slug="second")


def test_index_repo_allows_reindexing_the_same_slug(tmp_path: Path, monkeypatch):
    """The normal reindex/watch path: same slug, same path, must not trip."""
    from jarvis import config
    from jarvis.index_cli import _reject_duplicate_slug_for_path
    from jarvis.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("same", str(repo.resolve()), "python", "abc", "indexed")
        _reject_duplicate_slug_for_path(registry, "same", repo.resolve())  # must not raise
    finally:
        registry.close()


def test_reject_duplicate_slug_compares_resolved_paths(tmp_path: Path, monkeypatch):
    """Rows written before this change may hold unresolved paths; a trailing
    "/." or symlinked parent must still be recognised as the same repo."""
    from jarvis import config
    from jarvis.index_cli import IndexingError, _reject_duplicate_slug_for_path
    from jarvis.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("first", f"{repo}/.", "python", "abc", "indexed")
        with pytest.raises(IndexingError, match="already indexed as 'first'"):
            _reject_duplicate_slug_for_path(registry, "second", repo.resolve())
    finally:
        registry.close()


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


def test_java_indexer_env_prepends_shim_dir_when_bash_shim_exists(tmp_path: Path, monkeypatch):
    """The shim must come FIRST — the whole point is beating /bin/bash 3.2."""
    from jarvis.index_cli import _java_indexer_env

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "bash").write_text("#!/bin/sh\n")

    assert _java_indexer_env()["PATH"] == f"{shims}{os.pathsep}/usr/bin:/bin"


def test_java_indexer_env_omits_path_when_no_shim(tmp_path: Path, monkeypatch):
    """No shim on Linux or a modern-bash mac: the branch must stay inert."""
    from jarvis.index_cli import _java_indexer_env

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    assert "PATH" not in _java_indexer_env()


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


def test_forget_rejects_dotdot_slug(tmp_path: Path, monkeypatch, capsys):
    from jarvis.index_cli import _cmd_forget
    import argparse

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    rc = _cmd_forget(argparse.Namespace(slug=".."))
    assert rc == 1
    assert "error" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_end_to_end_atomic_swap_under_open_reader(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, root=data_root)

    from jarvis.index_cli import _tracked_blob_count

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.commit_sha is not None
        assert entry.tracked_files == _tracked_blob_count(repo_dir)
    finally:
        registry.close()

    target_dir = config.index_dir(slug, data_root)
    pointer = (target_dir / "current").read_text(encoding="utf-8").strip()
    assert pointer == f"index-{entry.commit_sha}.db"

    # Open a reader connection against the published db BEFORE reindexing,
    # to prove reindex-under-load doesn't corrupt an in-flight read.
    reader = sqlite3.connect(f"file:{target_dir / pointer}?mode=ro", uri=True)
    count_before = reader.execute("SELECT COUNT(*) FROM global_symbols").fetchone()[0]
    assert count_before > 0

    # Reindex (no new commit, but the pipeline runs again) — the open
    # reader's connection must remain valid throughout.
    index_repo(repo_dir, slug=slug, root=data_root)
    count_after = reader.execute("SELECT COUNT(*) FROM global_symbols").fetchone()[0]
    assert count_after == count_before
    reader.close()

    zoekt_shards = list((data_root / ".zoekt").glob("*.zoekt"))
    assert zoekt_shards, "zoekt-git-index should have written at least one shard"


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_marks_failed_on_indexer_error(tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr(
        "jarvis.index_cli._LANGUAGE_INDEXERS",
        {".py": ("python", ["definitely-not-a-real-binary"])},
    )

    from jarvis.index_cli import IndexingError

    with pytest.raises(IndexingError):
        index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
    finally:
        registry.close()


@pytest.mark.integration
@pytest.mark.skipif(_missing_swift, reason=f"missing required binaries: {_missing_swift}")
def test_index_repo_end_to_end_for_swift_repo(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    shutil.copytree(SWIFT_FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.language == "swift"
    finally:
        registry.close()

    target_dir = config.index_dir(slug, data_root)
    pointer = (target_dir / "current").read_text(encoding="utf-8").strip()
    db = sqlite3.connect(f"file:{target_dir / pointer}?mode=ro", uri=True)
    try:
        count = db.execute("SELECT COUNT(*) FROM global_symbols").fetchone()[0]
        assert count > 0
    finally:
        db.close()


@pytest.mark.integration
@pytest.mark.skipif(_missing_swift, reason=f"missing required binaries: {_missing_swift}")
def test_index_repo_preserves_scheme_override_when_not_repassed(tmp_path: Path):
    """Regression test for the jarvis-watch bug: a second index_repo()
    call with scheme=None (e.g. an unattended `jarvis watch` reindex)
    must not wipe a previously stored scheme_override."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(SWIFT_FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    slug = index_repo(repo_dir, root=data_root, scheme="some-scheme")
    index_repo(repo_dir, slug=slug, root=data_root, scheme=None)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.scheme_override == "some-scheme"
    finally:
        registry.close()


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


def test_resolve_scheme_preserves_stored_override_when_none_given(tmp_path: Path):
    from jarvis.index_cli import _resolve_scheme

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="ios_theme_ui")
    assert _resolve_scheme(registry, "my-repo", scheme=None) == "ios_theme_ui"
    registry.close()


def test_resolve_scheme_prefers_explicit_value_over_stored(tmp_path: Path):
    from jarvis.index_cli import _resolve_scheme

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="old")
    assert _resolve_scheme(registry, "my-repo", scheme="new") == "new"
    registry.close()


def test_resolve_scheme_returns_none_for_unknown_slug(tmp_path: Path):
    from jarvis.index_cli import _resolve_scheme

    registry = Registry(tmp_path / "registry.db")
    assert _resolve_scheme(registry, "nope", scheme=None) is None
    registry.close()


def test_parse_scip_version_reads_standard_output():
    from jarvis.index_cli import parse_scip_version

    assert parse_scip_version("scip version v0.9.0") == (0, 9, 0)
    assert parse_scip_version("scip version v0.10.2\n") == (0, 10, 2)
    assert parse_scip_version("v1.0.0") == (1, 0, 0)


def test_parse_scip_version_returns_none_on_junk():
    from jarvis.index_cli import parse_scip_version

    assert parse_scip_version("") is None
    assert parse_scip_version("not a version") is None


def test_check_scip_version_rejects_v070(monkeypatch):
    """v0.7.0 converts successfully but silently drops every range."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.7.0")
    with pytest.raises(cli.IndexingError) as exc:
        cli.check_scip_version()
    message = str(exc.value)
    assert "0.7.0" in message
    assert "0.9.0" in message, "must state the required floor"


def test_check_scip_version_accepts_v090(monkeypatch):
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.9.0")
    cli.check_scip_version()  # must not raise


def test_check_scip_version_accepts_newer(monkeypatch):
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v1.2.3")
    cli.check_scip_version()


def test_check_scip_version_tolerates_unparseable(monkeypatch):
    """An unrecognized format must not block indexing outright."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "weird build")
    cli.check_scip_version()  # must not raise


def test_zoekt_index_cmd_uses_git_index_with_pinned_flags(tmp_path: Path):
    """-incremental=false because the default would refuse to repair an
    already-published incomplete shard. -submodules=false because submodules
    are indexed as their own slugs, and including them here would both
    duplicate content and make the coverage expectation unreachable."""
    from jarvis.index_cli import _zoekt_index_cmd

    cmd = _zoekt_index_cmd(tmp_path / ".zoekt", tmp_path / "repo")

    assert cmd[0] == "zoekt-git-index"
    assert "-incremental=false" in cmd
    assert "-submodules=false" in cmd
    assert "-meta" not in cmd, "zoekt-git-index has no -meta flag"
    assert cmd[-1] == str(tmp_path / "repo")


def test_write_zoekt_meta_is_gone():
    """Replaced by _pin_zoekt_repo_name — zoekt-git-index takes no -meta."""
    import jarvis.index_cli as index_cli

    assert not hasattr(index_cli, "_write_zoekt_meta")


def test_run_returns_the_completed_process(tmp_path: Path):
    """Coverage parsing needs the indexer's stderr, which _run previously
    discarded on success."""
    from jarvis.index_cli import _run

    result = _run(["echo", "hello"], cwd=tmp_path, step="echo")

    assert result.stdout.strip() == "hello"


def test_run_turns_a_missing_binary_into_a_setup_remedy(tmp_path: Path):
    """A missing binary raised a bare FileNotFoundError, which says nothing
    about how to fix it. Matters most for the zoekt-index -> zoekt-git-index
    rename: existing installs must re-run setup.sh to get the new binary."""
    from jarvis.index_cli import IndexingError, _run

    with pytest.raises(IndexingError, match="setup.sh"):
        _run(["definitely-not-a-real-binary"], cwd=tmp_path, step="fake step")


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_zoekt_shard_is_named_by_slug_not_directory(tmp_path: Path):
    """Regression: searchCode(repo=<slug>) silently returned zero hits.

    Zoekt names shards from the directory basename unless -meta says
    otherwise, so a slug differing from the directory produced a shard the
    r: filter could never match.
    """
    repo_dir = tmp_path / "directoryname"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    index_repo(repo_dir, slug="totally-different-slug", root=data_root)

    shards = list((data_root / ".zoekt").glob("*.zoekt"))
    names = [s.name for s in shards]
    assert any(n.startswith("totally-different-slug") for n in names), names
    assert not any(n.startswith("directoryname") for n in names), names


def test_remove_zoekt_shards_deletes_matching_shard(tmp_path: Path, monkeypatch):
    from jarvis.index_cli import _remove_zoekt_shards

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "myslug_v16.00000.zoekt").write_bytes(b"x")
    (zoekt_dir / "myslug_v16.00001.zoekt").write_bytes(b"x")
    (zoekt_dir / "otherslug_v16.00000.zoekt").write_bytes(b"x")

    removed = _remove_zoekt_shards("myslug", root=tmp_path)

    assert len(removed) == 2
    assert not (zoekt_dir / "myslug_v16.00000.zoekt").exists()
    assert not (zoekt_dir / "myslug_v16.00001.zoekt").exists()
    assert (zoekt_dir / "otherslug_v16.00000.zoekt").exists(), "must not touch other repos"


def test_remove_zoekt_shards_does_not_prefix_match_other_slugs(tmp_path: Path):
    """"api" must not delete "api-gateway"'s shard."""
    from jarvis.index_cli import _remove_zoekt_shards

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "api_v16.00000.zoekt").write_bytes(b"x")
    (zoekt_dir / "api-gateway_v16.00000.zoekt").write_bytes(b"x")

    removed = _remove_zoekt_shards("api", root=tmp_path)

    assert len(removed) == 1
    assert not (zoekt_dir / "api_v16.00000.zoekt").exists()
    assert (zoekt_dir / "api-gateway_v16.00000.zoekt").exists()


def test_remove_zoekt_shards_is_safe_when_absent(tmp_path: Path):
    from jarvis.index_cli import _remove_zoekt_shards

    assert _remove_zoekt_shards("nothing-here", root=tmp_path) == []


def _make_index_db(path: Path, *, chunks: int, mentions: int, symbols: int = 3) -> None:
    """Minimal stand-in for the expt-convert schema, only the counted tables."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER);
        CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT);
        CREATE TABLE mentions (chunk_id INTEGER, symbol_id INTEGER, role INTEGER);
        """
    )
    conn.execute("INSERT INTO documents (relative_path) VALUES ('a.swift')")
    for i in range(symbols):
        conn.execute("INSERT INTO global_symbols (symbol) VALUES (?)", (f"sym{i}",))
    for i in range(chunks):
        conn.execute("INSERT INTO chunks (document_id) VALUES (1)")
    for i in range(mentions):
        conn.execute("INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (1, 1, 1)")
    conn.commit()
    conn.close()


def test_index_has_navigation_data_false_when_chunks_and_mentions_empty(tmp_path: Path):
    """The exact fingerprint of a converter that dropped every range."""
    from jarvis.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=0, mentions=0)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is False
    finally:
        conn.close()


def test_index_has_navigation_data_true_when_populated(tmp_path: Path):
    from jarvis.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=1, mentions=14)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is True
    finally:
        conn.close()


def test_index_has_navigation_data_false_when_only_chunks(tmp_path: Path):
    from jarvis.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=2, mentions=0)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is False
    finally:
        conn.close()


def test_reindex_forwards_stored_scheme_override(tmp_path: Path, monkeypatch):
    import argparse
    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="ios_theme_ui",
                    semantic_include=("src/gen",))
    registry.close()

    captured: dict = {}

    def fake_index_repo(path, *, slug=None, root=None, scheme=None, semantic_include=None,
                        language=None, search_only=None):
        captured["path"] = path
        captured["slug"] = slug
        captured["scheme"] = scheme
        captured["semantic_include"] = semantic_include
        captured["language"] = language
        return slug

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = cli._cmd_reindex(argparse.Namespace(slug="my-repo"))
    assert rc == 0
    assert captured["scheme"] == "ios_theme_ui"
    assert str(captured["path"]) == "/repos/my-repo"
    # _cmd_reindex forwards `list(repo.semantic_include)` to _cmd_index, which
    # converts it back to a tuple before calling index_repo — so the value
    # observed here (at the index_repo boundary) is a tuple, not a list.
    assert captured["semantic_include"] == ("src/gen",)


def test_forget_removes_the_zoekt_shard(tmp_path: Path, monkeypatch, capsys):
    """Regression: forgotten repos stayed searchable."""
    import argparse

    from jarvis import config
    from jarvis.index_cli import _cmd_forget
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("goneslug", str(tmp_path / "repo"), "python", "abc123", "indexed")
    registry.close()

    index_dir = config.index_dir("goneslug")
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index-abc123.db").write_bytes(b"x")

    zoekt_dir = config.data_dir() / ".zoekt"
    zoekt_dir.mkdir(parents=True, exist_ok=True)
    shard = zoekt_dir / "goneslug_v16.00000.zoekt"
    shard.write_bytes(b"x")

    rc = _cmd_forget(argparse.Namespace(slug="goneslug"))

    assert rc == 0
    assert not shard.exists(), "a forgotten repo must not stay searchable"
    assert not index_dir.exists()


def test_semantic_stage_skips_cleanly_when_extra_missing(monkeypatch, capsys):
    import sys

    import jarvis
    from jarvis.index_cli import _run_semantic_stage

    # Setting the sys.modules entry to None is the standard trick to force
    # the next `import` to raise ImportError -- but if some other test
    # module already ran `import jarvis.semantic` earlier in the suite,
    # Python has cached it as an attribute on the `jarvis` package
    # object, and `from jarvis import semantic` resolves via that
    # attribute without consulting sys.modules at all. Clearing the
    # attribute too makes this deterministic regardless of test order.
    monkeypatch.setitem(sys.modules, "jarvis.semantic", None)
    monkeypatch.delattr(jarvis, "semantic", raising=False)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    # Names the extra, so the warning stays useful to someone who installed the
    # CLI from PyPI rather than from a clone.
    assert "jarvis-mcp[semantic]" in capsys.readouterr().err


def test_semantic_stage_failure_is_nonfatal(monkeypatch, capsys):
    import jarvis.semantic as semantic_module
    from jarvis.index_cli import _run_semantic_stage

    def _boom(*args, **kwargs):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(semantic_module, "index_semantic", _boom)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    assert "still published" in capsys.readouterr().err


def test_semantic_stage_success_returns_true(monkeypatch):
    import jarvis.semantic as semantic_module
    from jarvis.index_cli import _run_semantic_stage
    from jarvis.semantic import SemanticIndexReport

    monkeypatch.setattr(semantic_module, "index_semantic",
                        lambda *a, **k: SemanticIndexReport(rows=5, files=1))
    assert _run_semantic_stage(Path("/repo"), "slug", None) is True


def test_forget_removes_lance_table_dir(tmp_path: Path, monkeypatch):
    import argparse

    from jarvis.index_cli import _cmd_forget

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("gone", "/p", "python", None, "indexed")
    registry.close()
    lance_dir = tmp_path / "lancedb" / "gone.lance"
    lance_dir.mkdir(parents=True)
    (lance_dir / "data.bin").write_text("x")

    assert _cmd_forget(argparse.Namespace(slug="gone")) == 0
    assert not lance_dir.exists()


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_builds_semantic_index_and_searches(tmp_path: Path, monkeypatch):
    """Full pipeline: chunk -> embed -> LanceDB -> hybrid search, exercised
    with a small real model (not the default) so the test stays minutes-not-
    hours on first download; the pipeline under test is identical either way.
    """
    pytest.importorskip("lancedb")
    pytest.importorskip("tree_sitter_language_pack")
    pytest.importorskip("sentence_transformers")

    monkeypatch.setenv("JARVIS_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import jarvis.embeddings as embeddings_module
    monkeypatch.setattr(embeddings_module, "_default", None)  # reset singleton

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, slug="semfix", root=data_root)

    from jarvis.semantic import semantic_search
    result = semantic_search(slug, "function definition", root=data_root)
    assert result["total"] >= 1
    assert all("filePath" in r for r in result["results"])


def test_semantic_include_flag_reaches_index_repo_as_a_tuple(tmp_path, monkeypatch):
    """The CLI collects repeated --semantic-include into a list; index_repo
    takes a tuple. Registry persistence itself is covered in test_registry.py."""
    import argparse

    from jarvis import index_cli

    captured = {}

    def _fake_index_repo(repo_path, *, slug=None, root=None, scheme=None,
                         semantic_include=None, language=None, search_only=None):
        captured["semantic_include"] = semantic_include
        return "myrepo"

    monkeypatch.setattr(index_cli, "index_repo", _fake_index_repo)
    args = argparse.Namespace(path=str(tmp_path), slug="myrepo", scheme=None,
                              semantic_include=["src/gen", "vendor/pb"])
    assert index_cli._cmd_index(args) == 0
    assert captured["semantic_include"] == ("src/gen", "vendor/pb")


def test_semantic_report_names_every_skipped_file(capsys):
    from jarvis.index_cli import _print_semantic_report
    from jarvis.semantic import SemanticIndexReport, SkippedFile

    report = SemanticIndexReport(
        rows=95, files=15,
        skipped=(SkippedFile("src/jarvis/scip_pb2.py", "banner:do not edit"),),
        truncated=3,
    )
    _print_semantic_report(report)
    err = capsys.readouterr().err
    assert "semantic: 95 chunks from 15 files" in err
    assert "semantic: skipped src/jarvis/scip_pb2.py (banner:do not edit)" in err
    assert "3 chunks exceeded" in err


def test_semantic_report_omits_truncation_line_when_zero(capsys):
    from jarvis.index_cli import _print_semantic_report
    from jarvis.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=0))
    err = capsys.readouterr().err
    assert "exceeded" not in err
    assert "could not measure" not in err


def test_semantic_report_notes_unmeasured_truncation(capsys):
    from jarvis.index_cli import _print_semantic_report
    from jarvis.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=None))
    assert "could not measure truncation" in capsys.readouterr().err


def test_report_prints_token_percentiles(capsys):
    from jarvis.index_cli import _print_semantic_report
    from jarvis.semantic import SemanticIndexReport, TokenStats

    _print_semantic_report(SemanticIndexReport(
        rows=95, files=15, truncated=0, token_stats=TokenStats(180, 410, 498)))
    assert "chunk tokens p50=180 p90=410 max=498" in capsys.readouterr().err


def test_report_omits_percentiles_when_absent(capsys):
    from jarvis.index_cli import _print_semantic_report
    from jarvis.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=0))
    assert "chunk tokens" not in capsys.readouterr().err


def test_report_prints_prefix_warning(capsys):
    from jarvis.index_cli import _print_semantic_report
    from jarvis.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(
        rows=10, files=2, truncated=0, prefix_warning="model X is not in the map"))
    assert "model X is not in the map" in capsys.readouterr().err


def test_indexer_by_language_covers_every_supported_language():
    from jarvis.index_cli import _INDEXER_BY_LANGUAGE

    assert sorted(_INDEXER_BY_LANGUAGE) == ["java", "python", "swift", "typescript"]
    assert _INDEXER_BY_LANGUAGE["python"] == ["scip-python", "index"]
    assert _INDEXER_BY_LANGUAGE["swift"] == ["scip-swift"]


def test_resolve_language_preserves_stored_override_when_none_given(tmp_path: Path):
    from jarvis.index_cli import _resolve_language

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    assert _resolve_language(registry, "my-repo", language=None) == "python"
    registry.close()


def test_resolve_language_prefers_explicit_value_over_stored(tmp_path: Path):
    from jarvis.index_cli import _resolve_language

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    assert _resolve_language(registry, "my-repo", language="java") == "java"
    registry.close()


def test_resolve_language_returns_none_for_unknown_slug(tmp_path: Path):
    from jarvis.index_cli import _resolve_language

    registry = Registry(tmp_path / "registry.db")
    assert _resolve_language(registry, "nope", language=None) is None
    registry.close()


def test_index_repo_language_override_skips_detection(tmp_path: Path, monkeypatch):
    """An override means "do not guess" -- detect_language must not run at
    all, so a repo whose plurality says otherwise still gets the forced
    language, and the registry records both the effective language and the
    fact that it was forced."""
    import jarvis.index_cli as cli

    for i in range(5):
        (tmp_path / f"f{i}.ts").write_text("export const x = 1;\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    def boom(_repo_path):
        raise AssertionError("detect_language must not be called when overridden")

    monkeypatch.setattr(cli, "detect_language", boom)
    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    captured: dict = {}

    def fake_run(cmd, *, cwd, step, env=None):
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
    import jarvis.index_cli as cli

    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "App.swift").write_text("let x = 1\n")
    (tmp_path / "App.xcodeproj").mkdir()
    (tmp_path / "App.xcodeproj" / "project.pbxproj").write_text("// stub\n")
    _init_git_repo(tmp_path)

    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    captured: dict = {}

    def fake_run(cmd, *, cwd, step, env=None):
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


def test_index_repo_raises_unsupported_language_for_stale_registry_override(
    tmp_path: Path, monkeypatch
):
    """`argparse`'s `choices=` only guards a fresh `--language` value typed
    by a user -- it does not guard a value already persisted in
    `registry.db` from an earlier version (e.g. a language a future release
    stops supporting, or a hand-edited row). `jarvis reindex` reaches
    `index_repo` with that stale value; it must raise `UnsupportedLanguageError`
    (caught at the CLI boundary), not a bare `KeyError`."""
    import jarvis.index_cli as cli

    (tmp_path / "app.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    data_root = tmp_path / "data"
    registry = Registry(config.data_dir(data_root) / "registry.db")
    registry.upsert("stale-lang", str(tmp_path), "cobol", "abc123", "indexed",
                    language_override="cobol")
    registry.close()

    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    with pytest.raises(UnsupportedLanguageError, match="cobol"):
        cli.index_repo(tmp_path, slug="stale-lang", root=data_root)


def test_cmd_reindex_reports_stale_language_override_as_error(tmp_path: Path, monkeypatch, capsys):
    """Same as above, exercised through the actual `reindex` CLI path."""
    import argparse
    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "app.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("stale-lang", str(tmp_path), "cobol", "abc123", "indexed",
                    language_override="cobol")
    registry.close()

    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    rc = cli._cmd_reindex(argparse.Namespace(slug="stale-lang"))

    assert rc == 1
    assert "cobol" in capsys.readouterr().err


def test_index_parser_accepts_language_flag():
    from jarvis.index_cli import build_parser

    args = build_parser().parse_args(["index", "/repos/x", "--language", "python"])
    assert args.language == "python"


def test_watch_parser_accepts_language_flag():
    from jarvis.index_cli import build_parser

    args = build_parser().parse_args(["watch", "/repos/x", "--language", "swift"])
    assert args.language == "swift"


def test_index_parser_rejects_unknown_language():
    from jarvis.index_cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["index", "/repos/x", "--language", "cobol"])


def test_reindex_forwards_stored_language_override(tmp_path: Path, monkeypatch):
    import argparse
    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed",
                    language_override="python")
    registry.close()

    captured: dict = {}

    def fake_index_repo(path, *, slug=None, root=None, scheme=None, semantic_include=None,
                        language=None, search_only=None):
        captured["language"] = language
        return slug

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = cli._cmd_reindex(argparse.Namespace(slug="my-repo"))
    assert rc == 0
    assert captured["language"] == "python"


def test_cmd_index_reports_non_git_directory_as_error(tmp_path: Path, monkeypatch, capsys):
    """NotAGitRepositoryError must be caught at the CLI boundary and printed,
    not escape as a traceback.

    Monkeypatches `index_repo` to raise `NotAGitRepositoryError` directly, so
    this test fails if that exception type were ever removed from
    `_cmd_index`'s except tuple -- unlike asserting on a real non-git
    directory's error text, which would keep passing on substring luck even
    with the type removed (git's own stderr for a plain `IndexingError`
    happens to contain the same phrase). The real end-to-end path -- that a
    non-git directory actually raises `NotAGitRepositoryError`, not a
    misdiagnosed "no commits yet" -- is pinned separately by
    `test_git_head_raises_not_a_git_repository_for_non_git_directory`."""
    import argparse
    import jarvis.index_cli as cli

    def fake_index_repo(*args, **kwargs):
        raise cli.NotAGitRepositoryError(f"{tmp_path} is not a git repository (fake)")

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = cli._cmd_index(argparse.Namespace(
        path=str(tmp_path), slug=None, scheme=None, semantic_include=None, language=None,
    ))

    assert rc == 1
    assert "not a git repository" in capsys.readouterr().err


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
    monkeypatch.setattr("jarvis.index_cli._run", lambda cmd, **kw: _fake_completed_process(cmd)
                        if cmd[0] in ("zoekt-git-index", "git") else _boom())
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
    monkeypatch.setattr("jarvis.index_cli._run", lambda cmd, **kw: _fake_completed_process(cmd))
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root, search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.language == UNKNOWN_LANGUAGE
    finally:
        registry.close()


def test_search_only_reindex_reuses_persisted_flag_for_an_unknown_language_repo(
    tmp_path: Path, monkeypatch
):
    """Regression test for an ordering bug: `search_only` must be resolved
    from the registry BEFORE `detect_language()` runs, not after (alongside
    `scheme`/`semantic_include`) -- the `except UnsupportedLanguageError`
    branch reads it, and a `jarvis reindex`/`watch` of a persisted
    search-only repo never re-passes `--search-only` (search_only=None),
    relying entirely on the persisted value. With the bug, this second call
    would see the raw, unresolved `None` and incorrectly re-raise instead of
    reusing the persisted `search_only=True`."""
    from jarvis.index_cli import SEARCH_ONLY_STATUS, UNKNOWN_LANGUAGE, index_repo

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.go").write_text("package main\n\nfunc main() {}\n")
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", lambda cmd, **kw: _fake_completed_process(cmd))
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    # First run: explicit --search-only, establishing the persisted row with
    # language=UNKNOWN_LANGUAGE (the repo has no SCIP-indexable source).
    slug = index_repo(repo_dir, root=data_root, search_only=True)

    # Second run: the reindex/watch case -- `--search-only` is omitted
    # (search_only=None). detect_language() still raises for real, since the
    # repo still has no supported source; this must not propagate.
    slug_again = index_repo(repo_dir, root=data_root, search_only=None)
    assert slug_again == slug

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.language == UNKNOWN_LANGUAGE
        assert entry.search_only is True
        assert entry.status == SEARCH_ONLY_STATUS
    finally:
        registry.close()

    assert not (config.index_dir(slug, data_root) / "current").exists()


def test_search_only_publish_retires_a_previously_published_scip_index(tmp_path: Path, monkeypatch):
    """The scenario this branch was built for: a repo that indexed fine
    before (e.g. Kotlin on 2.2.0) degrades to search-only on a later run
    (e.g. after a bump to 2.2.20 hits the ABI-mismatch signature). The OLD
    SCIP pointer, its versioned db/metadata files, and its graph edges must
    not survive — otherwise navigation tools would keep silently answering
    from the stale index instead of raising IndexNotFoundError."""
    from jarvis.graph import GraphStore
    from jarvis.index_cli import SEARCH_ONLY_STATUS, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"
    slug = config.repo_slug(repo_dir.name)

    # Hand-build a previously published SCIP index for this repo, rather
    # than running the real indexer/scip binaries (unavailable in a
    # unit-test environment) -- a fake pointer + versioned db/metadata is
    # exactly what a prior successful `index_repo()` run would have left
    # under `config.index_dir()`.
    target_dir = config.index_dir(slug, data_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    old_db = target_dir / "index-oldsha.db"
    old_metadata = target_dir / "index-oldsha.metadata.json"
    old_db.write_text("fake old index", encoding="utf-8")
    old_metadata.write_text("{}", encoding="utf-8")
    (target_dir / "current").write_text("index-oldsha.db", encoding="utf-8")

    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert(slug, str(repo_dir), "java", "oldsha", "indexed")
    finally:
        registry.close()

    # And a graph edge from this repo's own package to some other repo's
    # package, mirroring what `populate_graph_for_repo` would have recorded
    # for the old index.
    graph_store = GraphStore(data_root / "registry.db")
    try:
        local_id = graph_store.upsert_package(repo=slug, name="maven:demo:app")
        other_id = graph_store.upsert_package(repo="other-repo", name="maven:demo:dep")
        graph_store.add_edge(from_package_id=local_id, to_package_id=other_id)
        assert graph_store.get_dependents(other_id), "edge must exist before retirement"
    finally:
        graph_store.close()

    monkeypatch.setattr("jarvis.index_cli.detect_language", lambda p: ("java", ["nope"]))
    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", lambda cmd, **kw: _fake_completed_process(cmd)
                        if cmd[0] in ("zoekt-git-index", "git") else (_ for _ in ()).throw(
                            AssertionError("the SCIP indexer must not run in search-only mode")))
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug_again = index_repo(repo_dir, root=data_root, search_only=True)
    assert slug_again == slug

    assert not (target_dir / "current").exists()
    assert not old_db.exists()
    assert not old_metadata.exists()

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
    finally:
        registry.close()

    graph_store = GraphStore(data_root / "registry.db")
    try:
        assert not graph_store.get_dependents(other_id), "outgoing edge must be retired"
        # The package row itself must survive -- only outgoing edges are
        # cleared, matching `populate_graph_for_repo`'s own rebuild pattern.
        assert graph_store.get_package(local_id) is not None
    finally:
        graph_store.close()


def test_bash_shim_failure_detected():
    from jarvis.index_cli import _bash_shim_failure

    assert _bash_shim_failure(
        "Fatal error compiling: Could not retrieve version from "
        "/tmp/scip-java1/bin/javac. Exit code 1, Output: "
        "/tmp/scip-java1/bin/javac: line 38: LAUNCHER_ARGS[@]: unbound variable"
    )


def test_bash_shim_failure_ignores_other_output():
    from jarvis.index_cli import _bash_shim_failure

    assert not _bash_shim_failure("error: No SCIP shards found")


def test_bash_shim_failure_does_not_publish_search_only(tmp_path: Path, monkeypatch):
    """A fixable env problem must NOT persist search_only=1.

    --search-only is store_true/default=None: settable, never clearable. If this
    downgraded, a user who then installed bash would silently keep getting no
    navigation, escapable only via `jarvis forget`.
    """
    from jarvis.index_cli import IndexingError, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(JAVA_FIXTURE_REPO, repo_dir)
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

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

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


def test_bash_shim_failure_is_gated_on_java_language(tmp_path: Path, monkeypatch):
    """The bash-shim remedy is scip-java-specific: a non-Java indexer that
    happened to emit the same two substrings by coincidence must not get the
    scip-java remedy message wrapped around its error."""
    from jarvis.index_cli import IndexingError, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    original_message = (
        "Fatal error compiling: Could not retrieve version from "
        "/tmp/scip-java1/bin/javac. Exit code 1, Output: "
        "/tmp/scip-java1/bin/javac: line 38: LAUNCHER_ARGS[@]: unbound variable"
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(original_message)
        return None

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)

    with pytest.raises(IndexingError) as excinfo:
        index_repo(repo_dir, root=data_root)

    assert str(excinfo.value) == original_message, "must not get the scip-java-specific bash remedy"


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
        # " index" (leading space) matches only the indexer step (e.g.
        # "scip-python index"), not the later "zoekt-git-index" step that
        # _publish_search_only must still be allowed to run for real.
        if step.endswith(" index"):
            raise IndexingError("error: No SCIP shards found. scip-java cannot index this")
        return _fake_completed_process(cmd)

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
        # " index" (leading space) matches only the indexer step (e.g.
        # "scip-python index"), not the later "zoekt-git-index" step that
        # _publish_search_only must still be allowed to run for real.
        if step.endswith(" index"):
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
        assert any(
            "demo/Greeter#greet()" in row[0]
            for row in conn.execute("SELECT symbol FROM global_symbols")
        )
    finally:
        conn.close()

    assert symbols > 0, "no symbols — the indexer produced nothing navigable"
    assert chunks > 0 and mentions > 0, "symbols without occurrence ranges"


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_bare_name_resolution_against_a_real_index(tmp_path: Path):
    """End-to-end: a bare name resolves through a genuinely converted SCIP
    index, not a hand-built fixture. Guards against grammar assumptions that
    hold for synthetic symbols but not for real indexer output."""
    from jarvis.query import QueryService

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, root=data_root)

    cache = config.new_connection_cache(data_root)
    service = QueryService(cache)

    conn, _ = config.get_connection(cache, slug)
    path = conn.execute("SELECT relative_path FROM documents LIMIT 1").fetchone()[0]

    entries, _ = service.get_document_symbols(slug, path)
    assert entries, "fixture repo produced no document symbols"
    target = entries[0]

    parsed_name = target.displayName
    assert parsed_name, "displayName should be populated from the parser"

    resolved = service.resolve_symbol(slug, parsed_name)
    assert resolved == target.symbol

    # Idempotence: rung 1 returns a full symbol unchanged.
    assert service.resolve_symbol(slug, resolved) == resolved


def test_tracked_blob_count_counts_tracked_files(tmp_path: Path):
    from jarvis.index_cli import _tracked_blob_count

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _init_git_repo(tmp_path)

    assert _tracked_blob_count(tmp_path) == 2


def test_tracked_blob_count_ignores_untracked_files(tmp_path: Path):
    from jarvis.index_cli import _tracked_blob_count

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    (tmp_path / "untracked.py").write_text("z = 3\n")

    assert _tracked_blob_count(tmp_path) == 1


def test_tracked_blob_count_excludes_submodule_gitlinks(tmp_path: Path):
    """A submodule is one mode-160000 gitlink entry, not a file. Because
    zoekt-git-index runs with -submodules=false it never descends into it, so
    counting the gitlink would make the expectation permanently unreachable."""
    from jarvis.index_cli import _tracked_blob_count

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
    from jarvis.index_cli import NotAGitRepositoryError, _tracked_blob_count

    with pytest.raises(NotAGitRepositoryError):
        _tracked_blob_count(tmp_path)


def _git_config_value(repo_path: Path, key: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "config", "--get", key],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def test_pin_zoekt_repo_name_sets_the_slug(tmp_path: Path):
    from jarvis.index_cli import _pin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    _pin_zoekt_repo_name(tmp_path, "myslug")

    assert _git_config_value(tmp_path, "zoekt.name") == "myslug"


def test_pin_zoekt_repo_name_is_idempotent(tmp_path: Path):
    from jarvis.index_cli import _pin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    _pin_zoekt_repo_name(tmp_path, "first")
    _pin_zoekt_repo_name(tmp_path, "second")

    assert _git_config_value(tmp_path, "zoekt.name") == "second"


def test_pin_zoekt_repo_name_raises_for_non_git_directory(tmp_path: Path):
    """Must fail loudly: an unpinned name makes zoekt derive one from the
    origin remote URL, and `r:<slug>` then returns zero hits with no error."""
    from jarvis.index_cli import IndexingError, _pin_zoekt_repo_name

    with pytest.raises(IndexingError):
        _pin_zoekt_repo_name(tmp_path, "myslug")


def test_unpin_zoekt_repo_name_removes_the_key(tmp_path: Path):
    from jarvis.index_cli import _pin_zoekt_repo_name, _unpin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    _pin_zoekt_repo_name(tmp_path, "myslug")

    _unpin_zoekt_repo_name(tmp_path)

    assert _git_config_value(tmp_path, "zoekt.name") is None


def test_unpin_zoekt_repo_name_tolerates_a_missing_key(tmp_path: Path):
    """git config --unset exits 5 when the key is absent. Repos indexed
    before this change have no zoekt.name, and `forget` must still succeed."""
    from jarvis.index_cli import _unpin_zoekt_repo_name

    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    _unpin_zoekt_repo_name(tmp_path)  # must not raise


def test_unpin_zoekt_repo_name_tolerates_a_missing_directory(tmp_path: Path):
    """`forget` must work after the user has deleted the repo from disk."""
    from jarvis.index_cli import _unpin_zoekt_repo_name

    _unpin_zoekt_repo_name(tmp_path / "gone")  # must not raise


def test_forget_unpins_the_zoekt_repo_name(tmp_path: Path, monkeypatch, capsys):
    import argparse

    from jarvis import config
    from jarvis.index_cli import _cmd_forget, _pin_zoekt_repo_name
    from jarvis.registry import Registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _init_git_repo(repo)
    _pin_zoekt_repo_name(repo, "myslug")

    data_dir = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_dir))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("myslug", str(repo), "python", "abc", "indexed")
    finally:
        registry.close()

    assert _cmd_forget(argparse.Namespace(slug="myslug")) == 0
    assert _git_config_value(repo, "zoekt.name") is None


def test_parse_indexed_file_count_reads_the_indexer_log():
    from jarvis.index_cli import _parse_indexed_file_count

    output = (
        "2026/08/03 22:26:52 attempting to index 133 total files "
        "(0 via cat-file, 133 via go-git)\n"
        "2026/08/03 22:26:53 finished shard /x/jarvis_v16.00000.zoekt: "
        "6408345 index bytes (overhead 3.2), 133 files processed\n"
    )

    assert _parse_indexed_file_count(output) == 133


def test_parse_indexed_file_count_returns_none_on_unknown_format():
    """An upstream log change must degrade to "unknown", never fail a publish."""
    from jarvis.index_cli import _parse_indexed_file_count

    assert _parse_indexed_file_count("nothing recognisable here") is None


def test_warn_on_coverage_shortfall_warns(capsys):
    from jarvis.index_cli import _warn_on_coverage_shortfall

    _warn_on_coverage_shortfall("myslug", 133, "attempting to index 100 total files")

    assert "myslug" in capsys.readouterr().err


def test_warn_on_coverage_shortfall_is_quiet_when_complete(capsys):
    from jarvis.index_cli import _warn_on_coverage_shortfall

    _warn_on_coverage_shortfall("myslug", 133, "attempting to index 133 total files")

    assert capsys.readouterr().err == ""


def test_warn_on_coverage_shortfall_is_quiet_when_unparseable(capsys):
    from jarvis.index_cli import _warn_on_coverage_shortfall

    _warn_on_coverage_shortfall("myslug", 133, "unrecognised")

    assert capsys.readouterr().err == ""


def test_sweep_zoekt_tmp_orphans_removes_only_this_slugs_temp_files(tmp_path: Path):
    """A killed zoekt run leaves a .tmp that is never usable and never
    cleaned up; 545 MB of them accumulated once."""
    from jarvis.index_cli import _sweep_zoekt_tmp_orphans

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
    from jarvis.index_cli import _sweep_zoekt_tmp_orphans

    assert _sweep_zoekt_tmp_orphans("nothing-here", root=tmp_path) == []


def test_sweep_zoekt_tmp_orphans_survives_unlink_errors(tmp_path: Path, monkeypatch):
    """The sweep runs between a successful zoekt-git-index run and the
    atomic publish -- an EACCES/EBUSY/EPERM on unlink() must never
    propagate and discard an otherwise-successful publish."""
    from jarvis.index_cli import _sweep_zoekt_tmp_orphans

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "myslug_v16.00000.zoekt.tmp").write_bytes(b"x")

    def _raise_permission_error(self: Path) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(Path, "unlink", _raise_permission_error)

    removed = _sweep_zoekt_tmp_orphans("myslug", root=tmp_path)

    assert removed == []
    assert (zoekt_dir / "myslug_v16.00000.zoekt.tmp").exists(), "unremovable file must be left in place"


@pytest.mark.integration
def test_zoekt_git_index_excludes_gitignored_content(tmp_path: Path, monkeypatch):
    """The whole point: gitignored junk is absent by construction, with no
    denylist to maintain.

    The tracked-file count and the indexer's own log line are real signals,
    but neither directly proves the junk is unreachable via search (or that
    the tracked content is findable) -- so this also spins up a real
    zoekt-webserver and queries it, the same way
    test_get_index_status_reports_incomplete_after_a_shard_is_deleted does.

    Also covers the healthy/complete-coverage case of `_search_coverage_fields`
    -- the shard-deletion test below only covers the incomplete case.
    """
    if shutil.which("zoekt-git-index") is None:
        pytest.skip("zoekt-git-index not on PATH")
    if shutil.which("zoekt-webserver") is None:
        pytest.skip("zoekt-webserver not on PATH")

    from jarvis import config, server
    from jarvis.index_cli import (
        _pin_zoekt_repo_name, _tracked_blob_count, _zoekt_index_cmd,
    )
    from jarvis.registry import Registry
    from jarvis.search import ZoektLifecycle, search_zoekt

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

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("covslug", str(repo), "python", "abc", "indexed")
        registry.mark_tracked_files("covslug", _tracked_blob_count(repo))
    finally:
        registry.close()

    lifecycle = ZoektLifecycle(index_dir=zoekt_dir, data_dir=tmp_path, port=6078)
    try:
        base_url = lifecycle.ensure_running()
        assert search_zoekt(base_url, "junktoken_beta") == [], "gitignored content must not be searchable"
        assert len(search_zoekt(base_url, "sourcetoken_alpha")) >= 1, "tracked content must be searchable"

        monkeypatch.setattr(server, "_zoekt_base_url_if_running", lambda: base_url)
        fields = server._search_coverage_fields("covslug")
        assert fields["searchCoverage"]["complete"] is True
    finally:
        lifecycle.stop()


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

    from jarvis import config, server
    from jarvis.index_cli import _pin_zoekt_repo_name, _tracked_blob_count
    from jarvis.registry import Registry
    from jarvis.search import ZoektLifecycle

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

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
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


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_symbol_search_finds_definitions_in_real_index(tmp_path: Path):
    """The semanticSearch symbol signal, end-to-end against a real published
    index: an NL query naming the fixture's class returns its definition."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, root=data_root)

    target_dir = config.index_dir(slug, data_root)
    pointer = (target_dir / "current").read_text(encoding="utf-8").strip()
    conn = sqlite3.connect(f"file:{target_dir / pointer}?mode=ro&immutable=1", uri=True)
    try:
        from jarvis.symbol_search import search_symbols

        hits = search_symbols(conn, "where is the Greeter class defined")
        assert hits, "expected at least one symbol hit for 'Greeter'"
        top = hits[0]
        assert top.file_path == "greeter.py"
        assert top.dotted_path.endswith("Greeter")
        assert top.start_line == 9  # 1-based: `class Greeter:` is on line 9

        # A method query resolves too, proving defn_enclosing_ranges depth.
        method_hits = search_symbols(conn, "say_hi")
        say_hi_hit = next(h for h in method_hits if h.dotted_path.endswith("say_hi"))
        assert say_hi_hit.start_line == 10  # 1-based: `def say_hi(...)` is on line 10
    finally:
        conn.close()


def test_hard_failed_index_persists_cause_and_full_stderr(tmp_path: Path, monkeypatch):
    """D-02/D-03/D-05: a hard-failed run must leave a registry row carrying
    the origin slug, a one-line classified reason, and the COMPLETE failure
    text verbatim -- persistence is unbounded, so a >1000-char payload must
    survive a fresh read without truncation."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    marker = "BOOM-" + "x" * 1200
    carrier = (
        "scip-python index failed (scip-python index --output index.scip):\n"
        f"stdout noise\n{marker}\ntrailing stderr line\n"
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        # " index" (leading space) matches only the indexer step, so the
        # failure happens exactly where a real broken build would.
        if step.endswith(" index"):
            raise IndexingError(carrier)
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
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason  # non-empty...
        assert "\n" not in entry.status_reason  # ...and a single line (D-03)
        assert entry.status_reason.startswith("scip-python index failed")
        assert marker in entry.status_stderr
        assert entry.status_stderr == carrier  # verbatim, untruncated (D-02)
    finally:
        registry.close()


def test_cmd_status_explains_a_failed_repo(tmp_path: Path, monkeypatch, capsys):
    """STAT-01/D-12: `jarvis status` must explain what happened and how to
    recover, deriving the recovery command from the origin at read time."""
    import argparse

    from jarvis.index_cli import _cmd_status
    from jarvis.registry import ORIGIN_FAILED_HARD, Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    registry = Registry(data_root / "registry.db")
    try:
        registry.record_failure(
            "failing", "/abs/path/failing", "python", ORIGIN_FAILED_HARD,
            "scip-python index failed (scip-python)",
            "scip-python index failed (scip-python):\nfull stderr text",
        )
    finally:
        registry.close()

    rc = _cmd_status(argparse.Namespace(slug="failing"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "origin: failed_hard" in out
    assert "cause: scip-python index failed (scip-python)" in out
    assert "recovery: jarvis index /abs/path/failing" in out
