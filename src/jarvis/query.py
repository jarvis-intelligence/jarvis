"""Query layer: SQL + occurrence-blob decoding against the `scip expt-convert`
v0.7.0 schema (documents/chunks/global_symbols/mentions/defn_enclosing_ranges).

Ported near-verbatim from an internal reference implementation's `query_service.py`.
Two changes from the source:

* No project/branch dimension — every method takes a bare `repo` slug
  (see config.py); the vendored `IndexConnectionCache` 3-tuple is filled in
  with pinned constants under the hood.
* `get_index_status` compares the published commit against a local
  `git rev-parse HEAD` instead of a live git-hosting API call — this is a
  personal, local-first tool with no remote service to ask. It is therefore
  synchronous, not async, and takes an optional `repo_path` (the git
  working directory to check); omitted, it can't determine staleness and
  reports the same honest "we know the commit, we haven't compared it"
  freshness the source's sync path reports before its live check runs.

``mentions.role`` note (load-bearing for every query below, preserved
verbatim from the source): it is the RAW ``SymbolRoles`` bitmask value the
v0.7.0 converter saw for a given (chunk, symbol) pair — not a normalized
0/1 boolean. Every role filter below therefore uses a bitwise AND
(``m.role & ? != 0``), never exact equality.
"""

from __future__ import annotations

import subprocess
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from jarvis.config import get_connection
from jarvis.index_reader import IndexConnectionCache, IndexMetadata, IndexNotFoundError
from jarvis.models import (
    CallHierarchyEntry,
    DocumentSymbolEntry,
    Freshness,
    Location,
    Position,
    Range,
    SymbolInfo,
    TypeHierarchyEntry,
)
from jarvis.scip_decoder import (
    OccurrenceDecodeError,
    ScipOccurrence,
    SymbolRoles,
    decode_occurrences,
    decode_relationships,
    kind_name,
    scip_range_to_positions,
)
from jarvis import symbols

__all__ = [
    "IndexNotFoundError",
    "OccurrenceDecodeError",
    "QueryService",
]


@dataclass(frozen=True)
class FreshnessSnapshot:
    commit: str | None
    generated_at: datetime | None
    stale: bool
    freshness: Freshness
    checked_at: datetime


def _freshness_snapshot(metadata: IndexMetadata | None) -> FreshnessSnapshot:
    checked_at = datetime.now(UTC)
    if metadata is None or not metadata.commit_sha:
        return FreshnessSnapshot(
            commit=None,
            generated_at=None,
            stale=False,
            freshness=Freshness.UNKNOWN,
            checked_at=checked_at,
        )

    generated_at: datetime | None = None
    if metadata.published_at:
        try:
            generated_at = datetime.fromisoformat(metadata.published_at.replace("Z", "+00:00"))
        except ValueError:
            generated_at = None

    return FreshnessSnapshot(
        commit=metadata.commit_sha,
        generated_at=generated_at,
        stale=False,
        freshness=Freshness.FRESH if generated_at is not None else Freshness.UNKNOWN,
        checked_at=checked_at,
    )


