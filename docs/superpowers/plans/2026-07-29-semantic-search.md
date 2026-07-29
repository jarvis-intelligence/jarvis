# Semantic/Vector Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `semanticSearch` MCP tool backed by tree-sitter chunking, self-hosted embeddings (bge-m3), LanceDB vector storage, and RRF fusion with the existing Zoekt lexical search.

**Architecture:** Three new modules (`chunker.py`, `embeddings.py`, `semantic.py`) behind an optional `semantic` extra; a new non-fatal stage in `index_repo()` before the atomic pointer flip; one new MCP tool in `server.py`. A LanceDB table (one per repo, under `~/.codeintel/lancedb/`) only ever holds vectors from one model at one revision.

**Tech Stack:** Python 3.12+, tree-sitter + tree-sitter-language-pack, sentence-transformers, LanceDB (embedded), existing Zoekt/httpx.

**Spec:** `docs/superpowers/specs/2026-07-29-semantic-search-design.md` — the authority for behavior. Read it first.

## Global Constraints

- Python `>=3.12`; modern type hints (`str | None`, `list[T]`); frozen dataclasses for result types; direct `sqlite3`, parameterized queries.
- New deps ONLY under `[project.optional-dependencies] semantic`: `lancedb>=0.20`, `sentence-transformers>=3.0`, `tree-sitter>=0.25`, `tree-sitter-language-pack>=0.1`. Base install behavior must not change.
- Heavy imports (`tree_sitter_language_pack`, `sentence_transformers`, `lancedb`) are deferred inside functions — importing `codeintel.semantic`/`chunker`/`embeddings` must succeed without the extra installed.
- Env vars prefixed `CODEINTEL_`: `CODEINTEL_EMBEDDING_MODEL` (default `BAAI/bge-m3`), `CODEINTEL_EMBEDDING_BATCH_SIZE` (default 32).
- Unit tests must pass without the extra installed: test modules that need it start with `pytest.importorskip(...)`.
- Every MCP tool returns `{"error": "..."}` on failure, never raises.
- Commits: conventional format, no AI references. Run `uv run pytest -m "not integration"` before each commit.
- Dev setup for this work: `uv sync --extra semantic`.

---

### Task 1: `semantic` extra + config path

**Files:**
- Modify: `pyproject.toml:20-21` (optional-dependencies)
- Modify: `src/codeintel/config.py` (add `IGNORED_DIRS`, `lancedb_dir()`)
- Modify: `src/codeintel/index_cli.py:46` (alias `_IGNORED_DIRS` to config)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.lancedb_dir(root: Path | None = None) -> Path` (returns `data_dir(root) / "lancedb"`); `config.IGNORED_DIRS: set[str]` (the set currently at `index_cli.py:46`).

- [ ] **Step 1: Write failing tests** (append to `tests/test_config.py`)

```python
def test_lancedb_dir_under_data_dir(tmp_path):
    assert config.lancedb_dir(tmp_path) == tmp_path / "lancedb"


def test_ignored_dirs_shared_with_index_cli():
    from codeintel import index_cli
    assert index_cli._IGNORED_DIRS is config.IGNORED_DIRS
    assert "node_modules" in config.IGNORED_DIRS
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_config.py -v` → FAIL (`AttributeError: lancedb_dir`)

- [ ] **Step 3: Implement.** In `config.py` add below `BRANCH = "_"`:

```python
IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", "DerivedData", ".build"}


def lancedb_dir(root: Path | None = None) -> Path:
    """Directory holding one LanceDB table per repo (semantic search vectors)."""
    return data_dir(root) / "lancedb"
```

In `index_cli.py` replace the literal set at line 46 with `_IGNORED_DIRS = config.IGNORED_DIRS`. In `pyproject.toml` under `[project.optional-dependencies]` add:

```toml
semantic = [
    "lancedb>=0.20",
    "sentence-transformers>=3.0",
    "tree-sitter>=0.25",
    "tree-sitter-language-pack>=0.1",
]
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_config.py tests/test_index_cli.py -m "not integration" -v` → PASS. Then `uv sync --extra semantic` (installs dev deps for later tasks).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(semantic): add semantic extra and lancedb data dir"`

---

### Task 2: Chunker (`chunker.py`)

**Files:**
- Create: `src/codeintel/chunker.py`
- Test: `tests/test_chunker.py`

**Interfaces:**
- Consumes: `config.IGNORED_DIRS` (Task 1).
- Produces:
  - `Chunk` frozen dataclass: `file_path: str, start_line: int, end_line: int, content: str, symbol_name: str | None, content_hash: str, file_hash: str, language: str` (lines 1-indexed inclusive; `file_path` repo-relative posix).
  - `hash_file(data: bytes) -> str` (sha256 hex).
  - `language_for(path: Path) -> str | None` (tree-sitter language name by extension).
  - `iter_source_files(repo_path: Path) -> list[tuple[Path, str]]` — `(abs_path, rel_posix)` for supported extensions, `IGNORED_DIRS` pruned, sorted.
  - `chunk_file(rel_path: str, source: str, file_hash: str, language: str) -> list[Chunk]`.

