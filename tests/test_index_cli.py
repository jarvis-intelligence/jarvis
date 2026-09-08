"""Unit tests for language detection, plus an end-to-end integration test
against `tests/fixtures/mini_py_repo/` using the real scip-python + scip
CLI binaries (marked `@pytest.mark.integration` — skipped if unavailable).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
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


def test_swift_indexer_cmd_appends_cache_dir_without_xcodeproj(tmp_path, monkeypatch):
    """The cache flag is NOT xcodebuild-only: swiftpm runs carry it too,
    pointing at exactly config.swift_cache_dir(slug)."""
    from jarvis import config
    from jarvis.index_cli import _swift_indexer_cmd

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    cache = config.swift_cache_dir("demo")
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, None, cache) == [
        "scip-swift", "--cache-dir", str(tmp_path / "cache" / "scip-swift" / "demo"),
    ]


def test_swift_indexer_cmd_adds_xcodebuild_then_cache_dir(tmp_path):
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    cache = tmp_path / "cache" / "scip-swift" / "demo"
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, None, cache) == [
        "scip-swift", "--build-tool", "xcodebuild", "--cache-dir", str(cache),
    ]


def test_swift_indexer_cmd_orders_scheme_before_cache_dir(tmp_path):
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "MyLib.xcodeproj").mkdir()
    cache = tmp_path / "cache" / "scip-swift" / "demo"
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, "ios_theme_ui", cache) == [
        "scip-swift", "--build-tool", "xcodebuild", "--scheme", "ios_theme_ui",
        "--cache-dir", str(cache),
    ]


def test_swift_indexer_cmd_ignores_scheme_without_xcodeproj(tmp_path):
    """A --scheme override is meaningless (and unsupported by scip-swift)
    under the swiftpm build tool, so it must not leak into the command
    when there's no checked-in Xcode project to justify xcodebuild."""
    from jarvis.index_cli import _swift_indexer_cmd

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    cache = tmp_path / "cache" / "scip-swift" / "demo"
    assert _swift_indexer_cmd(["scip-swift"], tmp_path, "ios_theme_ui", cache) == [
        "scip-swift", "--cache-dir", str(cache),
    ]


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


def test_check_scip_swift_version_rejects_v021(monkeypatch):
    """v0.2.1 predates the xcodebuild-dispatch fix (restored in 0.3.0):
    indexing an .xcodeproj repo through it would silently produce a broken
    index, so it must fail loudly with a recovery hint."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_swift_version_output", lambda: "0.2.1 (swift 6.1.0)")
    with pytest.raises(cli.IndexingError) as exc:
        cli.check_scip_swift_version()
    message = str(exc.value)
    assert "0.2.1" in message, "must name the installed version"
    assert "0.3.0" in message, "must state the required floor"
    assert "setup.sh" in message, "must name the recovery path"


def test_check_scip_swift_version_accepts_v030_real_format(monkeypatch):
    """scip-swift prints "0.3.0 (swift 6.2.4)" -- no v prefix. The floor
    must accept the real output format, not a v-prefixed stand-in."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_swift_version_output", lambda: "0.3.0 (swift 6.2.4)")
    cli.check_scip_swift_version()  # must not raise


def test_check_scip_swift_version_tolerates_unparseable(monkeypatch):
    """Warn-by-omission, same policy as check_scip_version: an unexpected
    build string must not block indexing outright."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_swift_version_output", lambda: "weird build")
    cli.check_scip_swift_version()  # must not raise


def test_scip_swift_version_output_missing_binary_names_setup_sh(monkeypatch):
    """A missing binary surfaces as IndexingError naming setup.sh, mirroring
    _scip_version_output's FileNotFoundError wrap."""
    import jarvis.index_cli as cli

    def _no_binary(*_args, **_kwargs):
        raise FileNotFoundError("scip-swift")

    monkeypatch.setattr(cli.subprocess, "run", _no_binary)
    with pytest.raises(cli.IndexingError, match="setup.sh"):
        cli._scip_swift_version_output()


def test_index_repo_non_swift_never_probes_scip_swift_version(tmp_path: Path, monkeypatch):
    """The floor gate is Swift-only (D-04): a non-Swift index must not need
    the scip-swift binary at all -- hosts without Swift repos may never
    install it (setup.sh skips it off darwin/arm64)."""
    import jarvis.index_cli as cli

    for i in range(3):
        (tmp_path / f"m{i}.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    def boom():
        raise AssertionError("scip-swift must not be probed for a non-swift repo")

    monkeypatch.setattr(cli, "_scip_swift_version_output", boom)
    monkeypatch.setattr(cli, "check_scip_version", lambda: None)

    def fake_run(cmd, *, cwd, step, env=None):
        raise cli.IndexingError("stop after the indexer command is built")

    monkeypatch.setattr(cli, "_run", fake_run)

    # If the probe fired, its AssertionError (not IndexingError) propagates
    # through the pre-pipeline failure wrap's bare `raise` and fails here.
    with pytest.raises(cli.IndexingError, match="stop after the indexer"):
        cli.index_repo(tmp_path, slug="pure-py", root=tmp_path / "data")


def test_index_repo_search_only_swift_never_probes_scip_swift_version(
    tmp_path: Path, monkeypatch
):
    """CR-01 sentinel, mirroring the non-Swift one above: a --search-only
    run never invokes the language indexer, and setup.sh deliberately never
    installs scip-swift off darwin/arm64 -- so probing the binary there
    would hard-fail zoekt-only indexing of Swift repos on every Linux host
    (and reindex of a persisted search-only Swift repo likewise)."""
    from jarvis.index_cli import SEARCH_ONLY_STATUS

    import jarvis.index_cli as cli

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n")
    (tmp_path / "App.swift").write_text("let x = 1\n")
    _init_git_repo(tmp_path)

    def boom():
        raise AssertionError("scip-swift must not be probed for a search-only run")

    monkeypatch.setattr(cli, "_scip_swift_version_output", boom)
    monkeypatch.setattr(cli, "check_scip_version", lambda: None)
    monkeypatch.setattr(
        cli, "_run",
        lambda cmd, **kw: _fake_completed_process(cmd)
        if cmd[0] in ("zoekt-git-index", "git") else boom(),
    )
    monkeypatch.setattr(cli, "_run_semantic_stage", lambda *a, **k: False)

    # With the probe misplaced, its AssertionError (not a clean publish)
    # propagates through the pre-pipeline failure wrap and fails here.
    slug = cli.index_repo(tmp_path, slug="swift-searchless",
                          root=tmp_path / "data", search_only=True)

    registry = Registry(config.data_dir(tmp_path / "data") / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
    finally:
        registry.close()

    assert not (config.index_dir(slug, tmp_path / "data") / "current").exists()


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
                        language=None, search_only=None, fallback_search_only=None):
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


def test_forget_removes_swift_cache_dir_sparing_siblings(tmp_path: Path, monkeypatch, capsys):
    """D-06: forgetting a repo removes everything jarvis stored for it --
    including the out-of-repo scip-swift cache -- and only its own: the
    sweep path is built solely from the slug, so a sibling slug's cache
    must survive untouched."""
    import argparse

    from jarvis import config
    from jarvis.index_cli import _cmd_forget
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("gone", str(tmp_path / "repo"), "swift", "abc123", "indexed")
    registry.close()

    cache = config.swift_cache_dir("gone")
    cache.mkdir(parents=True)
    (cache / "manifest.json").write_text("{}")

    sibling = config.swift_cache_dir("keeper")
    sibling.mkdir(parents=True)
    (sibling / "manifest.json").write_text("{}")

    rc = _cmd_forget(argparse.Namespace(slug="gone"))

    assert rc == 0
    assert not cache.exists(), "the forgotten repo's cache must die with it"
    assert sibling.exists(), "a sibling slug's cache must survive"
    assert "forgot gone" in capsys.readouterr().out


def test_forget_succeeds_when_swift_cache_dir_absent(tmp_path: Path, monkeypatch, capsys):
    """The cache legitimately may not exist (never-Swift repo, or cache
    never created) -- forget must stay non-fatal and print the forgot line."""
    import argparse

    from jarvis import config
    from jarvis.index_cli import _cmd_forget
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("plainpy", str(tmp_path / "repo"), "python", None, "indexed")
    registry.close()

    assert not config.swift_cache_dir("plainpy").exists()

    rc = _cmd_forget(argparse.Namespace(slug="plainpy"))

    assert rc == 0
    assert "forgot plainpy" in capsys.readouterr().out


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
                         semantic_include=None, language=None, search_only=None,
                         fallback_search_only=None):
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
    # Hermetic on machines without scip-swift on PATH: the phase-02 runtime
    # floor is the first statement of the swift branch and would raise
    # "scip-swift not found on PATH" before the indexer command is built.
    monkeypatch.setattr(cli, "check_scip_swift_version", lambda: None)

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
                        language=None, search_only=None, fallback_search_only=None):
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


def test_manual_search_only_publish_stamps_manual_origin(tmp_path: Path, monkeypatch):
    """STAT-01 SC2: a `--search-only` publish must record WHICH path
    produced it -- origin 'manual', no reason (the user asked for it),
    no stderr (success paths never carry one)."""
    from jarvis.index_cli import SEARCH_ONLY_STATUS, index_repo
    from jarvis.registry import ORIGIN_MANUAL

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run",
                        lambda cmd, **kw: _fake_completed_process(cmd))
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root, search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_MANUAL
        assert entry.status_reason is None
        assert entry.status_stderr is None
    finally:
        registry.close()


def test_signature_fallback_stamps_signature_origin_and_matched_reason(
    tmp_path: Path, monkeypatch
):
    """The signature path's status_reason is the matched per-signature
    REASON text verbatim (the human explanation) -- never a remedy
    (D-11: recovery stays the generic read-time per-origin mapping)."""
    from jarvis.index_cli import (
        SEARCH_ONLY_STATUS,
        _SEARCH_ONLY_SIGNATURES,
        IndexingError,
        index_repo,
    )
    from jarvis.registry import ORIGIN_SIGNATURE

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    carrier = (
        "e: java.lang.AbstractMethodError: "
        "org.jetbrains.kotlin.fir.analysis.checkers.expression.FirSafeCallChecker.check()"
    )
    kotlin_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == ("AbstractMethodError", "org.jetbrains.kotlin.fir")
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        # " index" (leading space) matches only the indexer step (e.g.
        # "scip-python index"), not the later "zoekt-git-index" step that
        # _publish_search_only must still be allowed to run for real.
        if step.endswith(" index"):
            raise IndexingError(carrier)
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
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
        assert entry.status_reason == kotlin_reason  # verbatim, not a paraphrase
    finally:
        registry.close()


