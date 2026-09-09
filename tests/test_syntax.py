"""Unit tests for the shared grammar provider (src/jarvis/syntax.py)."""
from __future__ import annotations

import sys

import pytest

from jarvis.chunker import chunk_file
from jarvis.syntax import FACTORIES, ParserPool, SyntaxDependencyError, grammar_identity, slice_text
from tests.conftest import BlockImportFinder


def test_python_tree_and_chunk_preserve_multibyte_source():
    source = "# café\ndef greet():\n    return '你好'\n"
    raw = source.encode("utf-8")
    tree = ParserPool().parse("python", raw)
    chunks = chunk_file("greet.py", source, "hash", "python", tree=tree)
    assert [(c.start_line, c.end_line, c.symbol_name) for c in chunks] == [(2, 3, "greet")]
    assert chunks[0].content.endswith("def greet():\n    return '你好'")
    assert "é\ndef" not in chunks[0].content


# Tiny valid source per curated factory selection: the offline cutover is
# only usable if every mapped grammar constructs and parses locally (the
# wheel matrix proves resolution, not construction).
_TINY_SOURCES: dict[str, bytes] = {
    "python": b"def one():\n    return 1\n",
    "javascript": b"function one() {\n  return 1;\n}\n",
    "typescript": b"function one(): number {\n  return 1;\n}\n",
    "tsx": b"export function One(): JSX.Element {\n  return <p>hi</p>;\n}\n",
    "java": b"class One {\n    int one() {\n        return 1;\n    }\n}\n",
    "kotlin": b"fun one(): Int {\n    return 1\n}\n",
    "swift": b"func one() -> Int {\n    return 1\n}\n",
    "go": b"package main\n\nfunc one() int {\n\treturn 1\n}\n",
    "ruby": b"def one\n  1\nend\n",
    "rust": b"fn one() -> i32 {\n    1\n}\n",
    "c": b"int one(void) {\n    return 1;\n}\n",
    "cpp": b"int one() {\n    return 1;\n}\n",
    "csharp": b"class One {\n    int one() {\n        return 1;\n    }\n}\n",
    "php": b"<?php\nfunction one() {\n    return 1;\n}\n",
    "scala": b"def one: Int = 1\n",
    "bash": b"one() {\n    return 1\n}\n",
    "sql": b"CREATE TABLE one (id INTEGER);\n",
}


@pytest.mark.parametrize("language", sorted(FACTORIES))
def test_pool_parses_tiny_source_for_every_factory(language):
    tree = ParserPool().parse(language, _TINY_SOURCES[language])
    assert not tree.root_node.has_error


def test_slice_text_decodes_byte_ranges_not_str_indices():
    raw = "'你好'".encode("utf-8")
    # Bytes 0..4 = quote + 你 (3 bytes); a str-index slice would differ.
    assert slice_text(raw, 0, 4) == "'你"
    assert slice_text(raw, 1, 4) == "你"
    assert slice_text(raw, 4, len(raw)) == "好'"


def test_grammar_identity_is_a_deterministic_metadata_digest():
    identity = grammar_identity("python")
    assert identity == grammar_identity("python")
    assert len(identity) == 64 and int(identity, 16) >= 0
    # Distinct parser selections carry distinct identity (TSX vs TypeScript
    # share one distribution but must not share an extraction identity).
    assert grammar_identity("typescript") != grammar_identity("tsx")
    assert grammar_identity("python") != grammar_identity("kotlin")


def test_grammar_identity_rejects_unknown_language():
    with pytest.raises(ValueError, match="nosuchlanguage"):
        grammar_identity("nosuchlanguage")


def test_missing_grammar_distribution_raises_typed_dependency_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "tree_sitter_python", raising=False)
    monkeypatch.setattr(sys, "meta_path",
                        [BlockImportFinder("tree_sitter_python"), *sys.meta_path])
    with pytest.raises(SyntaxDependencyError, match="tree-sitter-python"):
        ParserPool().parse("python", b"x = 1\n")


def test_pool_reuses_its_constructed_parser(monkeypatch):
    """A warmed pool must keep serving its retained parser: with fresh
    grammar loading blocked, a second parse still succeeds."""
    pool = ParserPool()
    pool.parse("python", b"x = 1\n")
    monkeypatch.delitem(sys.modules, "tree_sitter_python", raising=False)
    monkeypatch.setattr(sys, "meta_path",
                        [BlockImportFinder("tree_sitter_python"), *sys.meta_path])
    assert pool.parse("python", b"y = 2\n") is not None



# ---------------------------------------------------------------------------
# Declaration extraction (spec TSI-03 "Declaration contract").
# ---------------------------------------------------------------------------

from jarvis.symbols import DescriptorKind
from jarvis.syntax import ParsedSyntax, Span, SyntaxSymbol, extract_file, language_for_path, syntax_id
from tests.fixtures.syntax_cases import CASES


