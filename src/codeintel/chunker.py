"""tree-sitter AST chunking for the semantic index.

Splits source files at function/class boundaries (256-512 token target;
tokens approximated as len(text)//4, no tokenizer dependency). An
oversized class splits into per-method chunks, each prefixed with the
file's imports (capped) plus the class definition line — the "context
re-add" pattern. Unparseable files fall back to fixed overlapping
windows. tree_sitter_language_pack is imported lazily so a base install
never needs it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from codeintel.config import IGNORED_DIRS

MAX_TOKENS = 512
MIN_TOKENS = 256
OVERLAP_TOKENS = 50
MAX_IMPORT_LINES = 10

LANGUAGES = {".py": "python", ".ts": "typescript", ".tsx": "tsx",
             ".java": "java", ".kt": "kotlin", ".swift": "swift"}

_DEF_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition"},
    "tsx": {"function_declaration", "class_declaration", "method_definition"},
    "java": {"method_declaration", "class_declaration", "interface_declaration"},
    "kotlin": {"function_declaration", "class_declaration"},
    "swift": {"function_declaration", "class_declaration", "protocol_declaration"},
}
_CLASS_NODE_TYPES = {"class_definition", "class_declaration",
                     "interface_declaration", "protocol_declaration"}
# Kotlin's grammar attaches no "name" field to class_declaration/function_declaration
# (verified by direct parse); the identifier is a plain positional child instead.
_IDENTIFIER_NODE_TYPES = {"type_identifier", "simple_identifier"}
_IMPORT_NODE_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "java": {"import_declaration"},
    "kotlin": {"import_list"},
    "swift": {"import_declaration"},
}


@dataclass(frozen=True)
class Chunk:
    file_path: str          # repo-relative posix path
    start_line: int         # 1-indexed
    end_line: int           # 1-indexed, inclusive
    content: str
    symbol_name: str | None  # None for fixed-window chunks
    content_hash: str
    file_hash: str
    language: str


def _tokens(text: str) -> int:
    return len(text) // 4


def hash_file(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def language_for(path: Path) -> str | None:
    return LANGUAGES.get(path.suffix)


def iter_source_files(repo_path: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(repo_path.rglob("*")):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in LANGUAGES:
            files.append((path, path.relative_to(repo_path).as_posix()))
    return files


def _make_chunk(rel_path: str, language: str, file_hash: str, content: str,
                start_line: int, end_line: int, symbol: str | None) -> Chunk:
    return Chunk(file_path=rel_path, start_line=start_line, end_line=end_line,
                 content=content, symbol_name=symbol,
                 content_hash=hashlib.sha256(content.encode()).hexdigest(),
                 file_hash=file_hash, language=language)


def _node_name(node) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return named.text.decode("utf-8", errors="replace")
    for child in node.children:  # e.g. decorated_definition wraps the real def
        # Suffix check avoids false positives like "function_value_parameters" /
        # "function_body", which also contain the substring "function".
        if child.type.endswith(("_definition", "_declaration")) and (
            child.type in _CLASS_NODE_TYPES or "function" in child.type or "method" in child.type
        ):
            return _node_name(child)
    for child in node.children:  # grammars with no "name" field (e.g. kotlin)
        if child.type in _IDENTIFIER_NODE_TYPES:
            return child.text.decode("utf-8", errors="replace")
    return None


def _walk(node):
    for child in node.children:
        yield child
        yield from _walk(child)


def _fixed_windows(rel_path: str, language: str, file_hash: str, source: str) -> list[Chunk]:
    lines = source.splitlines()
    if not lines:
        return []
    window_chars, overlap_chars = MAX_TOKENS * 4, OVERLAP_TOKENS * 4
    chunks: list[Chunk] = []
    i = 0
    while i < len(lines):
        j, size = i, 0
        while j < len(lines) and size < window_chars:
            size += len(lines[j]) + 1
            j += 1
        chunks.append(_make_chunk(rel_path, language, file_hash,
                                  "\n".join(lines[i:j]), i + 1, j, None))
        if j >= len(lines):
            break
        back, osize = j, 0
        while back > i + 1 and osize < overlap_chars:
            back -= 1
            osize += len(lines[back]) + 1
        i = back
    return chunks


def _split_class(node, source: str, rel_path: str, language: str,
                 file_hash: str, imports: list[str]) -> list[Chunk]:
    class_line = source[node.start_byte:node.end_byte].splitlines()[0]
    header = "\n".join([*imports[:MAX_IMPORT_LINES], class_line])
    methods = [n for n in _walk(node)
               if n.type in _DEF_NODE_TYPES[language] and n.type not in _CLASS_NODE_TYPES]
    if not methods:
        text = source[node.start_byte:node.end_byte]
        return [_make_chunk(rel_path, language, file_hash, text,
                            node.start_point[0] + 1, node.end_point[0] + 1, _node_name(node))]
    return [
        _make_chunk(rel_path, language, file_hash,
                    f"{header}\n{source[m.start_byte:m.end_byte]}",
                    m.start_point[0] + 1, m.end_point[0] + 1, _node_name(m))
        for m in methods
    ]


def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    merged: list[Chunk] = []
    for chunk in chunks:
        if merged and _tokens(merged[-1].content) < MIN_TOKENS and merged[-1].end_line < chunk.start_line:
            prev = merged.pop()
            merged.append(_make_chunk(chunk.file_path, chunk.language, chunk.file_hash,
                                      f"{prev.content}\n\n{chunk.content}",
                                      prev.start_line, chunk.end_line,
                                      prev.symbol_name or chunk.symbol_name))
        else:
            merged.append(chunk)
    return merged


def _collect_imports(root, source: str, language: str) -> list[str]:
    """One string per import statement, so MAX_IMPORT_LINES caps meaningfully.

    Most grammars give each import its own top-level node. Kotlin instead
    wraps every import in a single top-level `import_list` container node
    (verified by direct parse), so that container is expanded into its
    `import_header` children instead of being kept as one opaque blob."""
    imports: list[str] = []
    for node in root.children:
        if node.type not in _IMPORT_NODE_TYPES.get(language, set()):
            continue
        if node.type == "import_list":
            imports.extend(source[c.start_byte:c.end_byte]
                            for c in node.children if c.type == "import_header")
        else:
            imports.append(source[node.start_byte:node.end_byte])
    return imports


def chunk_file(rel_path: str, source: str, file_hash: str, language: str) -> list[Chunk]:
    try:
        from tree_sitter_language_pack import get_parser
        tree = get_parser(language).parse(source.encode("utf-8"))
    except Exception:
        return _fixed_windows(rel_path, language, file_hash, source)

    root = tree.root_node
    imports = _collect_imports(root, source, language)
    defs = [n for n in root.children if n.type in _DEF_NODE_TYPES.get(language, set())]
    if not defs:
        return _fixed_windows(rel_path, language, file_hash, source)

    chunks: list[Chunk] = []
    for node in defs:
        text = source[node.start_byte:node.end_byte]
        target = node.children[-1] if node.type == "decorated_definition" else node
        if target.type in _CLASS_NODE_TYPES and _tokens(text) > MAX_TOKENS:
            chunks.extend(_split_class(target, source, rel_path, language, file_hash, imports))
        else:
            chunks.append(_make_chunk(rel_path, language, file_hash, text,
                                      node.start_point[0] + 1, node.end_point[0] + 1,
                                      _node_name(node)))
    return _merge_small(chunks)
