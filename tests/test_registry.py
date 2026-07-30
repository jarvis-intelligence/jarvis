from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

from codeintel.registry import Registry, _ensure_scheme_override_column


def test_upsert_then_get_roundtrips(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    repo = reg.get("my-repo")
    assert repo is not None
    assert repo.path == "/repos/my-repo"
    assert repo.language == "python"
    assert repo.commit_sha == "abc123"
    assert repo.status == "indexed"
    reg.close()


def test_upsert_same_slug_updates_in_place(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    reg.upsert("my-repo", "/repos/my-repo", "python", "def456", "indexed")
    assert len(reg.list()) == 1
    assert reg.get("my-repo").commit_sha == "def456"
    reg.close()


def test_mark_status_transition(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", None, "indexing")
    reg.mark_status("my-repo", "failed")
    assert reg.get("my-repo").status == "failed"
    reg.close()


def test_get_unknown_slug_returns_none(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    assert reg.get("nope") is None
    reg.close()


def test_list_orders_by_slug(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("zeta", "/z", "python", None, "indexed")
    reg.upsert("alpha", "/a", "python", None, "indexed")
    assert [r.slug for r in reg.list()] == ["alpha", "zeta"]
    reg.close()


def test_forget_removes_row_and_reports_whether_it_existed(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", None, "indexed")
    assert reg.forget("my-repo") is True
    assert reg.get("my-repo") is None
    assert reg.forget("my-repo") is False
    reg.close()


def test_registry_persists_across_reopen(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    Registry(db_path).upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    reopened = Registry(db_path)
    assert reopened.get("my-repo").commit_sha == "abc123"
    reopened.close()


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


def test_ensure_scheme_override_column_re_raises_non_duplicate_errors():
    """Verify that _ensure_scheme_override_column discriminates on error message.
    It should only swallow "duplicate column name" errors (idempotent), but
    re-raise other OperationalErrors like "database is locked" so they don't
    silently hide as "column already exists"."""
    # Mock connection that raises "database is locked" for ALTER TABLE
    mock_conn = Mock(spec=sqlite3.Connection)
    locked_error = sqlite3.OperationalError("database is locked")
    mock_conn.execute.side_effect = locked_error

    # Verify the error is re-raised, not swallowed
    try:
        _ensure_scheme_override_column(mock_conn)
        assert False, "Expected OperationalError to be re-raised"
    except sqlite3.OperationalError as exc:
        assert str(exc) == "database is locked"


def test_ensure_scheme_override_column_swallows_duplicate_column_error():
    """Verify that _ensure_scheme_override_column swallows only
    "duplicate column name" errors, leaving the migration idempotent."""
    mock_conn = Mock(spec=sqlite3.Connection)
    # SQLite's actual error message for duplicate column
    dup_column_error = sqlite3.OperationalError("duplicate column name: scheme_override")
    mock_conn.execute.side_effect = dup_column_error

    # Should not raise — error is swallowed
    _ensure_scheme_override_column(mock_conn)
    # If we reach here, the test passed (no exception was raised)


def test_mark_semantic_indexed_sets_timestamp(tmp_path: Path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("r", "/p", "python", "sha", "indexed")
    assert registry.get("r").semantic_indexed_at is None
    registry.mark_semantic_indexed("r")
    assert registry.get("r").semantic_indexed_at is not None
    registry.close()


def test_upsert_preserves_semantic_timestamp(tmp_path: Path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("r", "/p", "python", "sha", "indexed")
    registry.mark_semantic_indexed("r")
    stamp = registry.get("r").semantic_indexed_at
    registry.upsert("r", "/p", "python", "sha2", "indexed")
    assert registry.get("r").semantic_indexed_at == stamp
    registry.close()


def test_migration_is_idempotent(tmp_path: Path):
    Registry(tmp_path / "registry.db").close()
    Registry(tmp_path / "registry.db").close()  # second open must not raise


def test_semantic_include_roundtrips_as_tuple(tmp_path):
    from codeintel.registry import Registry
    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("r", "/p", "python", None, "indexed",
                        semantic_include=("src/gen", "vendor/pb"))
        assert registry.get("r").semantic_include == ("src/gen", "vendor/pb")
        assert registry.list()[0].semantic_include == ("src/gen", "vendor/pb")
    finally:
        registry.close()


def test_semantic_include_defaults_to_empty_tuple(tmp_path):
    from codeintel.registry import Registry
    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("r", "/p", "python", None, "indexed")
        assert registry.get("r").semantic_include == ()
    finally:
        registry.close()


def test_semantic_include_column_added_to_preexisting_db(tmp_path):
    """A registry.db created before this column exists must migrate in
    place rather than crash."""
    import sqlite3
    from codeintel.registry import Registry
    db = tmp_path / "registry.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE repos (slug TEXT PRIMARY KEY, path TEXT NOT NULL, "
        "language TEXT NOT NULL, commit_sha TEXT, last_indexed TEXT NOT NULL, "
        "status TEXT NOT NULL, scheme_override TEXT, semantic_indexed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO repos VALUES ('old', '/p', 'python', NULL, "
        "'2026-01-01T00:00:00+00:00', 'indexed', NULL, NULL)"
    )
    conn.commit()
    conn.close()

    registry = Registry(db)
    try:
        assert registry.get("old").semantic_include == ()
        registry.upsert("old", "/p", "python", None, "indexed",
                        semantic_include=("src/gen",))
        assert registry.get("old").semantic_include == ("src/gen",)
    finally:
        registry.close()
