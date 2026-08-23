"""SQLite-backed registry of indexed repos: path, slug, language, commit_sha,
last_indexed, status.

Simplified from an internal reference implementation's `registry_store.py` (446 LOC,
SQLAlchemy async + Postgres, a registration state machine, per-team
quotas, an audit log) — jarvis is a single-user, local-first tool with
no multi-tenancy, no auth, and no state machine to enforce, so this is a
plain stdlib sqlite3 CRUD table instead.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Status for a repo published WITHOUT a SCIP index: Zoekt and the semantic
# table are live, navigation is not. Distinct from index_cli's PARTIAL_STATUS,
# which means a SCIP index exists but carries no occurrence ranges.
SEARCH_ONLY_STATUS = "search-only"

# Phase 3: a repo whose full build failed post-build-start with the opt-in
# fallback enabled — search is published (via _publish_search_only), but
# unlike SEARCH_ONLY_STATUS the row keeps search_only=False so every later
# run retries the full build (FALL-03 self-heal).
DEGRADED_STATUS = "degraded"

# Origin taxonomy for degraded/failed rows (D-01): stored as readable slugs
# so they can be inspected straight from the sqlite3 CLI. Additive -- Phase
# 3's `degraded` slots in as DEGRADED_STATUS above plus the ORIGIN_FALLBACK
# slug and one recovery_for branch, with no schema or payload change.
ORIGIN_FAILED_HARD = "failed_hard"
ORIGIN_SIGNATURE = "signature"
ORIGIN_MANUAL = "manual"
ORIGIN_FALLBACK = "fallback"

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
    semantic_include TEXT,
    language_override TEXT,
    search_only INTEGER NOT NULL DEFAULT 0,
    tracked_files INTEGER,
    status_origin TEXT,
    status_reason TEXT,
    status_stderr TEXT
)
"""