- [ ] **Step 1: Write failing tests** (`tests/test_chunker.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_chunker.py -v` → FAIL (module not found)

- [ ] **Step 3: Implement `src/codeintel/chunker.py`**

```python
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
_IMPORT_NODE_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "java": {"import_declaration"},
    "kotlin": {"import_header"},
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
        if child.type in _CLASS_NODE_TYPES or "function" in child.type or "method" in child.type:
            return _node_name(child)
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


def chunk_file(rel_path: str, source: str, file_hash: str, language: str) -> list[Chunk]:
    try:
        from tree_sitter_language_pack import get_parser
        tree = get_parser(language).parse(source.encode("utf-8"))
    except Exception:
        return _fixed_windows(rel_path, language, file_hash, source)

    root = tree.root_node
    imports = [source[n.start_byte:n.end_byte]
               for n in root.children if n.type in _IMPORT_NODE_TYPES.get(language, set())]
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
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_chunker.py -v` → PASS. If a tree-sitter node-type name is wrong for a grammar (test fails with 0 chunks), print the actual tree with `get_parser(lang).parse(src).root_node` children `.type` values and fix the `_DEF_NODE_TYPES`/`_IMPORT_NODE_TYPES` entry — do NOT loosen the tests.

- [ ] **Step 5: Commit** — `git add src/codeintel/chunker.py tests/test_chunker.py && git commit -m "feat(semantic): tree-sitter AST chunker with fixed-window fallback"`

---

### Task 3: Embedding wrapper (`embeddings.py`)

**Files:**
- Create: `src/codeintel/embeddings.py`
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Produces:
  - `SemanticExtraMissingError(Exception)` with message `"semantic search requires the 'semantic' extra: uv sync --extra semantic"`.
  - `EmbeddingModel(model_name: str | None = None, revision: str | None = None, batch_size: int | None = None)` with `.identity() -> tuple[str, str]`, `.embed_texts(texts: list[str]) -> list[list[float]]`, `.embed_query(query: str) -> list[float]`.
  - `default_model() -> EmbeddingModel` (module singleton).
  - Constants: `DEFAULT_MODEL`, `DEFAULT_REVISION`, `DEFAULT_BATCH_SIZE = 32`.

- [ ] **Step 1: Write failing tests** (`tests/test_embeddings.py`)

```python
"""Unit tests for the embedding wrapper. The heavy sentence-transformers
dependency is faked via sys.modules — these tests run without the extra."""
import sys
import types

import pytest

from codeintel import embeddings
from codeintel.embeddings import EmbeddingModel, SemanticExtraMissingError


class _FakeST:
    def __init__(self, model_name, revision=None, trust_remote_code=False):
        self.model_name, self.revision = model_name, revision
        self.calls: list[list[str]] = []
        self.normalize_flags: list[bool] = []

    def encode(self, batch, normalize_embeddings=False):
        self.calls.append(list(batch))
        self.normalize_flags.append(normalize_embeddings)
        return [[float(len(t)), 1.0] for t in batch]


def _install_fake(monkeypatch):
    holder = {}

    def ctor(*args, **kwargs):
        holder["model"] = _FakeST(*args, **kwargs)
        return holder["model"]

    fake_module = types.SimpleNamespace(SentenceTransformer=ctor)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    return holder


def test_missing_extra_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    model = EmbeddingModel()
    with pytest.raises(SemanticExtraMissingError, match="uv sync --extra semantic"):
        model.embed_texts(["x"])


def test_lazy_load_and_batching(monkeypatch):
    holder = _install_fake(monkeypatch)
    model = EmbeddingModel(model_name="m", revision="r", batch_size=2)
    assert "model" not in holder  # nothing loaded at construction
    vectors = model.embed_texts(["a", "bb", "ccc"])
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    assert holder["model"].calls == [["a", "bb"], ["ccc"]]  # batch_size respected
    assert holder["model"].normalize_flags == [True, True]  # L2-normalized vectors


def test_embed_query_encodes_raw_text(monkeypatch):
    # bge-m3 dropped the query-instruction-prefix requirement present in
    # earlier BGE versions, so embed_query is a direct passthrough.
    holder = _install_fake(monkeypatch)
    model = EmbeddingModel(model_name="m", revision="r")
    model.embed_query("auth")
    assert holder["model"].calls[0][0] == "auth"


def test_identity_and_env_overrides(monkeypatch):
    monkeypatch.setenv("CODEINTEL_EMBEDDING_MODEL", "custom/model")
    monkeypatch.setenv("CODEINTEL_EMBEDDING_BATCH_SIZE", "7")
    model = EmbeddingModel()
    assert model.identity() == ("custom/model", "unpinned")
    assert model.batch_size == 7
    default = EmbeddingModel(model_name=embeddings.DEFAULT_MODEL)
    assert default.identity() == (embeddings.DEFAULT_MODEL, embeddings.DEFAULT_REVISION)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_embeddings.py -v` → FAIL

- [ ] **Step 3: Pin the model revision.** Run:

```bash
uv run python -c "from huggingface_hub import HfApi; print(HfApi().model_info('BAAI/bge-m3').sha)"
```

(`huggingface_hub` ships with sentence-transformers.) Use the printed sha as `DEFAULT_REVISION` below. If offline, set `DEFAULT_REVISION = "main"` and leave a `# pin to a commit sha before first real index` comment — but try the fetch first.

- [ ] **Step 4: Implement `src/codeintel/embeddings.py`**

```python
"""Self-hosted embedding model wrapper (sentence-transformers, lazy-loaded).

The model is never loaded at MCP server startup — only on the first
embed call, mirroring ZoektLifecycle's lazy-spawn pattern. Vectors from
different models must never mix (see semantic.py's model-identity rule),
so the (model_name, revision) identity travels with every embedding.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_REVISION = "<sha-from-step-3>"
DEFAULT_BATCH_SIZE = 32
_INSTALL_HINT = "semantic search requires the 'semantic' extra: uv sync --extra semantic"


class SemanticExtraMissingError(Exception):
    """The `semantic` optional dependencies are not installed."""


class EmbeddingModel:
    def __init__(self, model_name: str | None = None, revision: str | None = None,
                 batch_size: int | None = None) -> None:
        self.model_name = model_name or os.environ.get("CODEINTEL_EMBEDDING_MODEL", DEFAULT_MODEL)
        if revision is not None:
            self.revision = revision
        else:
            self.revision = DEFAULT_REVISION if self.model_name == DEFAULT_MODEL else "unpinned"
        self.batch_size = batch_size or int(os.environ.get("CODEINTEL_EMBEDDING_BATCH_SIZE",
                                                           DEFAULT_BATCH_SIZE))
        self._model = None

    def identity(self) -> tuple[str, str]:
        return (self.model_name, self.revision)

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise SemanticExtraMissingError(_INSTALL_HINT) from exc
            revision = None if self.revision == "unpinned" else self.revision
            self._model = SentenceTransformer(self.model_name, revision=revision,
                                              trust_remote_code=True)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            # normalize_embeddings: L2-normalize so cosine ranking at query
            # time is exact (standard hubness mitigation).
            for vec in model.encode(texts[i:i + self.batch_size], normalize_embeddings=True):
                vectors.append(list(vec) if not hasattr(vec, "tolist") else vec.tolist())
        return vectors

    def embed_query(self, query: str) -> list[float]:
        # bge-m3 needs no query-side instruction prefix (unlike earlier BGE
        # versions) — encode the raw query text directly.
        return self.embed_texts([query])[0]


_default: EmbeddingModel | None = None


def default_model() -> EmbeddingModel:
    global _default
    if _default is None:
        _default = EmbeddingModel()
    return _default
```

Note the `sys.modules[name] = None` trick in the test makes `import sentence_transformers` raise `ImportError` — that is exactly the production missing-extra path.

- [ ] **Step 5: Verify** — `uv run pytest tests/test_embeddings.py -v` → PASS

- [ ] **Step 6: Commit** — `git add src/codeintel/embeddings.py tests/test_embeddings.py && git commit -m "feat(semantic): lazy sentence-transformers embedding wrapper with pinned revision"`

---

### Task 4: RRF fusion (`semantic.py`, part 1)

**Files:**
- Create: `src/codeintel/semantic.py`
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `ZoektHit` from `codeintel.search` (fields `repo, path, line_number, line_text`).
- Produces:
  - `FusedHit` frozen dataclass: `repo: str, file_path: str, start_line: int, end_line: int, symbol_name: str | None, content: str, score: float, sources: tuple[str, ...]`.
  - `reciprocal_rank_fusion(repo: str, vector_rows: list[dict], zoekt_hits: list[ZoektHit], *, k: int = 60) -> list[FusedHit]` — `vector_rows` are LanceDB row dicts carrying at least `file_path, start_line, end_line, symbol_name, content`, ranked best-first. Zoekt hits map onto a vector row when the path matches and the hit line falls inside the row's line range; otherwise they become their own single-line entry.
  - Constants: `RRF_K = 60`, `VECTOR_TOP_K = 30`, `ZOEKT_TOP_K = 30`, `CONTENT_TRUNCATE = 500`.

- [ ] **Step 1: Write failing tests** (start `tests/test_semantic.py`; NO importorskip needed for this part — RRF is pure Python)

```python
"""Unit tests for semantic store + RRF fusion."""
import pytest

from codeintel.search import ZoektHit
from codeintel.semantic import FusedHit, reciprocal_rank_fusion


def _row(path, start, end, symbol="s", content="body"):
    return {"file_path": path, "start_line": start, "end_line": end,
            "symbol_name": symbol, "content": content}


def test_rrf_scores_are_hand_computed():
    vector_rows = [_row("a.py", 1, 10), _row("b.py", 1, 10)]
    zoekt_hits = [ZoektHit(repo="r", path="a.py", line_number=5, line_text="x")]
    fused = reciprocal_rank_fusion("r", vector_rows, zoekt_hits, k=60)
    by_path = {f.file_path: f for f in fused}
    # a.py: vector rank 1 + zoekt rank 1 = 1/61 + 1/61; b.py: vector rank 2 = 1/62
    assert by_path["a.py"].score == pytest.approx(2 / 61)
    assert by_path["b.py"].score == pytest.approx(1 / 62)
    assert fused[0].file_path == "a.py"  # sorted best-first
    assert by_path["a.py"].sources == ("vector", "zoekt")
    assert by_path["b.py"].sources == ("vector",)


def test_zoekt_only_hit_becomes_single_line_entry():
    zoekt_hits = [ZoektHit(repo="r", path="c.py", line_number=7, line_text="the line")]
    fused = reciprocal_rank_fusion("r", [], zoekt_hits)
    assert len(fused) == 1
    hit = fused[0]
    assert (hit.start_line, hit.end_line, hit.content) == (7, 7, "the line")
    assert hit.sources == ("zoekt",) and hit.symbol_name is None


def test_zoekt_hit_outside_chunk_range_does_not_merge():
    vector_rows = [_row("a.py", 1, 4)]
    zoekt_hits = [ZoektHit(repo="r", path="a.py", line_number=99, line_text="x")]
    fused = reciprocal_rank_fusion("r", vector_rows, zoekt_hits)
    assert len(fused) == 2


def test_content_truncated_to_500_chars():
    vector_rows = [_row("a.py", 1, 9, content="z" * 900)]
    fused = reciprocal_rank_fusion("r", vector_rows, [])
    assert len(fused[0].content) == 500
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_semantic.py -v` → FAIL

- [ ] **Step 3: Implement** the top of `src/codeintel/semantic.py`:

```python
"""LanceDB vector storage + hybrid semantic search (vector + Zoekt via RRF).

A table only ever contains vectors from one model at one revision — the
model-identity rule. lancedb is imported lazily; importing this module
works without the `semantic` extra.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from codeintel import config
from codeintel.chunker import Chunk, chunk_file, hash_file, iter_source_files, language_for
from codeintel.embeddings import EmbeddingModel, default_model
from codeintel.search import ZoektHit, ZoektUnavailableError, search_zoekt

RRF_K = 60
VECTOR_TOP_K = 30
ZOEKT_TOP_K = 30
CONTENT_TRUNCATE = 500


class NoSemanticIndexError(Exception):
    """No LanceDB table exists for the requested repo."""


@dataclass(frozen=True)
class FusedHit:
    repo: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    content: str
    score: float
    sources: tuple[str, ...]


def reciprocal_rank_fusion(repo: str, vector_rows: list[dict],
                           zoekt_hits: list[ZoektHit], *, k: int = RRF_K) -> list[FusedHit]:
    entries: dict[tuple, dict] = {}

    def _add(key: tuple, rank: int, source: str, row: dict | None) -> None:
        entry = entries.setdefault(key, {"score": 0.0, "sources": [], "row": row})
        entry["score"] += 1.0 / (k + rank)
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if entry["row"] is None:
            entry["row"] = row

    for rank, row in enumerate(vector_rows, start=1):
        _add((row["file_path"], row["start_line"], row["end_line"]), rank, "vector", row)

    for rank, hit in enumerate(zoekt_hits, start=1):
        merged_key = next(
            ((r["file_path"], r["start_line"], r["end_line"]) for r in vector_rows
             if r["file_path"] == hit.path and r["start_line"] <= hit.line_number <= r["end_line"]),
            None,
        )
        if merged_key is not None:
            _add(merged_key, rank, "zoekt", None)
        else:
            _add((hit.path, hit.line_number, hit.line_number), rank, "zoekt",
                 {"file_path": hit.path, "start_line": hit.line_number,
                  "end_line": hit.line_number, "symbol_name": None, "content": hit.line_text})

    fused = [
        FusedHit(repo=repo, file_path=e["row"]["file_path"],
                 start_line=e["row"]["start_line"], end_line=e["row"]["end_line"],
                 symbol_name=e["row"]["symbol_name"],
                 content=(e["row"]["content"] or "")[:CONTENT_TRUNCATE],
                 score=e["score"], sources=tuple(e["sources"]))
        for e in entries.values()
    ]
    return sorted(fused, key=lambda h: h.score, reverse=True)
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_semantic.py -v` → PASS

- [ ] **Step 5: Commit** — `git add src/codeintel/semantic.py tests/test_semantic.py && git commit -m "feat(semantic): reciprocal rank fusion of vector and zoekt hits"`

---

### Task 5: SemanticStore + write path (`semantic.py`, part 2)

**Files:**
- Modify: `src/codeintel/semantic.py`
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `Chunk`, `iter_source_files`, `chunk_file`, `hash_file`, `language_for` (Task 2); `EmbeddingModel.identity()/embed_texts()` (Task 3); `config.lancedb_dir` (Task 1).
- Produces:
  - `SemanticStore(db_dir: Path)` with `.table_identity(slug) -> tuple[str, str] | None`, `.rows_by_path(slug) -> dict[str, list[dict]]`, `.overwrite(slug, rows: list[dict]) -> None` (uses `create_table(mode="overwrite")` — an atomic replace; empty `rows` drops the table), `.search(slug, vector: list[float], limit: int) -> list[dict]`, `.drop(slug) -> None`.
  - `index_semantic(repo_path: Path, slug: str, *, root: Path | None = None, model: EmbeddingModel | None = None) -> int` — returns the number of rows written. Row schema: `chunk_id, content_hash, file_hash, file_path, start_line, end_line, symbol_name (may be ""), language, content, vector, model_name, model_revision`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_semantic.py`; these need lancedb)

```python
class FakeEmbedder:
    """Deterministic 3-dim embedder; counts embed calls for reuse assertions."""
    def __init__(self, name="fake-model", revision="rev1"):
        self._identity = (name, revision)
        self.embedded: list[str] = []

    def identity(self):
        return self._identity

    def embed_texts(self, texts):
        self.embedded.extend(texts)
        return [[float(len(t) % 97), 1.0, 0.0] for t in texts]

    def embed_query(self, query):
        return self.embed_texts([query])[0]


FUNC = 'def f_{n}():\n    """{pad}"""\n    return {n}\n'


def _write_repo(tmp_path, n_files=2):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    for i in range(n_files):
        (repo / f"mod_{i}.py").write_text(FUNC.format(n=i, pad="p" * 1100))
    return repo


@pytest.fixture()
def lancedb_available():
    pytest.importorskip("lancedb")


def test_index_semantic_writes_rows(tmp_path, lancedb_available):
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    embedder = FakeEmbedder()
    count = index_semantic(repo, "myrepo", root=tmp_path / "data", model=embedder)
    assert count == 2
    store = SemanticStore((tmp_path / "data") / "lancedb")
    assert store.table_identity("myrepo") == ("fake-model", "rev1")
    rows = store.rows_by_path("myrepo")
    assert set(rows) == {"mod_0.py", "mod_1.py"}
    assert rows["mod_0.py"][0]["language"] == "python"


def test_unchanged_files_carry_over_without_reembedding(tmp_path, lancedb_available):
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    first = FakeEmbedder()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=first)
    (repo / "mod_1.py").write_text(FUNC.format(n=99, pad="q" * 1100))
    second = FakeEmbedder()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=second)
    assert len(second.embedded) == 1  # only the changed file's chunk
    assert "f_99" in second.embedded[0]


