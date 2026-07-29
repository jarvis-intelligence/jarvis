"""Unit tests for tree-sitter AST chunking. Requires the `semantic` extra."""
import pytest

pytest.importorskip("tree_sitter_language_pack")

from codeintel.chunker import (
    MAX_TOKENS, Chunk, chunk_file, hash_file, iter_source_files, language_for,
)

# Each function body is >256 tokens (pad expanded at build time, not literal
# text) so the two functions stand as separate chunks instead of merging.
PY_TWO_FUNCS = f'''import os
from pathlib import Path


def alpha(x):
    """{"p" * 1100}"""
    return x + 1


def beta(y):
    """{"q" * 1100}"""
    return y - 1
'''


def test_functions_become_chunks_with_symbol_names():
    chunks = chunk_file("m.py", PY_TWO_FUNCS, "fh", "python")
    names = [c.symbol_name for c in chunks]
    assert "alpha" in names and "beta" in names
    alpha = next(c for c in chunks if c.symbol_name == "alpha")
    assert alpha.start_line == 5
    assert "def alpha" in alpha.content
    assert alpha.file_hash == "fh" and alpha.language == "python"


def test_oversized_class_splits_into_methods_with_imports_and_class_header():
    body = "\n".join(
        f"    def method_{i}(self):\n        return {i}  # " + "pad " * 120
        for i in range(8)
    )
    source = f"import os\nfrom sys import path\n\n\nclass Big:\n{body}\n"
    chunks = chunk_file("big.py", source, "fh", "python")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.startswith("import os\nfrom sys import path\nclass Big:")
        assert chunk.symbol_name.startswith("method_")


def test_tiny_siblings_merge():
    source = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    chunks = chunk_file("t.py", source, "fh", "python")
    assert len(chunks) == 1
    assert chunks[0].start_line == 1 and "def b" in chunks[0].content


def test_unparseable_language_falls_back_to_fixed_windows():
    source = "just some text\n" * 400
    chunks = chunk_file("x.py", source, "fh", "nosuchlanguage")
    assert len(chunks) >= 2
    assert all(c.symbol_name is None for c in chunks)
    assert chunks[1].start_line <= chunks[0].end_line  # overlap


def test_iter_source_files_prunes_ignored_dirs(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.py").write_text("x = 2")
    (tmp_path / "README.md").write_text("nope")
    files = iter_source_files(tmp_path)
    assert [rel for _, rel in files] == ["a.py"]


def test_content_hash_is_stable_sha256():
    chunks = chunk_file("m.py", PY_TWO_FUNCS, "fh", "python")
    import hashlib
    c = chunks[0]
    assert c.content_hash == hashlib.sha256(c.content.encode()).hexdigest()
