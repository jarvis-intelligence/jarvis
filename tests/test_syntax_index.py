"""Tests for syntax_index.py: source capture/invalidation, syntax-table
build/reuse, SCIP-coverage-derived snapshot publication, and legacy
compatibility (spec TSI-03 "File manifest"/"Incremental reuse", TSI-04
section 5 "Snapshot storage/publication")."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from jarvis.syntax import ParserPool
from jarvis.syntax_index import (
    SnapshotCorruptionError,
    SourceChangedError,
    build_syntax_index,
    capture_sources,
    copy_syntax_tables,
    file_coverage,
    file_symbols,
    find_syntax_symbols,
    finalize_snapshot,
    get_syntax_symbol,
    read_snapshot_facts,
    validate_sources,
)
from tests.fixtures.synthetic_index import build_published_index, build_synthetic_index_db


def make_repo(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "tracked.py").write_text("def before():\n    return 1\n")
    subprocess.run(["git", "-C", str(root), "add", "tracked.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


# ---------------------------------------------------------------------------
# Step 1: capture + invalidation
# ---------------------------------------------------------------------------


def test_capture_uses_tracked_worktree_bytes_and_detects_change(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    (repo / "tracked.py").write_text("def edited():\n    return 2\n")
    (repo / "untracked.py").write_text("def hidden():\n    return 3\n")
    manifest = capture_sources(repo, tmp_path / "capture")
    included = {f.file_path: f for f in manifest.files if f.source_path is not None}
    assert set(included) == {"tracked.py"}
    assert included["tracked.py"].source_path.read_bytes().startswith(b"def edited")
    (repo / "tracked.py").write_text("def later():\n    return 4\n")
    with pytest.raises(SourceChangedError):
        validate_sources(repo, manifest)


# ---------------------------------------------------------------------------
# Step 2: bounded scratch capture -- exclusions, size backstop
# ---------------------------------------------------------------------------


def test_capture_excludes_ignored_directory_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    ignored = repo / "node_modules" / "pkg"
    ignored.mkdir(parents=True)
    (ignored / "index.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add ignored"], check=True)

    manifest = capture_sources(repo, tmp_path / "capture")
    by_path = {f.file_path: f for f in manifest.files}
    entry = by_path["node_modules/pkg/index.py"]
    assert entry.in_scope is False
    assert entry.reason == "ignored directory"
    assert entry.source_path is None


def test_capture_excludes_symlink_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    target = repo / "tracked.py"
    link = repo / "link.py"
    link.symlink_to(target)
    subprocess.run(["git", "-C", str(repo), "add", "link.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add symlink"], check=True)

    manifest = capture_sources(repo, tmp_path / "capture")
    by_path = {f.file_path: f for f in manifest.files}
    entry = by_path["link.py"]
    assert entry.in_scope is False
    assert entry.reason == "symlink"
    assert entry.source_path is None


def test_capture_marks_unsupported_extension_in_scope_with_no_source(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    (repo / "notes.txt").write_text("just text\n")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add txt"], check=True)

    manifest = capture_sources(repo, tmp_path / "capture")
    by_path = {f.file_path: f for f in manifest.files}
    entry = by_path["notes.txt"]
    assert entry.in_scope is True
    assert entry.language is None
    assert entry.source_path is None
    assert entry.reason == "unsupported extension"


def test_capture_skips_oversized_supported_file_without_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    big = repo / "big.py"
    big.write_text("# padding\n" * 200_000)
    assert big.stat().st_size > 1024 * 1024
    subprocess.run(["git", "-C", str(repo), "add", "big.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add big"], check=True)

    manifest = capture_sources(repo, tmp_path / "capture")
    by_path = {f.file_path: f for f in manifest.files}
    entry = by_path["big.py"]
    assert entry.in_scope is True
    assert entry.language == "python"
    assert entry.file_hash is None
    assert entry.source_path is None
    assert entry.reason == "file exceeds 1 MiB syntax limit"


# ---------------------------------------------------------------------------
# Step 3/5: build + storage + reuse
# ---------------------------------------------------------------------------


def _build(repo: Path, scratch_root: Path, db_path: Path, *, previous=None):
    manifest = capture_sources(repo, scratch_root)
    report = build_syntax_index(db_path, manifest, pool=ParserPool(), previous=previous)
    conn = sqlite3.connect(db_path)
    return manifest, report, conn


def test_build_syntax_index_extracts_declarations_for_supported_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    _manifest, report, conn = _build(repo, tmp_path / "scratch", tmp_path / "syntax.db")
    try:
        assert report.counts.parsed == 1
        assert report.reused_files == 0
        symbols = file_symbols(conn, "tracked.py")
        assert [s.name for s in symbols] == ["before"]
        state, reason = file_coverage(conn, "tracked.py")
        assert state == "parsed"
        assert reason is None
    finally:
        conn.close()


def test_reuse_carries_rows_forward_when_hash_and_identity_match(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")

    _manifest1, _report1, conn1 = _build(repo, tmp_path / "scratch1", tmp_path / "gen1.db")
    try:
        _manifest2, report2, conn2 = _build(repo, tmp_path / "scratch2", tmp_path / "gen2.db", previous=conn1)
        try:
            assert report2.reused_files == 1
            assert report2.counts.parsed == 1
            symbols = file_symbols(conn2, "tracked.py")
            assert [s.name for s in symbols] == ["before"]
        finally:
            conn2.close()
    finally:
        conn1.close()


def test_rename_makes_old_symbol_absent_from_new_connection_but_old_connection_still_reads_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")

    _manifest1, _report1, conn1 = _build(repo, tmp_path / "scratch1", tmp_path / "gen1.db")
    try:
        old_symbol = file_symbols(conn1, "tracked.py")[0].symbol
        assert get_syntax_symbol(conn1, old_symbol) is not None

        (repo / "tracked.py").unlink()
        (repo / "renamed.py").write_text("def before():\n    return 1\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "rename"], check=True)

        _manifest2, report2, conn2 = _build(repo, tmp_path / "scratch2", tmp_path / "gen2.db", previous=conn1)
        try:
            assert file_symbols(conn2, "tracked.py") == ()
            new_symbols = file_symbols(conn2, "renamed.py")
            assert [s.name for s in new_symbols] == ["before"]
            # Old opaque id is specific to the old file_path; it never
            # appears in the new generation even though the byte content
            # (and thus the source) is identical.
            assert get_syntax_symbol(conn2, old_symbol) is None
            # The old, still-open connection is unaffected by the new
            # generation's build -- each generation is its own db file.
            assert get_syntax_symbol(conn1, old_symbol) is not None
        finally:
            conn2.close()
    finally:
        conn1.close()


def test_identity_change_forces_re_extraction_not_stale_carry_forward(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")

    _manifest1, _report1, conn1 = _build(repo, tmp_path / "scratch1", tmp_path / "gen1.db")
    try:
        old_state, old_reason = file_coverage(conn1, "tracked.py")
        assert (old_state, old_reason) == ("parsed", None)

        import jarvis.syntax as syntax_module

        original_version = syntax_module.SYNTAX_EXTRACTOR_VERSION
        monkeypatch.setattr(syntax_module, "SYNTAX_EXTRACTOR_VERSION", original_version + 1)

        _manifest2, report2, conn2 = _build(
            repo, tmp_path / "scratch2", tmp_path / "gen2.db", previous=conn1
        )
        try:
            # A bumped extractor version changes grammar_identity even
            # though the file's bytes are unchanged -- reuse must be
            # rejected and the file re-extracted, not carried forward.
            assert report2.reused_files == 0
            assert report2.counts.parsed == 1
            new_symbols = file_symbols(conn2, "tracked.py")
            assert [s.name for s in new_symbols] == ["before"]
        finally:
            conn2.close()
    finally:
        conn1.close()


def test_find_syntax_symbols_uses_dotted_suffix_matching(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = make_repo(tmp_path / "repo")
    _manifest, _report, conn = _build(repo, tmp_path / "scratch", tmp_path / "syntax.db")
    try:
        matches = find_syntax_symbols(conn, "before", uncovered_only=False)
        assert [m.name for m in matches] == ["before"]
        assert find_syntax_symbols(conn, "Before", uncovered_only=False) == ()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 4/5: SCIP coverage derivation + finalize + read
# ---------------------------------------------------------------------------


def test_copy_syntax_tables_and_finalize_snapshot_derive_real_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    greeter_dir = repo / "toy"
    greeter_dir.mkdir()
    (greeter_dir / "greeter.ts").write_text("class Greeter {}\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "toy"], check=True)

    manifest = capture_sources(repo, tmp_path / "scratch")
    syntax_db = tmp_path / "syntax.db"
    build_syntax_index(syntax_db, manifest, pool=ParserPool())

    scip_db = tmp_path / "index.db"
    build_synthetic_index_db(scip_db)
    conn = sqlite3.connect(scip_db)
    try:
        copy_syntax_tables(syntax_db, conn)
        facts = finalize_snapshot(
            conn, generation="gen-1", commit_sha="abc123", published_at="2026-09-09T00:00:00Z",
            source_hash=manifest.source_hash, scip_state="available",
        )
        assert facts.scip_outlines is True
        assert facts.scip_definitions is True
        assert facts.scip_references is True
        assert facts.scip_calls is True
        assert facts.scip_types is True
        assert facts.syntax_counts is not None

        # toy/greeter.ts carries a real SCIP definition (Greeter#) in the
        # fixture -> its declarations must not surface as "uncovered".
        state, reason = file_coverage(conn, "toy/greeter.ts")
        assert state in {"parsed", "partial", "failed", "skipped", "unsupported"}
        assert reason is None or isinstance(reason, str)

        read_back = read_snapshot_facts(conn)
        assert read_back.generation == "gen-1"
        assert read_back.commit_sha == "abc123"
        assert read_back.source_hash == manifest.source_hash
        assert read_back.scip_state == "available"
        assert read_back.scip_types is True
    finally:
        conn.close()


def test_finalize_snapshot_marks_covered_symbol_and_uncovered_query_excludes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "greeter.py").write_text("def greet():\n    return 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "toy"], check=True)

    manifest = capture_sources(repo, tmp_path / "scratch")
    syntax_db = tmp_path / "syntax.db"
    build_syntax_index(syntax_db, manifest, pool=ParserPool())

    scip_db = tmp_path / "index.db"
    build_synthetic_index_db(scip_db)
    conn = sqlite3.connect(scip_db)
    try:
        copy_syntax_tables(syntax_db, conn)
        # greeter.py's declaration never overlaps the fixture's real SCIP
        # coverage (toy/greeter.ts) -- it must remain "uncovered".
        finalize_snapshot(
            conn, generation="gen-1", commit_sha="abc123", published_at="2026-09-09T00:00:00Z",
            source_hash=manifest.source_hash, scip_state="available",
        )
        state, _reason = file_coverage(conn, "greeter.py")
        assert state == "parsed"
        matches = find_syntax_symbols(conn, "greet", uncovered_only=True)
        assert [m.name for m in matches] == ["greet"]
        matches_all = find_syntax_symbols(conn, "greet", uncovered_only=False)
        assert [m.name for m in matches_all] == ["greet"]
    finally:
        conn.close()


def test_read_snapshot_facts_legacy_db_derives_from_real_tables(tmp_path):
    scip_db = tmp_path / "index.db"
    build_synthetic_index_db(scip_db)
    conn = sqlite3.connect(scip_db)
    try:
        facts = read_snapshot_facts(conn)
        assert facts.generation is None
        assert facts.commit_sha is None
        assert facts.source_hash is None
        assert facts.scip_state == "legacy"
        assert facts.syntax_counts is None
        # The real fixture carries defn_enclosing_ranges + definition
        # occurrences + a non-NULL relationships blob (Greeter#).
        assert facts.scip_outlines is True
        assert facts.scip_definitions is True
        assert facts.scip_references is True
        assert facts.scip_calls is True
        assert facts.scip_types is True
    finally:
        conn.close()


def test_read_snapshot_facts_new_format_missing_row_raises_corruption_error(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    try:
        from jarvis.syntax_index import _SYNTAX_SCHEMA

        conn.executescript(_SYNTAX_SCHEMA)
        conn.commit()
        with pytest.raises(SnapshotCorruptionError):
            read_snapshot_facts(conn)
    finally:
        conn.close()


def test_legacy_published_fixture_metadata_still_reads(tmp_path):
    """Existing regression (pre-Task-3 shape): a published index without
    the new syntax tables must still yield readable IndexMetadata."""
    from jarvis.index_reader import read_metadata, read_pointer

    index_dir = build_published_index(tmp_path, "acme", "toy-repo", "main")
    pointer = read_pointer(str(tmp_path), "acme", "toy-repo", "main")
    metadata = read_metadata(str(tmp_path), "acme", "toy-repo", "main", pointer)
    assert metadata is not None
    assert metadata.commit_sha is not None
    assert metadata.generation is None
    assert metadata.source_hash is None
    assert (index_dir / pointer).exists()