def test_deleted_files_drop_out(tmp_path, lancedb_available):
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    (repo / "mod_1.py").unlink()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    rows = SemanticStore((tmp_path / "data") / "lancedb").rows_by_path("myrepo")
    assert set(rows) == {"mod_0.py"}


def test_model_change_forces_full_reembed(tmp_path, lancedb_available):
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    changed = FakeEmbedder(revision="rev2")
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=changed)
    assert len(changed.embedded) == 2  # nothing reused across revisions
    identity = SemanticStore((tmp_path / "data") / "lancedb").table_identity("myrepo")
    assert identity == ("fake-model", "rev2")


def test_duplicate_content_embedded_once(tmp_path, lancedb_available):
    from codeintel.semantic import index_semantic
    repo = tmp_path / "repo"
    repo.mkdir()
    same = FUNC.format(n=1, pad="r" * 1100)
    (repo / "a.py").write_text(same)
    (repo / "b.py").write_text(same)
    embedder = FakeEmbedder()
    count = index_semantic(repo, "myrepo", root=tmp_path / "data", model=embedder)
    assert count == 2 and len(embedder.embedded) == 1
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_semantic.py -v` → new tests FAIL

- [ ] **Step 3: Implement** (append to `semantic.py`):

```python
class SemanticStore:
    """One LanceDB table per repo, named by slug (on disk: `<slug>.lance/`)."""

    def __init__(self, db_dir: Path) -> None:
        self._db_dir = db_dir
        self._db = None

    def _connect(self):
        if self._db is None:
            import lancedb
            self._db_dir.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self._db_dir))
        return self._db

    def _open(self, slug: str):
        db = self._connect()
        if slug not in db.table_names():
            return None
        return db.open_table(slug)

    def table_identity(self, slug: str) -> tuple[str, str] | None:
        table = self._open(slug)
        if table is None:
            return None
        rows = table.head(1).to_pylist()
        if not rows:
            return None
        return (rows[0]["model_name"], rows[0]["model_revision"])

    def rows_by_path(self, slug: str) -> dict[str, list[dict]]:
        table = self._open(slug)
        if table is None:
            return {}
        grouped: dict[str, list[dict]] = {}
        for row in table.to_arrow().to_pylist():
            grouped.setdefault(row["file_path"], []).append(row)
        return grouped

    def overwrite(self, slug: str, rows: list[dict]) -> None:
        db = self._connect()
        if not rows:
            self.drop(slug)
            return
        db.create_table(slug, data=rows, mode="overwrite")

    def search(self, slug: str, vector: list[float], limit: int) -> list[dict]:
        table = self._open(slug)
        if table is None:
            return []
        # Cosine, never LanceDB's default L2 — vectors are normalized at
        # encode time, so cosine ranking is exact.
        return table.search(vector).metric("cosine").limit(limit).to_list()

    def drop(self, slug: str) -> None:
        db = self._connect()
        if slug in db.table_names():
            db.drop_table(slug)


