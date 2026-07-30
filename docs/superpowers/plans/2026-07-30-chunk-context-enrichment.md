# Chunk Context Enrichment and Model-Aware Prefixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every semantic chunk file-path and class context before embedding, apply the correct query/document prefix for whichever embedding model is configured, cap file size, honor `.gitignore`, and report chunk-size distribution.

**Architecture:** A context header (`# file: <path>` plus `# in class: <Parent>` when applicable) is baked into `Chunk.content` by a **final pass** in `chunk_file()` — after splitting and merging, because `_merge_small` concatenates contents and an earlier-attached header would appear twice in a merged chunk. Splitting reserves `HEADER_RESERVE_TOKENS` so headers cannot overflow `MAX_TOKENS`. Model prefixes are applied only at encode time and never stored. A `content_format` version joins the table identity so unchanged files cannot carry header-less rows forward forever.

**Tech Stack:** Python 3.12, stdlib `sqlite3` and `subprocess`, frozen dataclasses, pytest, sentence-transformers (optional `semantic` extra), LanceDB.

**Spec:** `docs/superpowers/specs/2026-07-30-chunk-context-enrichment-design.md`

## Global Constraints

- Python 3.12 modern type-hint syntax throughout: `str | None`, `list[T]`, `tuple[str, ...]` — never `Optional[...]` or `List[...]`.
- Result types are **frozen dataclasses** (`@dataclass(frozen=True)`), never Pydantic. Use tuples, not lists, for their collection fields.
- Test files mirror source modules 1:1 — `test_chunker.py` ↔ `chunker.py`, etc. Never create a new test file for an existing module.
- `semantic.py` and `embeddings.py` must never print to stdout/stderr — both are imported by the MCP stdio server. Warnings are returned as values; only `index_cli.py` prints.
- Never mutate a published `index-<sha>.db`. This plan does not touch SCIP indexes.
- Unit tests must not download models or require network. Integration tests are marked `@pytest.mark.integration`.
- Commit messages: conventional commits, no AI references.
- Run unit tests with `uv run pytest -m "not integration"`.

## Task Dependency Order

```
Task 1 (headers, chunker.py) ─┐
Task 2 (size cap + gitignore) ─┤
Task 3 (model prefixes) ───────┴─→ Task 4 (TableIdentity) → Task 5 (TokenStats + CLI) → Task 6 (verify)
```

Tasks 1, 2, and 3 are independent and may be done in any order. Task 4 consumes `CONTENT_FORMAT` from Task 1 and the prefix API from Task 3.

---

### Task 1: Context headers in `chunker.py`

**Files:**
- Modify: `src/codeintel/chunker.py` (constants at lines 25-28; `Chunk` at 71-80; `_make_chunk` at 132; `_split_class` at 229-255; `_merge_small` at 258-274; delete `_collect_imports` at 277-293; `chunk_file` at 296-321; `_window_lines` line 132 sizing)
- Test: `tests/test_chunker.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `Chunk.parent_name: str | None` — enclosing class name, `None` otherwise.
  - `CONTENT_FORMAT: int = 1` — module constant; Task 4 imports it.
  - `HEADER_RESERVE_TOKENS: int = 32`.
  - Every chunk's `content` now begins with `# file: <rel_path>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_chunker.py`:

```python
def test_every_chunk_starts_with_file_header():
    source = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
    chunks = chunk_file("pkg/mod.py", source, "fh", "python")
    assert chunks
    for chunk in chunks:
        assert chunk.content.startswith("# file: pkg/mod.py\n")


def test_top_level_def_header_omits_redundant_symbol_line():
    """A top-level def's own name is already the first line of its code —
    the header carries the path only."""
    source = "def alpha():\n    return 1\n"
    chunk = chunk_file("pkg/mod.py", source, "fh", "python")[0]
    assert chunk.content.startswith("# file: pkg/mod.py\n\ndef alpha():")
    assert "# in class:" not in chunk.content


def test_split_method_header_carries_parent_class():
    body = "\n".join(
        f"    def method_{i}(self):\n        return {i}  # " + "pad " * 120
        for i in range(8)
    )
    source = f"class Big:\n{body}\n"
    chunks = chunk_file("big.py", source, "fh", "python")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.startswith("# file: big.py\n# in class: Big\n\n")
        assert chunk.parent_name == "Big"


def test_fixed_window_fallback_chunks_get_the_file_header():
    """An unparseable file falls back to fixed windows — those chunks have no
    symbol and no parent, so they carry the file line alone."""
    source = "x = 1\n" * 400
    chunks = chunk_file("data.py", source, "fh", "python")
    assert chunks
    for chunk in chunks:
        assert chunk.content.startswith("# file: data.py\n\n")
        assert chunk.symbol_name is None and chunk.parent_name is None


def test_header_not_duplicated_after_merge():
    source = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    for chunk in chunk_file("m.py", source, "fh", "python"):
        assert chunk.content.count("# file: m.py") == 1


def test_chunks_with_different_parents_do_not_merge():
    """A class's last method sits adjacent to the next top-level function;
    merging them would stamp `# in class: Big` on code outside Big."""
    body = "\n".join(
        f"    def method_{i}(self):\n        return {i}  # " + "pad " * 120
        for i in range(8)
    )
    source = f"class Big:\n{body}\n\n\ndef loose():\n    return 0\n"
    chunks = chunk_file("mix.py", source, "fh", "python")
    for chunk in chunks:
        if chunk.parent_name == "Big":
            assert "def loose" not in chunk.content


def test_no_chunk_exceeds_max_tokens_including_header():
    """The HEADER_RESERVE_TOKENS allowance must keep header+body under cap."""
    body = "\n".join(
        f"    def method_{i}(self):\n        return {i}  # " + "pad " * 200
        for i in range(6)
    )
    source = f"class Wide:\n{body}\n"
    for chunk in chunk_file("wide.py", source, "fh", "python"):
        assert len(chunk.content) // 4 <= MAX_TOKENS


