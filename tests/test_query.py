"""Tests for query.py against the real-schema fixture (see
tests/fixtures/synthetic_index.py) — every method asserted against real
decoded data; nothing fabricated. Ported from an internal reference
implementation's test_query_service.py, targeting codeintel.query's
bare-`repo`-slug API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codeintel import config
from codeintel.index_reader import IndexConnectionCache, IndexNotFoundError
from codeintel.models import Freshness
from codeintel.query import QueryService
from codeintel.symbols import SymbolNotFoundError
from tests.fixtures.synthetic_index import (
    ANIMAL_SYMBOL,
    CLASS_SYMBOL,
    COMMIT_SHA,
    CONST_SYMBOL,
    DOC_ANIMAL,
    DOC_CONSTANTS,
    DOC_GREETER,
    METHOD_SYMBOL,
    SAY_HI_METHOD_SYMBOL,
    build_published_index,
)

REPO = "toy-repo"


@pytest.fixture
def query_service(tmp_path: Path) -> QueryService:
    build_published_index(tmp_path, config.PROJECT, REPO, config.BRANCH)
    return QueryService(IndexConnectionCache(str(tmp_path)))


def test_get_definitions_returns_class_definition(query_service: QueryService):
    locations, freshness = query_service.get_definitions(REPO, CLASS_SYMBOL)
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER
    assert (locations[0].range.start.line, locations[0].range.start.character) == (0, 6)
    assert (locations[0].range.end.line, locations[0].range.end.character) == (0, 13)
    assert freshness.commit == COMMIT_SHA
    assert freshness.freshness == Freshness.FRESH
    assert freshness.stale is False


def test_get_definitions_returns_method_definition(query_service: QueryService):
    locations, _ = query_service.get_definitions(REPO, METHOD_SYMBOL)
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER
    assert (locations[0].range.start.line, locations[0].range.start.character) == (1, 2)


def test_get_definitions_matches_combined_bitmask_role(query_service: QueryService):
    """CONST_SYMBOL's mentions row has role=17 (Definition|Generated), not a
    bare 1 — regression-tests the bitwise-AND role filter."""
    locations, _ = query_service.get_definitions(REPO, CONST_SYMBOL)
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER
    assert (locations[0].range.start.line, locations[0].range.start.character) == (6, 6)


def test_get_definitions_unknown_symbol_raises(query_service: QueryService):
    """An unknown bare name now raises instead of returning a silently-empty
    result — the regression this feature exists to fix."""
    with pytest.raises(SymbolNotFoundError):
        query_service.get_definitions(REPO, "no-such-symbol")


def test_get_definitions_missing_index_raises_index_not_found(query_service: QueryService):
    with pytest.raises(IndexNotFoundError):
        query_service.get_definitions("does-not-exist", CLASS_SYMBOL)


def test_get_document_symbols_merges_outline_and_decoded_const_ordered(query_service: QueryService):
    entries, _ = query_service.get_document_symbols(REPO, DOC_GREETER)
    assert [e.displayName for e in entries] == ["Greeter", "greet", "DEFAULT_NAME", "sayHi"]


def test_find_references_returns_definition_and_usage(query_service: QueryService):
    locations, _ = query_service.find_references(REPO, METHOD_SYMBOL)
    paths = {loc.path for loc in locations}
    assert paths == {DOC_GREETER, DOC_CONSTANTS}


def test_call_hierarchy_returns_real_incoming_and_outgoing(query_service: QueryService):
    incoming, outgoing, _ = query_service.call_hierarchy(REPO, METHOD_SYMBOL)
    assert len(incoming) == 1
    assert incoming[0].symbol.symbol == SAY_HI_METHOD_SYMBOL
    assert outgoing == []


def test_call_hierarchy_unknown_symbol_raises(query_service: QueryService):
    """After wiring resolution, an unknown bare name raises instead of
    returning a silently-empty result — the whole point of this feature."""
    with pytest.raises(SymbolNotFoundError):
        query_service.call_hierarchy(REPO, "no-such-symbol")


def test_type_hierarchy_returns_real_supertype(query_service: QueryService):
    supertypes, subtypes, _, available = query_service.type_hierarchy(REPO, CLASS_SYMBOL)
    assert available is True
    assert len(supertypes) == 1
    assert supertypes[0].symbol.symbol == ANIMAL_SYMBOL
    assert supertypes[0].location.path == DOC_ANIMAL
    assert subtypes == []


def test_type_hierarchy_empty_for_null_relationships(query_service: QueryService):
    """METHOD_SYMBOL's relationships is NULL — the real-world v0.7.0 case
    for every symbol — must be an honest empty result, never an error."""
    supertypes, subtypes, _, _ = query_service.type_hierarchy(REPO, METHOD_SYMBOL)
    assert supertypes == []
    assert subtypes == []


def test_relationship_data_present_false_when_all_null(tmp_path):
    from codeintel.query import relationship_data_present

    db = tmp_path / "i.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE global_symbols (
            id INTEGER PRIMARY KEY, symbol TEXT, relationships BLOB
        );
        INSERT INTO global_symbols (symbol, relationships) VALUES ('a', NULL);
        INSERT INTO global_symbols (symbol, relationships) VALUES ('b', NULL);
        """
    )
    conn.commit()
    try:
        assert relationship_data_present(conn) is False
    finally:
        conn.close()


