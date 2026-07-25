"""SQLite-backed registry of indexed repos: path, slug, language, commit_sha,
last_indexed, status.

Simplified from polaris-code-intelligence's `registry_store.py` (446 LOC,
SQLAlchemy async + Postgres, a registration state machine, Bitbucket team
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
    status TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class RegisteredRepo:
    slug: str
    path: str
    language: str
    commit_sha: str | None
    last_indexed: datetime
    status: str  # "indexed" | "indexing" | "failed"


def _row_to_repo(row: tuple) -> RegisteredRepo:
    slug, path, language, commit_sha, last_indexed, status = row
    return RegisteredRepo(
        slug=slug,
        path=path,
        language=language,
        commit_sha=commit_sha,
        last_indexed=datetime.fromisoformat(last_indexed),
        status=status,
    )


class Registry:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def upsert(self, slug: str, path: str, language: str, commit_sha: str | None, status: str) -> RegisteredRepo:
        last_indexed = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status",
            (slug, path, language, commit_sha, last_indexed.isoformat(), status),
        )
        self._conn.commit()
        return RegisteredRepo(
            slug=slug, path=path, language=language, commit_sha=commit_sha,
            last_indexed=last_indexed, status=status,
        )

    def mark_status(self, slug: str, status: str) -> None:
        self._conn.execute(
            "UPDATE repos SET status = ?, last_indexed = ? WHERE slug = ?",
            (status, datetime.now(UTC).isoformat(), slug),
        )
        self._conn.commit()

    def get(self, slug: str) -> RegisteredRepo | None:
        row = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status FROM repos WHERE slug = ?",
            (slug,),
        ).fetchone()
        return _row_to_repo(row) if row is not None else None

    def list(self) -> list[RegisteredRepo]:
        rows = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status FROM repos ORDER BY slug"
        ).fetchall()
        return [_row_to_repo(row) for row in rows]

    def forget(self, slug: str) -> bool:
        cursor = self._conn.execute("DELETE FROM repos WHERE slug = ?", (slug,))
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()