def index_semantic(repo_path: Path, slug: str, *, root: Path | None = None,
                   model: EmbeddingModel | None = None) -> int:
    model = model or default_model()
    store = SemanticStore(config.lancedb_dir(root))
    model_name, model_revision = model.identity()

    # Model-identity rule: the previous table is only a reuse source when
    # its identity matches — vectors from different models never mix.
    previous = (store.rows_by_path(slug)
                if store.table_identity(slug) == (model_name, model_revision) else {})
    vector_by_hash = {row["content_hash"]: row["vector"]
                      for rows in previous.values() for row in rows}

    carried: list[dict] = []
    pending: list[Chunk] = []
    for abs_path, rel_path in iter_source_files(repo_path):
        data = abs_path.read_bytes()
        file_hash = hash_file(data)
        old_rows = previous.get(rel_path)
        if old_rows and old_rows[0]["file_hash"] == file_hash:
            carried.extend(old_rows)  # unchanged file: no re-parse, no re-embed
            continue
        language = language_for(abs_path)
        source = data.decode("utf-8", errors="replace")
        pending.extend(chunk_file(rel_path, source, file_hash, language))

    unique = [c for c in {c.content_hash: c for c in pending}.values()
              if c.content_hash not in vector_by_hash]
    for chunk, vector in zip(unique, model.embed_texts([c.content for c in unique])):
        vector_by_hash[chunk.content_hash] = vector

    rows = carried + [
        {"chunk_id": uuid.uuid4().hex, "content_hash": c.content_hash,
         "file_hash": c.file_hash, "file_path": c.file_path,
         "start_line": c.start_line, "end_line": c.end_line,
         "symbol_name": c.symbol_name or "", "language": c.language,
         "content": c.content, "vector": vector_by_hash[c.content_hash],
         "model_name": model_name, "model_revision": model_revision}
        for c in pending
    ]
    store.overwrite(slug, rows)
    return len(rows)
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_semantic.py -v` → PASS. If `table.head(1)`/`to_arrow()` API names differ in the installed lancedb version, check `uv run python -c "import lancedb; help(lancedb.table.Table)"` and adjust the store internals — the tests define the contract, keep them as-is.

- [ ] **Step 5: Commit** — `git add src/codeintel/semantic.py tests/test_semantic.py && git commit -m "feat(semantic): lancedb store with file carry-over and model-identity rule"`

---

### Task 6: Query path (`semantic.py`, part 3)

**Files:**
- Modify: `src/codeintel/semantic.py`
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `SemanticStore`, `reciprocal_rank_fusion` (Tasks 4-5); `search_zoekt(base_url, query)` and `ZoektUnavailableError` from `codeintel.search`.
- Produces: `semantic_search(slug: str, query: str, limit: int = 10, *, root: Path | None = None, zoekt_base_url: str | None = None, model: EmbeddingModel | None = None) -> dict` returning `{"query", "results": [<FusedHit as dict, plus repo>], "total"}` and optionally `"warning"`. Raises `NoSemanticIndexError` when no table exists.

- [ ] **Step 1: Write failing tests** (append to `tests/test_semantic.py`)

```python
def _indexed(tmp_path):
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    return tmp_path / "data"