def test_relationship_data_present_true_when_any_non_null(tmp_path):
    from codeintel.query import relationship_data_present

    db = tmp_path / "i.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE global_symbols (
            id INTEGER PRIMARY KEY, symbol TEXT, relationships BLOB
        );
        INSERT INTO global_symbols (symbol, relationships) VALUES ('a', NULL);
        INSERT INTO global_symbols (symbol, relationships) VALUES ('b', X'00');
        """
    )
    conn.commit()
    try:
        assert relationship_data_present(conn) is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bare-name resolution wiring (Task 3)
# ---------------------------------------------------------------------------

def test_get_definitions_accepts_a_bare_name(query_service: QueryService):
    """The regression this whole feature exists for: a bare name used to
    return [] with no error."""
    locations, _ = query_service.get_definitions(REPO, "Greeter")
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER


def test_get_definitions_still_accepts_a_full_scip_symbol(query_service: QueryService):
    locations, _ = query_service.get_definitions(REPO, CLASS_SYMBOL)
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER


def test_find_references_accepts_a_bare_name(query_service: QueryService):
    by_bare, _ = query_service.find_references(REPO, "greet")
    by_full, _ = query_service.find_references(REPO, METHOD_SYMBOL)
    assert by_bare == by_full
    assert by_bare


def test_call_hierarchy_accepts_a_bare_name(query_service: QueryService):
    bare_in, bare_out, _ = query_service.call_hierarchy(REPO, "greet")
    full_in, full_out, _ = query_service.call_hierarchy(REPO, METHOD_SYMBOL)
    assert bare_in == full_in
    assert bare_out == full_out


def test_unknown_name_raises_instead_of_returning_empty(query_service: QueryService):
    with pytest.raises(SymbolNotFoundError):
        query_service.get_definitions(REPO, "NoSuchSymbol")


def test_resolve_symbol_returns_the_full_scip_string(query_service: QueryService):
    assert query_service.resolve_symbol(REPO, "Greeter") == CLASS_SYMBOL


def test_resolve_symbol_is_idempotent(query_service: QueryService):
    """server.py resolves, then the nav method resolves the result again.
    Rung 1 makes the second pass a no-op."""
    once = query_service.resolve_symbol(REPO, "Greeter")
    assert query_service.resolve_symbol(REPO, once) == once


def test_type_hierarchy_reports_unavailable_before_resolving(tmp_path: Path):
    """The availability check must run before resolution, so even a nonsense
    symbol gets the 'unavailable' explanation rather than a resolution error.
    Uses a dedicated index with no relationships data (the real-world case)."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL, start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL, occurrences BLOB NOT NULL);
        CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE,
            display_name TEXT, kind INTEGER, documentation TEXT, signature BLOB,
            enclosing_symbol TEXT, relationships BLOB);
        CREATE TABLE mentions (chunk_id INTEGER NOT NULL, symbol_id INTEGER NOT NULL,
            role INTEGER NOT NULL, PRIMARY KEY (chunk_id, symbol_id, role));
        CREATE TABLE defn_enclosing_ranges (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,
            symbol_id INTEGER NOT NULL, start_line INTEGER NOT NULL, start_char INTEGER NOT NULL,
            end_line INTEGER NOT NULL, end_char INTEGER NOT NULL);
    """)
    conn.execute("INSERT INTO documents (id, relative_path) VALUES (1, 'f.ts')")
    conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (1, 'local 0')")
    conn.commit()
    conn.close()

    index_dir = tmp_path / "scip" / config.PROJECT / "norel" / config.BRANCH
    index_dir.mkdir(parents=True)
    import shutil
    shutil.copy(db_path, index_dir / "index-test.db")
    (index_dir / "index-test.metadata.json").write_text('{"commit_sha":"x","published_at":"2026-01-01T00:00:00Z"}')
    (index_dir / "current").write_text("index-test.db")

    service = QueryService(IndexConnectionCache(str(tmp_path)))
    supertypes, subtypes, _, available = service.type_hierarchy("norel", "NoSuchSymbol")
    assert available is False
    assert supertypes == []
    assert subtypes == []


def test_document_symbols_populate_display_name_and_kind(query_service: QueryService):
    """scip expt-convert leaves global_symbols.display_name and .kind NULL
    for every row, so these were always null before the parser fallback."""
    entries, _ = query_service.get_document_symbols(REPO, DOC_GREETER)
    by_symbol = {e.symbol: e for e in entries}
    greeter = by_symbol[CLASS_SYMBOL]
    assert greeter.displayName == "Greeter"
    assert greeter.kind == "TYPE"
    method = by_symbol[METHOD_SYMBOL]
    assert method.displayName == "greet"
    assert method.kind == "METHOD"


def test_display_name_prefers_a_populated_column_over_the_parser():
    """Self-healing: if a future converter starts populating the real
    columns, they win over the syntax-derived fallback."""
    from codeintel.query import _display_and_kind

    display_name, kind = _display_and_kind(CLASS_SYMBOL, "FromColumn", 5)
    assert display_name == "FromColumn"
    assert kind != "TYPE"  # kind_name(5) came from the column, not the parser


def test_display_and_kind_falls_back_for_unparseable_symbols():
    from codeintel.query import _display_and_kind

    assert _display_and_kind("local 0", None, None) == (None, None)
