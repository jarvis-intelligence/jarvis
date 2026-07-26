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


def test_get_definitions_unknown_symbol_returns_empty(query_service: QueryService):
    locations, _ = query_service.get_definitions(REPO, "no-such-symbol")
    assert locations == []


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


def test_call_hierarchy_empty_for_symbol_with_no_data(query_service: QueryService):
    incoming, outgoing, _ = query_service.call_hierarchy(REPO, "no-such-symbol")
    assert incoming == []
    assert outgoing == []


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
