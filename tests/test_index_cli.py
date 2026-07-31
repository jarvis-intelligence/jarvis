"""Unit tests for language detection, plus an end-to-end integration test
against `tests/fixtures/mini_py_repo/` using the real scip-python + scip
CLI binaries (marked `@pytest.mark.integration` — skipped if unavailable).
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from codeintel import config
from codeintel.index_cli import PARTIAL_STATUS, UnsupportedLanguageError, detect_language, index_repo
from codeintel.registry import Registry

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_py_repo"
SWIFT_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_swift_repo"

_REQUIRED_BINARIES = ["scip-python", "scip", "zoekt-index"]
_missing = [b for b in _REQUIRED_BINARIES if shutil.which(b) is None]

_SWIFT_REQUIRED_BINARIES = ["scip-swift", "scip", "zoekt-index"]
_missing_swift = [b for b in _SWIFT_REQUIRED_BINARIES if shutil.which(b) is None]


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def test_git_tracked_files_lists_committed_paths(tmp_path: Path):
    from codeintel.index_cli import _git_tracked_files

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
    from codeintel.index_cli import _git_tracked_files

    (tmp_path / "café.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)

    assert _git_tracked_files(tmp_path) == ["café.py"]


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


def test_git_head_raises_not_a_git_repository_for_non_git_directory(tmp_path: Path):
    """Distinct from the no-commits case above: a directory that isn't a
    git repository at all must not be misdiagnosed as "has no commits
    yet" -- `git rev-parse HEAD` fails identically in both cases, so
    `_git_head` must check `--is-inside-work-tree` first."""
    from codeintel.index_cli import NotAGitRepositoryError, _git_head

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
    `codeintel forget ..` could `shutil.rmtree()` outside the data dir."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("x = 1\n")
    with pytest.raises(ValueError):
        index_repo(repo_dir, slug="..", root=tmp_path / "data")
    # No registry or scip dir should have been created for the rejected slug.
    assert not (tmp_path / "data").exists()


def test_forget_rejects_dotdot_slug(tmp_path: Path, monkeypatch, capsys):
    from codeintel.index_cli import _cmd_forget
    import argparse

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
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

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == "indexed"
        assert entry.commit_sha is not None
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
    assert zoekt_shards, "zoekt-index should have written at least one shard"


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_index_repo_marks_failed_on_indexer_error(tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    monkeypatch.setattr(
        "codeintel.index_cli._LANGUAGE_INDEXERS",
        {".py": ("python", ["definitely-not-a-real-binary"])},
    )

    from codeintel.index_cli import IndexingError

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
    """Regression test for the codeintel-watch bug: a second index_repo()
    call with scheme=None (e.g. an unattended `codeintel watch` reindex)
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
    from codeintel.index_cli import _resolve_scheme

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="ios_theme_ui")
    assert _resolve_scheme(registry, "my-repo", scheme=None) == "ios_theme_ui"
    registry.close()


def test_resolve_scheme_prefers_explicit_value_over_stored(tmp_path: Path):
    from codeintel.index_cli import _resolve_scheme

    registry = Registry(tmp_path / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="old")
    assert _resolve_scheme(registry, "my-repo", scheme="new") == "new"
    registry.close()


def test_resolve_scheme_returns_none_for_unknown_slug(tmp_path: Path):
    from codeintel.index_cli import _resolve_scheme

    registry = Registry(tmp_path / "registry.db")
    assert _resolve_scheme(registry, "nope", scheme=None) is None
    registry.close()


def test_parse_scip_version_reads_standard_output():
    from codeintel.index_cli import parse_scip_version

    assert parse_scip_version("scip version v0.9.0") == (0, 9, 0)
    assert parse_scip_version("scip version v0.10.2\n") == (0, 10, 2)
    assert parse_scip_version("v1.0.0") == (1, 0, 0)


def test_parse_scip_version_returns_none_on_junk():
    from codeintel.index_cli import parse_scip_version

    assert parse_scip_version("") is None
    assert parse_scip_version("not a version") is None