def _git_head(repo_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _occurrence_to_location(path: str, occ: ScipOccurrence) -> Location:
    start_line, start_char, end_line, end_char = scip_range_to_positions(occ.range)
    return Location(
        path=path,
        range=Range(
            start=Position(line=start_line, character=start_char),
            end=Position(line=end_line, character=end_char),
        ),
    )


def _occurrences_for_symbol(
    conn: sqlite3.Connection, symbol: str, role_bit: int | None = None
) -> list[tuple[str, ScipOccurrence]]:
    sql = (
        "SELECT DISTINCT c.id, d.relative_path, c.occurrences "
        "FROM mentions m "
        "JOIN global_symbols s ON s.id = m.symbol_id "
        "JOIN chunks c ON c.id = m.chunk_id "
        "JOIN documents d ON d.id = c.document_id "
        "WHERE s.symbol = ?"
    )
    params: list[object] = [symbol]
    if role_bit is not None:
        sql += " AND (m.role & ?) != 0"
        params.append(role_bit)
    sql += " ORDER BY d.relative_path, c.chunk_index"

    rows = conn.execute(sql, params).fetchall()

    results: list[tuple[str, ScipOccurrence]] = []
    for _chunk_id, relative_path, blob in rows:
        for occ in decode_occurrences(blob):
            if occ.symbol == symbol:
                results.append((relative_path, occ))
    return results


def _occurrences_for_symbol_with_doc(
    conn: sqlite3.Connection, symbol: str
) -> list[tuple[int, str, ScipOccurrence]]:
    sql = (
        "SELECT DISTINCT c.document_id, d.relative_path, c.occurrences "
        "FROM mentions m "
        "JOIN global_symbols s ON s.id = m.symbol_id "
        "JOIN chunks c ON c.id = m.chunk_id "
        "JOIN documents d ON d.id = c.document_id "
        "WHERE s.symbol = ? "
        "ORDER BY d.relative_path, c.chunk_index"
    )
    rows = conn.execute(sql, (symbol,)).fetchall()
    results: list[tuple[int, str, ScipOccurrence]] = []
    for document_id, relative_path, blob in rows:
        for occ in decode_occurrences(blob):
            if occ.symbol == symbol:
                results.append((document_id, relative_path, occ))
    return results


def _symbol_display_and_kind(conn: sqlite3.Connection, symbol: str) -> tuple[str | None, int | None]:
    row = conn.execute("SELECT display_name, kind FROM global_symbols WHERE symbol = ?", (symbol,)).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def _display_and_kind(
    symbol: str, display_name: str | None, kind: int | None
) -> tuple[str | None, str | None]:
    """Resolve a symbol's display name and kind, preferring the DB columns.

    `scip expt-convert` declares `global_symbols.display_name` and `.kind`
    but never populates either (measured: 0 of 2180 rows on a real index),
    so both are always NULL today and every documentSymbols response
    carried nulls. Falling back to the symbol string fixes that.

    The fallback kind is derived from *descriptor syntax* ('#' -> TYPE,
    '().' -> METHOD), not from the indexer's semantic classification, which
    is what the integer `kind` column was meant to carry. It is therefore
    not a SCIP `SymbolKind` value. Same self-healing property as
    `relationship_data_present`: a converter that starts populating the real
    columns immediately takes precedence, with no code change here.
    """
    column_kind = kind_name(kind)
    if display_name is not None and column_kind is not None:
        return display_name, column_kind
    parsed = symbols.parse_symbol(symbol)
    if parsed is None:
        return display_name, column_kind
    return display_name or parsed.name, column_kind or str(parsed.kind)


def _symbol_info(conn: sqlite3.Connection, symbol: str) -> SymbolInfo:
    display_name, kind = _symbol_display_and_kind(conn, symbol)
    display_name, kind_label = _display_and_kind(symbol, display_name, kind)
    return SymbolInfo(symbol=symbol, displayName=display_name, kind=kind_label)


def _document_path(conn: sqlite3.Connection, document_id: int) -> str | None:
    row = conn.execute("SELECT relative_path FROM documents WHERE id = ?", (document_id,)).fetchone()
    return row[0] if row is not None else None


def _innermost_enclosing_definition(
    conn: sqlite3.Connection, document_id: int, line: int
) -> tuple[str, str | None, int | None] | None:
    row = conn.execute(
        "SELECT s.symbol, s.display_name, s.kind "
        "FROM defn_enclosing_ranges r "
        "JOIN global_symbols s ON s.id = r.symbol_id "
        "WHERE r.document_id = ? AND r.start_line <= ? AND r.end_line >= ? "
        "ORDER BY (r.end_line - r.start_line) ASC LIMIT 1",
        (document_id, line, line),
    ).fetchone()
    if row is None:
        return None
    return row[0], row[1], row[2]


def _call_hierarchy_incoming(conn: sqlite3.Connection, symbol: str) -> list[CallHierarchyEntry]:
    entries: list[CallHierarchyEntry] = []
    for document_id, relative_path, occ in _occurrences_for_symbol_with_doc(conn, symbol):
        if occ.is_definition():
            continue
        start_line, _start_char, _end_line, _end_char = scip_range_to_positions(occ.range)
        enclosing = _innermost_enclosing_definition(conn, document_id, start_line)
        if enclosing is None:
            continue  # module top-level reference — documented, not an error
        caller_symbol, display_name, kind = enclosing
        entries.append(
            CallHierarchyEntry(
                symbol=SymbolInfo(symbol=caller_symbol, displayName=display_name, kind=kind_name(kind)),
                location=_occurrence_to_location(relative_path, occ),
            )
        )
    return entries


def _call_hierarchy_outgoing(conn: sqlite3.Connection, symbol: str) -> list[CallHierarchyEntry]:
    own_ranges = conn.execute(
        "SELECT r.document_id, r.start_line, r.end_line "
        "FROM defn_enclosing_ranges r JOIN global_symbols s ON s.id = r.symbol_id "
        "WHERE s.symbol = ?",
        (symbol,),
    ).fetchall()

    entries: list[CallHierarchyEntry] = []
    seen: set[tuple[str, int, int]] = set()
    for document_id, range_start, range_end in own_ranges:
        chunk_rows = conn.execute(
            "SELECT occurrences FROM chunks WHERE document_id = ? AND start_line <= ? AND end_line >= ?",
            (document_id, range_end, range_start),
        ).fetchall()
        relative_path = _document_path(conn, document_id)
        for (blob,) in chunk_rows:
            for occ in decode_occurrences(blob):
                if occ.symbol.startswith("local ") or occ.symbol == symbol or occ.is_definition():
                    continue
                start_line, start_char, _end_line, _end_char = scip_range_to_positions(occ.range)
                if not (range_start <= start_line <= range_end):
                    continue  # chunk overlap is coarser than the exact enclosing range
                key = (occ.symbol, start_line, start_char)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    CallHierarchyEntry(
                        symbol=_symbol_info(conn, occ.symbol),
                        location=_occurrence_to_location(relative_path, occ),
                    )
                )
    return entries


