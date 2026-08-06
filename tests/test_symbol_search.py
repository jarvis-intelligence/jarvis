"""Unit tests for symbol_search: token extraction, matching, ranking,
location resolution. In-memory SQLite fixtures, no external binaries."""

from __future__ import annotations

import sqlite3

from jarvis.symbol_search import extract_tokens


def test_extract_tokens_lowercases_and_drops_noise():
    tokens = extract_tokens("How does the ZoektLifecycle spawn it?")
    assert tokens == ["zoektlifecycle", "spawn"]


def test_extract_tokens_keeps_dotted_tokens_whole():
    tokens = extract_tokens("where is semantic.SemanticStore defined")
    assert "semantic.semanticstore" in tokens


def test_extract_tokens_drops_short_and_stopword_tokens():
    assert extract_tokens("in a of it") == []


def test_extract_tokens_dedupes_preserving_order():
    assert extract_tokens("spawn spawn Spawn") == ["spawn"]


from jarvis.symbol_search import SymbolHit, search_symbols
from jarvis.symbols import DescriptorKind

# Realistic scip-python symbol strings: leaf 'ZoektLifecycle' is a TYPE,
# 'spawn' a METHOD, 'render' exists as both TYPE and METHOD to test
# kind-priority, 'external_thing' has no definition row.
_PKG = "scip-python python mypkg 0.1 "
CLASS_SYM = _PKG + "search/ZoektLifecycle#"
METHOD_SYM = _PKG + "search/ZoektLifecycle#spawn()."
RENDER_TYPE_SYM = _PKG + "views/Render#"
RENDER_METHOD_SYM = _PKG + "widget/render()."
EXTERNAL_SYM = _PKG + "vendor/external_thing#"

_DDL = """
CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE);
CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE);
CREATE TABLE defn_enclosing_ranges (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL, start_line INTEGER NOT NULL, start_char INTEGER NOT NULL,
    end_line INTEGER NOT NULL, end_char INTEGER NOT NULL);
"""


def _index_conn(*symbols_with_defs: tuple[str, str, int, int],
                orphans: tuple[str, ...] = ()) -> sqlite3.Connection:
    """In-memory index db. Each entry is (symbol, path, start_line, end_line);
    inserted start_line/end_line are 0-based, mirroring real SCIP storage.
    `orphans` get a global_symbols row but no definition range."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    doc_ids: dict[str, int] = {}
    for i, (sym, path, start, end) in enumerate(symbols_with_defs, start=1):
        if path not in doc_ids:
            doc_ids[path] = len(doc_ids) + 1
            conn.execute("INSERT INTO documents (id, relative_path) VALUES (?, ?)",
                         (doc_ids[path], path))
        conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (?, ?)", (i, sym))
        conn.execute(
            "INSERT INTO defn_enclosing_ranges "
            "(document_id, symbol_id, start_line, start_char, end_line, end_char) "
            "VALUES (?, ?, ?, 0, ?, 0)",
            (doc_ids[path], i, start, end),
        )
    next_id = len(symbols_with_defs) + 1
    for j, sym in enumerate(orphans):
        conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (?, ?)",
                     (next_id + j, sym))
    conn.commit()
    return conn


def test_verbatim_token_finds_definition():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    hits = search_symbols(conn, "ZoektLifecycle")
    assert hits == [SymbolHit(file_path="search.py", start_line=11, end_line=41,
                              dotted_path="mypkg.search.ZoektLifecycle",
                              kind=DescriptorKind.TYPE)]


def test_match_is_case_insensitive():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    assert search_symbols(conn, "zoektlifecycle") != []


def test_bigram_concatenation_matches_split_identifier():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    hits = search_symbols(conn, "zoekt lifecycle spawning")
    assert [h.dotted_path for h in hits] == ["mypkg.search.ZoektLifecycle"]


def test_bigram_counts_two_tokens_and_outranks_single_token_match():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40),
                       (METHOD_SYM, "search.py", 12, 20))
    hits = search_symbols(conn, "zoekt lifecycle spawn")
    # spawn matches METHOD_SYM with 1 token; the bigram 'zoektlifecycle'
    # matches CLASS_SYM with 2 -> class first.
    assert [h.dotted_path for h in hits] == [
        "mypkg.search.ZoektLifecycle",
        "mypkg.search.ZoektLifecycle.spawn",
    ]


def test_kind_priority_ranks_type_over_method_on_tie():
    conn = _index_conn((RENDER_METHOD_SYM, "widget.py", 5, 9),
                       (RENDER_TYPE_SYM, "views.py", 1, 50))
    hits = search_symbols(conn, "render")
    assert [h.kind for h in hits] == [DescriptorKind.TYPE, DescriptorKind.METHOD]


def test_dotted_token_suffix_matches():
    conn = _index_conn((METHOD_SYM, "search.py", 12, 20))
    hits = search_symbols(conn, "ZoektLifecycle.spawn")
    assert [h.dotted_path for h in hits] == ["mypkg.search.ZoektLifecycle.spawn"]


def test_candidates_without_definition_rows_are_dropped():
    conn = _index_conn(orphans=(EXTERNAL_SYM,))
    assert search_symbols(conn, "external_thing") == []


def test_empty_defn_enclosing_ranges_yields_empty_list():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (1, ?)", (CLASS_SYM,))
    conn.commit()
    assert search_symbols(conn, "ZoektLifecycle") == []


def test_no_matching_tokens_yields_empty_list():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    assert search_symbols(conn, "how does it work") == []
