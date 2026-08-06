"""Tests for symbols.py — the SCIP symbol-string grammar parser and the
bare-name resolution ladder. Parser tests are pure text with no fixtures;
resolution tests build an in-memory global_symbols table."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis.symbols import (
    AmbiguousSymbolError,
    CANDIDATE_LIMIT,
    DescriptorKind,
    ParsedSymbol,
    SymbolNotFoundError,
    parse_symbol,
    resolve,
)

# Real symbols captured from live indexes: Python (codeintel itself) and
# TypeScript (tests/fixtures/synthetic_index.py).
PY_METHOD = (
    "scip-python python codeintel-navigation-mcp 0.3.1 "
    "`codeintel.search`/search_zoekt()."
)
PY_PARAM = (
    "scip-python python codeintel-navigation-mcp 0.3.1 "
    "`codeintel.search`/search_zoekt().(base_url)"
)
PY_FIELD = (
    "scip-python python codeintel-navigation-mcp 0.3.1 "
    "`codeintel.search`/ZoektHit#repo."
)
TS_TYPE = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#"
TS_METHOD = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet()."


def test_parses_python_method():
    parsed = parse_symbol(PY_METHOD)
    assert parsed == ParsedSymbol(
        package="codeintel-navigation-mcp",
        name="search_zoekt",
        kind=DescriptorKind.METHOD,
        parents=("codeintel.search",),
    )


def test_backtick_escaped_name_containing_dots_is_one_parent():
    """`codeintel.search` is a single descriptor whose name contains dots —
    not two namespace descriptors. Splitting on '.' would get this wrong."""
    parsed = parse_symbol(PY_METHOD)
    assert parsed.parents == ("codeintel.search",)


def test_dotted_path_leads_with_the_package():
    """The package is the outermost segment. It is the only thing separating
    symbols whose descriptors are byte-identical across modules — measured at
    1,403 such dotted paths in one Swift repo before this was added."""
    parsed = parse_symbol(PY_METHOD)
    assert parsed.dotted_path == (
        "codeintel-navigation-mcp.codeintel.search.search_zoekt"
    )


def test_package_separates_identical_descriptors_across_modules():
    swift_a = "scip-swift xcodebuild epost_comp_showcase_sdk . `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`."
    swift_b = "scip-swift xcodebuild ios_theme_ui . `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`."
    a, b = parse_symbol(swift_a), parse_symbol(swift_b)
    assert a.name == b.name and a.parents == b.parents  # descriptors identical
    assert a.dotted_path != b.dotted_path               # package separates them


def test_parses_parameter_descriptor():
    parsed = parse_symbol(PY_PARAM)
    assert parsed.name == "base_url"
    assert parsed.kind is DescriptorKind.PARAMETER
    assert parsed.parents == ("codeintel.search", "search_zoekt")


def test_parses_term_descriptor():
    parsed = parse_symbol(PY_FIELD)
    assert parsed.name == "repo"
    assert parsed.kind is DescriptorKind.TERM
    assert parsed.parents == ("codeintel.search", "ZoektHit")


def test_parses_typescript_type():
    parsed = parse_symbol(TS_TYPE)
    assert parsed.name == "Greeter"
    assert parsed.kind is DescriptorKind.TYPE
    assert parsed.parents == ("src", "greeter.ts")


def test_parses_typescript_method_nested_under_type():
    parsed = parse_symbol(TS_METHOD)
    assert parsed.name == "greet"
    assert parsed.kind is DescriptorKind.METHOD
    assert parsed.dotted_path == "@toy/pkg.src.greeter.ts.Greeter.greet"


def test_parses_type_parameter():
    parsed = parse_symbol("scip-java maven com.example 1.0 com/example/Box#[T]")
    assert parsed.name == "T"
    assert parsed.kind is DescriptorKind.TYPE_PARAMETER


def test_parses_meta_descriptor():
    parsed = parse_symbol("scip-python python pkg 1.0 `mod`/thing:")
    assert parsed.name == "thing"
    assert parsed.kind is DescriptorKind.META


def test_parses_method_with_disambiguator():
    parsed = parse_symbol("scip-java maven com.example 1.0 com/example/C#m(+1).")
    assert parsed.name == "m"
    assert parsed.kind is DescriptorKind.METHOD


def test_doubled_backtick_is_a_literal_backtick():
    parsed = parse_symbol("scip-python python pkg 1.0 `we``ird`/x.")
    assert parsed.parents == ("we`ird",)
    assert parsed.name == "x"


def test_local_symbol_returns_none():
    assert parse_symbol("local 0") is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "too few fields",
        "scip-python python pkg 1.0 ",              # empty descriptors
        "scip-python python pkg 1.0 noSuffix",      # descriptor with no suffix
        "scip-python python pkg 1.0 `unterminated", # unclosed backtick
        "scip-python python pkg 1.0 (unclosed",     # unclosed parameter
    ],
)
def test_malformed_returns_none_never_raises(bad: str):
    assert parse_symbol(bad) is None


# ---------------------------------------------------------------------------
# Resolution ladder tests
# ---------------------------------------------------------------------------

TS_SAY_HI = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#sayHi()."
TS_ANIMAL_GREET = "scip-typescript npm @toy/pkg 0.0.1 src/`animal.ts`/Animal#greet()."


def _conn(*symbols: str) -> sqlite3.Connection:
    """In-memory global_symbols holding just the columns resolution reads."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE)")
    conn.executemany("INSERT INTO global_symbols (symbol) VALUES (?)", [(s,) for s in symbols])
    return conn