def test_swift_no_build_system_signature_degrades_search_only(
    tmp_path: Path, monkeypatch
):
    """SWFT-04: a scip-swift failure carrying the captured no-build-system
    stderr degrades to search-only automatically (no opt-in, Kotlin/AGP
    parity) with origin 'signature' and the matched reason verbatim."""
    # Provenance: captured 2026-08-23 from scip-swift 0.3.0
    # (sha256 b0de7201…85a5, Xcode 26.3 / Swift 6.2.4) against a git repo
    # with tracked .swift sources and no Package.swift and no
    # .xcodeproj/.xcworkspace; carrier verified end-to-end through a real
    # `jarvis index` run (04-RESEARCH.md, out/jarvis-nobuildsystem.txt).
    from jarvis.index_cli import (
        SEARCH_ONLY_STATUS,
        _SEARCH_ONLY_SIGNATURES,
        IndexingError,
        index_repo,
    )
    from jarvis.registry import ORIGIN_SIGNATURE

    repo_dir = tmp_path / "repo"
    shutil.copytree(SWIFT_FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    # Exact captured stderr; only the repo path is rewritten to this test's
    # tmp repo (the original interpolated the capture workspace's path).
    carrier = (
        f"Error: Could not detect a build system at {repo_dir}: "
        "no Package.swift and no .xcodeproj/.xcworkspace found. "
        "Pass --build-tool swiftpm or --build-tool xcodebuild explicitly."
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(carrier)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    # Hermetic on machines without scip-swift on PATH (CI ubuntu legs): the
    # swift branch probes the binary pre-pipeline and would raise
    # "scip-swift not found on PATH" before the mocked indexer step.
    monkeypatch.setattr("jarvis.index_cli.check_scip_swift_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root)

    # Tripwire: looked up by the exact token tuple AFTER index_repo, so a
    # missing/drifted entry fails the test at the registry assertion (or as
    # StopIteration here) rather than masking the failure mode under test.
    swift_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == (
            "Could not detect a build system",
            "no Package.swift and no .xcodeproj/.xcworkspace found",
        )
    )

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
        assert entry.status_reason == swift_reason  # verbatim, not a paraphrase
    finally:
        registry.close()


def test_swift_no_index_store_signature_degrades_search_only(
    tmp_path: Path, monkeypatch
):
    """SWFT-04: the second captured scip-swift class — build succeeded but
    produced no IndexStore — degrades to search-only automatically with
    origin 'signature' and the matched reason verbatim."""
    # Provenance: captured 2026-08-23 from scip-swift 0.3.0 (sha256
    # b0de7201…85a5, Xcode 26.3 / Swift 6.2.4). Trigger shape is an
    # .xcodeproj whose target's Sources build phase is empty (build exits 0,
    # zero Swift compiled, argv --build-tool xcodebuild); the identical
    # wording occurs on the swiftpm backend with a different store path, so
    # one entry covers both backends.
    from jarvis.index_cli import (
        SEARCH_ONLY_STATUS,
        _SEARCH_ONLY_SIGNATURES,
        IndexingError,
        index_repo,
    )
    from jarvis.registry import ORIGIN_SIGNATURE

    repo_dir = tmp_path / "repo"
    shutil.copytree(SWIFT_FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    # Exact captured stderr; only the store path is rewritten to a tmp-style
    # path (the original interpolated the capture workspace's cache dir).
    store_path = tmp_path / "derived-data" / "Index.noindex" / "DataStore"
    carrier = (
        f"Error: Build succeeded but no IndexStore was produced at {store_path}. "
        "This commonly happens when the code being indexed cannot compile on this host "
        "(for example, Apple-platform-only imports such as UIKit/WatchKit/WidgetKit "
        "on a non-macOS host, or a missing SDK)."
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(carrier)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    # Hermetic on machines without scip-swift on PATH (CI ubuntu legs): the
    # swift branch probes the binary pre-pipeline and would raise
    # "scip-swift not found on PATH" before the mocked indexer step.
    monkeypatch.setattr("jarvis.index_cli.check_scip_swift_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root)

    # Tripwire: looked up by the exact token tuple AFTER index_repo, so a
    # missing/drifted entry fails the test loudly rather than masking the
    # failure mode under test.
    swift_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == ("Build succeeded but no IndexStore was produced",)
    )

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
        assert entry.status_reason == swift_reason  # verbatim, not a paraphrase
    finally:
        registry.close()


def test_swift_generic_build_failure_wrapper_never_matches():
    """SC3: the generic scip-swift build-failure wrappers must never match —
    every fixable Swift failure (repo bugs, environment, host-compile
    issues) keeps failing hard rather than being laundered into a silent
    search-only degrade. Keep-hard exhibits captured 2026-08-23 from
    scip-swift 0.3.0 (04-RESEARCH.md keep-hard table)."""
    from jarvis.index_cli import _search_only_reason

    wrapper_spm = "Error: 'swift build' failed with exit code 1:\n"
    for class_line in (
        "error: manifest parse error",
        "package 'demo' is using Swift tools version 999.0.0 "
        "but the installed version is 6.2.4",
        "The package does not contain a buildable target.",
        "no such module 'UIKit'",
    ):
        assert _search_only_reason(wrapper_spm + class_line) is None

    wrapper_xcodebuild = "Error: 'xcodebuild' failed with exit code 65:\n"
    assert _search_only_reason(wrapper_xcodebuild + "** BUILD FAILED **") is None


def test_empty_or_stdout_only_failure_carriers_never_match():
    """Empty-carrier probe: carriers with no class-specific stderr content
    must never match. Reconstructs the exact shape `_run` raises (step+argv
    header line, then stdout, then stderr) — the header embeds per-run cache
    and scratch paths, so this also pins that no token ever derives from it."""
    from jarvis.index_cli import _search_only_reason

    def _carrier(stdout: str = "", stderr: str = "") -> str:
        return (
            "scip-swift index failed (scip-swift --cache-dir "
            "/tmp/run-cache/scip-swift/slug --output /tmp/jarvis-index-ab12/index.scip):\n"
            f"{stdout}\n{stderr}"
        )

    # Header-only: empty stdout and stderr.
    assert _search_only_reason(_carrier()) is None
    # Whitespace-only stderr.
    assert _search_only_reason(_carrier(stderr="  \n\n")) is None
    # Stdout-only content on a non-zero exit.
    assert _search_only_reason(_carrier(stdout="Wrote 0 document(s) to /tmp/out")) is None


def test_search_only_reason_first_listed_match_wins():
    """Ordering probe: `_search_only_reason` scans `_SEARCH_ONLY_SIGNATURES`
    in list order, so when a carrier matches multiple entries the first
    listed wins. The two Swift entries are mutually exclusive in practice
    (a repo cannot both lack a build system and complete a build); this
    composite pins the ordering semantics itself, plus Kotlin precedence
    over Swift."""
    from jarvis.index_cli import _SEARCH_ONLY_SIGNATURES, _search_only_reason

    no_build_system_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == (
            "Could not detect a build system",
            "no Package.swift and no .xcodeproj/.xcworkspace found",
        )
    )
    kotlin_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == ("AbstractMethodError", "org.jetbrains.kotlin.fir")
    )

    # Both captured Swift error strings in one carrier: the no-build-system
    # entry precedes the IndexStore entry in the list, so it wins.
    swift_both = (
        "Error: Could not detect a build system at /tmp/repo: "
        "no Package.swift and no .xcodeproj/.xcworkspace found. "
        "Pass --build-tool swiftpm or --build-tool xcodebuild explicitly.\n"
        "Error: Build succeeded but no IndexStore was produced at "
        "/tmp/cache/derived-data/Index.noindex/DataStore."
    )
    assert _search_only_reason(swift_both) == no_build_system_reason

    # Kotlin text concatenated with a Swift string: the Kotlin entries
    # precede the Swift ones, so the Kotlin reason wins.
    kotlin_plus_swift = (
        "e: java.lang.AbstractMethodError: "
        "org.jetbrains.kotlin.fir.analysis.checkers.expression.FirSafeCallChecker.check()\n"
        "Error: Could not detect a build system at /tmp/repo: "
        "no Package.swift and no .xcodeproj/.xcworkspace found."
    )
    assert _search_only_reason(kotlin_plus_swift) == kotlin_reason


def test_failed_search_only_publish_records_a_full_failure_row(tmp_path: Path, monkeypatch):
    """A search-only run whose OWN publish fails must record a full failed
    row (origin 'failed_hard' + cause), not a bare status flip -- the row
    is what `jarvis status`/`reindex` need to explain and recover (D-05)."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    def _boom(*args, **kwargs):
        raise RuntimeError("zoekt-git-index exploded")

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._publish_search_only", _boom)

    with pytest.raises(IndexingError):
        index_repo(repo_dir, root=data_root, search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason  # non-empty one-liner (D-03)
        assert "zoekt-git-index exploded" in entry.status_stderr
    finally:
        registry.close()


def test_unmatched_indexer_failure_never_gains_a_signature_origin(tmp_path: Path, monkeypatch):
    """Fail-loudly guard: an unrecognized indexer error must hard-fail with
    origin 'failed_hard' -- never laundered into a search-only row or a
    signature origin it did not match (index_cli's design intent)."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError("error: could not resolve dependency com.example:thing:1.0")
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    with pytest.raises(IndexingError):
        index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
        assert entry.search_only is False
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert "could not resolve dependency" in entry.status_stderr
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
        assert search_zoekt(base_url, "junktoken_beta").hits == [], "gitignored content must not be searchable"
        assert len(search_zoekt(base_url, "sourcetoken_alpha").hits) >= 1, "tracked content must be searchable"

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


def test_cmd_list_marks_repo_health_at_a_glance(tmp_path: Path, monkeypatch, capsys):
    """D-08: glyphs in the status field (✗ failed / ◐ search-only / ✓
    everything else), reason one-liner as a 6th field on failed rows only;
    columns 1-5 keep the existing TSV order for scripts."""
    import argparse

    from jarvis.index_cli import _cmd_list
    from jarvis.registry import ORIGIN_FAILED_HARD, SEARCH_ONLY_STATUS, Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    registry = Registry(data_root / "registry.db")
    try:
        registry.record_failure("broken", "/repos/broken", "python",
                                ORIGIN_FAILED_HARD, "scip-python index failed",
                                "scip-python index failed:\nboom")
        registry.upsert("legacy", "/repos/legacy", "unknown", None,
                        SEARCH_ONLY_STATUS, search_only=True)
        registry.upsert("healthy", "/repos/healthy", "python", "abc123", "indexed")
    finally:
        registry.close()

    assert _cmd_list(argparse.Namespace()) == 0
    rows = {line.split("\t")[0]: line.split("\t")
            for line in capsys.readouterr().out.splitlines()}

    failed = rows["broken"]
    assert failed[1] == "✗ failed"
    assert failed[2] == "python"
    assert failed[3] == "-"
    assert failed[4] == "/repos/broken"
    assert failed[5] == "scip-python index failed"  # 6th field: reason only

    search_only = rows["legacy"]
    assert search_only[1] == "◐ search-only"
    assert len(search_only) == 5  # no trailing empty 6th field

    healthy = rows["healthy"]
    assert healthy[1] == "✓ indexed"
    assert healthy[3] == "abc123"
    assert len(healthy) == 5

def test_cmd_list_renders_degraded_rows_with_glyph_and_reason(tmp_path: Path, monkeypatch, capsys):
    """FALL-01: a degraded repo renders ◐ (search still answers) with the
    failure cause as a 6th TSV field — the same reason-first contract as
    failed rows, on a row that still self-heals on the next reindex."""
    import argparse

    from jarvis.index_cli import _cmd_list
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FALLBACK, Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert("crashed", "/repos/crashed", "python", "abc123",
                        DEGRADED_STATUS, status_origin=ORIGIN_FALLBACK,
                        status_reason="scip-python crashed mid-build")
    finally:
        registry.close()

    assert _cmd_list(argparse.Namespace()) == 0
    rows = {line.split("\t")[0]: line.split("\t")
            for line in capsys.readouterr().out.splitlines()}

    degraded = rows["crashed"]
    assert degraded[1] == "◐ degraded"
    assert degraded[5] == "scip-python crashed mid-build"  # 6th field: the cause
    assert len(degraded) == 6


def test_cmd_status_explains_a_degraded_repo(tmp_path: Path, monkeypatch, capsys):
    """Pin: `jarvis status` on a degraded row prints the fallback origin,
    the persisted cause, and the self-heal recovery verb — origin-driven
    since Phase 1; the degraded origin needs no status-command change."""
    import argparse

    from jarvis.index_cli import _cmd_status
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FALLBACK, Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert("crashed", "/repos/crashed", "python", "abc123",
                        DEGRADED_STATUS, status_origin=ORIGIN_FALLBACK,
                        status_reason="scip-python crashed mid-build")
    finally:
        registry.close()

    rc = _cmd_status(argparse.Namespace(slug="crashed"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "origin: fallback" in out
    assert "cause: scip-python crashed mid-build" in out
    recovery = next(line for line in out.splitlines() if line.startswith("recovery:"))
    assert "jarvis reindex" in recovery


def test_cmd_list_keeps_search_only_rows_five_field_beside_degraded(tmp_path: Path, monkeypatch, capsys):
    """The degraded branch adds a 6th field only for degraded rows — a
    search-only row in the same listing keeps its 5-field shape (D-08)."""
    import argparse

    from jarvis.index_cli import _cmd_list
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FALLBACK, SEARCH_ONLY_STATUS, Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert("crashed", "/repos/crashed", "python", "abc123",
                        DEGRADED_STATUS, status_origin=ORIGIN_FALLBACK,
                        status_reason="scip-python crashed mid-build")
        registry.upsert("legacy", "/repos/legacy", "unknown", None,
                        SEARCH_ONLY_STATUS, search_only=True)
    finally:
        registry.close()

    assert _cmd_list(argparse.Namespace()) == 0
    rows = {line.split("\t")[0]: line.split("\t")
            for line in capsys.readouterr().out.splitlines()}

    assert rows["crashed"][1] == "◐ degraded"
    assert len(rows["crashed"]) == 6
    assert rows["legacy"][1] == "◐ search-only"
    assert len(rows["legacy"]) == 5  # no 6th-field regression


def test_cmd_status_prints_stderr_tail_and_pointer(tmp_path: Path, monkeypatch, capsys):
    """D-02 display side: only the last ~20 lines are printed plus a
    pointer to the persisted full log; persistence itself stays unbounded."""
    import argparse

    from jarvis.index_cli import _cmd_status
    from jarvis.registry import ORIGIN_FAILED_HARD, Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    stderr = "\n".join(f"stderr line {i:02d}" for i in range(40))
    registry = Registry(data_root / "registry.db")
    try:
        registry.record_failure("broken", "/repos/broken", "python",
                                ORIGIN_FAILED_HARD, "scip-python index failed",
                                f"scip-python index failed:\n{stderr}")
    finally:
        registry.close()

    rc = _cmd_status(argparse.Namespace(slug="broken"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "stderr line 00" not in out  # head is display-truncated...
    assert "stderr line 19" not in out  # ...at the last 20 lines
    assert "stderr line 20" in out
    assert "stderr line 39" in out
    assert "persisted in the registry" in out


def test_cmd_status_omits_stderr_block_when_absent(tmp_path: Path, monkeypatch, capsys):
    """A row with no persisted stderr (every success path) must print no
    stderr block at all."""
    import argparse

    from jarvis.index_cli import _cmd_status
    from jarvis.registry import Registry

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert("healthy", "/repos/healthy", "python", "abc123", "indexed")
    finally:
        registry.close()

    rc = _cmd_status(argparse.Namespace(slug="healthy"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "stderr" not in out
    assert "full log" not in out


def test_pre_pipeline_version_gate_failure_creates_a_recoverable_row(
    tmp_path: Path, monkeypatch
):
    """D-05 close-out: a failure BEFORE the pipeline proper (the scip
    version gate) must leave a registry row -- status 'failed', origin
    'failed_hard', language 'unknown' (detection never ran) -- so the
    slug resolves for `jarvis reindex <slug>` and `jarvis status` can
    explain it."""
    from jarvis.index_cli import UNKNOWN_LANGUAGE, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _boom():
        raise RuntimeError("scip v0.7.0 is below the required floor v0.9.0")

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", _boom)

    with pytest.raises(RuntimeError, match="below the required floor"):
        index_repo(repo_dir, root=data_root)

    slug = config.repo_slug(repo_dir.name)
    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None, "the failed first index must leave a row (D-05)"
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.language == UNKNOWN_LANGUAGE  # detection never completed
        assert entry.status_reason == "scip v0.7.0 is below the required floor v0.9.0"
        assert "below the required floor" in entry.status_stderr
    finally:
        registry.close()


def test_pre_pipeline_stale_language_override_failure_overwrites_the_row(
    tmp_path: Path, monkeypatch
):
    """D-05/D-06: a persisted unsupported language override raises before
    the pipeline; the existing row must be overwritten with the failed
    attempt's facts (status/origin/reason set, language reflecting that
    resolution never completed), not keep the last good run's."""
    from jarvis.index_cli import UNKNOWN_LANGUAGE, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert("stale", str(repo_dir), "cobol", "abc123", "indexed",
                        language_override="cobol")
    finally:
        registry.close()

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)

    with pytest.raises(UnsupportedLanguageError, match="cobol"):
        index_repo(repo_dir, slug="stale", root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get("stale")
        assert entry is not None
        assert entry.status == "failed"  # not the stale 'indexed'
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert "cobol" in entry.status_reason
        assert "cobol" in entry.status_stderr
        assert entry.language == UNKNOWN_LANGUAGE  # D-06: no last-good facts linger
        assert entry.commit_sha is None
    finally:
        registry.close()


def test_duplicate_slug_rejection_writes_no_failure_row(tmp_path: Path, monkeypatch):
    """The duplicate-slug gate rejects the REQUEST (a path already indexed
    under another slug), it is not a failed run: stamping a failure would
    create a phantom row for the rejected slug that re-trips the gate on
    every later attempt. The existing row must stay byte-identical and no
    new row may appear."""
    from jarvis.index_cli import IndexingError, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    registry = Registry(data_root / "registry.db")
    try:
        before = registry.upsert("first", str(repo_dir), "python", "abc123", "indexed")
    finally:
        registry.close()

    def _tripwire():
        # If the duplicate gate ever stops rejecting, the pre-pipeline
        # handler would stamp this row -- the untouched assertions below
        # would then fail loudly instead of silently indexing.
        raise RuntimeError("must not reach the version gate")

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", _tripwire)

    with pytest.raises(IndexingError, match="already indexed as 'first'"):
        index_repo(repo_dir, slug="second", root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get("first")
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.status_origin is None
        assert entry.status_reason is None
        assert entry.status_stderr is None
        assert entry.language == "python"
        assert entry.commit_sha == "abc123"
        assert entry.last_indexed == before.last_indexed  # untouched, not re-stamped
        assert registry.get("second") is None  # no phantom row for the rejected request
    finally:
        registry.close()


# --- Phase 3: opt-in self-healing fallback (FALL-01/FALL-04) ---------------

def _degrade_mock_run(failures: dict[str, Exception]):
    """A `_run` stand-in keyed on step name: steps named in `failures`
    raise; everything else succeeds (house pattern of the signature
    fallback tests)."""
    def _run(cmd, *, cwd, step, env=None):
        for fail_step, exc in failures.items():
            if step == fail_step:
                raise exc
        return _fake_completed_process(cmd)

    return _run


def test_degraded_publish_on_post_build_start_failure(tmp_path: Path, monkeypatch):
    """FALL-01 (the tracer): with fallback resolved on, a post-build-start
    indexer failure publishes search-only instead of leaving nothing --
    exit-0 return, terminal row degraded/fallback with one-line reason and
    full stderr, search_only False (self-heal keeps retrying), commit_sha
    stamped with the attempt sha."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FALLBACK

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    full_text = "error: simulated post-build-start failure\nsome stderr detail\nmore detail"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(full_text)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)

    head_sha = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == DEGRADED_STATUS
        assert entry.status_origin == ORIGIN_FALLBACK
        assert entry.status_reason == "error: simulated post-build-start failure"
        assert entry.status_stderr == full_text
        assert entry.search_only is False, "degraded must keep retrying the full build"
        assert entry.commit_sha == head_sha
    finally:
        registry.close()


def test_degraded_run_prints_exactly_one_warning_line(
    tmp_path: Path, monkeypatch, capsys
):
    """FALL-01 reporting contract: the degraded run is a success (returns
    the slug, no raise) and says so once on stderr -- the reason rides the
    single 'degraded to search-only' warning line."""
    from jarvis.index_cli import IndexingError, index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError("error: simulated post-build-start failure")
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)
    assert slug  # success path, no raise

    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if "degraded to search-only" in line]
    assert len(lines) == 1
    assert "error: simulated post-build-start failure" in lines[0]


def test_missing_binary_failure_stays_hard_with_fallback_enabled(
    tmp_path: Path, monkeypatch
):
    """FALL-04: a missing binary is a setup.sh problem -- degrading on it
    would launder 'run setup.sh' into a published index. Even with
    fallback on, the run raises and the row reads failed/failed_hard."""
    from jarvis.index_cli import IndexingError, MissingBinaryError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr(
        "jarvis.index_cli._run",
        _degrade_mock_run({"scip-python index": MissingBinaryError(
            "scip-python index failed: scip-python not found on PATH — run setup.sh"
        )}),
    )
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    with pytest.raises(IndexingError, match="not found on PATH"):
        index_repo(repo_dir, root=data_root, fallback_search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.search_only is False
    finally:
        registry.close()


def test_run_translates_file_not_found_into_missing_binary_error(tmp_path: Path):
    """FALL-04 exclusion input: the real `_run` (not a mock) turns
    FileNotFoundError into the MissingBinaryError subclass, not a plain
    IndexingError -- the degrade gate keys on the type."""
    from jarvis.index_cli import MissingBinaryError, _run

    with pytest.raises(MissingBinaryError, match="not found on PATH") as excinfo:
        _run(["definitely-not-a-real-binary-xyz"], cwd=tmp_path, step="step-x")
    assert type(excinfo.value) is MissingBinaryError


def test_bash_shim_failure_stays_hard_with_fallback_enabled(
    tmp_path: Path, monkeypatch
):
    """FALL-04: a bash-shim failure is environment misconfiguration (one
    `brew install bash` fixes it) -- both tokens present in the failure
    text keeps it a loud hard failure even with fallback on."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    shim_text = (
        "error: line 8: ${LAUNCHER_ARGS[@]}: unbound variable\n"
        "scip-java wrapper needs bash >= 4.4"
    )
    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr(
        "jarvis.index_cli._run",
        _degrade_mock_run({"scip-python index": IndexingError(shim_text)}),
    )
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    with pytest.raises(IndexingError, match="unbound variable"):
        index_repo(repo_dir, root=data_root, fallback_search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
    finally:
        registry.close()


def test_pre_pipeline_version_gate_stays_hard_with_fallback_enabled(
    tmp_path: Path, monkeypatch
):
    """FALL-04: pre-pipeline gates (version floors) raise inside the
    pre-pipeline wrap and never reach the degrade branch -- fallback on
    must not soften a too-old scip binary."""
    from jarvis.index_cli import index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _boom():
        raise RuntimeError("scip v0.7.0 is below the required floor v0.9.0")

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", _boom)

    with pytest.raises(RuntimeError, match="below the required floor"):
        index_repo(repo_dir, root=data_root, fallback_search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
    finally:
        registry.close()


def test_failed_degraded_publish_preserves_the_previous_current_pointer(
    tmp_path: Path, monkeypatch
):
    """FALL-01 ordering: a degraded publish whose zoekt step fails must
    leave the previously-published current pointer intact and the row
    failed/failed_hard -- search is published before SCIP artifacts are
    retired, so the fallback promise failing never destroys a good index."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    # Phase 1: a fully mocked-successful run publishes a real current pointer.
    def _successful_run(cmd, *, cwd, step, env=None):
        if step == "scip expt-convert":
            # cmd: [scip, expt-convert, --output, <db>, <scip path>]
            _make_index_db(Path(cmd[3]), chunks=1, mentions=14)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _successful_run)
    monkeypatch.setattr(
        "jarvis.index_cli.populate_graph_for_repo", lambda *a, **k: None
    )
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root)
    pointer_file = config.index_dir(slug, data_root) / "current"
    assert pointer_file.exists(), "phase 1 must publish a current pointer"

    # Phase 2: the indexer step fails AND the fallback publish's zoekt step
    # fails -- the old pointer must survive the failed degraded publish.
    monkeypatch.setattr(
        "jarvis.index_cli._run",
        _degrade_mock_run({
            "scip-python index": IndexingError("error: simulated indexer failure"),
            "zoekt-git-index": IndexingError("zoekt-git-index failed:\nboom"),
        }),
    )

    with pytest.raises(IndexingError, match="simulated indexer failure"):
        index_repo(repo_dir, slug=slug, root=data_root, fallback_search_only=True)

    assert pointer_file.exists(), "a failing zoekt publish must not retire the old index"
    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
    finally:
        registry.close()


def test_post_publish_registry_failure_stays_hard_not_degraded(
    tmp_path: Path, monkeypatch
):
    """CR-01 regression: the degrade gate must not fire once the pointer has
    flipped. A registry write failing AFTER `_publish_atomically` (locked
    registry.db from a concurrent watch reindex, disk-full) is a bookkeeping
    failure against a fully-published index -- pre-fix, the gate retired that
    index, republished zoekt-only, and wrote degraded/fallback citing the
    sqlite error as the indexer failure. Post-fix the run stays a hard
    failure (failed/failed_hard with the real cause) and the live pointer
    survives untouched."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _successful_run(cmd, *, cwd, step, env=None):
        if step == "scip expt-convert":
            _make_index_db(Path(cmd[3]), chunks=1, mentions=14)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _successful_run)
    monkeypatch.setattr(
        "jarvis.index_cli.populate_graph_for_repo", lambda *a, **k: None
    )
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    # The locked-db race the fix exists for: every upsert succeeds except the
    # terminal status="indexed" write -- i.e. the failure lands strictly
    # AFTER the publish flipped the pointer.
    real_upsert = Registry.upsert

    def _locked_on_terminal_upsert(self, slug, path, language, commit_sha, status, **kw):
        if status == "indexed":
            raise sqlite3.OperationalError("database is locked")
        return real_upsert(self, slug, path, language, commit_sha, status, **kw)

    monkeypatch.setattr(Registry, "upsert", _locked_on_terminal_upsert)

    with pytest.raises(IndexingError, match="database is locked"):
        index_repo(repo_dir, root=data_root, fallback_search_only=True)

    slug = config.repo_slug(repo_dir.name)
    pointer_file = config.index_dir(slug, data_root) / "current"
    assert pointer_file.exists(), (
        "a bookkeeping failure must not retire the just-published index"
    )
    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason == "database is locked"
        assert entry.search_only is False
    finally:
        registry.close()


def test_degraded_row_write_failure_reports_what_landed(
    tmp_path: Path, monkeypatch, capsys
):
    """WR-01 regression: when the degrade publish SUCCEEDS but writing the
    degraded row fails (locked db), the run must not claim "nothing
    published" -- zoekt IS live and the old SCIP index IS retired. The
    message says what landed and what failed; the row records the
    bookkeeping failure (the run's proximate cause) as failed/failed_hard,
    and the run raises instead of exiting 0 with the row stranded at
    'indexing'."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError("error: simulated post-build-start failure")
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    # The search-only publish succeeds; only the degraded row write hits the
    # locked db (the same race as the CR-01 test, one step later).
    real_upsert = Registry.upsert

    def _locked_on_degraded_upsert(self, slug, path, language, commit_sha, status, **kw):
        if status == DEGRADED_STATUS:
            raise sqlite3.OperationalError("database is locked")
        return real_upsert(self, slug, path, language, commit_sha, status, **kw)

    monkeypatch.setattr(Registry, "upsert", _locked_on_degraded_upsert)

    with pytest.raises(IndexingError, match="database is locked"):
        index_repo(repo_dir, root=data_root, fallback_search_only=True)

    err = capsys.readouterr().err
    assert "nothing published" not in err, (
        "search-only IS published at this point -- the claim would be false"
    )
    assert "degraded to search-only" in err
    assert "error: simulated post-build-start failure" in err
    assert "database is locked" in err

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason == "database is locked"
    finally:
        registry.close()


def test_degraded_publish_retire_failure_reports_partial_landing(
    tmp_path: Path, monkeypatch, capsys
):
    """WR-02 regression: a degraded publish whose zoekt step SUCCEEDS but
    whose SCIP-retire step fails is a partial landing -- zoekt shards are
    live and the previous navigation index still stands -- so "nothing
    published" would be false. The warning and the row's status_stderr must
    state what landed, the one-line reason stays the ORIGINAL indexer
    failure, and the run stays a hard failure raising the original error
    (the locked degraded-publish-failure constraint)."""
    from jarvis.index_cli import IndexingError, index_repo
    from jarvis.registry import ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    # Phase 1: a fully mocked-successful run publishes a real current pointer.
    def _successful_run(cmd, *, cwd, step, env=None):
        if step == "scip expt-convert":
            _make_index_db(Path(cmd[3]), chunks=1, mentions=14)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _successful_run)
    monkeypatch.setattr(
        "jarvis.index_cli.populate_graph_for_repo", lambda *a, **k: None
    )
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root)
    pointer_file = config.index_dir(slug, data_root) / "current"
    assert pointer_file.exists(), "phase 1 must publish a current pointer"

    # Phase 2: the indexer fails (degrade branch runs), the degraded publish's
    # zoekt step succeeds, and the retire step dies on the realistic
    # locked-registry.db race -- retire opens a GraphStore (a write) on the
    # same registry.db the degrade exists to survive. Locking GraphStore
    # construction keeps the REAL retire code under test, including its
    # teardown ordering.
    monkeypatch.setattr(
        "jarvis.index_cli._run",
        _degrade_mock_run(
            {"scip-python index": IndexingError("error: simulated post-build-start failure")}
        ),
    )

    def _locked_graph_store(db_path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("jarvis.index_cli.GraphStore", _locked_graph_store)

    with pytest.raises(IndexingError, match="simulated post-build-start failure"):
        index_repo(repo_dir, slug=slug, root=data_root, fallback_search_only=True)

    err = capsys.readouterr().err
    assert "nothing published" not in err, (
        "the zoekt shards ARE live at this point -- the claim would be false"
    )
    assert "did not complete" in err
    assert "search shards ARE published" in err
    assert "retiring the previous SCIP index failed" in err
    assert "database is locked" in err
    assert "navigation index is untouched" in err

    # The previous navigation index must survive a retire that failed before
    # its (deliberately last) rmtree.
    assert pointer_file.exists(), "a failed retire must not destroy the old index"

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason == "error: simulated post-build-start failure"
        stderr_field = entry.status_stderr or ""
        assert "degraded publish did not complete" in stderr_field
        assert "search shards ARE published" in stderr_field
        assert "database is locked" in stderr_field
    finally:
        registry.close()


def test_degraded_row_write_and_record_failure_both_locked(
    tmp_path: Path, monkeypatch, capsys
):
    """WR-03 regression: when the degraded-row write fails AND recording
    that failure also fails (the same locked registry.db), the recording
    failure must not mask the original -- one extra stderr warning names
    both failures, the CLI still prints its clean one-line `error:` and
    exits 1 (no traceback), and the raised error is the original
    bookkeeping failure, never the raw sqlite3.OperationalError."""
    import argparse

    import jarvis.index_cli as cli
    from jarvis.index_cli import IndexingError
    from jarvis.registry import DEGRADED_STATUS

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError("error: simulated post-build-start failure")
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    # The degraded-row write hits the lock; recording that failure hits a
    # second, distinct sqlite error (disk I/O) so the test can tell the
    # original failure from the recording failure apart.
    real_upsert = Registry.upsert

    def _locked_on_degraded_upsert(self, slug, path, language, commit_sha, status, **kw):
        if status == DEGRADED_STATUS:
            raise sqlite3.OperationalError("database is locked")
        return real_upsert(self, slug, path, language, commit_sha, status, **kw)

    monkeypatch.setattr(Registry, "upsert", _locked_on_degraded_upsert)

    def _disk_full_record_failure(self, *a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Registry, "record_failure", _disk_full_record_failure)

    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))
    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug=None, scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=True,
    ))

    assert rc == 1
    err = capsys.readouterr().err
    # The honest WR-01 warning about the degraded-row write...
    assert "degraded to search-only" in err
    assert "database is locked" in err
    # ...plus the WR-03 warning naming BOTH the recording failure and the
    # original failure it must not mask.
    assert "recording the failed run" in err
    assert "disk I/O error" in err
    # The clean one-line error carries the ORIGINAL failure (not the
    # secondary recording failure), and no traceback replaces it.
    assert err.splitlines()[-1] == "error: database is locked"
    assert "Traceback" not in err

    # The row was honestly left where the last successful write put it
    # ('indexing') -- nothing was laundered past the failed record.
    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(config.repo_slug(repo_dir.name))
        assert entry is not None
        assert entry.status == "indexing"
    finally:
        registry.close()


def test_pre_pipeline_record_failure_failure_does_not_mask_the_original(
    tmp_path: Path, monkeypatch, capsys
):
    """WR-03 mirror in the pre-pipeline wrap: when the failure row's own
    write raises there too, the ORIGINAL gate error (a RuntimeError here,
    not an IndexingError and not the sqlite error) is still the one that
    propagates, with one warning naming both failures."""
    from jarvis.index_cli import index_repo

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    def _boom():
        raise RuntimeError("scip v0.7.0 is below the required floor v0.9.0")

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", _boom)

    def _disk_full_record_failure(self, *a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Registry, "record_failure", _disk_full_record_failure)

    with pytest.raises(RuntimeError, match="below the required floor"):
        index_repo(repo_dir, root=data_root, fallback_search_only=True)

    err = capsys.readouterr().err
    assert "recording the failed run" in err
    assert "disk I/O error" in err
    assert "below the required floor" in err


# --- Phase 3: precedence, persistence, self-heal (FALL-02/FALL-03) ----------

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


def _mock_failing_indexer(monkeypatch, message):
    """Mocks for a post-build-start indexer failure: the language indexer
    step raises, every other step (including the degrade publish's zoekt)
    still succeeds — the house signature-fallback pattern."""
    from jarvis.index_cli import IndexingError

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(message)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)


@pytest.mark.parametrize("cli", [True, False, None])
@pytest.mark.parametrize("persisted", [True, False, None])
@pytest.mark.parametrize("env", ["1", None])
def test_fallback_precedence_matrix_cli_persisted_env(
    tmp_path: Path, monkeypatch, cli, persisted, env
):
    """FALL-02 full precedence matrix: CLI wins whenever present; else the
    persisted value when not NULL; else the env tier; else off."""
    from jarvis.index_cli import _resolve_fallback
    from jarvis.registry import Registry

    if env is None:
        monkeypatch.delenv("JARVIS_FALLBACK_SEARCH_ONLY", raising=False)
    else:
        monkeypatch.setenv("JARVIS_FALLBACK_SEARCH_ONLY", env)

    registry = Registry(tmp_path / "registry.db")
    try:
        if persisted is not None:
            registry.upsert("mine", "/repos/mine", "python", "abc", "indexed")
            registry.set_fallback_enabled("mine", persisted)
        expected = cli if cli is not None else (
            persisted if persisted is not None else (env == "1")
        )
        assert _resolve_fallback(registry, "mine", cli) is expected
    finally:
        registry.close()


def test_explicit_cli_fallback_flag_persists_after_a_healthy_run(
    tmp_path: Path, monkeypatch
):
    """FALL-02 persistence — explicit only: a healthy full run invoked
    with fallback_search_only=True leaves fallback_enabled=True on the
    row (the setter fires right after the transitional indexing upsert)."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    _mock_healthy_full_run(monkeypatch)
    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.fallback_enabled is True
    finally:
        registry.close()


def test_no_cli_flag_leaves_fallback_enabled_null(tmp_path: Path, monkeypatch):
    """Pitfall 1: only the explicit CLI value is ever persisted. Runs with
    no CLI flag leave the row NULL whether the env tier is off or on — the
    env value is consumed at run time only, so a later env-on still
    governs a repo that merely ran once with env off."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.delenv("JARVIS_FALLBACK_SEARCH_ONLY", raising=False)
    _mock_healthy_full_run(monkeypatch)
    slug = index_repo(repo_dir, root=data_root)
    registry = Registry(data_root / "registry.db")
    try:
        assert registry.get(slug).fallback_enabled is None

        monkeypatch.setenv("JARVIS_FALLBACK_SEARCH_ONLY", "1")
        _mock_healthy_full_run(monkeypatch)
        index_repo(repo_dir, slug=slug, root=data_root)
        assert registry.get(slug).fallback_enabled is None
    finally:
        registry.close()


def test_explicit_fallback_opt_out_governs_immediately_on_a_degraded_repo(
    tmp_path: Path, monkeypatch
):
    """FALL-02 opt-out is immediate: --no-fallback-search-only on an
    already-degraded repo makes the next still-broken run a hard failure —
    the degraded state never traps."""
    from jarvis.index_cli import IndexingError
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FAILED_HARD

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    _mock_failing_indexer(monkeypatch, "error: simulated post-build-start failure")
    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)
    registry = Registry(data_root / "registry.db")
    try:
        assert registry.get(slug).status == DEGRADED_STATUS

        _mock_failing_indexer(monkeypatch, "error: simulated post-build-start failure")
        with pytest.raises(IndexingError):
            index_repo(repo_dir, slug=slug, root=data_root, fallback_search_only=False)

        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "failed"
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.fallback_enabled is False  # the opt-out is now durable
    finally:
        registry.close()


def test_degraded_repo_self_heals_to_indexed_on_a_successful_rerun(
    tmp_path: Path, monkeypatch
):
    """FALL-03: degraded keeps search_only=False so the next run retries
    the full build; success ends status='indexed' with the D-04
    NULL-clearing of origin/reason/stderr — no stale failure facts."""
    from jarvis.registry import DEGRADED_STATUS

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    _mock_failing_indexer(monkeypatch, "error: simulated post-build-start failure")
    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)
    registry = Registry(data_root / "registry.db")
    try:
        assert registry.get(slug).status == DEGRADED_STATUS

        _mock_healthy_full_run(monkeypatch)
        index_repo(repo_dir, slug=slug, root=data_root)  # no CLI flag

        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.search_only is False
        assert (entry.status_origin, entry.status_reason, entry.status_stderr) == (
            None, None, None,
        )
    finally:
        registry.close()


def test_degraded_repo_degrades_again_on_a_fresh_failure(
    tmp_path: Path, monkeypatch
):
    """FALL-03: the retry is real — a still-failing second run degrades
    again with a FRESH failure record (the new reason text proves the
    build was retried, not a stale cached state served back)."""
    from jarvis.registry import DEGRADED_STATUS, ORIGIN_FALLBACK

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    _mock_failing_indexer(monkeypatch, "error: first failure")
    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)
    registry = Registry(data_root / "registry.db")
    try:
        first = registry.get(slug)
        assert first is not None
        assert first.status == DEGRADED_STATUS
        assert first.status_reason == "error: first failure"

        _mock_failing_indexer(monkeypatch, "error: second fresh failure")
        index_repo(repo_dir, slug=slug, root=data_root)  # no CLI flag

        second = registry.get(slug)
        assert second is not None
        assert second.status == DEGRADED_STATUS
        assert second.status_origin == ORIGIN_FALLBACK
        assert second.status_reason == "error: second fresh failure"
    finally:
        registry.close()


def test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo(
    tmp_path: Path, monkeypatch
):
    """FALL-03 adjacency: a signature-matched indexer failure takes the
    permanent search-only path inside the indexer-step handler, before the
    generic degrade branch is ever reachable — an opted-in repo ends
    search_only=True / origin 'signature', NOT degraded."""
    from jarvis.registry import ORIGIN_SIGNATURE, SEARCH_ONLY_STATUS

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    _mock_failing_indexer(
        monkeypatch, "error: No SCIP shards found. scip-java cannot index this"
    )
    slug = index_repo(repo_dir, root=data_root, fallback_search_only=True)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
    finally:
        registry.close()


# --- Phase 3: watch anti-treadmill (FALL-05) --------------------------------


def _watch_entry(**overrides):
    """A RegisteredRepo for the watch-skip predicate tests; keyword
    overrides pick the status/sha under test (the `_entry` pattern from
    tests/test_registry.py's recovery-mapping tests)."""
    from datetime import UTC, datetime

    from jarvis.registry import RegisteredRepo

    fields = dict(
        slug="mine", path="/repos/mine", language="python", commit_sha="abc123",
        last_indexed=datetime.now(UTC), status="indexed",
    )
    fields.update(overrides)
    return RegisteredRepo(**fields)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(None, True, id="no-row"),
        pytest.param({"status": "failed", "commit_sha": "abc123"}, True,
                     id="failed-even-with-matching-sha"),
        pytest.param({"status": "indexed", "commit_sha": "abc123"}, True,
                     id="indexed"),
        pytest.param({"status": "search-only", "commit_sha": "abc123"}, True,
                     id="search-only"),
        pytest.param({"status": "degraded", "commit_sha": None}, True,
                     id="degraded-null-sha"),
        pytest.param({"status": "degraded", "commit_sha": "def456"}, True,
                     id="degraded-changed-sha"),
        pytest.param({"status": "degraded", "commit_sha": "abc123"}, False,
                     id="degraded-same-sha"),
    ],
)
def test_watch_should_retry_full_build_matrix(overrides, expected):
    """FALL-05 skip predicate: the ONLY row that declines the retry is a
    degraded row whose persisted commit_sha equals the current sha — the
    attempt that already failed. A missing row, a hard-failed row
    (record_failure writes commit_sha=NULL, and NULL never equals a real
    sha), an indexed/search-only row, or any sha change all retry —
    FALL-03 self-heal stays the default and manual `jarvis index` never
    skips."""
    from jarvis.index_cli import _watch_should_retry_full_build

    entry = None if overrides is None else _watch_entry(**overrides)
    assert _watch_should_retry_full_build(entry, "abc123") is expected


def test_watch_parser_accepts_tri_state_fallback_flag():
    from jarvis.index_cli import build_parser

    on = build_parser().parse_args(["watch", "/repos/x", "--fallback-search-only"])
    off = build_parser().parse_args(["watch", "/repos/x", "--no-fallback-search-only"])
    absent = build_parser().parse_args(["watch", "/repos/x"])
    assert on.fallback_search_only is True
    assert off.fallback_search_only is False
    assert absent.fallback_search_only is None


def test_watch_skip_check_skips_only_while_sha_unchanged(tmp_path: Path):
    """FALL-05 consult against a real tmp Registry + git repo: a degraded
    row whose commit_sha equals the current HEAD declines the retry; a
    source change (new sha) re-triggers the full build (ROADMAP criterion
    5); a missing row fails open to retry."""
    from jarvis.index_cli import _git_head, _watch_skip_check
    from jarvis.registry import DEGRADED_STATUS

    data_root = tmp_path / "data"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("x = 1\n")
    _init_git_repo(repo_dir)

    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert("mine", str(repo_dir), "python",
                        _git_head(repo_dir), DEGRADED_STATUS)
    finally:
        registry.close()

    # Unchanged sha on a degraded row: skip the full-build retry.
    assert _watch_skip_check(repo_dir, "mine", root=data_root) is False

    # A source change produces a new sha: the full build re-triggers.
    (repo_dir / "feature.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "source change"],
                   cwd=repo_dir, check=True)
    assert _watch_skip_check(repo_dir, "mine", root=data_root) is True

    # No row at all: fail open to retry.
    assert _watch_skip_check(repo_dir, "never-registered", root=data_root) is True


def test_watch_skip_check_fails_open_when_the_consult_raises(tmp_path: Path):
    """T-3-07: ANY consult failure (locked db, git hiccup) retries —
    fail-open is the safe direction for self-heal; the worst outcome of a
    broken consult is one extra build, never a suppressed retry."""
    from jarvis.index_cli import _watch_skip_check

    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    # _git_head raises NotAGitRepositoryError inside the consult; the
    # wrapper must convert it to "retry" instead of propagating.
    assert _watch_skip_check(not_a_repo, "anything", root=tmp_path / "data") is True


_UNSET = object()


class _FakeEvent:
    is_directory = False
    src_path = "/repos/x/main.py"


class _FakeObserver:
    """Minimal stand-in for watchdog's Observer: schedule() captures the
    handler; start() delivers one fake source-file event (driving the
    Debouncer); stop/join no-op."""

    def __init__(self):
        self.handler = None

    def schedule(self, handler, path, recursive=True):
        self.handler = handler

    def start(self):
        self.handler.on_any_event(_FakeEvent())

    def stop(self):
        pass

    def join(self):
        pass


def _install_fake_watchdog(monkeypatch):
    """Patch sys.modules so `_cmd_watch`'s deferred imports resolve to
    fakes — the tests drive the real `_reindex` closure without the
    `watch` extra (CI installs only `semantic`; the tests themselves never
    import watchdog)."""
    import types

    events = types.ModuleType("watchdog.events")
    events.FileSystemEventHandler = object
    observers = types.ModuleType("watchdog.observers")
    observers.Observer = _FakeObserver
    watchdog_pkg = types.ModuleType("watchdog")
    watchdog_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "watchdog", watchdog_pkg)
    monkeypatch.setitem(sys.modules, "watchdog.events", events)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers)


def _drive_cmd_watch(cli, monkeypatch, args_kwargs, sleep_results):
    """Run `_cmd_watch` to completion under the fake observer and a
    scripted `time.sleep` (the loop's only clock use). `sleep_results` is
    an iterator: each loop iteration first consumes one sleep result — a
    `None` sleeps on, an exception instance is raised to break the loop
    (KeyboardInterrupt is `_cmd_watch`'s own exit path)."""
    import argparse
    import types

    _install_fake_watchdog(monkeypatch)

    def _sleep(seconds):
        result = next(sleep_results)
        if isinstance(result, BaseException):
            raise result
        return result

    fake_time = types.ModuleType("time")
    fake_time.sleep = _sleep
    monkeypatch.setattr(cli, "time", fake_time)
    return cli._cmd_watch(argparse.Namespace(**args_kwargs))


def test_cmd_watch_reindex_forwards_fallback_flag_to_index_repo(
    tmp_path: Path, monkeypatch
):
    """FALL-02 wiring: watch is just another reindex driver — `_reindex`
    forwards the tri-state fallback_search_only to index_repo beside
    scheme/language. The fake index_repo raises KeyboardInterrupt (a
    BaseException `_reindex`'s broad `except Exception` must not swallow)
    to break the watch loop after capturing; a non-git path makes the
    skip consult fail open, reaching index_repo."""
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

    import itertools

    rc = _drive_cmd_watch(
        cli, monkeypatch,
        {"path": str(repo_dir), "slug": None, "scheme": "myscheme",
         "debounce": 0.0, "language": "swift", "fallback_search_only": True},
        # Loop exits on the first poll (fake index_repo raises
        # KeyboardInterrupt); the bounded chain is only the no-hang guard
        # if the wiring ever regressed into never invoking index_repo.
        sleep_results=itertools.chain(itertools.repeat(None, 5), [KeyboardInterrupt()]),
    )

    assert rc == 0
    assert captured["fallback_search_only"] is True
    assert captured["slug"] == repo_dir.name


def test_cmd_watch_skips_full_build_retry_on_degraded_row_at_same_sha(
    tmp_path: Path, monkeypatch, capsys
):
    """FALL-05 end-to-end through the watch driver: a degraded row whose
    commit_sha equals HEAD makes `_reindex` print one stderr note and
    return WITHOUT invoking index_repo — no treadmill on a
    persistently-failing repo at an unchanged sha. The scripted sleep
    breaks the loop on the second iteration (the skip path returns
    normally, so sleep is the only exit left)."""
    import jarvis.index_cli as cli
    from jarvis.index_cli import _git_head
    from jarvis.registry import DEGRADED_STATUS

    data_root = tmp_path / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_root))

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("x = 1\n")
    _init_git_repo(repo_dir)

    registry = Registry(data_root / "registry.db")
    try:
        registry.upsert(repo_dir.name, str(repo_dir), "python",
                        _git_head(repo_dir), DEGRADED_STATUS)
    finally:
        registry.close()

    invoked: list = []

    def fake_index_repo(*args, **kwargs):
        invoked.append(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = _drive_cmd_watch(
        cli, monkeypatch,
        {"path": str(repo_dir), "slug": None, "scheme": None,
         "debounce": 0.0, "language": None, "fallback_search_only": None},
        sleep_results=iter([None, KeyboardInterrupt()]),
    )

    assert rc == 0
    assert invoked == [], "the skip path must not invoke index_repo"
    assert "still degraded at the same commit" in capsys.readouterr().err


# --- Phase 5: semantic install onboarding (SEMA-01/02) ----------------------


def _offer_test_repo(tmp_path: Path, name: str = "repo") -> Path:
    """A fixture repo the offer tests can index through the mocked
    healthy pipeline (the degraded-row CLI test setup)."""
    repo_dir = tmp_path / name
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    return repo_dir


def _force_offer_seams(monkeypatch) -> None:
    """Force every SEMA-01 gate open except the decline bit: the extra is
    missing and both streams are a TTY. Seams are monkeypatched by full
    path — no real stdin, no real find_spec (this checkout and CI both
    have the extra installed, so detection must never run for real)."""
    monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: True)
    monkeypatch.setattr("jarvis.index_cli._at_interactive_tty", lambda: True)


def _script_input(monkeypatch, answer=None, error=None) -> list[str]:
    """Replace builtins.input with a recorder returning `answer` (or
    raising `error`); returns every prompt string seen."""
    prompts: list[str] = []

    def _input(prompt=""):
        prompts.append(prompt)
        if error is not None:
            raise error
        return answer

    monkeypatch.setattr("builtins.input", _input)
    return prompts


def test_cmd_index_tty_offer_decline_answer_persists_semantic_declined(
    tmp_path: Path, monkeypatch, capsys
):
    """SEMA-01/SC2 tracer: on a TTY with the extra missing, `jarvis index`
    offers exactly once, after the index is published, with the locked
    prompt text; Enter (the safe default) declines and the bit persists
    per-repo with no traceback."""
    import argparse

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = _offer_test_repo(tmp_path)
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)
    prompts = _script_input(monkeypatch, answer="")

    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug="declined-repo", scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))

    assert rc == 0
    assert prompts == ["Install semantic search support for this repo? [y/N] "]
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get("declined-repo")
        assert entry is not None
        assert entry.semantic_declined is True
    finally:
        registry.close()


def test_cmd_index_tty_offer_not_repeated_for_a_declined_repo(
    tmp_path: Path, monkeypatch
):
    """SC2 memory: a second `jarvis index` of a declined repo never
    prompts (input itself is poisoned) and keeps the bit."""
    import argparse

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = _offer_test_repo(tmp_path)
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)
    _script_input(monkeypatch, answer="")
    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug="declined-repo", scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))
    assert rc == 0

    # Second run: prompting at all is the failure.
    _script_input(monkeypatch, error=AssertionError("must not prompt for a declined repo"))
    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug="declined-repo", scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))

    assert rc == 0
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get("declined-repo")
        assert entry is not None
        assert entry.semantic_declined is True
    finally:
        registry.close()