def _ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Idempotent `ALTER TABLE repos ADD COLUMN` for databases created before
    `name` existed.

    A "duplicate column name" `OperationalError` means a previous run (or a
    fresh `_SCHEMA` create) already added it, so it is ignored. Any other
    `OperationalError` -- notably "database is locked" from a concurrent
    `jarvis watch` reindex -- is re-raised rather than swallowed: a lock
    timeout during migration would otherwise look identical to "already
    exists" while actually leaving the column missing.
    """
    try:
        conn.execute(f"ALTER TABLE repos ADD COLUMN {name} {decl}")
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
    status: str  # "indexed" | "indexing" | "failed" | "partial" | "search-only"
    scheme_override: str | None = None
    semantic_indexed_at: datetime | None = None
    semantic_include: tuple[str, ...] = ()
    language_override: str | None = None
    search_only: bool = False
    tracked_files: int | None = None
    # Failure/degradation cause (Phase 1): origin slug (D-01), one-line
    # classified reason (D-03), and the complete failure text (D-02).
    # NULL on every successful run -- `upsert` clears all three (D-04).
    status_origin: str | None = None
    status_reason: str | None = None
    status_stderr: str | None = None
    # Opt-in self-healing fallback (Phase 3): tri-state NULL/0/1. NULL =
    # never set, defer to the env tier; set only by `set_fallback_enabled`
    # with the explicit CLI value (FALL-02, Pitfall 1).
    fallback_enabled: bool | None = None
    # Semantic-install decline memory (Phase 5): 1 = user declined the
    # offer; NULL = never asked. No tri-state — NULL reads False.
    semantic_declined: bool = False


def _row_to_repo(row: tuple) -> RegisteredRepo:
    (slug, path, language, commit_sha, last_indexed, status,
     scheme_override, semantic_indexed_at, semantic_include, language_override,
     search_only, tracked_files, status_origin, status_reason, status_stderr,
     fallback_enabled, semantic_declined) = row
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
        language_override=language_override,
        search_only=bool(search_only),
        tracked_files=tracked_files,
        status_origin=status_origin,
        status_reason=status_reason,
        status_stderr=status_stderr,
        fallback_enabled=bool(fallback_enabled) if fallback_enabled is not None else None,
        semantic_declined=bool(semantic_declined) if semantic_declined is not None else False,
    )


def origin_of(entry: RegisteredRepo) -> str | None:
    """Origin slug for a row at read time. Pre-migration search_only=1 rows
    carry a NULL origin -- manual vs signature is unrecoverable post-hoc,
    and the manual escape is valid for whichever created the row, so they
    read as 'manual'."""
    if entry.status_origin is not None:
        return entry.status_origin
    return ORIGIN_MANUAL if entry.search_only else None


def recovery_for(entry: RegisteredRepo) -> str | None:
    """Derive the recovery command from the row's origin at read time
    (D-09) -- a mapping in code, never persisted per-row, so wording
    changes and Phase 3's `degraded` origin need no data migration.
    Returns None when there is nothing to recover from (successful rows,
    origin-less rows, unknown future origins)."""
    origin = origin_of(entry)
    if origin == ORIGIN_FAILED_HARD:
        return f"jarvis index {entry.path}"  # D-12: the full original command
    if origin == ORIGIN_SIGNATURE:
        return f"jarvis reindex {entry.slug}"  # D-11: generic; per-signature remedy prose stays out
    if origin == ORIGIN_FALLBACK:
        # Locked wording (Phase 3 Area 3): names the self-heal — a plain
        # reindex retries the full build; no forget/escape is needed.
        return f"fix the indexer failure, then `jarvis reindex {entry.slug}` (full build retries automatically)"
    if origin == ORIGIN_MANUAL:
        return f"jarvis forget {entry.slug} && jarvis index {entry.path}"  # D-10: the one-way-flag escape
    return None


class Registry:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        # `jarvis watch`'s background reindex and a manual `jarvis
        # index`/`reindex` can legitimately race on this same file — a
        # busy_timeout makes SQLite retry for up to 5s instead of raising
        # "database is locked" on the first contended write.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        _ensure_column(self._conn, "scheme_override", "TEXT")
        _ensure_column(self._conn, "semantic_indexed_at", "TEXT")
        _ensure_column(self._conn, "semantic_include", "TEXT")
        _ensure_column(self._conn, "language_override", "TEXT")
        _ensure_column(self._conn, "search_only", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(self._conn, "tracked_files", "INTEGER")
        _ensure_column(self._conn, "status_origin", "TEXT")
        _ensure_column(self._conn, "status_reason", "TEXT")
        _ensure_column(self._conn, "status_stderr", "TEXT")
        # Tri-state NULL/0/1 (FALL-02). NULL = never set, defer to the env
        # tier; written only via set_fallback_enabled with the explicit
        # CLI value, never by upsert (Pitfall 1).
        _ensure_column(self._conn, "fallback_enabled", "INTEGER")
        # SEMA-01 decline memory. NULL = never answered (offer again);
        # written only via set_semantic_declined — never by upsert, whose
        # transitional `indexing` writes would NULL-reset it (the
        # tracked_files trap).
        _ensure_column(self._conn, "semantic_declined", "INTEGER")

    def upsert(
        self,
        slug: str,
        path: str,
        language: str,
        commit_sha: str | None,
        status: str,
        scheme_override: str | None = None,
        semantic_include: tuple[str, ...] = (),
        language_override: str | None = None,
        search_only: bool = False,
        status_origin: str | None = None,
        status_reason: str | None = None,
        status_stderr: str | None = None,
    ) -> RegisteredRepo:
        last_indexed = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, "
            "scheme_override, semantic_indexed_at, semantic_include, language_override, "
            "search_only, status_origin, status_reason, status_stderr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=excluded.commit_sha, "
            "last_indexed=excluded.last_indexed, status=excluded.status, "
            "scheme_override=excluded.scheme_override, "
            "semantic_include=excluded.semantic_include, "
            "language_override=excluded.language_override, "
            "search_only=excluded.search_only, "
            # D-04: success paths leave status_origin/status_reason/stderr at
            # their NULL defaults -- so excluded.* is NULL here, and listing
            # all three columns is what clears a stale failure (or a stale
            # degraded record once the full build succeeds again) on the next
            # successful index. The documented inverse of tracked_files'
            # deliberate exclusion.
            "status_origin=excluded.status_origin, "
            "status_reason=excluded.status_reason, "
            "status_stderr=excluded.status_stderr",
            (slug, path, language, commit_sha, last_indexed.isoformat(), status,
             scheme_override, _join_include(semantic_include), language_override,
             int(search_only), status_origin, status_reason, status_stderr),
        )
        self._conn.commit()
        return RegisteredRepo(
            slug=slug, path=path, language=language, commit_sha=commit_sha,
            last_indexed=last_indexed, status=status, scheme_override=scheme_override,
            semantic_indexed_at=None, semantic_include=semantic_include,
            language_override=language_override, search_only=search_only,
            status_origin=status_origin, status_reason=status_reason,
            status_stderr=status_stderr,
        )

    def mark_status(self, slug: str, status: str) -> None:
        self._conn.execute(
            "UPDATE repos SET status = ?, last_indexed = ? WHERE slug = ?",
            (status, datetime.now(UTC).isoformat(), slug),
        )
        self._conn.commit()

    def record_failure(
        self,
        slug: str,
        path: str,
        language: str,
        origin: str,
        reason: str,
        stderr: str,
    ) -> RegisteredRepo:
        """Persist a failed run's cause (D-05/D-06) and return the row.

        INSERT ... ON CONFLICT, not `mark_status`: a bare UPDATE silently
        no-ops when the failure preceded the first `upsert` -- exactly the
        hard-failed-first-index case this exists to record. The conflict
        branch deliberately does NOT touch `search_only`, the overrides,
        `semantic_indexed_at`, or `tracked_files`: losing `search_only`
        would make `_resolve_search_only` re-run a build that already
        proved un-indexable, and the others are facts about the last good
        run, not about the failure.
        """
        last_indexed = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO repos (slug, path, language, commit_sha, last_indexed, status, "
            "status_origin, status_reason, status_stderr) "
            "VALUES (?, ?, ?, NULL, ?, 'failed', ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "path=excluded.path, language=excluded.language, commit_sha=NULL, "
            "last_indexed=excluded.last_indexed, status='failed', "
            "status_origin=excluded.status_origin, "
            "status_reason=excluded.status_reason, "
            "status_stderr=excluded.status_stderr",
            (slug, path, language, last_indexed.isoformat(), origin, reason, stderr),
        )
        self._conn.commit()
        entry = self.get(slug)
        assert entry is not None  # the INSERT above guarantees the row exists
        return entry

    def mark_semantic_indexed(self, slug: str) -> None:
        self._conn.execute(
            "UPDATE repos SET semantic_indexed_at = ? WHERE slug = ?",
            (datetime.now(UTC).isoformat(), slug),
        )
        self._conn.commit()

    def get(self, slug: str) -> RegisteredRepo | None:
        row = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include, language_override, search_only, tracked_files, "
            "status_origin, status_reason, status_stderr, fallback_enabled, semantic_declined "
            "FROM repos WHERE slug = ?",
            (slug,),
        ).fetchone()
        return _row_to_repo(row) if row is not None else None

    def list(self) -> list[RegisteredRepo]:
        rows = self._conn.execute(
            "SELECT slug, path, language, commit_sha, last_indexed, status, scheme_override, "
            "semantic_indexed_at, semantic_include, language_override, search_only, tracked_files, "
            "status_origin, status_reason, status_stderr, fallback_enabled, semantic_declined "
            "FROM repos ORDER BY slug"
        ).fetchall()
        return [_row_to_repo(row) for row in rows]

    def mark_tracked_files(self, slug: str, count: int) -> None:
        """Record how many git-tracked blobs the last successful index saw.

        Deliberately not a column on `upsert`: a reindex upserts `indexing`
        before the count is known, and `upsert`'s ON CONFLICT list would then
        reset it to NULL.
        """
        self._conn.execute(
            "UPDATE repos SET tracked_files = ? WHERE slug = ?", (count, slug)
        )
        self._conn.commit()

    def set_fallback_enabled(self, slug: str, value: bool) -> None:
        """Persist the explicit `--(no-)fallback-search-only` CLI value
        (FALL-02).

        Deliberately not an upsert column, twice over. (a) `upsert`'s
        full-overwrite ON CONFLICT list would reset the tri-state on every
        transitional `indexing` write. (b) Only the *explicit CLI value*
        may ever be persisted: persisting the *resolved* bool would
        collapse NULL to 0 on the first env-off run and permanently lock a
        later env-on out (precedence is CLI > persisted > env > off, so
        persisted outranks env — Pitfall 1). Callers gate on
        `fallback_search_only is not None`.
        """
        self._conn.execute(
            "UPDATE repos SET fallback_enabled = ? WHERE slug = ?",
            (int(value), slug),
        )
        self._conn.commit()

    def set_semantic_declined(self, slug: str, value: bool) -> None:
        """Persist the SEMA-01 per-repo decline memory (the y/N offer's
        "no" answer, or EOF/Ctrl-C at the prompt).

        Deliberately not an upsert column, exactly like tracked_files and
        fallback_enabled: `upsert`'s ON CONFLICT list would NULL-reset the
        memory on every transitional `indexing` write. Only 1 is ever
        written; clearing is row death via `jarvis forget` (once the
        extra exists anywhere the gate order makes the bit moot).
        """
        self._conn.execute(
            "UPDATE repos SET semantic_declined = ? WHERE slug = ?",
            (int(value), slug),
        )
        self._conn.commit()

    def forget(self, slug: str) -> bool:
        cursor = self._conn.execute("DELETE FROM repos WHERE slug = ?", (slug,))
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()
