"""Tests for symbols.py — the SCIP symbol-string grammar parser and the
bare-name resolution ladder. Parser tests are pure text with no fixtures;
resolution tests build an in-memory global_symbols table."""

from __future__ import annotations

import pytest

from codeintel.symbols import DescriptorKind, ParsedSymbol, parse_symbol

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