def test_nested_declarations_keep_byte_locations_and_stable_ids():
    raw = "@dec\nclass Café:\n    def run(self):\n        def inner():\n            pass\n".encode()
    first = extract_file("cafe.py", raw, "python", pool=ParserPool())
    second = extract_file("cafe.py", raw, "python", pool=ParserPool())
    by_name = {s.qualified_name: s for s in first.symbols}
    assert set(by_name) == {"Café", "Café.run", "Café.run.inner"}
    assert raw[by_name["Café"].selection.start_byte:by_name["Café"].selection.end_byte] == "Café".encode()
    assert (by_name["Café"].selection.start_line, by_name["Café"].selection.start_character, by_name["Café"].selection.end_character) == (1, 6, 11)
    assert by_name["Café.run"].parent_symbol == by_name["Café"].symbol
    assert by_name["Café.run.inner"].parent_symbol == by_name["Café.run"].symbol
    assert [s.symbol for s in first.symbols] == [s.symbol for s in second.symbols]


# The decorator wraps only the class it immediately precedes: its span
# extends the class's declaration range but never the identifier span, and
# the nested method/function must not inherit the decorator's extension.
def test_decorator_extends_only_its_own_declaration_span():
    raw = "@dec\nclass Box:\n    def run(self):\n        pass\n".encode()
    result = extract_file("d.py", raw, "python", pool=ParserPool())
    by_name = {s.qualified_name: s for s in result.symbols}
    assert by_name["Box"].declaration.start_byte == 0  # includes "@dec\n"
    assert by_name["Box"].selection.start_byte == 11    # "Box" identifier only
    assert by_name["Box.run"].declaration.start_byte > by_name["Box"].declaration.start_byte


@pytest.mark.parametrize("language,path,source,expected_names", CASES)
def test_extraction_corpus_matches_expected_qualified_names(language, path, source, expected_names):
    raw = source.encode()
    result = extract_file(path, raw, language, pool=ParserPool())
    by_qualified = {s.qualified_name: s for s in result.symbols}
    assert set(by_qualified) == expected_names
    for symbol in result.symbols:
        decoded = raw[symbol.selection.start_byte:symbol.selection.end_byte].decode("utf-8")
        assert decoded == symbol.name


# Targeted kind assertions so a name-only implementation cannot pass.
_EXPECTED_KINDS = {
    ("python", "Box"): DescriptorKind.TYPE,
    ("python", "Box.run"): DescriptorKind.METHOD,
    ("python", "Alias"): DescriptorKind.TYPE,
    ("javascript", "View"): DescriptorKind.METHOD,
    ("javascript", "Box"): DescriptorKind.TYPE,
    ("javascript", "Box.field"): DescriptorKind.METHOD,
    ("typescript", "N"): DescriptorKind.NAMESPACE,
    ("typescript", "N.P"): DescriptorKind.TYPE,
    ("typescript", "N.P.call"): DescriptorKind.METHOD,
    ("typescript", "N.Alias"): DescriptorKind.TYPE,
    ("go", "p"): DescriptorKind.NAMESPACE,
    ("go", "p.Box"): DescriptorKind.TYPE,
    ("go", "p.outer"): DescriptorKind.METHOD,
    ("csharp", "N"): DescriptorKind.NAMESPACE,
    ("csharp", "N.D"): DescriptorKind.TYPE,
    ("rust", "n"): DescriptorKind.NAMESPACE,
    ("rust", "n.P.Item"): DescriptorKind.TYPE,
    ("sql", "users"): DescriptorKind.TYPE,
}


@pytest.mark.parametrize("language,qualified_name,expected_kind", [
    (lang, name, kind) for (lang, name), kind in _EXPECTED_KINDS.items()
])
def test_extraction_corpus_assigns_expected_kinds(language, qualified_name, expected_kind):
    language_source = {case[0]: (case[1], case[2]) for case in CASES}
    path, source = language_source[language]
    result = extract_file(path, source.encode(), language, pool=ParserPool())
    by_qualified = {s.qualified_name: s for s in result.symbols}
    assert by_qualified[qualified_name].kind == expected_kind


def test_invalid_utf8_fails_with_no_symbols_and_no_tree():
    raw = b"class Box:\n    def run(self):\n        return 1\n" + b"\xff\xfe"
    result = extract_file("bad.py", raw, "python", pool=ParserPool())
    assert result.state == "failed"
    assert result.reason is not None
    assert result.symbols == ()
    assert result.tree is None


def test_empty_file_parses_with_zero_symbols():
    result = extract_file("empty.py", b"", "python", pool=ParserPool())
    assert result.state == "parsed"
    assert result.symbols == ()


def test_broken_body_beside_valid_class_keeps_the_valid_declaration():
    raw = b"class Box:\n    def run(self):\n        return 1\ndef broken(:\n    pass\n"
    result = extract_file("broken.py", raw, "python", pool=ParserPool())
    names = {s.qualified_name for s in result.symbols}
    assert {"Box", "Box.run"} <= names
    assert result.state == "partial"


