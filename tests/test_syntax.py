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