def test_imports_are_no_longer_prepended():
    body = "\n".join(
        f"    def method_{i}(self):\n        return {i}  # " + "pad " * 120
        for i in range(8)
    )
    source = f"import os\nfrom sys import path\n\n\nclass Big:\n{body}\n"
    for chunk in chunk_file("big.py", source, "fh", "python"):
        assert "import os" not in chunk.content
```

Add `MAX_TOKENS` to the existing import from `codeintel.chunker` at line 7 if not already present (it is).

- [ ] **Step 2: Rewrite the two tests that assert the old imports header**

Replace `test_oversized_class_splits_into_methods_with_imports_and_class_header` (line 37) with:

```python
def test_oversized_class_splits_into_methods_with_class_header():
    body = "\n".join(
        f"    def method_{i}(self):\n        return {i}  # " + "pad " * 120
        for i in range(8)
    )
    source = f"import os\nfrom sys import path\n\n\nclass Big:\n{body}\n"
    chunks = chunk_file("big.py", source, "fh", "python")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.startswith("# file: big.py\n# in class: Big\n")
        assert chunk.symbol_name.startswith("method_")
```

Replace `test_kotlin_oversized_class_splits_into_methods_with_symbol_names` (line 174) with:

```python
def test_kotlin_oversized_class_splits_into_methods_with_symbol_names():
    body = "\n".join(
        f"    fun method_{i}(): Int {{\n        return {i}  // " + "pad " * 120 + "\n    }"
        for i in range(8)
    )
    source = f"import kotlin.text.Regex\n\nclass Big {{\n{body}\n}}\n"
    chunks = chunk_file("big.kt", source, "fh", "kotlin")
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.startswith("# file: big.kt\n# in class: Big\n")
        assert chunk.symbol_name.startswith("method_")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: FAIL — new tests fail on missing `# file:` prefix; rewritten tests fail because content still starts with `import os`.

- [ ] **Step 4: Add constants and the `parent_name` field**

In `src/codeintel/chunker.py`, replace lines 25-28:

```python
MAX_TOKENS = 512
MIN_TOKENS = 256
OVERLAP_TOKENS = 50

# Every chunk gets a context header appended by _apply_headers(). Splitting
# happens before that header exists, so the sizing paths reserve this
# allowance -- otherwise a chunk sized exactly at MAX_TOKENS would overflow
# the cap the moment its header lands.
HEADER_RESERVE_TOKENS = 32
_EFFECTIVE_MAX_TOKENS = MAX_TOKENS - HEADER_RESERVE_TOKENS

# Shape of the text stored in Chunk.content. Bump when that shape changes:
# semantic.py folds this into the table identity, so a bump invalidates every
# stored vector and forces a full re-chunk. Needed because unchanged files are
# matched by file_hash, not content_hash -- without a version, a file whose
# bytes never changed would carry its old-format rows forward forever.
CONTENT_FORMAT = 1
```

(`MAX_IMPORT_LINES` is deleted — it has no remaining callers after Step 7.)

Add the field to `Chunk` (after `language`, line 80):

```python
    parent_name: str | None = None   # enclosing class, for the context header
```

- [ ] **Step 5: Thread `parent_name` through `_make_chunk`**

Replace `_make_chunk` (lines 132-136):

```python
def _make_chunk(rel_path: str, language: str, file_hash: str, content: str,
                start_line: int, end_line: int, symbol: str | None,
                parent: str | None = None) -> Chunk:
    return Chunk(file_path=rel_path, start_line=start_line, end_line=end_line,
                 content=content, symbol_name=symbol,
                 content_hash=hashlib.sha256(content.encode()).hexdigest(),
                 file_hash=file_hash, language=language, parent_name=parent)
```

- [ ] **Step 6: Add the header builder and the final pass**

Add immediately after `_make_chunk`:

```python
def _chunk_header(rel_path: str, chunk: Chunk) -> str:
    """Path plus enclosing class. The chunk's own symbol name is deliberately
    omitted -- it is already the first line of the chunk's code, so repeating
    it spends budget without adding a matchable term. The parent class is a
    different case: a method split out of an oversized class no longer
    contains its class name anywhere."""
    lines = [f"# file: {rel_path}"]
    if chunk.parent_name:
        lines.append(f"# in class: {chunk.parent_name}")
    return "\n".join(lines)


def _apply_headers(chunks: list[Chunk], rel_path: str) -> list[Chunk]:
    """Attach the context header to every chunk, as the last step of
    chunk_file(). Deliberately a final pass rather than done at construction:
    _merge_small concatenates chunk contents, so a header attached earlier
    would appear twice inside a merged chunk."""
    return [
        _make_chunk(rel_path, c.language, c.file_hash,
                    f"{_chunk_header(rel_path, c)}\n\n{c.content}",
                    c.start_line, c.end_line, c.symbol_name, c.parent_name)
        for c in chunks
    ]
```

- [ ] **Step 7: Drop the imports header from `_split_class` and set `parent_name`**

Replace `_split_class` (lines 229-255):