def test_cmd_index_tty_offer_still_made_for_a_different_repo(
    tmp_path: Path, monkeypatch
):
    """SC2 per-repo memory: declining one repo never suppresses the offer
    for a different repo in the same data dir."""
    import argparse

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = _offer_test_repo(tmp_path, name="repo-a")
    other_dir = _offer_test_repo(tmp_path, name="repo-b")
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)
    prompts = _script_input(monkeypatch, answer="")

    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug="repo-a", scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))
    assert rc == 0
    assert len(prompts) == 1
    prompts.clear()

    rc = cli._cmd_index(argparse.Namespace(
        path=str(other_dir), slug="repo-b", scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))
    assert rc == 0
    assert prompts == ["Install semantic search support for this repo? [y/N] "]
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        assert registry.get("repo-a").semantic_declined is True
        assert registry.get("repo-b").semantic_declined is True
    finally:
        registry.close()


def _offer_run(monkeypatch, tmp_path, slug, answer=None, error=None):
    """One mocked healthy `jarvis index` run for `slug` under the forced
    offer seams, with a scripted prompt answer; returns (rc, prompts)."""
    import argparse

    import jarvis.index_cli as cli

    repo_dir = _offer_test_repo(tmp_path, name=slug)
    prompts = _script_input(monkeypatch, answer=answer, error=error)
    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug=slug, scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))
    return rc, prompts