def test_semantic_search_returns_fused_results(tmp_path, lancedb_available, monkeypatch):
    from codeintel import semantic
    data = _indexed(tmp_path)
    monkeypatch.setattr(semantic, "search_zoekt",
                        lambda url, q: [ZoektHit(repo="myrepo", path="mod_0.py",
                                                 line_number=1, line_text="def f_0():")])
    result = semantic.semantic_search("myrepo", "function zero", root=data,
                                      zoekt_base_url="http://x", model=FakeEmbedder())
    assert result["total"] >= 1 and "warning" not in result
    top = result["results"][0]
    assert top["filePath"] == "mod_0.py" and set(top["sources"]) == {"vector", "zoekt"}


def test_missing_table_raises_reindex_hint(tmp_path, lancedb_available):
    from codeintel.semantic import NoSemanticIndexError, semantic_search
    with pytest.raises(NoSemanticIndexError, match="codeintel reindex nosuch"):
        semantic_search("nosuch", "q", root=tmp_path, model=FakeEmbedder())


def test_query_model_mismatch_uses_table_model_and_warns(tmp_path, lancedb_available, monkeypatch):
    from codeintel import semantic
    data = _indexed(tmp_path)  # table identity: fake-model@rev1
    # semantic_search rebuilds a model from the table's identity; patch the
    # class so it never tries to download "fake-model" from HuggingFace.
    monkeypatch.setattr(
        semantic, "EmbeddingModel",
        lambda model_name, revision: FakeEmbedder(name=model_name, revision=revision),
    )
    configured = FakeEmbedder(name="other-model", revision="rev9")
    result = semantic.semantic_search("myrepo", "query", root=data, model=configured)
    assert "reindex" in result["warning"]
    assert configured.embedded == []  # configured model never used for the query