```python
def _split_class(node, source: str, rel_path: str, language: str,
                 file_hash: str) -> list[Chunk]:
    """Methods of an oversized class become their own chunks, tagged with the
    class name so _apply_headers can re-add that context later."""
    class_name = _node_name(node)
    methods = [n for n in _walk(node)
               if n.type in _DEF_NODE_TYPES[language] and n.type not in _CLASS_NODE_TYPES]
    if not methods:
        text = source[node.start_byte:node.end_byte]
        return [_make_chunk(rel_path, language, file_hash, text,
                            node.start_point[0] + 1, node.end_point[0] + 1, class_name)]
    chunks: list[Chunk] = []
    for m in methods:
        symbol = _node_name(m)
        text = source[m.start_byte:m.end_byte]
        if _tokens(text) > _EFFECTIVE_MAX_TOKENS:
            # A single method can itself exceed the cap -- window it the same
            # way an oversized top-level def would be.
            base_line = m.start_point[0] + 1
            chunks.extend(
                _make_chunk(rel_path, language, file_hash, piece, start, end,
                            symbol, class_name)
                for piece, start, end in _window_lines(text.splitlines(), base_line)
            )
        else:
            chunks.append(_make_chunk(rel_path, language, file_hash, text,
                                      m.start_point[0] + 1, m.end_point[0] + 1,
                                      symbol, class_name))
    return chunks
```

- [ ] **Step 8: Guard `_merge_small` against merging across parents**

Replace `_merge_small` (lines 258-274):

```python
def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    merged: list[Chunk] = []
    for chunk in chunks:
        if (merged and _tokens(merged[-1].content) < MIN_TOKENS
                and merged[-1].end_line < chunk.start_line
                # Never merge across a parent boundary: a class's last method
                # is adjacent to the next top-level def, and merging them would
                # stamp "# in class: X" onto code that is not in X.
                and merged[-1].parent_name == chunk.parent_name
                # MAX_TOKENS is a hard cap; MIN_TOKENS is only a soft
                # preference -- never merge past the hard cap just to grow
                # a too-small chunk.
                and _tokens(merged[-1].content) + _tokens(chunk.content)
                <= _EFFECTIVE_MAX_TOKENS):
            prev = merged.pop()
            merged.append(_make_chunk(chunk.file_path, chunk.language, chunk.file_hash,
                                      f"{prev.content}\n\n{chunk.content}",
                                      prev.start_line, chunk.end_line,
                                      prev.symbol_name or chunk.symbol_name,
                                      prev.parent_name))
        else:
            merged.append(chunk)
    return merged
```

- [ ] **Step 9: Delete the now-dead imports machinery**

Delete `_collect_imports` entirely (lines 277-293) and delete the `_IMPORT_NODE_TYPES` dict (lines 61-68). Both lose their only caller in the next step.

- [ ] **Step 10: Wire the reserve and the final pass into `chunk_file`**

Replace `chunk_file` (lines 296-321):

```python
def chunk_file(rel_path: str, source: str, file_hash: str, language: str) -> list[Chunk]:
    try:
        from tree_sitter_language_pack import get_parser
        tree = get_parser(language).parse(source.encode("utf-8"))
    except Exception:
        return _apply_headers(_fixed_windows(rel_path, language, file_hash, source), rel_path)

    root = tree.root_node
    defs = [n for n in root.children if n.type in _DEF_NODE_TYPES.get(language, set())]
    if not defs:
        return _apply_headers(_fixed_windows(rel_path, language, file_hash, source), rel_path)

    chunks: list[Chunk] = []
    for node in defs:
        text = source[node.start_byte:node.end_byte]
        target = node.children[-1] if node.type == "decorated_definition" else node
        if target.type in _CLASS_NODE_TYPES and _tokens(text) > _EFFECTIVE_MAX_TOKENS:
            chunks.extend(_split_class(target, source, rel_path, language, file_hash))
        elif _tokens(text) > _EFFECTIVE_MAX_TOKENS:
            chunks.extend(_split_oversized_def(node, text, rel_path, language, file_hash))
        else:
            chunks.append(_make_chunk(rel_path, language, file_hash, text,
                                      node.start_point[0] + 1, node.end_point[0] + 1,
                                      _node_name(node)))
    return _apply_headers(_merge_small(chunks), rel_path)
```

- [ ] **Step 11: Size the fallback windows against the reserve**

In `_window_lines`, change the first line of the body (line 132) from
`window_chars, overlap_chars = MAX_TOKENS * 4, OVERLAP_TOKENS * 4` to:

```python
    window_chars, overlap_chars = _EFFECTIVE_MAX_TOKENS * 4, OVERLAP_TOKENS * 4
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: PASS — all new tests, both rewritten tests, and every pre-existing test.

- [ ] **Step 13: Commit**

```bash
git add src/codeintel/chunker.py tests/test_chunker.py
git commit -m "feat(semantic): add file and class context headers to every chunk"
```

---

### Task 2: File-size cap and `.gitignore` filtering

**Files:**
- Modify: `src/codeintel/chunker.py` (add predicates after `skip_reason`, ends line 119; `iter_source_files` at 122-129)
- Modify: `src/codeintel/semantic.py` (loop at lines 184-202)
- Test: `tests/test_chunker.py`, `tests/test_semantic.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `oversized_file_reason(size_bytes: int) -> str | None` — `"too-large:<bytes>"` past 1 MB.
  - `gitignored(repo_path: Path, rel_paths: list[str]) -> set[str]`.
  - `MAX_FILE_BYTES: int = 1_048_576`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_chunker.py`:

```python
def test_oversized_file_reason_threshold():
    from codeintel.chunker import MAX_FILE_BYTES, oversized_file_reason
    assert oversized_file_reason(MAX_FILE_BYTES) is None
    reason = oversized_file_reason(MAX_FILE_BYTES + 1)
    assert reason is not None and reason.startswith("too-large:")


def test_gitignored_returns_empty_set_for_non_git_dir(tmp_path):
    """The whole unit-test suite indexes plain tmp_path dirs, not git repos."""
    from codeintel.chunker import gitignored
    assert gitignored(tmp_path, ["a.py", "b.py"]) == set()


@pytest.mark.integration
def test_gitignored_reads_real_gitignore(tmp_path):
    import subprocess
    from codeintel.chunker import gitignored
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n")
    assert gitignored(tmp_path, ["build/out.py", "src/keep.py"]) == {"build/out.py"}