def test_cmd_index_offer_accept_parse_table(tmp_path: Path, monkeypatch):
    """SEMA-01 parse table, exactly the locked one: strip+lower in
    {"y","yes"} accepts and installs (no decline bit); every other
    answer — empty, whitespace, n/N/no, garbage — declines and persists
    the bit, without ever attempting an install."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)

    for i, answer in enumerate(["y", "Y", "yes", "Yes", "YES"]):
        slug = f"accept-{i}"
        installs: list[str] = []

        def _install():
            installs.append(slug)
            return True

        monkeypatch.setattr("jarvis.index_cli._install_semantic_extra", _install)
        rc, prompts = _offer_run(monkeypatch, tmp_path, slug, answer=answer)
        assert rc == 0
        assert len(installs) == 1
        assert len(prompts) == 1
        registry = Registry(tmp_path / "data" / "registry.db")
        try:
            assert registry.get(slug).semantic_declined is False
        finally:
            registry.close()

    for i, answer in enumerate(["", " ", "n", "N", "no", "maybe"]):
        slug = f"refuse-{i}"

        def _boom():
            raise AssertionError("a declined answer must never install")

        monkeypatch.setattr("jarvis.index_cli._install_semantic_extra", _boom)
        rc, _ = _offer_run(monkeypatch, tmp_path, slug, answer=answer)
        assert rc == 0
        registry = Registry(tmp_path / "data" / "registry.db")
        try:
            assert registry.get(slug).semantic_declined is True
        finally:
            registry.close()


def test_cmd_index_offer_yes_installs_and_enables_semantic_same_invocation(
    tmp_path: Path, monkeypatch, capsys
):
    """SEMA-01/SC1: a consented install re-enables semantic in the SAME
    invocation — install → importlib.invalidate_caches → the semantic
    stage re-run with the row's include prefixes → mark_semantic_indexed,
    and consent never writes a decline bit."""
    import argparse

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = _offer_test_repo(tmp_path)
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)
    _script_input(monkeypatch, answer="y")
    monkeypatch.setattr("jarvis.index_cli._install_semantic_extra", lambda: True)

    invalidations: list = []
    monkeypatch.setattr(
        "importlib.invalidate_caches", lambda: invalidations.append(1)
    )

    stage_calls: list = []

    def _stage(repo_path, slug, root, include_prefixes=()):
        stage_calls.append((repo_path, slug, root, include_prefixes))
        # First call = the pipeline's in-run stage: keep the healthy-mock
        # False so the pipeline itself stamps nothing (line "if
        # semantic_ok: mark_semantic_indexed") and only the offer's re-run
        # (second call) can set semantic_indexed_at.
        return len(stage_calls) > 1

    # After _mock_healthy_full_run, so this overrides its False lambda.
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", _stage)

    rc = cli._cmd_index(argparse.Namespace(
        path=str(repo_dir), slug="consented-repo", scheme=None, semantic_include=None,
        language=None, search_only=None, fallback_search_only=None, offer_semantic=True,
    ))

    assert rc == 0
    assert len(invalidations) == 1
    # Two stage calls, in order: the pipeline's in-run stage, then the
    # offer's re-run — which carries the row's persisted include
    # prefixes (none persisted here) and root=None from _cmd_index.
    assert len(stage_calls) == 2
    assert stage_calls[1] == (Path(str(repo_dir)), "consented-repo", None, ())
    assert stage_calls[0][1] == "consented-repo"
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get("consented-repo")
        assert entry is not None
        assert entry.semantic_indexed_at is not None  # mark_semantic_indexed ran
        assert entry.semantic_declined is False  # consent writes no bit
    finally:
        registry.close()


def test_cmd_index_offer_install_failure_warns_and_does_not_remember_decline(
    tmp_path: Path, monkeypatch, capsys
):
    """SEMA-01 three-outcome contract: install failure (uv absent,
    non-zero exit, timeout — all False at the seam) warns with exactly
    ONE stderr line naming the tried command, exits 0, never re-runs the
    stage, and writes no decline bit — failure is not a refusal, so the
    next TTY index offers again."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)
    monkeypatch.setattr("jarvis.index_cli._install_semantic_extra", lambda: False)

    stage_calls: list = []

    def _stage(repo_path, slug, root, include_prefixes=()):
        stage_calls.append((repo_path, slug, root, include_prefixes))
        # Healthy-mock False everywhere: nothing stamps, and any offer
        # re-run would still be recorded (a second call per slug).
        return False

    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", _stage)

    for i, shape in enumerate(["uv-absent", "non-zero-exit", "timeout"]):
        slug = f"failed-install-{i}"
        rc, _ = _offer_run(monkeypatch, tmp_path, slug, answer="y")
        assert rc == 0, shape
        err = capsys.readouterr().err
        naming = [
            line for line in err.splitlines()
            if "uv pip install" in line and "jarvis-mcp[semantic]" in line
        ]
        assert len(naming) == 1, (shape, err)
        assert (
            len([line for line in err.splitlines()
                 if "warning: semantic extra install failed" in line]) == 1
        ), (shape, err)
        registry = Registry(tmp_path / "data" / "registry.db")
        try:
            entry = registry.get(slug)
            assert entry is not None
            assert entry.semantic_declined is False, shape
        finally:
            registry.close()
    # Exactly one stage call per run — the pipeline's in-run stage. A
    # second call for any slug would be the offer's re-run, which must
    # never happen on the failure path.
    assert len(stage_calls) == 3
    assert sorted(c[1] for c in stage_calls) == [
        "failed-install-0", "failed-install-1", "failed-install-2",
    ]