def test_full_scip_symbol_passes_through_verbatim():
    conn = _conn(TS_METHOD)
    assert resolve(conn, TS_METHOD) == TS_METHOD


def test_bare_leaf_name_resolves():
    conn = _conn(TS_METHOD, TS_TYPE)
    assert resolve(conn, "greet") == TS_METHOD


def test_parent_qualifier_disambiguates():
    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    assert resolve(conn, "Greeter.greet") == TS_METHOD
    assert resolve(conn, "Animal.greet") == TS_ANIMAL_GREET


def test_full_dotted_path_resolves():
    conn = _conn(TS_METHOD)
    assert resolve(conn, "@toy/pkg.src.greeter.ts.Greeter.greet") == TS_METHOD


def test_package_qualifier_disambiguates_identical_descriptors():
    """The Swift/TypeScript case parent qualification cannot touch: same
    descriptors, different module. Only the package separates them."""
    swift_a = "scip-swift xcodebuild epost_comp_showcase_sdk . `UIView(im)centerXAnchor`."
    swift_b = "scip-swift xcodebuild ios_theme_ui . `UIView(im)centerXAnchor`."
    conn = _conn(swift_a, swift_b)
    with pytest.raises(AmbiguousSymbolError):
        resolve(conn, "UIView(im)centerXAnchor")
    assert resolve(conn, "ios_theme_ui.UIView(im)centerXAnchor") == swift_b


def test_dot_boundary_prevents_partial_identifier_match():
    """'zoekt' must not match 'search_zoekt' — suffix matching requires a
    '.' boundary, otherwise every substring would resolve."""
    conn = _conn(PY_METHOD)
    with pytest.raises(SymbolNotFoundError):
        resolve(conn, "zoekt")


def test_name_containing_dots_resolves_via_full_scan_fallback():
    """A leaf descriptor name may itself contain dots — scip-typescript emits
    `greeter.ts` as one backtick-escaped namespace name. Under a naive
    rsplit('.') its bucket key is 'ts', which indexes nothing, so only the
    fallback scan makes the spec's suffix rule actually hold."""
    ts_namespace = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/"
    conn = _conn(ts_namespace, TS_TYPE)
    assert resolve(conn, "greeter.ts") == ts_namespace


def test_ambiguous_name_raises_with_sorted_candidates():
    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    with pytest.raises(AmbiguousSymbolError) as excinfo:
        resolve(conn, "greet")
    error = excinfo.value
    assert error.query == "greet"
    assert error.total == 2
    assert [c.dotted_path for c in error.candidates] == [
        "@toy/pkg.src.animal.ts.Animal.greet",
        "@toy/pkg.src.greeter.ts.Greeter.greet",
    ]