def test_zoekt_down_degrades_to_vector_only(tmp_path, lancedb_available, monkeypatch):
    from codeintel import semantic

    def _boom(url, q):
        raise ZoektUnavailableError("down")

    monkeypatch.setattr(semantic, "search_zoekt", _boom)
    data = _indexed(tmp_path)
    result = semantic.semantic_search("myrepo", "query", root=data,
                                      zoekt_base_url="http://x", model=FakeEmbedder())
    assert result["total"] >= 1
    assert all(h["sources"] == ["vector"] for h in result["results"])
```

Also add the imports the new tests need at the top of the test file: `from codeintel.search import ZoektHit, ZoektUnavailableError`.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_semantic.py -v` → new tests FAIL

- [ ] **Step 3: Implement** (append to `semantic.py`):

```python
def semantic_search(slug: str, query: str, limit: int = 10, *, root: Path | None = None,
                    zoekt_base_url: str | None = None,
                    model: EmbeddingModel | None = None) -> dict:
    store = SemanticStore(config.lancedb_dir(root))
    identity = store.table_identity(slug)
    if identity is None:
        raise NoSemanticIndexError(f"no semantic index for {slug} — run codeintel reindex {slug}")

    model = model or default_model()
    warning: str | None = None
    if model.identity() != identity:
        # Query must use the model the table was built with — never the
        # configured one. Correct results now; the warning nudges a reindex.
        warning = (f"configured embedding model {model.identity()[0]}@{model.identity()[1]} "
                   f"differs from the index's {identity[0]}@{identity[1]}; queried with the "
                   f"index's model — run codeintel reindex {slug} to migrate")
        model = EmbeddingModel(model_name=identity[0], revision=identity[1])

    vector_rows = store.search(slug, model.embed_query(query), VECTOR_TOP_K)

    zoekt_hits: list[ZoektHit] = []
    if zoekt_base_url is not None:
        try:
            zoekt_hits = search_zoekt(zoekt_base_url, f"r:{slug} {query}")[:ZOEKT_TOP_K]
        except ZoektUnavailableError:
            pass  # hybrid degrades to vector-only; sources fields reflect it

    fused = reciprocal_rank_fusion(slug, vector_rows, zoekt_hits)[:limit]
    result = {
        "query": query,
        "results": [
            {"repo": h.repo, "filePath": h.file_path, "startLine": h.start_line,
             "endLine": h.end_line, "symbolName": h.symbol_name or None,
             "content": h.content, "score": h.score, "sources": list(h.sources)}
            for h in fused
        ],
        "total": len(fused),
    }
    if warning:
        result["warning"] = warning
    return result
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_semantic.py -v` → PASS

- [ ] **Step 5: Commit** — `git add src/codeintel/semantic.py tests/test_semantic.py && git commit -m "feat(semantic): hybrid semantic_search with table-model authority and zoekt fallback"`

---

### Task 7: Registry `semantic_indexed_at`

**Files:**
- Modify: `src/codeintel/registry.py`
- Modify: `src/codeintel/index_cli.py:346-347` (`_cmd_status` output)
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `RegisteredRepo.semantic_indexed_at: datetime | None = None` (new last field); `Registry.mark_semantic_indexed(slug: str) -> None`; idempotent `_ensure_semantic_indexed_at_column()` migration mirroring `_ensure_scheme_override_column` (`registry.py:31-46`). `upsert()` must NOT touch the column (ON CONFLICT update list unchanged), so the timestamp survives reindexes whose semantic stage failed.

- [ ] **Step 1: Write failing tests** (append to `tests/test_registry.py`)

```python
def test_mark_semantic_indexed_sets_timestamp(tmp_path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("r", "/p", "python", "sha", "indexed")
    assert registry.get("r").semantic_indexed_at is None
    registry.mark_semantic_indexed("r")
    assert registry.get("r").semantic_indexed_at is not None
    registry.close()


def test_upsert_preserves_semantic_timestamp(tmp_path):
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("r", "/p", "python", "sha", "indexed")
    registry.mark_semantic_indexed("r")
    stamp = registry.get("r").semantic_indexed_at
    registry.upsert("r", "/p", "python", "sha2", "indexed")
    assert registry.get("r").semantic_indexed_at == stamp
    registry.close()


def test_migration_is_idempotent(tmp_path):
    Registry(tmp_path / "registry.db").close()
    Registry(tmp_path / "registry.db").close()  # second open must not raise
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_registry.py -v` → FAIL