def test_cmd_index_offer_eof_or_keyboard_interrupt_at_prompt_declines_remembered(
    tmp_path: Path, monkeypatch, capsys
):
    """SEMA-01/SC2: abnormal prompt input — EOF (piped-off stdin), Ctrl-C,
    and undecodable bytes (input() decodes stdin strict, so pasted binary
    garbage raises UnicodeDecodeError before any answer exists) — is a
    decline: remembered, exit 0, no traceback, and no install ever
    attempted."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)

    def _boom():
        raise AssertionError("EOF/Ctrl-C must never reach an install")

    monkeypatch.setattr("jarvis.index_cli._install_semantic_extra", _boom)

    for i, error in enumerate([
        EOFError(),
        KeyboardInterrupt(),
        UnicodeDecodeError("utf-8", b"\x80\x81", 0, 1, "invalid start byte"),
    ]):
        slug = f"interrupted-{i}"
        rc, prompts = _offer_run(monkeypatch, tmp_path, slug, error=error)
        assert rc == 0
        assert len(prompts) == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out + captured.err
        registry = Registry(tmp_path / "data" / "registry.db")
        try:
            entry = registry.get(slug)
            assert entry is not None
            assert entry.semantic_declined is True
        finally:
            registry.close()


def test_install_semantic_extra_argv_and_failure_paths(monkeypatch):
    """The locked uv command as a fixed argv: [resolved uv, "pip",
    "install", "--python", sys.executable, "jarvis-mcp[semantic]"] — list
    form (no shell), spec unpinned, decoded with errors="replace" so a
    legacy-locale uv can't crash the decode. uv absent → False with no
    subprocess at all; a non-zero exit → False; a TimeoutExpired → False.
    Spawn OSErrors and decode failures are pinned by the WR-02 test
    below."""
    import sys as _sys

    from jarvis.index_cli import _install_semantic_extra

    runs: list = []

    class _Result:
        def __init__(self, returncode):
            self.returncode = returncode

    def _run(cmd, **kwargs):
        runs.append((cmd, kwargs))
        return _Result(0)

    monkeypatch.setattr("jarvis.index_cli.shutil.which", lambda name: "/fake/bin/uv")
    monkeypatch.setattr("jarvis.index_cli.subprocess.run", _run)

    assert _install_semantic_extra() is True
    assert runs == [(
        ["/fake/bin/uv", "pip", "install", "--python", _sys.executable,
         "jarvis-mcp[semantic]"],
        {"capture_output": True, "text": True, "errors": "replace", "timeout": 600},
    )]

    # Non-zero exit → False.
    monkeypatch.setattr(
        "jarvis.index_cli.subprocess.run", lambda cmd, **k: _Result(1)
    )
    assert _install_semantic_extra() is False

    # uv not on PATH → False, and no subprocess is ever spawned.
    runs.clear()
    monkeypatch.setattr("jarvis.index_cli.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "jarvis.index_cli.subprocess.run",
        lambda cmd, **k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )
    assert _install_semantic_extra() is False
    assert runs == []

    # A stalled install times out into the same failure path.
    monkeypatch.setattr("jarvis.index_cli.shutil.which", lambda name: "/fake/bin/uv")

    def _timeout(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr("jarvis.index_cli.subprocess.run", _timeout)
    assert _install_semantic_extra() is False


def test_install_semantic_extra_swallows_spawn_and_decode_failures(monkeypatch):
    """WR-02: every spawn/decode failure inside the install helper lands
    in the same warn-and-continue False path as a timeout — an OSError
    from subprocess.run (uv unexecutable after `which` said yes, a TOCTOU
    unlink, EACCES) or a UnicodeDecodeError from decoding uv's captured
    output must never escape to traceback the just-published index."""
    from jarvis.index_cli import _install_semantic_extra

    monkeypatch.setattr("jarvis.index_cli.shutil.which", lambda name: "/fake/bin/uv")

    for exc in (
        FileNotFoundError(2, "No such file or directory"),
        PermissionError(13, "Permission denied"),
        UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid start byte"),
    ):
        def _raise(cmd, **kwargs):
            raise exc

        monkeypatch.setattr("jarvis.index_cli.subprocess.run", _raise)
        assert _install_semantic_extra() is False, repr(exc)


def test_cmd_index_non_tty_never_prompts_or_blocks(
    tmp_path: Path, monkeypatch
):
    """SEMA-02: a piped/cron `jarvis index` (either stream redirected)
    never prompts or blocks on stdin, never installs, and never writes
    the decline bit — never answered, so a later TTY run still offers."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: True)
    monkeypatch.setattr("jarvis.index_cli._at_interactive_tty", lambda: False)
    _script_input(
        monkeypatch, error=AssertionError("a non-TTY run must never prompt")
    )
    monkeypatch.setattr(
        "jarvis.index_cli._install_semantic_extra",
        lambda: (_ for _ in ()).throw(
            AssertionError("a non-TTY run must never install")
        ),
    )

    rc, _ = _offer_run(monkeypatch, tmp_path, "piped-repo")

    assert rc == 0
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get("piped-repo")
        assert entry is not None
        assert entry.semantic_declined is False
    finally:
        registry.close()