def _resolve_symbol_location(conn: sqlite3.Connection, symbol: str) -> Location | None:
    row = conn.execute(
        "SELECT d.relative_path, r.start_line, r.start_char, r.end_line, r.end_char "
        "FROM defn_enclosing_ranges r "
        "JOIN global_symbols s ON s.id = r.symbol_id "
        "JOIN documents d ON d.id = r.document_id "
        "WHERE s.symbol = ? ORDER BY r.id LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is not None:
        path, start_line, start_char, end_line, end_char = row
        return Location(
            path=path,
            range=Range(
                start=Position(line=start_line, character=start_char),
                end=Position(line=end_line, character=end_char),
            ),
        )
    for _document_id, relative_path, occ in _occurrences_for_symbol_with_doc(conn, symbol):
        if occ.is_definition():
            return _occurrence_to_location(relative_path, occ)
    return None


def _type_hierarchy_supertypes(conn: sqlite3.Connection, symbol: str) -> list[TypeHierarchyEntry]:
    row = conn.execute("SELECT relationships FROM global_symbols WHERE symbol = ?", (symbol,)).fetchone()
    if row is None or row[0] is None:
        return []
    entries: list[TypeHierarchyEntry] = []
    for rel in decode_relationships(row[0]):
        if not (rel.is_implementation or rel.is_type_definition):
            continue
        location = _resolve_symbol_location(conn, rel.symbol)
        if location is None:
            continue
        entries.append(TypeHierarchyEntry(symbol=_symbol_info(conn, rel.symbol), location=location))
    return entries


def _type_hierarchy_subtypes(conn: sqlite3.Connection, symbol: str) -> list[TypeHierarchyEntry]:
    rows = conn.execute(
        "SELECT symbol, relationships FROM global_symbols WHERE relationships IS NOT NULL"
    ).fetchall()
    entries: list[TypeHierarchyEntry] = []
    for other_symbol, blob in rows:
        for rel in decode_relationships(blob):
            if rel.symbol == symbol and (rel.is_implementation or rel.is_type_definition):
                location = _resolve_symbol_location(conn, other_symbol)
                if location is None:
                    continue
                entries.append(TypeHierarchyEntry(symbol=_symbol_info(conn, other_symbol), location=location))
    return entries


def relationship_data_present(conn: sqlite3.Connection) -> bool:
    """True when any symbol carries relationship data.

    `scip expt-convert` declares `global_symbols.relationships` in its schema
    but never writes it -- `insertGlobalSymbols()` binds only symbol,
    display_name, kind, documentation and enclosing_symbol (verified against
    cmd/scip/convert.go at v0.9.0). Type hierarchy is therefore unanswerable
    on every real index, and reporting an empty result would assert that a
    type has no supertypes rather than that we cannot tell.

    Self-healing: this flips to True with no code change if a future
    converter starts populating the column.
    """
    row = conn.execute("SELECT 1 FROM global_symbols WHERE relationships IS NOT NULL LIMIT 1").fetchone()
    return row is not None


class QueryService:
    """Implements the 5 SCIP nav tools + getIndexStatus against index.db."""

    def __init__(self, connection_cache: IndexConnectionCache) -> None:
        self._cache = connection_cache

    def _resolved(self, conn: sqlite3.Connection, symbol: str) -> str:
        """Accept either a bare name or a full SCIP symbol. Explicit at each
        call site rather than a decorator: this codebase is consistently
        explicit, and hiding resolution would make misfires hard to trace."""
        return symbols.resolve(conn, symbol)

    def resolve_symbol(self, repo: str, symbol: str) -> str:
        """Public resolution for callers that need the canonical symbol
        string itself — `server.py` reports it as `resolvedSymbol`."""
        conn, _ = get_connection(self._cache, repo)
        return self._resolved(conn, symbol)

    def connection(self, repo: str) -> sqlite3.Connection:
        """The repo's cached read-only index connection.

        Exists for semanticSearch's symbol signal (symbol_search.py), which
        needs raw table access rather than a nav operation. Raises
        IndexNotFoundError when no index is published — callers treating
        the connection as optional catch that and pass None.
        """
        conn, _ = get_connection(self._cache, repo)
        return conn

    def get_definitions(self, repo: str, symbol: str) -> tuple[list[Location], FreshnessSnapshot]:
        conn, metadata = get_connection(self._cache, repo)
        symbol = self._resolved(conn, symbol)
        locations = [
            _occurrence_to_location(path, occ)
            for path, occ in _occurrences_for_symbol(conn, symbol, role_bit=SymbolRoles.DEFINITION)
            if occ.is_definition()
        ]
        return locations, _freshness_snapshot(metadata)

    def find_references(self, repo: str, symbol: str) -> tuple[list[Location], FreshnessSnapshot]:
        """ALL occurrences of `symbol`, definition sites included — no role filter."""
        conn, metadata = get_connection(self._cache, repo)
        symbol = self._resolved(conn, symbol)
        locations = [
            _occurrence_to_location(path, occ) for path, occ in _occurrences_for_symbol(conn, symbol, role_bit=None)
        ]
        return locations, _freshness_snapshot(metadata)

    def get_document_symbols(
        self, repo: str, path: str
    ) -> tuple[list[DocumentSymbolEntry], FreshnessSnapshot]:
        conn, metadata = get_connection(self._cache, repo)

        outline_rows = conn.execute(
            "SELECT s.symbol, s.display_name, s.kind, "
            "r.start_line, r.start_char, r.end_line, r.end_char "
            "FROM defn_enclosing_ranges r "
            "JOIN global_symbols s ON s.id = r.symbol_id "
            "JOIN documents d ON d.id = r.document_id "
            "WHERE d.relative_path = ?",
            (path,),
        ).fetchall()

        entries: dict[str, DocumentSymbolEntry] = {}
        for symbol, display_name, kind, start_line, start_char, end_line, end_char in outline_rows:
            entry_name, entry_kind = _display_and_kind(symbol, display_name, kind)
            entries[symbol] = DocumentSymbolEntry(
                symbol=symbol,
                displayName=entry_name,
                kind=entry_kind,
                range=Range(
                    start=Position(line=start_line, character=start_char),
                    end=Position(line=end_line, character=end_char),
                ),
            )

        chunk_rows = conn.execute(
            "SELECT c.occurrences FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.relative_path = ?",
            (path,),
        ).fetchall()

        for (blob,) in chunk_rows:
            for occ in decode_occurrences(blob):
                if occ.symbol.startswith("local ") or occ.symbol in entries or not occ.is_definition():
                    continue
                display_name, kind = _symbol_display_and_kind(conn, occ.symbol)
                entry_name, entry_kind = _display_and_kind(occ.symbol, display_name, kind)
                start_line, start_char, end_line, end_char = scip_range_to_positions(occ.range)
                entries[occ.symbol] = DocumentSymbolEntry(
                    symbol=occ.symbol,
                    displayName=entry_name,
                    kind=entry_kind,
                    range=Range(
                        start=Position(line=start_line, character=start_char),
                        end=Position(line=end_line, character=end_char),
                    ),
                )

        ordered = sorted(entries.values(), key=lambda e: (e.range.start.line, e.range.start.character))
        return ordered, _freshness_snapshot(metadata)

    def call_hierarchy(
        self, repo: str, symbol: str
    ) -> tuple[list[CallHierarchyEntry], list[CallHierarchyEntry], FreshnessSnapshot]:
        conn, metadata = get_connection(self._cache, repo)
        symbol = self._resolved(conn, symbol)
        incoming = _call_hierarchy_incoming(conn, symbol)
        outgoing = _call_hierarchy_outgoing(conn, symbol)
        return incoming, outgoing, _freshness_snapshot(metadata)

    def type_hierarchy(
        self, repo: str, symbol: str
    ) -> tuple[list[TypeHierarchyEntry], list[TypeHierarchyEntry], FreshnessSnapshot, bool]:
        """Returns (supertypes, subtypes, freshness, available).

        `available` is False when the index carries no relationship data at
        all, which the caller must surface as "cannot answer" rather than as
        an empty hierarchy.
        """
        conn, metadata = get_connection(self._cache, repo)
        available = relationship_data_present(conn)
        if not available:
            return [], [], _freshness_snapshot(metadata), False
        symbol = self._resolved(conn, symbol)
        supertypes = _type_hierarchy_supertypes(conn, symbol)
        subtypes = _type_hierarchy_subtypes(conn, symbol)
        return supertypes, subtypes, _freshness_snapshot(metadata), True

    def get_index_status(self, repo: str, repo_path: str | None = None) -> tuple[bool, FreshnessSnapshot]:
        """`repo_path` (optional): a local git working directory to compare
        the published commit against via `git rev-parse HEAD`. Omitted, the
        published commit is reported without a staleness comparison (honest
        "we know the commit, we haven't checked" freshness, never stale=True
        without evidence)."""
        try:
            _, metadata = get_connection(self._cache, repo)
        except IndexNotFoundError:
            return False, _freshness_snapshot(None)

        snapshot = _freshness_snapshot(metadata)
        if repo_path is None or snapshot.commit is None:
            return True, snapshot

        live_head = _git_head(repo_path)
        if live_head is None:
            return True, FreshnessSnapshot(
                commit=snapshot.commit,
                generated_at=snapshot.generated_at,
                stale=False,
                freshness=Freshness.UNKNOWN,
                checked_at=snapshot.checked_at,
            )

        stale = snapshot.commit != live_head
        return True, FreshnessSnapshot(
            commit=snapshot.commit,
            generated_at=snapshot.generated_at,
            stale=stale,
            freshness=Freshness.STALE if stale else Freshness.FRESH,
            checked_at=snapshot.checked_at,
        )