- [ ] **Step 3: Implement.** In `registry.py`: add `semantic_indexed_at TEXT` to `_SCHEMA`; add the migration function (copy the `_ensure_scheme_override_column` pattern verbatim with the new column name) and call it in `__init__`; add the field to `RegisteredRepo` (default `None`); extend `_row_to_repo` and every `SELECT` column list with `semantic_indexed_at` (parse with `datetime.fromisoformat` when not None); add:

```python
    def mark_semantic_indexed(self, slug: str) -> None:
        self._conn.execute(
            "UPDATE repos SET semantic_indexed_at = ? WHERE slug = ?",
            (datetime.now(UTC).isoformat(), slug),
        )
        self._conn.commit()
```

In `index_cli.py` `_cmd_status` (line 347) append:

```python
    semantic = repo.semantic_indexed_at.isoformat() if repo.semantic_indexed_at else "-"
    print(f"semantic: {semantic}")
```

- [ ] **Step 4: Verify** — `uv run pytest tests/test_registry.py tests/test_index_cli.py -m "not integration" -v` → PASS

- [ ] **Step 5: Commit** — `git add src/codeintel/registry.py src/codeintel/index_cli.py tests/test_registry.py && git commit -m "feat(semantic): track semantic_indexed_at in the registry"`

---

### Task 8: Pipeline integration (`index_cli.py`)

**Files:**
- Modify: `src/codeintel/index_cli.py` (new `_run_semantic_stage()`; call inside `index_repo()` after the `zoekt-index` `_run` at line 279-283 and before the publish block at line 285; `mark_semantic_indexed` after the final `registry.upsert`; lance cleanup in `_cmd_forget`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `index_semantic(repo_path, slug, root=root)` (Task 5), `SemanticExtraMissingError` (Task 3), `Registry.mark_semantic_indexed` (Task 7), `config.lancedb_dir` (Task 1).
- Produces: `_run_semantic_stage(repo_path: Path, slug: str, root: Path | None) -> bool` — True when the table was rebuilt; never raises. On-disk lance table dir for forget: `config.lancedb_dir() / f"{slug}.lance"`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_index_cli.py`, following its existing monkeypatch style)

```python
def test_semantic_stage_skips_cleanly_when_extra_missing(monkeypatch, capsys):
    import sys
    from codeintel.index_cli import _run_semantic_stage
    monkeypatch.setitem(sys.modules, "codeintel.semantic", None)  # forces ImportError
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    assert "uv sync --extra semantic" in capsys.readouterr().err


def test_semantic_stage_failure_is_nonfatal(monkeypatch, capsys):
    import codeintel.semantic as semantic_module
    from codeintel.index_cli import _run_semantic_stage

    def _boom(*args, **kwargs):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(semantic_module, "index_semantic", _boom)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is False
    assert "still published" in capsys.readouterr().err


def test_semantic_stage_success_returns_true(monkeypatch):
    import codeintel.semantic as semantic_module
    from codeintel.index_cli import _run_semantic_stage
    monkeypatch.setattr(semantic_module, "index_semantic", lambda *a, **k: 5)
    assert _run_semantic_stage(Path("/repo"), "slug", None) is True


def test_forget_removes_lance_table_dir(tmp_path, monkeypatch):
    # Arrange a registered repo + fake lance dir, then forget it.
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    registry = Registry(tmp_path / "registry.db")
    registry.upsert("gone", "/p", "python", None, "indexed")
    registry.close()
    lance_dir = tmp_path / "lancedb" / "gone.lance"
    lance_dir.mkdir(parents=True)
    (lance_dir / "data.bin").write_text("x")
    from codeintel.index_cli import _cmd_forget
    assert _cmd_forget(argparse.Namespace(slug="gone")) == 0
    assert not lance_dir.exists()
```

(The test file already imports `argparse`, `Path`, and `Registry`; add any that are missing.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_index_cli.py -m "not integration" -v` → FAIL

- [ ] **Step 3: Implement.** In `index_cli.py` add:

```python
def _run_semantic_stage(repo_path: Path, slug: str, root: Path | None) -> bool:
    """Chunk + embed + write the LanceDB table. Optional and non-fatal:
    a missing `semantic` extra skips with a hint, any other failure warns
    and lets the SCIP/Zoekt publish proceed — the previous semantic table
    (if any) stays live."""
    try:
        from codeintel import semantic
        from codeintel.embeddings import SemanticExtraMissingError
    except ImportError:
        print("semantic indexing skipped — install with `uv sync --extra semantic`",
              file=sys.stderr)
        return False
    try:
        semantic.index_semantic(repo_path, slug, root=root)
        return True
    except SemanticExtraMissingError as exc:
        print(f"semantic indexing skipped — {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"warning: semantic indexing failed (SCIP/Zoekt index still published): {exc}",
              file=sys.stderr)
        return False
```

In `index_repo()` insert after the `zoekt-index` `_run(...)` call and before `target_dir = config.index_dir(slug, root)`:

```python
            semantic_ok = _run_semantic_stage(repo_path, slug, root)
```

And after the final-status `registry.upsert(...)` (line 295):

```python
        if semantic_ok:
            registry.mark_semantic_indexed(slug)
```

In `_cmd_forget`, after `_remove_zoekt_shards(slug)`:

```python
    shutil.rmtree(config.lancedb_dir() / f"{slug}.lance", ignore_errors=True)
```

Note `semantic.index_semantic` is accessed as an attribute of the module (not `from ... import index_semantic`) so tests can monkeypatch it.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_index_cli.py -m "not integration" -v` → PASS; then full unit suite `uv run pytest -m "not integration"` → PASS

- [ ] **Step 5: Commit** — `git add src/codeintel/index_cli.py tests/test_index_cli.py && git commit -m "feat(semantic): non-fatal semantic stage in index pipeline and forget cleanup"`

---

### Task 9: `semanticSearch` MCP tool (`server.py`)

**Files:**
- Modify: `src/codeintel/server.py` (new tool after `search_code`, line 177; update the module docstring's tool count/list at lines 4-5)
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `semantic_search(slug, query, limit, zoekt_base_url=...)` (Task 6); `_zoekt()` helper (`server.py:34-39`).
- Produces: MCP tool `semanticSearch(repo: str, query: str, limit: int = 10) -> dict`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_server_tools.py`, following its existing pattern for calling tool functions directly)