def test_at_interactive_tty_requires_both_streams_tty(monkeypatch):
    """SEMA-02 both-stream gate: pip's convention — either stream
    redirected means automation. Pins the stdin-TTY-but-stdout-piped edge
    from the phase coverage report (a prompt there would hang a pipe)."""
    import sys

    from jarvis.index_cli import _at_interactive_tty

    class _Stream:
        def __init__(self, tty: bool):
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    for stdin_tty, stdout_tty, expected in [
        (True, True, True),
        (True, False, False),  # stdin is a TTY but stdout is piped
        (False, True, False),
        (False, False, False),
    ]:
        monkeypatch.setattr(sys, "stdin", _Stream(stdin_tty))
        monkeypatch.setattr(sys, "stdout", _Stream(stdout_tty))
        assert _at_interactive_tty() is expected


def test_offer_semantic_defaults_true_only_on_index_subparser(tmp_path: Path):
    """SEMA-02 structural gate at the parser level: only the `index`
    subparser carries offer_semantic; reindex/watch/list (and their
    synthetic Namespaces) read False via getattr's default."""
    from jarvis.index_cli import build_parser

    parser = build_parser()

    assert parser.parse_args(["index", str(tmp_path)]).offer_semantic is True
    for argv in [["reindex", "some-slug"], ["watch", str(tmp_path)], ["list"]]:
        assert getattr(parser.parse_args(argv), "offer_semantic", False) is False, argv


