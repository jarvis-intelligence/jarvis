from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

from jarvis.registry import Registry


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
    from jarvis.registry import Registry
    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("r", "/p", "python", None, "indexed",
                        semantic_include=("src/gen", "vendor/pb"))
        assert registry.get("r").semantic_include == ("src/gen", "vendor/pb")
        assert registry.list()[0].semantic_include == ("src/gen", "vendor/pb")
    finally:
        registry.close()


def test_semantic_include_defaults_to_empty_tuple(tmp_path):
    from jarvis.registry import Registry
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
    from jarvis.registry import Registry
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




def test_ensure_column_is_idempotent(tmp_path: Path):
    """Second call must not raise: "duplicate column name" means a previous
    run (or a fresh _SCHEMA create) already added it."""
    import sqlite3

    from jarvis.registry import _ensure_column

    conn = sqlite3.connect(tmp_path / "r.db")
    conn.execute("CREATE TABLE repos (slug TEXT PRIMARY KEY)")
    _ensure_column(conn, "extra_col", "TEXT")
    _ensure_column(conn, "extra_col", "TEXT")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(repos)")}
    conn.close()
    assert "extra_col" in cols


def test_ensure_column_reraises_non_duplicate_errors(tmp_path: Path):
    """A lock timeout must not be swallowed as "already exists" -- that
    would leave the column missing while looking like success."""
    import sqlite3

    import pytest

    from jarvis.registry import _ensure_column

    conn = sqlite3.connect(tmp_path / "r.db")
    with pytest.raises(sqlite3.OperationalError):
        _ensure_column(conn, "c", "TEXT")  # no `repos` table exists
    conn.close()


def test_tracked_files_defaults_to_none_and_round_trips(tmp_path: Path):
    from jarvis.registry import Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        assert registry.get("myslug").tracked_files is None
        registry.mark_tracked_files("myslug", 133)
        assert registry.get("myslug").tracked_files == 133
    finally:
        registry.close()


def test_mark_tracked_files_survives_a_later_upsert(tmp_path: Path):
    """upsert's ON CONFLICT list must not clobber tracked_files -- a reindex
    upserts status before the new count is known."""
    from jarvis.registry import Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        registry.upsert("myslug", "/repos/mine", "python", "abc", "indexed")
        registry.mark_tracked_files("myslug", 133)
        registry.upsert("myslug", "/repos/mine", "python", "def", "indexing")
        assert registry.get("myslug").tracked_files == 133
    finally:
        registry.close()


def _entry(**overrides):
    """A RegisteredRepo for recovery-mapping tests; keyword overrides pick
    the origin/state under test."""
    from datetime import UTC, datetime

    from jarvis.registry import RegisteredRepo

    fields = dict(
        slug="mine", path="/repos/mine", language="python", commit_sha=None,
        last_indexed=datetime.now(UTC), status="failed",
    )
    fields.update(overrides)
    return RegisteredRepo(**fields)


def test_record_failure_creates_row_when_absent(tmp_path: Path):
    """D-05: a hard-failed FIRST index must still leave a row -- nothing
    else can explain the failure or make `jarvis reindex` work."""
    from jarvis.registry import ORIGIN_FAILED_HARD, Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        assert registry.get("new") is None
        entry = registry.record_failure(
            "new", "/repos/new", "python", ORIGIN_FAILED_HARD,
            "scip-python index failed (scip-python)",
            "scip-python index failed (scip-python):\nboom",
        )
        assert registry.get("new") == entry
        assert entry.path == "/repos/new"
        assert entry.language == "python"
        assert entry.status == "failed"
        assert entry.commit_sha is None
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason == "scip-python index failed (scip-python)"
        assert entry.status_stderr == "scip-python index failed (scip-python):\nboom"
    finally:
        registry.close()


def test_record_failure_overwrites_existing_row_and_preserves_search_only(tmp_path: Path):
    """D-06: a failed reindex fully overwrites the run facts (commit_sha
    NULL, status failed, fresh last_indexed, failure fields stamped) while
    search_only survives -- losing it would make _resolve_search_only
    re-run a build that already proved un-indexable."""
    import time
    from datetime import UTC, datetime

    from jarvis.registry import ORIGIN_FAILED_HARD, Registry

    registry = Registry(tmp_path / "registry.db")
    try:
        before = registry.upsert("mine", "/repos/mine", "python", "abc123",
                                 "indexed", search_only=True)
        time.sleep(0.002)  # guarantee a strictly later last_indexed timestamp
        registry.record_failure(
            "mine", "/repos/mine", "python", ORIGIN_FAILED_HARD,
            "zoekt-git-index failed", "zoekt-git-index failed:\nboom",
        )
        entry = registry.get("mine")
        assert entry is not None
        assert entry.status == "failed"
        assert entry.commit_sha is None
        assert entry.last_indexed > before.last_indexed
        assert entry.status_origin == ORIGIN_FAILED_HARD
        assert entry.status_reason == "zoekt-git-index failed"
        assert entry.status_stderr == "zoekt-git-index failed:\nboom"
        assert entry.search_only is True
    finally:
        registry.close()


def test_recovery_for_derives_per_origin_commands():
    """D-09/D-11/D-12: recovery is derived from origin at read time, one
    command per origin -- never persisted per-row."""
    from jarvis.registry import (
        ORIGIN_FAILED_HARD,
        ORIGIN_MANUAL,
        ORIGIN_SIGNATURE,
        recovery_for,
    )

    assert recovery_for(_entry(status_origin=ORIGIN_FAILED_HARD)) == "jarvis index /repos/mine"
    assert recovery_for(_entry(status_origin=ORIGIN_SIGNATURE)) == "jarvis reindex mine"
    assert recovery_for(_entry(status_origin=ORIGIN_MANUAL)) == (
        "jarvis forget mine && jarvis index /repos/mine"
    )


def test_recovery_for_treats_legacy_search_only_as_manual():
    """Pre-migration search_only=1 rows have a NULL origin; the D-10 escape
    (forget + re-index) is valid for whichever path created them."""
    from jarvis.registry import recovery_for

    assert recovery_for(_entry(search_only=True, status="search-only")) == (
        "jarvis forget mine && jarvis index /repos/mine"
    )


def test_recovery_for_returns_none_for_successful_and_unknown_rows():
    from jarvis.registry import recovery_for

    assert recovery_for(_entry(status="indexed", commit_sha="abc")) is None
    assert recovery_for(_entry(status_origin="from-the-future")) is None