```python
def test_semantic_search_tool_returns_results(monkeypatch):
    from codeintel import server

    def _fake_search(repo, query, limit, zoekt_base_url=None):
        return {"query": query, "results": [], "total": 0}

    monkeypatch.setattr(server, "_zoekt_base_url_or_none", lambda: "http://x")
    monkeypatch.setattr("codeintel.semantic.semantic_search", _fake_search)
    result = server.semantic_search_tool(repo="r", query="auth logic")
    assert result == {"query": "auth logic", "results": [], "total": 0}


def test_semantic_search_tool_wraps_errors(monkeypatch):
    from codeintel import server

    def _boom(*args, **kwargs):
        raise RuntimeError("no semantic index for r — run codeintel reindex r")

    monkeypatch.setattr(server, "_zoekt_base_url_or_none", lambda: None)
    monkeypatch.setattr("codeintel.semantic.semantic_search", _boom)
    result = server.semantic_search_tool(repo="r", query="q")
    assert "no semantic index" in result["error"]
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_server_tools.py -v` → FAIL

- [ ] **Step 3: Implement.** In `server.py` add after `search_code`:

```python
def _zoekt_base_url_or_none() -> str | None:
    """Hybrid search wants Zoekt but must not require it — a Zoekt spawn
    failure degrades semanticSearch to vector-only rather than erroring."""
    try:
        return _zoekt().ensure_running()
    except Exception:
        return None


@mcp.tool(name="semanticSearch")
def semantic_search_tool(repo: str, query: str, limit: int = 10) -> dict[str, Any]:
    """Natural-language code search over `repo`: embeds `query`, retrieves
    top vector matches from the repo's semantic index, fuses them with
    Zoekt lexical hits via reciprocal rank fusion. Requires the repo to
    have been indexed with the `semantic` extra installed."""
    from codeintel import semantic  # deferred: tool must exist even without the extra

    try:
        return semantic.semantic_search(repo, query, limit,
                                        zoekt_base_url=_zoekt_base_url_or_none())
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return {"error": str(exc)}
```

Update the docstring at the top of `server.py`: "Registers 9 tools: ... searchCode, semanticSearch, blastRadius."

- [ ] **Step 4: Verify** — `uv run pytest tests/test_server_tools.py -v` → PASS

- [ ] **Step 5: Commit** — `git add src/codeintel/server.py tests/test_server_tools.py && git commit -m "feat(semantic): semanticSearch MCP tool"`

---

### Task 10: Integration test + docs

**Files:**
- Modify: `tests/test_index_cli.py` (one integration test)
- Modify: `CLAUDE.md` (architecture section), `README.md` (feature mention + extra install line)

**Interfaces:** consumes everything above; produces nothing new.

- [ ] **Step 1: Write the integration test** (append to `tests/test_index_cli.py`; model the setup on the existing integration tests' fixture usage — same fixture repo, same env handling)

```python
@pytest.mark.integration
def test_index_repo_builds_semantic_index_and_searches(tmp_path, monkeypatch):
    pytest.importorskip("lancedb")
    pytest.importorskip("tree_sitter_language_pack")
    pytest.importorskip("sentence_transformers")
    # A small real model keeps this test minutes-not-hours; the pipeline
    # under test is identical to the default model's.
    monkeypatch.setenv("CODEINTEL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    import codeintel.embeddings as embeddings_module
    monkeypatch.setattr(embeddings_module, "_default", None)  # reset singleton

    fixture = Path(__file__).parent / "fixtures" / "python-repo"  # match existing tests' fixture path
    slug = index_repo(fixture, slug="semfix", root=tmp_path)

    from codeintel.semantic import semantic_search
    result = semantic_search(slug, "function definition", root=tmp_path)
    assert result["total"] >= 1
    assert all("filePath" in r for r in result["results"])
```

Before writing, open the existing `@pytest.mark.integration` tests in `test_index_cli.py` and reuse their exact fixture-repo path and any git-init helper they use — the fixture must be a git repo for `_git_head`.

- [ ] **Step 2: Run it** — `uv run pytest tests/test_index_cli.py -m integration -k semantic -v` → PASS (first run downloads ~80MB model). Also run the full unit suite once more: `uv run pytest -m "not integration"` → PASS.

- [ ] **Step 3: Update docs.**
  - `CLAUDE.md`: in Architecture, add a fourth engine bullet — Semantic (`chunker.py` + `embeddings.py` + `semantic.py`): tree-sitter chunking → sentence-transformers embeddings → per-repo LanceDB table under `~/.codeintel/lancedb/`; hybrid `semanticSearch` fuses vector + Zoekt hits via RRF; optional `semantic` extra, stage is non-fatal in `codeintel index`; a table only holds vectors from one model+revision. Add `uv sync --extra semantic` to the Commands block.
  - `README.md`: add `semanticSearch` to the tool list and the extra-install line.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "test(semantic): end-to-end integration test; document semantic search"`

---

## Verification (whole feature)

- [ ] `uv run pytest -m "not integration"` — green without the extra installed (check with a scratch `uv sync` without extras if convenient, otherwise rely on the importorskip guards).
- [ ] `uv run pytest` (with extras + binaries) — green including integration.
- [ ] Manual smoke: `uv run codeintel index <some-python-repo>` prints no semantic errors; `codeintel status <slug>` shows `semantic: <timestamp>`; a `semanticSearch` call through the MCP server returns fused results.