def test_cmd_reindex_never_offers_semantic_install(
    tmp_path: Path, monkeypatch
):
    """SEMA-02 / planner decision 1: `jarvis reindex` delegates to
    `_cmd_index` through a synthetic Namespace that omits offer_semantic
    — so even with every other gate forced open (extra missing, both
    streams TTY) on a never-declined repo, reindex never prompts."""
    import argparse

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: True)

    # Seed a completed, never-declined row non-interactively.
    monkeypatch.setattr("jarvis.index_cli._at_interactive_tty", lambda: False)
    slug = "reindex-repo"
    rc, _ = _offer_run(monkeypatch, tmp_path, slug)
    assert rc == 0

    # Now every gate is open except the structural one, and prompting at
    # all is the failure.
    monkeypatch.setattr("jarvis.index_cli._at_interactive_tty", lambda: True)
    _script_input(monkeypatch, error=AssertionError("reindex must never prompt"))
    rc = cli._cmd_reindex(argparse.Namespace(slug=slug))

    assert rc == 0
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.semantic_declined is False
    finally:
        registry.close()


def test_cmd_watch_reindex_never_prompts_even_at_a_tty(
    tmp_path: Path, monkeypatch
):
    """SEMA-02 watch leg: watch IS a foreground TTY (05-RESEARCH Pitfall
    1), so the isatty gate alone could never protect it — the structural
    proof is that `_reindex` calls index_repo directly and can never
    reach `_cmd_index`'s offer. Extra missing, TTY forced True, input
    poisoned: still rc 0, no prompt, no raise. (The MCP leg is the same
    structural property — server.py imports no index path at all.)"""
    import argparse
    import itertools

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = tmp_path / "not-a-repo"
    repo_dir.mkdir()
    monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: True)
    monkeypatch.setattr("jarvis.index_cli._at_interactive_tty", lambda: True)
    _script_input(monkeypatch, error=AssertionError("watch must never prompt"))

    def fake_index_repo(path, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = _drive_cmd_watch(
        cli, monkeypatch,
        {"path": str(repo_dir), "slug": None, "scheme": None,
         "debounce": 0.0, "language": None, "fallback_search_only": None},
        sleep_results=itertools.chain(itertools.repeat(None, 5), [KeyboardInterrupt()]),
    )
    assert rc == 0


def test_semantic_extra_missing_detects_missing_top_level_modules(monkeypatch):
    """SEMA-01 detection set: exactly the two modules the semantic stage
    imports lazily — either missing means the extra is missing.
    tree_sitter_language_pack is deliberately NEVER queried (chunker.py
    falls back to fixed-window chunking; its absence never disables
    semantic)."""
    from jarvis.index_cli import _semantic_extra_missing

    available = {
        "lancedb": object(),
        "sentence_transformers": object(),
        "tree_sitter_language_pack": object(),
    }
    seen: list[str] = []

    def _find_spec(name):
        seen.append(name)
        return available.get(name)

    monkeypatch.setattr("jarvis.index_cli.importlib.util.find_spec", _find_spec)

    assert _semantic_extra_missing() is False  # both present
    seen.clear()
    available["lancedb"] = None
    assert _semantic_extra_missing() is True  # lancedb gone
    available["lancedb"] = object()
    available["sentence_transformers"] = None
    assert _semantic_extra_missing() is True  # sentence_transformers gone
    available["sentence_transformers"] = object()
    available["tree_sitter_language_pack"] = None
    assert _semantic_extra_missing() is False  # tree-sitter is not a requirement
    assert "tree_sitter_language_pack" not in seen


def test_cmd_index_no_prompt_when_extra_already_installed(
    tmp_path: Path, monkeypatch
):
    """Locked Area 3: extra installed → no prompt ever, and the decline
    bit is never written (the find_spec gate precedes the decline gate,
    so a stale declined bit is moot once the extra exists)."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: False)
    monkeypatch.setattr("jarvis.index_cli._at_interactive_tty", lambda: True)
    _script_input(
        monkeypatch, error=AssertionError("must not prompt when the extra exists")
    )

    rc, _ = _offer_run(monkeypatch, tmp_path, "installed-repo")

    assert rc == 0
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get("installed-repo")
        assert entry is not None
        assert entry.semantic_declined is False  # the bit is never written
    finally:
        registry.close()


def test_cmd_index_semantic_include_runs_on_declined_repo_without_clearing_bit(
    tmp_path: Path, monkeypatch
):
    """Locked Area 3: an explicit --semantic-include is direct user
    intent — the semantic stage runs with those prefixes on a declined
    repo, never blocked, and the decline bit stays set."""
    import argparse

    import jarvis.index_cli as cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    _mock_healthy_full_run(monkeypatch)
    _force_offer_seams(monkeypatch)
    slug = "include-repo"

    # Pre-decline the repo (one offer run, Enter = No).
    rc, _ = _offer_run(monkeypatch, tmp_path, slug, answer="")
    assert rc == 0

    # Now the extra is present (no offer) and the user passes an explicit
    # include: the pipeline stage must run with those prefixes.
    monkeypatch.setattr("jarvis.index_cli._semantic_extra_missing", lambda: False)
    stage_calls: list = []

    def _stage(repo_path, slug, root, include_prefixes=()):
        stage_calls.append((repo_path, slug, root, include_prefixes))
        return True

    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", _stage)
    rc = cli._cmd_index(argparse.Namespace(
        path=str(tmp_path / slug), slug=slug, scheme=None,
        semantic_include=["src/"], language=None, search_only=None,
        fallback_search_only=None, offer_semantic=True,
    ))

    assert rc == 0
    assert [c[3] for c in stage_calls] == [("src/",)]
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.semantic_declined is True  # never cleared
        assert entry.semantic_include == ("src/",)
    finally:
        registry.close()