@pytest.mark.integration
def test_gitignored_handles_no_matches(tmp_path):
    """git check-ignore exits 1 when nothing matches -- that is NOT an error."""
    import subprocess
    from codeintel.chunker import gitignored
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n")
    assert gitignored(tmp_path, ["src/keep.py"]) == set()
```

Add to `tests/test_semantic.py`:

```python
def test_oversized_file_is_skipped_with_reason(tmp_path, lancedb_available, monkeypatch):
    from codeintel import chunker
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(chunker, "MAX_FILE_BYTES", 10)
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert report.rows == 0
    assert all(s.reason.startswith("too-large:") for s in report.skipped)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_chunker.py -k "oversized_file or gitignored" tests/test_semantic.py -k oversized_file -v`
Expected: FAIL — `ImportError: cannot import name 'oversized_file_reason'`

- [ ] **Step 3: Add the two predicates**

In `src/codeintel/chunker.py`, add `import subprocess` to the imports at the top, add the constant beside the other admission constants (after `MAX_LINE_CHARS = 5000`, line 43):

```python
MAX_FILE_BYTES = 1_048_576   # 1 MB backstop for files that dodge the other rules
```

and add after `skip_reason` (ends line 119):

```python
def oversized_file_reason(size_bytes: int) -> str | None:
    """Checked from the file's stat() before read_bytes(), so a huge file is
    never read into memory just to be rejected. A pure backstop: banner and
    long-line detection already catch generated and minified content, leaving
    only the large-file-with-normal-lines shape for this rule."""
    if size_bytes > MAX_FILE_BYTES:
        return f"too-large:{size_bytes}"
    return None


def gitignored(repo_path: Path, rel_paths: list[str]) -> set[str]:
    """Which of `rel_paths` git ignores, via one batched subprocess.

    `git check-ignore` does not use the usual exit-code convention: 0 means
    some paths matched, 1 means none matched, and anything else is a real
    error. Treating non-zero as failure would silently disable filtering on
    every repo that happens to ignore nothing. Any genuine failure -- not a
    git repo, git absent, a hang -- degrades to "nothing ignored", so the
    worst case is indexing more, never less."""
    if not rel_paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "check-ignore", "--stdin"],
            input="\n".join(rel_paths), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    return {line for line in result.stdout.splitlines() if line}