def test_crlf_source_keeps_correct_selection_bytes():
    raw = b"class Box:\r\n    def run(self):\r\n        return 1\r\n"
    result = extract_file("crlf.py", raw, "python", pool=ParserPool())
    by_name = {s.qualified_name: s for s in result.symbols}
    assert set(by_name) == {"Box", "Box.run"}
    box = by_name["Box"]
    assert raw[box.selection.start_byte:box.selection.end_byte] == b"Box"


def test_forward_declaration_is_a_type_but_pointer_use_is_not():
    raw = b"struct S;\nstruct S *p;\n"
    result = extract_file("fwd.c", raw, "c", pool=ParserPool())
    names = {s.qualified_name for s in result.symbols}
    assert names == {"S"}
    assert result.symbols[0].kind == DescriptorKind.TYPE


def test_package_header_scopes_every_following_sibling():
    raw = b"package p\nfunc a() {}\nfunc b() {}\n"
    result = extract_file("multi.go", raw, "go", pool=ParserPool())
    names = {s.qualified_name for s in result.symbols}
    assert names == {"p", "p.a", "p.b"}


def test_quoted_and_operator_names_preserve_source_spelling():
    raw = b'const obj = { "my-key": () => 1 };'
    result = extract_file("q.js", raw, "javascript", pool=ParserPool())
    names = {s.name for s in result.symbols}
    assert '"my-key"' in names

    cpp_raw = b"class Box { public: ~Box(); Box operator+(Box b); };"
    cpp_result = extract_file("op.cpp", cpp_raw, "cpp", pool=ParserPool())
    cpp_names = {s.qualified_name for s in cpp_result.symbols}
    assert {"Box.~Box", "Box.operator+"} <= cpp_names


def test_unnamed_companion_object_has_no_row_but_its_members_qualify_under_the_class():
    raw = b"class Outer {\n    companion object {\n        fun make() {}\n    }\n}\n"
    result = extract_file("companion.kt", raw, "kotlin", pool=ParserPool())
    names = {s.qualified_name for s in result.symbols}
    assert names == {"Outer", "Outer.make"}
    make = next(s for s in result.symbols if s.qualified_name == "Outer.make")
    outer = next(s for s in result.symbols if s.qualified_name == "Outer")
    assert make.parent_symbol == outer.symbol


def test_extension_and_impl_contexts_qualify_without_a_duplicate_type_row():
    swift_raw = b"class Box {}\nextension Box { func run() {} }\n"
    swift_result = extract_file("ext.swift", swift_raw, "swift", pool=ParserPool())
    swift_names = {s.qualified_name for s in swift_result.symbols}
    assert swift_names == {"Box", "Box.run"}
    assert sum(1 for s in swift_result.symbols if s.qualified_name == "Box") == 1

    rust_raw = b"struct Box;\nimpl Box { fn run(&self) {} }\n"
    rust_result = extract_file("impl.rs", rust_raw, "rust", pool=ParserPool())
    rust_names = {s.qualified_name for s in rust_result.symbols}
    assert rust_names == {"Box", "Box.run"}


def test_dual_name_function_expression_records_both_names_at_distinct_spans():
    raw = b"const publicName = function internalName() {};"
    result = extract_file("dual.js", raw, "javascript", pool=ParserPool())
    by_name = {s.qualified_name: s for s in result.symbols}
    assert set(by_name) == {"publicName", "publicName.internalName"}
    assert by_name["publicName"].selection != by_name["publicName.internalName"].selection
    assert by_name["publicName.internalName"].parent_symbol == by_name["publicName"].symbol


def test_sql_create_procedure_is_partial_coverage_with_no_invented_symbol():
    raw = b"CREATE SCHEMA s; CREATE PROCEDURE p() AS 'x';"
    result = extract_file("proc.sql", raw, "sql", pool=ParserPool())
    names = {s.qualified_name for s in result.symbols}
    assert names == {"s"}
    assert result.state == "partial"


def test_language_for_path_resolves_the_full_extension_table():
    assert language_for_path("src/a.py") == "python"
    assert language_for_path("src/a.tsx") == "tsx"
    assert language_for_path("src/a.ts") == "typescript"
    assert language_for_path("src/a.h") == "c"
    assert language_for_path("src/a.hpp") == "cpp"
    assert language_for_path("noext") is None
    assert language_for_path("data.json") is None


def test_syntax_id_is_deterministic_and_prefixed():
    selection = Span(start_byte=0, end_byte=3, start_line=0, start_character=0, end_line=0, end_character=3)
    first = syntax_id("a.py", "hash1", selection, DescriptorKind.TYPE)
    second = syntax_id("a.py", "hash1", selection, DescriptorKind.TYPE)
    assert first == second
    assert first.startswith("syntax:")
    assert first != syntax_id("a.py", "hash2", selection, DescriptorKind.TYPE)
    assert first != syntax_id("a.py", "hash1", selection, DescriptorKind.METHOD)


def test_every_factory_language_appears_in_the_extraction_corpus():
    assert {case[0] for case in CASES} == set(FACTORIES)
