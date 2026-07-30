"""SQLite-backed registry of indexed repos: path, slug, language, commit_sha,
last_indexed, status.

Simplified from an internal reference implementation's `registry_store.py` (446 LOC,
SQLAlchemy async + Postgres, a registration state machine, per-team
quotas, an audit log) — codeintel is a single-user, local-first tool with
no multi-tenancy, no auth, and no state machine to enforce, so this is a
plain stdlib sqlite3 CRUD table instead.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    slug TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    commit_sha TEXT,
    last_indexed TEXT NOT NULL,
    status TEXT NOT NULL,
    scheme_override TEXT,
    semantic_indexed_at TEXT,
    semantic_include TEXT
)
"""


def _ensure_scheme_override_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration for databases created before this column
    existed. `ALTER TABLE ... ADD COLUMN` on a column that already exists
    raises `sqlite3.OperationalError` with a "duplicate column name"
    message -- caught and ignored, since that means a previous run (or a
    fresh `_SCHEMA` create) already added it. Any other `OperationalError`
    (e.g. "database is locked" from a concurrent `codeintel watch`
    reindex) is re-raised rather than silently swallowed -- otherwise a
    lock timeout during migration would look identical to "column already
    exists" while actually leaving the column missing."""
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN scheme_override TEXT")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _ensure_semantic_indexed_at_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration for databases created before this column
    existed. `ALTER TABLE ... ADD COLUMN` on a column that already exists
    raises `sqlite3.OperationalError` with a "duplicate column name"
    message -- caught and ignored, since that means a previous run (or a
    fresh `_SCHEMA` create) already added it. Any other `OperationalError`
    (e.g. "database is locked" from a concurrent `codeintel watch`
    reindex) is re-raised rather than silently swallowed -- otherwise a
    lock timeout during migration would look identical to "column already
    exists" while actually leaving the column missing."""
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN semantic_indexed_at TEXT")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _ensure_semantic_include_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration for databases created before this column
    existed. Same contract as `_ensure_scheme_override_column`: a
    "duplicate column name" error means a previous run (or a fresh
    `_SCHEMA` create) already added it, so it is ignored; any other
    `OperationalError` (e.g. "database is locked" from a concurrent
    `codeintel watch` reindex) is re-raised rather than swallowed."""
    try:
        conn.execute("ALTER TABLE repos ADD COLUMN semantic_include TEXT")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _split_include(raw: str | None) -> tuple[str, ...]:
    """Force-include prefixes are stored newline-joined; NULL or empty
    means none. Newline is a safe separator — a path prefix cannot
    contain one."""
    return tuple(prefix for prefix in (raw or "").split("\n") if prefix)


def _join_include(prefixes: tuple[str, ...]) -> str | None:
    return "\n".join(prefixes) or None


@dataclass(frozen=True)
class RegisteredRepo:
    slug: str
    path: str
    language: str
    commit_sha: str | None
    last_indexed: datetime
    status: str  # "indexed" | "indexing" | "failed" | "partial"
    scheme_override: str | None = None
    semantic_indexed_at: datetime | None = None
    semantic_include: tuple[str, ...] = ()


def _row_to_repo(row: tuple) -> RegisteredRepo:
    (slug, path, language, commit_sha, last_indexed, status,
     scheme_override, semantic_indexed_at, semantic_include) = row
    return RegisteredRepo(
        slug=slug,
        path=path,
        language=language,
        commit_sha=commit_sha,
        last_indexed=datetime.fromisoformat(last_indexed),
        status=status,
        scheme_override=scheme_override,
        semantic_indexed_at=datetime.fromisoformat(semantic_indexed_at) if semantic_indexed_at is not None else None,
        semantic_include=_split_include(semantic_include),
    )


class Registry:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        # `codeintel watch`'s background reindex and a manual `codeintel
        # index`/`reindex` can legitimately race on this same file — a
        # busy_timeout makes SQLite retry for up to 5s instead of raising
        # "database is locked" on the first contended write.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        _ensure_scheme_override_column(self._conn)
        _ensure_semantic_indexed_at_column(self._conn)
        _ensure_semantic_include_column(self._conn)

    def upsert(
        self,
        slug: str,
        path: str,
        language: str,
        commit_sha: str | None,
        status: str,
        scheme_override: str | None = None,
        semantic_include: tuple[str, ...] = (),
    ) -> RegisteredRepo:
        last_indexed = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, "
            "scheme_override, semantic_indexed_at, semantic_include) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status, "
            "scheme_override=excluded.scheme_override, "
            "semantic_include=excluded.semantic_include",
            (slug, path, language, commit_sha, last_indexed.isoformat(), status,
             scheme_override, _join_include(semantic_include)),
        )
        self._conn.commit()
        return RegisteredRepo(
            slug=slug, path=path, language=language, commit_sha=commit_sha,
            last_indexed=last_indexed, status=status, scheme_override=scheme_override,
            semantic_indexed_at=None, semantic_include=semantic_include,
        )

    def mark_status(self, slug: str, status: str) -> None:
        self._conn.execute(
            "UPDATE repos SET status = ?, last_indexed = ? WHERE slug = ?",
            (status, datetime.now(UTC).isoformat(), slug),
        )
        self._conn.commit()

    def mark_semantic_indexed(self, slug: str) -> None:
        self._conn.execute(
            "UPDATE repos SET semantic_indexed_at = ? WHERE slug = ?",
            (datetime.now(UTC).isoformat(), slug),
        )
        self._conn.commit()

    def get(self, slug: str) -> RegisteredRepo | None:
        row = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include "
            "FROM repos WHERE slug = ?",
            (slug,),
        ).fetchone()
        return _row_to_repo(row) if row is not None else None

    def list(self) -> list[RegisteredRepo]:
        rows = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include "
            "FROM repos ORDER BY slug"
        ).fetchall()
        return [_row_to_repo(row) for row in rows]

    def forget(self, slug: str) -> bool:
        cursor = self._conn.execute("DELETE FROM repos WHERE slug = ?", (slug,))
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()