def test_candidates_are_capped_but_total_is_honest():
    symbols = [
        f"scip-typescript npm @toy/pkg 0.0.1 src/`m{i}.ts`/C#dup()."
        for i in range(CANDIDATE_LIMIT + 5)
    ]
    conn = _conn(*symbols)
    with pytest.raises(AmbiguousSymbolError) as excinfo:
        resolve(conn, "dup")
    assert len(excinfo.value.candidates) == CANDIDATE_LIMIT
    assert excinfo.value.total == CANDIDATE_LIMIT + 5


def test_parameters_are_not_resolution_targets():
    conn = _conn(PY_PARAM)
    with pytest.raises(SymbolNotFoundError) as excinfo:
        resolve(conn, "base_url")
    assert "parameter" in str(excinfo.value).lower()


def test_a_method_wins_over_a_same_named_parameter_elsewhere():
    """Exclusion drops parameters from the map; it must not shadow a real
    method that happens to share a parameter's name. `base_url` is both a
    parameter of search_zoekt and a method on ZoektLifecycle in this repo."""
    py_method_base_url = (
        "scip-python python codeintel-navigation-mcp 0.3.1 "
        "`codeintel.search`/ZoektLifecycle#base_url()."
    )
    conn = _conn(PY_PARAM, py_method_base_url)
    assert resolve(conn, "base_url") == py_method_base_url


def test_parameter_still_reachable_by_full_symbol():
    conn = _conn(PY_PARAM)
    assert resolve(conn, PY_PARAM) == PY_PARAM


def test_unknown_name_message_says_not_found():
    conn = _conn(TS_TYPE)
    with pytest.raises(SymbolNotFoundError) as excinfo:
        resolve(conn, "NoSuchThing")
    assert "no symbol named" in str(excinfo.value)


def test_stale_full_symbol_hints_the_bare_name_instead_of_a_bare_not_found():
    """A query that parses as a full SCIP symbol but matches nothing is very
    likely a stale, version-mismatched string (SCIP symbols embed the
    package version) -- the design spec's opening scenario -- not a typo'd
    bare name. The message should hint the stable bare name to retry with,
    not the generic 'no symbol named' text."""
    conn = _conn(TS_METHOD)
    stale = TS_METHOD.replace("0.0.1", "9.9.9")
    with pytest.raises(SymbolNotFoundError) as excinfo:
        resolve(conn, stale)
    message = str(excinfo.value)
    assert "stale" in message.lower() or "different package version" in message.lower()
    assert "'greet'" in message


def test_public_name_map_buckets_by_leaf_name():
    """name_map() is the public accessor symbol_search composes with —
    same cached map resolve() uses, keyed by leaf descriptor name."""
    from jarvis.symbols import name_map

    conn = _conn(TS_METHOD, TS_TYPE)
    buckets = name_map(conn)
    assert {c.symbol for c in buckets["greet"]} == {TS_METHOD}
    assert {c.symbol for c in buckets["Greeter"]} == {TS_TYPE}


def test_dotted_suffix_matches_is_case_sensitive_by_default():
    """Default preserves resolve()'s existing rung-2 semantics exactly."""
    from jarvis.symbols import dotted_suffix_matches, name_map

    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    buckets = name_map(conn)
    assert {c.symbol for c in dotted_suffix_matches(buckets, "Greeter.greet")} == {TS_METHOD}
    assert dotted_suffix_matches(buckets, "greeter.greet") == []  # wrong case, no match


def test_dotted_suffix_matches_case_insensitive_when_requested():
    """symbol_search.py's use case: lowercased query against the map."""
    from jarvis.symbols import dotted_suffix_matches, name_map

    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    buckets = name_map(conn)
    hits = dotted_suffix_matches(buckets, "greeter.greet", case_sensitive=False)
    assert {c.symbol for c in hits} == {TS_METHOD}