def test_check_scip_version_rejects_v070(monkeypatch):
    """v0.7.0 converts successfully but silently drops every range."""
    import codeintel.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.7.0")
    with pytest.raises(cli.IndexingError) as exc:
        cli.check_scip_version()
    message = str(exc.value)
    assert "0.7.0" in message
    assert "0.9.0" in message, "must state the required floor"


def test_check_scip_version_accepts_v090(monkeypatch):
    import codeintel.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.9.0")
    cli.check_scip_version()  # must not raise


def test_check_scip_version_accepts_newer(monkeypatch):
    import codeintel.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v1.2.3")
    cli.check_scip_version()


def test_check_scip_version_tolerates_unparseable(monkeypatch):
    """An unrecognized format must not block indexing outright."""
    import codeintel.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "weird build")
    cli.check_scip_version()  # must not raise


def test_write_zoekt_meta_contains_slug(tmp_path: Path):
    import json as _json

    from codeintel.index_cli import _write_zoekt_meta

    meta_path = _write_zoekt_meta(tmp_path, "my-slug")
    assert meta_path.is_file()
    assert _json.loads(meta_path.read_text())["Name"] == "my-slug"


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
    from codeintel.index_cli import _remove_zoekt_shards

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
    from codeintel.index_cli import _remove_zoekt_shards

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "api_v16.00000.zoekt").write_bytes(b"x")
    (zoekt_dir / "api-gateway_v16.00000.zoekt").write_bytes(b"x")

    removed = _remove_zoekt_shards("api", root=tmp_path)

    assert len(removed) == 1
    assert not (zoekt_dir / "api_v16.00000.zoekt").exists()
    assert (zoekt_dir / "api-gateway_v16.00000.zoekt").exists()


def test_remove_zoekt_shards_is_safe_when_absent(tmp_path: Path):
    from codeintel.index_cli import _remove_zoekt_shards

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
    from codeintel.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=0, mentions=0)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is False
    finally:
        conn.close()


def test_index_has_navigation_data_true_when_populated(tmp_path: Path):
    from codeintel.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=1, mentions=14)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is True
    finally:
        conn.close()


def test_index_has_navigation_data_false_when_only_chunks(tmp_path: Path):
    from codeintel.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=2, mentions=0)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is False
    finally:
        conn.close()


def test_reindex_forwards_stored_scheme_override(tmp_path: Path, monkeypatch):
    import argparse
    import codeintel.index_cli as cli

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("my-repo", "/repos/my-repo", "swift", "abc123", "indexed", scheme_override="ios_theme_ui",
                    semantic_include=("src/gen",))
    registry.close()

    captured: dict = {}

    def fake_index_repo(path, *, slug=None, root=None, scheme=None, semantic_include=None,
                        language=None):
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

    from codeintel import config
    from codeintel.index_cli import _cmd_forget
    from codeintel.registry import Registry

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))

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

    import codeintel
    from codeintel.index_cli import _run_semantic_stage

    # Setting the sys.modules entry to None is the standard trick to force
    # the next `import` to raise ImportError -- but if some other test
    # module already ran `import codeintel.semantic` earlier in the suite,
    # Python has cached it as an attribute on the `codeintel` package
    # object, and `from codeintel import semantic` resolves via that
    # attribute without consulting sys.modules at all. Clearing the
    # attribute too makes this deterministic regardless of test order.
    monkeypatch.setitem(sys.modules, "codeintel.semantic", None)
    monkeypatch.delattr(codeintel, "semantic", raising=False)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    assert "uv sync --extra semantic" in capsys.readouterr().err


def test_semantic_stage_failure_is_nonfatal(monkeypatch, capsys):
    import codeintel.semantic as semantic_module
    from codeintel.index_cli import _run_semantic_stage

    def _boom(*args, **kwargs):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(semantic_module, "index_semantic", _boom)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    assert "still published" in capsys.readouterr().err


def test_semantic_stage_success_returns_true(monkeypatch):
    import codeintel.semantic as semantic_module
    from codeintel.index_cli import _run_semantic_stage
    from codeintel.semantic import SemanticIndexReport

    monkeypatch.setattr(semantic_module, "index_semantic",
                        lambda *a, **k: SemanticIndexReport(rows=5, files=1))
    assert _run_semantic_stage(Path("/repo"), "slug", None) is True


