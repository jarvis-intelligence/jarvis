"""Unit tests for tree-sitter AST chunking. Requires the `semantic` extra."""
import pytest

pytest.importorskip("tree_sitter_language_pack")

from codeintel.chunker import (
    MAX_TOKENS, Chunk, chunk_file, hash_file, iter_source_files, language_for,
    skip_reason,
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


def test_oversized_function_splits_into_windows_with_shared_symbol_name():
    # A single top-level function has no natural sub-boundary like a class's
    # methods, so an oversized one must fall back to windowed splitting
    # instead of shipping as one unbounded chunk.
    body = "\n".join(f"    x{i} = {i}  # " + "pad " * 20 for i in range(80))
    source = f"def huge():\n{body}\n    return x0\n"
    chunks = chunk_file("h.py", source, "fh", "python")
    assert len(chunks) > 1
    assert all(c.symbol_name == "huge" for c in chunks)
    assert all(c.file_hash == "fh" and c.language == "python" for c in chunks)
    assert chunks[0].start_line == 1


def test_single_oversized_line_is_character_sliced():
    # A generated/minified file can have one line far exceeding the token
    # budget (e.g. a serialized literal) -- the fixed-window fallback must
    # not ship it whole as one unbounded chunk.
    huge_line = "x = 1  # " + "z" * 3000
    source = "just some text\n" * 200 + huge_line + "\n" + "just some text\n" * 200
    chunks = chunk_file("gen.py", source, "fh", "nosuchlanguage")
    from codeintel.chunker import MAX_TOKENS, _tokens
    assert all(_tokens(c.content) <= MAX_TOKENS for c in chunks)
    assert any(huge_line[:50] in c.content for c in chunks)


def test_merge_never_exceeds_max_tokens():
    # A tiny function (well under MIN_TOKENS on its own) directly followed by
    # a function close to but under MAX_TOKENS: naively merging them (as
    # "small chunk, always merge with neighbor") would push the combined
    # chunk over the hard MAX_TOKENS cap. MIN_TOKENS is only a soft
    # preference; MAX_TOKENS must never be violated to satisfy it.
    from codeintel.chunker import MAX_TOKENS, _tokens
    tiny = f'def tiny():\n    """{"p" * 600}"""\n    return 1\n'
    large_body = "\n".join(f"    y{i} = {i}  # " + "pad " * 10 for i in range(30))
    large = f"def large():\n{large_body}\n    return y0\n"
    assert _tokens(large) <= MAX_TOKENS  # precondition: fits alone

    source = f"{tiny}\n\n{large}"
    assert _tokens(tiny) + _tokens(large) > MAX_TOKENS  # precondition: combined would not

    chunks = chunk_file("m.py", source, "fh", "python")
    assert all(_tokens(c.content) <= MAX_TOKENS for c in chunks)
    names = {c.symbol_name for c in chunks}
    assert "tiny" in names and "large" in names


def test_oversized_method_within_oversized_class_is_windowed():
    # _split_class emits one chunk per method -- if a single method (plus
    # its context header) is itself over budget, it must be windowed too,
    # not shipped whole just because the class-level split already happened.
    from codeintel.chunker import MAX_TOKENS, _tokens
    small_body = "\n".join(f"    def m{i}(self):\n        return {i}\n" for i in range(3))
    big_lines = "\n".join(f"        z{i} = {i}  # " + "pad " * 15 for i in range(50))
    source = (
        "import os\n\n\n"
        f"class Big:\n{small_body}\n"
        f"    def huge(self):\n{big_lines}\n        return z0\n"
    )
    chunks = chunk_file("c.py", source, "fh", "python")
    assert all(_tokens(c.content) <= MAX_TOKENS for c in chunks)
    huge_chunks = [c for c in chunks if c.symbol_name == "huge"]
    assert len(huge_chunks) > 1
    # Only the first window carries the header -- repeating it on every
    # window would waste the very budget this split is meant to protect.
    assert huge_chunks[0].content.startswith("import os\nclass Big:")


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


# Kotlin's grammar attaches no "name" field to class_declaration/function_declaration
# nodes (unlike python/typescript/java/swift), so _node_name needs a Kotlin-specific
# fallback. Padded the same way as PY_TWO_FUNCS so the two defs don't merge.
KT_FUNC_AND_CLASS = f'''import kotlin.text.Regex

fun standalone(): Int {{
    // {"p" * 1100}
    return 1
}}

class Foo {{
    fun bar(): Int {{
        return 1
    }}
}}
'''


def test_kotlin_function_and_class_symbol_names_resolve():
    chunks = chunk_file("k.kt", KT_FUNC_AND_CLASS, "fh", "kotlin")
    names = [c.symbol_name for c in chunks]
    assert "standalone" in names
    assert "Foo" in names
    assert None not in names


def test_kotlin_oversized_class_splits_into_methods_with_symbol_names():
    body = "\n".join(
        f"    fun method_{i}(): Int {{\n        return {i}  // " + "pad " * 120 + "\n    }"
        for i in range(8)
    )
    source = f"import kotlin.text.Regex\n\nclass Big {{\n{body}\n}}\n"
    chunks = chunk_file("big.kt", source, "fh", "kotlin")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.startswith("import kotlin.text.Regex\nclass Big")
        assert chunk.symbol_name.startswith("method_")


@pytest.mark.parametrize("marker", [
    "auto-generated", "@generated", "code generated by",
    "do not edit", "do not modify this file",
])
def test_skip_reason_detects_each_banner(marker):
    source = f"# {marker}\ndef f():\n    return 1\n"
    assert skip_reason("a.py", source) == f"banner:{marker}"


def test_skip_reason_is_case_insensitive():
    source = "# Generated by the protocol buffer compiler.  DO NOT EDIT!\ndef f():\n    return 1\n"
    assert skip_reason("a.py", source) == "banner:do not edit"


def test_skip_reason_ignores_banner_past_scan_window():
    filler = "x = 1\n" * 500          # 3000 chars, past BANNER_SCAN_CHARS
    assert len(filler) > 2048
    assert skip_reason("a.py", filler + "# @generated\n") is None


def test_skip_reason_detects_long_line():
    source = "def f():\n    return '" + "z" * 6000 + "'\n"
    reason = skip_reason("a.py", source)
    assert reason is not None and reason.startswith("long-line:")


def test_skip_reason_admits_ordinary_source():
    assert skip_reason("a.py", "def f():\n    return 1\n") is None


def test_force_include_overrides_banner():
    source = "# @generated\ndef f():\n    return 1\n"
    assert skip_reason("src/gen/a.py", source, ("src/gen",)) is None


def test_force_include_matches_exact_file():
    source = "# @generated\ndef f():\n    return 1\n"
    assert skip_reason("src/gen/a.py", source, ("src/gen/a.py",)) is None


def test_force_include_respects_directory_boundary():
    """`src/gen` must not force-include `src/generated/...` — the naive
    startswith() bug this guards against."""
    source = "# @generated\ndef f():\n    return 1\n"
    assert skip_reason("src/generated/a.py", source, ("src/gen",)) == "banner:@generated"


@pytest.mark.integration
def test_own_generated_protobuf_is_skipped():
    """Dogfood check: codeintel's own scip_pb2.py is the generated file
    that motivated this filter. It carries three banners and a
    12k-character line, so any one of the rules should catch it."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src"
    skipped = []
    for abs_path, rel_path in iter_source_files(src):
        source = abs_path.read_bytes().decode("utf-8", errors="replace")
        if skip_reason(rel_path, source) is not None:
            skipped.append(rel_path)
    assert any(path.endswith("scip_pb2.py") for path in skipped)