```

- [ ] **Step 4: Filter ignored paths in `iter_source_files`**

Replace `iter_source_files` (lines 122-129):

```python
def iter_source_files(repo_path: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(repo_path.rglob("*")):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in LANGUAGES:
            files.append((path, path.relative_to(repo_path).as_posix()))
    ignored = gitignored(repo_path, [rel for _, rel in files])
    return [(path, rel) for path, rel in files if rel not in ignored]
```

- [ ] **Step 5: Check size before reading, in `index_semantic`**

In `src/codeintel/semantic.py`, extend the chunker import (line 15) to include `oversized_file_reason`, then replace the first three lines of the loop body (lines 185-186):

```python
        reason = oversized_file_reason(abs_path.stat().st_size)
        if reason is not None:
            skipped.append(SkippedFile(rel_path, reason))
            continue
        data = abs_path.read_bytes()
        source = data.decode("utf-8", errors="replace")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_chunker.py tests/test_semantic.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/codeintel/chunker.py src/codeintel/semantic.py tests/test_chunker.py tests/test_semantic.py
git commit -m "feat(semantic): cap file size and honor .gitignore"
```

---

### Task 3: Model-aware prefixes in `embeddings.py`

**Files:**
- Modify: `src/codeintel/embeddings.py` (constants near line 21; `__init__` at 30-39; `embed_texts` at 56-64; `count_oversized` at 66-83; `embed_query` at 85-88)
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `EmbeddingModel.prefixes() -> tuple[str, str]` — `(query_prefix, doc_prefix)`.
  - `EmbeddingModel.prefix_warning() -> str | None`.
  - Env vars `CODEINTEL_EMBEDDING_QUERY_PREFIX`, `CODEINTEL_EMBEDDING_DOC_PREFIX`.

**The trap in this task:** `embed_query` currently delegates to `embed_texts`. Once `embed_texts` prepends the *document* prefix, that delegation would silently stamp queries with the document prefix — the precise bug this task exists to prevent. The fix is a private `_encode()` that both public methods call after applying their own prefix.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_embeddings.py`:

```python
@pytest.mark.parametrize("model_name,expected", [
    ("BAAI/bge-m3", ("", "")),
    ("intfloat/multilingual-e5-large", ("query: ", "passage: ")),
    ("nomic-ai/nomic-embed-text-v1.5", ("search_query: ", "search_document: ")),
])
def test_prefixes_resolve_from_model_map(model_name, expected):
    from codeintel.embeddings import EmbeddingModel
    assert EmbeddingModel(model_name=model_name).prefixes() == expected


def test_env_override_beats_the_map(monkeypatch):
    from codeintel.embeddings import EmbeddingModel
    monkeypatch.setenv("CODEINTEL_EMBEDDING_QUERY_PREFIX", "Q> ")
    monkeypatch.setenv("CODEINTEL_EMBEDDING_DOC_PREFIX", "D> ")
    assert EmbeddingModel(model_name="intfloat/multilingual-e5-large").prefixes() == ("Q> ", "D> ")


def test_unlisted_model_warns_and_uses_no_prefix():
    from codeintel.embeddings import EmbeddingModel
    model = EmbeddingModel(model_name="some-vendor/unknown-model")
    assert model.prefixes() == ("", "")
    warning = model.prefix_warning()
    assert warning is not None and "CODEINTEL_EMBEDDING_QUERY_PREFIX" in warning


def test_listed_model_produces_no_warning():
    from codeintel.embeddings import EmbeddingModel
    assert EmbeddingModel(model_name="BAAI/bge-m3").prefix_warning() is None


def test_query_gets_query_prefix_not_doc_prefix(monkeypatch):
    """embed_query must not inherit the document prefix by delegating to
    embed_texts -- that would be the exact bug this feature prevents."""
    from codeintel.embeddings import EmbeddingModel
    seen: list[str] = []

    class _FakeModel:
        def encode(self, texts, normalize_embeddings=True):
            seen.extend(texts)
            return [[0.0, 1.0] for _ in texts]

    model = EmbeddingModel(model_name="intfloat/multilingual-e5-large")
    monkeypatch.setattr(model, "_load", lambda: _FakeModel())
    model.embed_query("auth flow")
    model.embed_texts(["def f(): pass"])
    assert seen == ["query: auth flow", "passage: def f(): pass"]


def test_explicit_prefixes_win_over_env_and_map(monkeypatch):
    """Task 4 restores a table's prefixes this way — it must beat both."""
    from codeintel.embeddings import EmbeddingModel
    monkeypatch.setenv("CODEINTEL_EMBEDDING_QUERY_PREFIX", "ENV> ")
    model = EmbeddingModel(model_name="intfloat/multilingual-e5-large",
                           query_prefix="TABLE> ", doc_prefix="TDOC> ")
    assert model.prefixes() == ("TABLE> ", "TDOC> ")
    assert model.prefix_warning() is None


def test_count_oversized_measures_the_prefixed_text(monkeypatch):
    """The doc prefix is part of what reaches the encoder, so it counts
    toward the sequence length the model will truncate at."""
    from codeintel import embeddings
    from codeintel.embeddings import EmbeddingModel

    class _FakeTokenizer:
        def __call__(self, texts):
            return {"input_ids": [list(range(len(t))) for t in texts]}

    class _FakeModel:
        tokenizer = _FakeTokenizer()

    model = EmbeddingModel(model_name="x", doc_prefix="P" * 20)
    monkeypatch.setattr(model, "_load", lambda: _FakeModel())
    # Body alone is under the cap; body + 20-char prefix goes over it.
    body = "a" * (embeddings.MAX_SEQ_LENGTH - 10)
    assert model.count_oversized([body]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_embeddings.py -k "prefix" -v`
Expected: FAIL with `AttributeError: 'EmbeddingModel' object has no attribute 'prefixes'`

- [ ] **Step 3: Add the prefix map**

In `src/codeintel/embeddings.py`, after `MAX_SEQ_LENGTH = 1024` (line 21):

```python
# Query/document instruction prefixes, by model. Matching is substring-based
# so vendor-prefixed names resolve ("intfloat/multilingual-e5-large" matches
# "e5"), and longest-pattern-first so the result is deterministic when two
# patterns both match. A future model whose name coincidentally contains a
# pattern would get the wrong prefix -- the env override exists for that case.
MODEL_PREFIXES: dict[str, tuple[str, str]] = {
    "bge-m3": ("", ""),
    "e5": ("query: ", "passage: "),
    "nomic-embed": ("search_query: ", "search_document: "),
}
```

- [ ] **Step 4: Resolve prefixes in `__init__`**

Change the signature (line 30-31) to accept explicit prefixes — Task 4 needs
them to restore a table's identity without reaching into private attributes:

```python
    def __init__(self, model_name: str | None = None, revision: str | None = None,
                 batch_size: int | None = None, query_prefix: str | None = None,
                 doc_prefix: str | None = None) -> None:
```

Append to `EmbeddingModel.__init__`, after `self._model = None` (line 39):

```python
        env_query = os.environ.get("CODEINTEL_EMBEDDING_QUERY_PREFIX")
        env_doc = os.environ.get("CODEINTEL_EMBEDDING_DOC_PREFIX")
        matched = next(
            (MODEL_PREFIXES[p] for p in sorted(MODEL_PREFIXES, key=len, reverse=True)
             if p in self.model_name.lower()),
            None,
        )
        # Explicit args win (restoring a table's identity), then env vars, then
        # the model map. Each side resolves independently -- some models use
        # asymmetric prefixes, so setting only one env var is legal.
        base_query, base_doc = matched if matched is not None else ("", "")
        self._query_prefix = next(
            p for p in (query_prefix, env_query, base_query) if p is not None)
        self._doc_prefix = next(
            p for p in (doc_prefix, env_doc, base_doc) if p is not None)
        # Not a config problem when prefixes were passed explicitly.
        self._unlisted = (matched is None and env_query is None and env_doc is None
                          and query_prefix is None and doc_prefix is None)
```

- [ ] **Step 5: Add the accessors**

Add after `identity()` (line 42):

```python
    def prefixes(self) -> tuple[str, str]:
        """(query_prefix, doc_prefix). Env override wins, else the model map,
        else no prefix."""
        return (self._query_prefix, self._doc_prefix)

    def prefix_warning(self) -> str | None:
        """Message when the model is unlisted and no override is set, naming
        the env var to fix it. This module never prints -- semantic.py is
        imported by the MCP stdio server, and a per-query print would repeat
        on every call. Callers decide where to surface this."""
        if not self._unlisted:
            return None
        return (f"embedding model {self.model_name} is not in the known-prefix map; "
                "no query/document prefix will be applied. If this model needs one, "
                "set CODEINTEL_EMBEDDING_QUERY_PREFIX and CODEINTEL_EMBEDDING_DOC_PREFIX.")
```

- [ ] **Step 6: Split encoding from prefixing**

Replace `embed_texts` (lines 56-64) with a private encoder plus a prefixing wrapper:

```python
    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            # normalize_embeddings: L2-normalize so cosine ranking at query
            # time is exact (standard hubness mitigation).
            for vec in model.encode(texts[i:i + self.batch_size], normalize_embeddings=True):
                vectors.append(list(vec) if not hasattr(vec, "tolist") else vec.tolist())
        return vectors

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._encode([f"{self._doc_prefix}{text}" for text in texts])
```

Replace `embed_query` (lines 85-88):

```python
    def embed_query(self, query: str) -> list[float]:
        # Deliberately NOT delegating to embed_texts: that would apply the
        # document prefix to a query.
        return self._encode([f"{self._query_prefix}{query}"])[0]
```

- [ ] **Step 7: Measure truncation on the prefixed text**

In `count_oversized`, replace the tokenizer line inside the loop (line 81):

```python
            batch = [f"{self._doc_prefix}{text}" for text in texts[i:i + self.batch_size]]
            encoded = tokenizer(batch)["input_ids"]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_embeddings.py -v`
Expected: PASS. If `test_embed_query_encodes_raw_text` (line 53) now fails, update its expectation to `"BAAI/bge-m3"`'s empty prefix — the default model adds no prefix, so raw text is still correct for it.

- [ ] **Step 9: Commit**

```bash
git add src/codeintel/embeddings.py tests/test_embeddings.py
git commit -m "feat(semantic): apply model-aware query and document prefixes"
```

---

### Task 4: `TableIdentity` and `content_format` in `semantic.py`

**Files:**
- Modify: `src/codeintel/semantic.py` (imports line 15-16; dataclasses near line 83; `table_identity` at 126-133; `index_semantic` at 166-228; `semantic_search` at 231-247)
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `CONTENT_FORMAT` from Task 1; `prefixes()` and `prefix_warning()` from Task 3.
- Produces: `TableIdentity` frozen dataclass with fields `model_name`, `model_revision`, `query_prefix`, `doc_prefix`, `content_format`.

- [ ] **Step 1: Write the failing tests**

Add `prefixes` and `prefix_warning` to the existing `FakeEmbedder` class in `tests/test_semantic.py` so the double matches the real protocol:

```python
    def prefixes(self):
        return ("", "")

    def prefix_warning(self):
        return None
```

Then add these tests:

```python
def test_table_identity_round_trips(tmp_path, lancedb_available):
    from codeintel.chunker import CONTENT_FORMAT
    from codeintel.semantic import SemanticStore, TableIdentity, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    identity = SemanticStore((tmp_path / "data") / "lancedb").table_identity("myrepo")
    assert identity == TableIdentity("fake-model", "rev1", "", "", CONTENT_FORMAT)


def test_stale_rows_not_carried_after_content_format_bump(tmp_path, lancedb_available, monkeypatch):
    """Unchanged files are matched by file_hash, not content_hash -- so without
    a content_format in the identity, a file whose bytes never changed would
    carry its old-format rows forward forever."""
    from codeintel import semantic as semantic_module
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(semantic_module, "CONTENT_FORMAT", 0)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    store = SemanticStore((tmp_path / "data") / "lancedb")
    assert all(r["content_format"] == 0 for rows in store.rows_by_path("myrepo").values()
               for r in rows)

    monkeypatch.undo()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    rows = store.rows_by_path("myrepo")
    assert rows and all(r["content_format"] != 0 for rs in rows.values() for r in rs)


def test_old_table_without_new_columns_forces_rebuild(tmp_path, lancedb_available):
    """An index written before these columns existed must read cleanly and
    trigger a full rebuild rather than raising KeyError."""
    import lancedb
    from codeintel.semantic import SemanticStore
    db_dir = (tmp_path / "data") / "lancedb"
    db_dir.mkdir(parents=True)
    lancedb.connect(str(db_dir)).create_table("myrepo", data=[{
        "chunk_id": "x", "content_hash": "h", "file_hash": "fh", "file_path": "a.py",
        "start_line": 1, "end_line": 2, "symbol_name": "", "language": "python",
        "content": "def a(): pass", "vector": [0.0, 1.0, 0.0],
        "model_name": "fake-model", "model_revision": "rev1",
    }], mode="overwrite")
    identity = SemanticStore(db_dir).table_identity("myrepo")
    assert identity is not None and identity.content_format == 0


def test_query_uses_the_tables_prefixes_not_the_configured_ones(tmp_path, lancedb_available, monkeypatch):
    """A prefix mismatch must rebuild the query model from the TABLE's
    identity. EmbeddingModel is patched because the real one would try to
    download 'fake-model' from HuggingFace on reconstruction."""
    from codeintel import semantic as semantic_module
    from codeintel.semantic import index_semantic, semantic_search
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    monkeypatch.setattr(semantic_module, "search_zoekt", lambda *a, **k: [])

    rebuilt: list[tuple] = []

    def _fake_ctor(model_name=None, revision=None, query_prefix=None, doc_prefix=None, **kw):
        rebuilt.append((model_name, revision, query_prefix, doc_prefix))
        return FakeEmbedder(name=model_name, revision=revision)

    monkeypatch.setattr(semantic_module, "EmbeddingModel", _fake_ctor)

    class _PrefixedEmbedder(FakeEmbedder):
        def prefixes(self):
            return ("WRONG: ", "WRONG: ")

    result = semantic_search("myrepo", "auth", root=tmp_path / "data",
                             model=_PrefixedEmbedder())
    assert "warning" in result and "prefix" in result["warning"].lower()
    # Rebuilt with the table's empty prefixes, not the configured "WRONG: ".
    assert rebuilt == [("fake-model", "rev1", "", "")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: FAIL — `ImportError: cannot import name 'TableIdentity'`

- [ ] **Step 3: Add the dataclass and imports**

In `src/codeintel/semantic.py`, extend the chunker import (line 15) to include `CONTENT_FORMAT`, and add after the `SkippedFile` dataclass:

```python
@dataclass(frozen=True)
class TableIdentity:
    """Everything that must match for stored vectors to be reusable. Extends
    the model-identity rule with the prefixes actually applied at encode time
    and the format of the stored chunk text."""
    model_name: str
    model_revision: str
    query_prefix: str
    doc_prefix: str
    content_format: int
```

- [ ] **Step 4: Return `TableIdentity` from the store**

Replace `table_identity` (lines 126-133):

```python
    def table_identity(self, slug: str) -> TableIdentity | None:
        table = self._open(slug)
        if table is None:
            return None
        rows = table.head(1).to_pylist()
        if not rows:
            return None
        row = rows[0]
        # .get() with defaults: a table written before these columns existed
        # reads as content_format 0, which mismatches the current format and
        # forces a full rebuild instead of raising KeyError.
        return TableIdentity(
            model_name=row["model_name"],
            model_revision=row["model_revision"],
            query_prefix=row.get("query_prefix", ""),
            doc_prefix=row.get("doc_prefix", ""),
            content_format=row.get("content_format", 0),
        )
```

- [ ] **Step 5: Build and store the identity in `index_semantic`**

Replace lines 171-178 of `index_semantic`:

```python
    query_prefix, doc_prefix = model.prefixes()
    identity = TableIdentity(*model.identity(), query_prefix, doc_prefix, CONTENT_FORMAT)

    # Reuse rule: the previous table is only a reuse source when its full
    # identity matches -- model, prefixes, and stored-content format alike.
    previous = store.rows_by_path(slug) if store.table_identity(slug) == identity else {}
    vector_by_hash = {row["content_hash"]: row["vector"]
                      for rows in previous.values() for row in rows}
```

Replace the row dict (lines 218-224) so the three new columns are written:

```python
        {"chunk_id": uuid.uuid4().hex, "content_hash": c.content_hash,
         "file_hash": c.file_hash, "file_path": c.file_path,
         "start_line": c.start_line, "end_line": c.end_line,
         "symbol_name": c.symbol_name or "", "language": c.language,
         "content": c.content, "vector": vector_by_hash[c.content_hash],
         "model_name": identity.model_name, "model_revision": identity.model_revision,
         "query_prefix": identity.query_prefix, "doc_prefix": identity.doc_prefix,
         "content_format": identity.content_format}
```

- [ ] **Step 6: Restore the table's prefixes at query time**

Replace lines 239-247 of `semantic_search`:

```python
    model = model or default_model()
    warning: str | None = None
    configured = TableIdentity(*model.identity(), *model.prefixes(), CONTENT_FORMAT)
    if configured != identity:
        # Query with the model AND prefixes the table was built with, never the
        # configured ones -- applying a different prefix to the query than the
        # documents were embedded with is exactly the silent mismatch this
        # feature exists to prevent.
        warning = (f"configured embedding model {configured.model_name}@"
                   f"{configured.model_revision} (prefixes "
                   f"{configured.query_prefix!r}/{configured.doc_prefix!r}) differs from "
                   f"the index's {identity.model_name}@{identity.model_revision} "
                   f"(prefixes {identity.query_prefix!r}/{identity.doc_prefix!r}); "
                   f"queried with the index's — run codeintel reindex {slug} to migrate")
        model = EmbeddingModel(model_name=identity.model_name,
                               revision=identity.model_revision,
                               query_prefix=identity.query_prefix,
                               doc_prefix=identity.doc_prefix)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/semantic.py tests/test_semantic.py
git commit -m "feat(semantic): version the stored content format in table identity"
```

---

### Task 5: Chunk-size percentiles in the index report

**Files:**
- Modify: `src/codeintel/semantic.py` (`SemanticIndexReport` near line 89; `index_semantic` return near line 227)
- Modify: `src/codeintel/index_cli.py` (`_print_semantic_report`)
- Test: `tests/test_semantic.py`, `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `SemanticIndexReport` and `TableIdentity` from Task 4; `prefix_warning()` from Task 3.
- Produces: `TokenStats(p50: int, p90: int, max: int)`; `SemanticIndexReport.token_stats: TokenStats | None`; `SemanticIndexReport.prefix_warning: str | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_semantic.py`:

```python
def test_token_stats_reported_for_new_chunks(tmp_path, lancedb_available):
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert report.token_stats is not None
    stats = report.token_stats
    assert 0 < stats.p50 <= stats.p90 <= stats.max


def test_token_stats_is_none_when_nothing_new(tmp_path, lancedb_available):
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    again = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert again.token_stats is None
```

Add to `tests/test_index_cli.py`:

```python
def test_report_prints_token_percentiles(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport, TokenStats

    _print_semantic_report(SemanticIndexReport(
        rows=95, files=15, truncated=0, token_stats=TokenStats(180, 410, 498)))
    assert "chunk tokens p50=180 p90=410 max=498" in capsys.readouterr().err


def test_report_omits_percentiles_when_absent(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(rows=10, files=2, truncated=0))
    assert "chunk tokens" not in capsys.readouterr().err


def test_report_prints_prefix_warning(capsys):
    from codeintel.index_cli import _print_semantic_report
    from codeintel.semantic import SemanticIndexReport

    _print_semantic_report(SemanticIndexReport(
        rows=10, files=2, truncated=0, prefix_warning="model X is not in the map"))
    assert "model X is not in the map" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -k token_stats tests/test_index_cli.py -k "percentiles or prefix_warning" -v`
Expected: FAIL — `ImportError: cannot import name 'TokenStats'`

- [ ] **Step 3: Add `TokenStats` and extend the report**

In `src/codeintel/semantic.py`, add before `SemanticIndexReport`:

```python
@dataclass(frozen=True)
class TokenStats:
    """Chunk-size distribution over newly-chunked content. Percentiles beat a
    bucketed histogram here: one line of CLI output that is directly
    actionable against MAX_TOKENS."""
    p50: int
    p90: int
    max: int
```

Add two fields to `SemanticIndexReport`:

```python
    token_stats: TokenStats | None = None
    prefix_warning: str | None = None
```

- [ ] **Step 4: Compute the percentiles**

In `index_semantic`, add before the `rows = carried + [...]` assignment:

```python
    token_counts = sorted(len(c.content) // 4 for c in pending)
    stats = TokenStats(
        p50=token_counts[len(token_counts) // 2],
        p90=token_counts[min(int(len(token_counts) * 0.9), len(token_counts) - 1)],
        max=token_counts[-1],
    ) if token_counts else None
```

and extend the return:

```python
    return SemanticIndexReport(rows=len(rows), files=admitted,
                               skipped=tuple(skipped), truncated=truncated,
                               token_stats=stats,
                               prefix_warning=model.prefix_warning())
```

- [ ] **Step 5: Surface the prefix warning at query time too**

In `semantic_search`, immediately before `if warning:` at the end, add:

```python
    prefix_note = model.prefix_warning()
    if prefix_note:
        warning = f"{warning}; {prefix_note}" if warning else prefix_note
```

- [ ] **Step 6: Print both new lines**

In `src/codeintel/index_cli.py`, add to `_print_semantic_report`, after the existing skipped-file loop and before the truncation branch:

```python
    if report.token_stats is not None:
        stats = report.token_stats
        print(f"semantic: chunk tokens p50={stats.p50} p90={stats.p90} "
              f"max={stats.max}", file=sys.stderr)
    if report.prefix_warning:
        print(f"warning: {report.prefix_warning}", file=sys.stderr)
```

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest -m "not integration" -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/codeintel/semantic.py src/codeintel/index_cli.py tests/test_semantic.py tests/test_index_cli.py
git commit -m "feat(semantic): report chunk-size percentiles and prefix warnings"
```

---

### Task 6: Verify the acceptance criteria

**Files:**
- Test: `tests/test_chunker.py` (one integration test)
- Modify: `docs/superpowers/specs/2026-07-30-chunk-context-enrichment-design.md`

**Interfaces:**
- Consumes: everything from Tasks 1-5. Produces nothing new.

The spec's criteria are structural invariants, deliberately not chunk counts — the previous plan's hardcoded total went stale the moment implementation added code to the tree being measured.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_chunker.py`:

```python
@pytest.mark.integration
def test_own_repo_satisfies_header_invariants():
    """Acceptance criteria 1 and 2, checked against real source."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src"
    checked = 0
    for abs_path, rel_path in iter_source_files(src):
        source = abs_path.read_bytes().decode("utf-8", errors="replace")
        if skip_reason(rel_path, source) is not None:
            continue
        for chunk in chunk_file(rel_path, source, "fh", language_for(abs_path)):
            assert chunk.content.startswith(f"# file: {rel_path}\n")
            assert len(chunk.content) // 4 <= MAX_TOKENS
            checked += 1
    assert checked > 50
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_chunker.py -m integration -v`
Expected: PASS

- [ ] **Step 3: Verify criterion 3 — a second index re-embeds nothing**

```bash
uv run python -c "
from pathlib import Path
import tempfile, shutil
from codeintel.semantic import index_semantic
from codeintel.embeddings import EmbeddingModel

class Counting(EmbeddingModel):
    def __init__(self):
        super().__init__(model_name='fake', revision='r')
        self.count = 0
    def identity(self): return ('fake', 'r')
    def embed_texts(self, texts):
        self.count += len(texts)
        return [[float(len(t) % 97), 1.0, 0.0] for t in texts]
    def count_oversized(self, texts): return 0

root = Path(tempfile.mkdtemp())
m1 = Counting(); index_semantic(Path('src'), 'selftest', root=root, model=m1)
m2 = Counting(); index_semantic(Path('src'), 'selftest', root=root, model=m2)
print(f'first={m1.count} second={m2.count}')
assert m2.count == 0, 'second index must re-embed nothing'
shutil.rmtree(root)
print('criterion 3 OK')
"
```

Expected: `second=0` and `criterion 3 OK`. A non-zero second count means carry-forward is broken — stop and diagnose rather than adjusting the check.

- [ ] **Step 4: Verify criterion 4 — the percentiles line appears**

```bash
uv run codeintel index . --slug selfcheck 2>&1 | grep "chunk tokens"
```

Expected: a line like `semantic: chunk tokens p50=… p90=… max=…`. Then clean up: `uv run codeintel forget selfcheck`.

- [ ] **Step 5: Record the result in the spec**

Under `## Acceptance criteria` in the spec, append:

```markdown
**Verified 2026-07-30:** all four criteria met — headers present on every chunk,
no chunk over `MAX_TOKENS`, a second consecutive index re-embeds 0 chunks, and
the percentiles line is emitted.
```

- [ ] **Step 6: Run the complete suite**

Run: `uv run pytest -v`
Expected: PASS. Integration tests needing `scip`/`zoekt-index` skip cleanly if absent — expected, not a failure.

- [ ] **Step 7: Commit**

```bash
git add tests/test_chunker.py docs/superpowers/specs/2026-07-30-chunk-context-enrichment-design.md
git commit -m "test(semantic): verify context-header invariants against own repo"
```

---

## Out of Scope

Do not implement — see the spec's Deferred section:

- LLM-generated per-chunk context.
- Content-type routing for docs/config files.
- Stable FQN chunk IDs (verified inert under rebuild-not-accumulate storage).
- Late chunking.
- A retrieval eval set to measure the enrichment delta.
