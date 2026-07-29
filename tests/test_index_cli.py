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
from codeintel.index_cli import UnsupportedLanguageError, detect_language, index_repo
from codeintel.registry import Registry

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_py_repo"
SWIFT_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_swift_repo"

_REQUIRED_BINARIES = ["scip-python", "scip", "zoekt-index"]
_missing = [b for b in _REQUIRED_BINARIES if shutil.which(b) is None]

_SWIFT_REQUIRED_BINARIES = ["scip-swift", "scip", "zoekt-index"]
_missing_swift = [b for b in _SWIFT_REQUIRED_BINARIES if shutil.which(b) is None]


def test_detect_language_picks_python_for_py_files(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    language, cmd = detect_language(tmp_path)
    assert language == "python"
    assert cmd[0] == "scip-python"


def test_detect_language_picks_majority_extension(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"f{i}.ts").write_text("export const x = 1;\n")
    (tmp_path / "g.py").write_text("x = 1\n")
    language, _ = detect_language(tmp_path)
    assert language == "typescript"


def test_detect_language_ignores_node_modules_and_git(tmp_path: Path):
    (tmp_path / "src.py").write_text("x = 1\n")
    ignored = tmp_path / "node_modules" / "pkg"
    ignored.mkdir(parents=True)
    for i in range(5):
        (ignored / f"f{i}.ts").write_text("export const x = 1;\n")
    language, _ = detect_language(tmp_path)
    assert language == "python"


def test_detect_language_ignores_derived_data_and_dot_build(tmp_path: Path):
    (tmp_path / "src.swift").write_text("let x = 1\n")
    derived_data = tmp_path / "DerivedData" / "SourcePackages" / "checkouts" / "SomeDep"
    derived_data.mkdir(parents=True)
    dot_build = tmp_path / ".build" / "checkouts" / "SomeDep"
    dot_build.mkdir(parents=True)
    for i in range(5):
        (derived_data / f"f{i}.py").write_text("x = 1\n")
        (dot_build / f"g{i}.py").write_text("x = 1\n")
    language, _ = detect_language(tmp_path)
    assert language == "swift"


def test_detect_language_picks_swift_for_swift_files(tmp_path: Path):
    (tmp_path / "a.swift").write_text("let x = 1\n")
    (tmp_path / "b.swift").write_text("let y = 2\n")
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
    _, cmd = detect_language(tmp_path)
    assert cmd == ["scip-swift"], f"must stay bare for version tolerance, got {cmd}"


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


def test_detect_language_tie_break_prefers_earlier_priority_over_swift(tmp_path: Path):
    (tmp_path / "a.java").write_text("class A {}\n")
    (tmp_path / "b.java").write_text("class B {}\n")
    (tmp_path / "a.swift").write_text("let x = 1\n")
    (tmp_path / "b.swift").write_text("let y = 2\n")
    language, _ = detect_language(tmp_path)
    assert language == "java"


def test_detect_language_raises_for_no_supported_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello\n")
    with pytest.raises(UnsupportedLanguageError):
        detect_language(tmp_path)


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


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


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

    monkeypatch.setattr(semantic_module, "index_semantic", lambda *a, **k: 5)
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