def test_forget_removes_lance_table_dir(tmp_path: Path, monkeypatch):
    import argparse

    from codeintel.index_cli import _cmd_forget

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
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

    monkeypatch.setenv("CODEINTEL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    import codeintel.embeddings as embeddings_module
    monkeypatch.setattr(embeddings_module, "_default", None)  # reset singleton

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, slug="semfix", root=data_root)

    from codeintel.semantic import semantic_search
    result = semantic_search(slug, "function definition", root=data_root)
    assert result["total"] >= 1
    assert all("filePath" in r for r in result["results"])


def test_semantic_include_flag_reaches_index_repo_as_a_tuple(tmp_path, monkeypatch):
    """The CLI collects repeated --semantic-include into a list; index_repo
    takes a tuple. Registry persistence itself is covered in test_registry.py."""
    import argparse

    from codeintel import index_cli

    captured = {}

    def _fake_index_repo(repo_path, *, slug=None, root=None, scheme=None,
                         semantic_include=None, language=None):
        captured["semantic_include"] = semantic_include
        return "myrepo"

    monkeypatch.setattr(index_cli, "index_repo", _fake_index_repo)
    args = argparse.Namespace(path=str(tmp_path), slug="myrepo", scheme=None,
                              semantic_include=["src/gen", "vendor/pb"])
    assert index_cli._cmd_index(args) == 0
    assert captured["semantic_include"] == ("src/gen", "vendor/pb")


def test_semantic_report_names_every_skipped_file(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport, SkippedFile

    report = SemanticIndexReport(
        rows=95, files=15,
        skipped=(SkippedFile("src/codeintel/scip_pb2.py", "banner:do not edit"),),
        truncated=3,
    )
    _print_semantic_report(report)
    err = capsys.readouterr().err
    assert "semantic: 95 chunks from 15 files" in err
    assert "semantic: skipped src/codeintel/scip_pb2.py (banner:do not edit)" in err
    assert "3 chunks exceeded" in err


def test_semantic_report_omits_truncation_line_when_zero(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=0))
    err = capsys.readouterr().err
    assert "exceeded" not in err
    assert "could not measure" not in err


def test_semantic_report_notes_unmeasured_truncation(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=None))
    assert "could not measure truncation" in capsys.readouterr().err


def test_report_prints_token_percentiles(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport, TokenStats

    _print_semantic_report(SemanticIndexReport(
        rows=95, files=15, truncated=0, token_stats=TokenStats(180, 410, 498)))
    assert "chunk tokens p50=180 p90=410 max=498" in capsys.readouterr().err


def test_report_omits_percentiles_when_absent(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=0))
    assert "chunk tokens" not in capsys.readouterr().err


def test_report_prints_prefix_warning(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(
        rows=10, files=2, truncated=0, prefix_warning="model X is not in the map"))
    assert "model X is not in the map" in capsys.readouterr().err


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


def test_index_repo_raises_unsupported_language_for_stale_registry_override(
    tmp_path: Path, monkeypatch
):
    """`argparse`'s `choices=` only guards a fresh `--language` value typed
    by a user -- it does not guard a value already persisted in
    `registry.db` from an earlier version (e.g. a language a future release
    stops supporting, or a hand-edited row). `codeintel reindex` reaches
    `index_repo` with that stale value; it must raise `UnsupportedLanguageError`
    (caught at the CLI boundary), not a bare `KeyError`."""
    import codeintel.index_cli as cli

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
    import codeintel.index_cli as cli

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
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
    import codeintel.index_cli as cli

    def fake_index_repo(*args, **kwargs):
        raise cli.NotAGitRepositoryError(f"{tmp_path} is not a git repository (fake)")

    monkeypatch.setattr(cli, "index_repo", fake_index_repo)

    rc = cli._cmd_index(argparse.Namespace(
        path=str(tmp_path), slug=None, scheme=None, semantic_include=None, language=None,
    ))

    assert rc == 1
    assert "not a git repository" in capsys.readouterr().err
