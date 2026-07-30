# Chunk Context Enrichment and Model-Aware Prefixes — Design

**Date:** 2026-07-30
**Status:** Approved, ready for implementation planning
**Follows on from:** `2026-07-30-semantic-index-file-filter-design.md`

## Problem

Four gaps remain in the semantic pipeline after the generated-file admission
filter shipped. They are grouped here because two of them invalidate every
existing vector, and doing them together costs one full re-embed instead of two.

**1. Context enrichment is inconsistent (the retrieval-quality gap).** Only
oversized-class method splits get a context header (`_split_class` prepends up
to 10 import statements plus the class line). Ordinary top-level functions,
small classes, and fixed-window fallback chunks get no file path and no scope
at all. A chunk containing `def apply_discount(...)` is an orphan: the query
`"invoice billing discount"` has nothing to match, because the words `invoice`
and `billing` live in the file path, not the function body.

**2. Prefix handling is silently wrong for non-default models.** `embed_query()`
and `embed_texts()` apply no prefix regardless of which model is loaded. That
is correct for the default — BGE-M3 genuinely requires no instruction prefix,
unlike earlier BGE versions — but `CODEINTEL_EMBEDDING_MODEL` can select a model
that *does* require one. `multilingual-e5-large` requires `"query: "` and
`"passage: "`; nomic models require `"search_query: "` and
`"search_document: "`. Omitting them degrades retrieval measurably, with no
error.

**3. No max file size cap.** Long-line and banner detection catch the common
pathological shapes, but a large file with normal-length lines and no banner is
chunked in full.

**4. No `.gitignore` support.** Only the fixed `IGNORED_DIRS` set is honored, so
build outputs and vendored dependencies in non-standard directories are indexed.

**5. No chunk-size distribution reporting.** The truncation counter shows how
many chunks the model will truncate, but nothing shows how chunk sizes are
actually distributed.

### Evidence for enrichment

Contextual retrieval measurements: top-20 retrieval failure rate drops from
5.7% to 2.9% (a 49% reduction) on a hybrid vector + BM25 setup; Pass@10 improves
from ~87% to ~95%; roughly 35% average reduction in retrieval failures across
domains. Independent reports put precision gains at 5-15%.

**Important caveat:** those figures are for *LLM-generated* per-chunk context
(~50-100 tokens per chunk, ~$1.02 per million document tokens). That variant is
a non-starter for a local-first tool with no indexing LLM budget. The same
sources present **static structural headers as the free approximation** — no LLM
call per chunk — and for code the canonical form is `file path → class → method`.
The mechanism is well-evidenced; what is unmeasured is the delta size on this
specific corpus, not whether it helps.

## Goals

1. Give every chunk file-path and scope context before embedding.
2. Apply the correct query/document prefix for whichever model is configured,
   and make an unsupported model visible rather than silent.
3. Cap file size and honor `.gitignore`.
4. Report chunk-size distribution at index time.

## Non-goals

- LLM-generated per-chunk context (cost model does not fit a local-first tool).
- Content-type routing for docs/config files — an open scope question, not a
  pipeline defect.
- Stable FQN chunk IDs — verified inert under the rebuild-not-accumulate
  storage model.
- Late chunking.

## Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Header contents | File path + parent scope only | The symbol's own name is already the first line of the chunk's code. The path is present nowhere; the parent class is absent from a split-method chunk. Every header line must earn its budget. |
| Header storage | Baked into `Chunk.content` | `content_hash` then covers the header automatically — rename a file, hash changes, re-embed happens. The alternatives require maintaining that invariant by hand. |
| Header application point | Final pass after split + merge | `_merge_small` concatenates chunk contents; attaching headers earlier would duplicate them inside merged chunks. |
| Budget safety | `HEADER_RESERVE_TOKENS = 32` | Splitting decides size before the header exists. Reserving a fixed allowance avoids per-chunk header-length bookkeeping during the split. |
| Prefix resolution | Known-model map + env override | The only option where every failure mode is both visible and fixable without a code change. |
| `.gitignore` | Batched `git check-ignore --stdin` | Zero new dependencies, and the semantics are git's rather than a reimplementation. `_git_head` already makes git a hard requirement. |
| Max file size | 1 MB | A pure backstop; long-line and banner detection already catch the other shapes. |
| Distribution reporting | p50 / p90 / max chunk tokens | One actionable line in a CLI beats histogram buckets. |

### Rejected alternatives

**Header as a separate `Chunk.header` field, composed at embed time.** Keeps
stored content as raw code, but `content_hash` must then explicitly combine two
fields or a file rename silently skips re-embedding. A two-form invariant
someone eventually gets wrong, for no retrieval benefit.

**Header computed at embed time, never stored.** Rejected on correctness:
hashing raw content means a rename changes the header but not the hash, so the
carry-forward path reuses vectors embedded under the old path.

**Extending `_split_class`'s import-blob header to every chunk.** Up to 10
import statements is 100-150 of the 512-token budget (20-30%) on every chunk,
and it is the part the evidence does not credit — developer queries do not
contain import lines. It also maximizes intra-file homogenization, since every
chunk in a file would share a large identical prefix.

## Architecture

Three text forms, each transformation happening in exactly one place:

```
raw source
  ├─ iter_source_files():  IGNORED_DIRS + ext allowlist + NEW gitignore (batched)
  ├─ oversized_file_reason(stat().st_size)   NEW — before read_bytes(), so a huge
  │                                          file is never read just to be rejected
  ├─ skip_reason(rel_path, source, ...)      existing
  └─ chunk_file():
       AST split, sized against MAX_TOKENS − HEADER_RESERVE_TOKENS
       → _merge_small()
       → _apply_headers()   NEW final pass — the single place headers attach

Chunk.content  = "# file: <path>\n# in class: <Parent>\n\n<code>"   ← stored + hashed
embed (index)  = doc_prefix   + Chunk.content   → encoder   ← prefix never stored
embed (query)  = query_prefix + query           → encoder
```

Header shape:

```
# file: src/billing/invoice.py
# in class: InvoiceService

def apply_discount(self, invoice_id, rate):
```

The scope line appears **only** when the chunk has a parent class — i.e. a
method split out of an oversized class, where the class name is genuinely absent
from the chunk body. Top-level defs and fixed-window fallback chunks carry the
file line alone, because a top-level def's own name is already the first line of
its code and repeating it buys nothing.

### Preserving class context

Unifying `_split_class` onto the final pass would lose information: it currently
emits the class line, but a final pass sees only `Chunk.symbol_name`, which for
a split method is the *method* name. `Chunk` therefore gains a
`parent_name: str | None` field that `_split_class` populates. The class context
that exists today is preserved, as a structured field rather than embedded text.

### The `content_format` version — and why it is needed here

The header changes `content_hash` for every chunk, so no chunk is *reused*. But
unchanged files are matched by **`file_hash`**, not content hash, so a file
untouched since the last index would have its old, header-less rows **carried
forward verbatim** — permanently. The result would be a table mixing header-ed
and header-less content, with the files that needed no re-indexing being exactly
the ones stuck stale.

Separately, old LanceDB tables have no `query_prefix`/`doc_prefix` columns, so
reading them naively raises `KeyError`.

Both are solved by putting a `content_format` version in the table identity. An
old table reads as version 0, mismatches the current 1, so `previous = {}` and
everything is re-chunked and re-embedded. The existing model-identity rule does
the work; no new machinery.

**Note on the previous spec.** `2026-07-30-semantic-index-file-filter-design.md`
rejected a `filter_version` column as unnecessary bookkeeping. That call was
correct there — filtering *before* the carry-forward check made purging
automatic. It does not apply here, because this change rewrites content for
files whose bytes never changed, which no ordering can catch. Same-sounding
mechanism, genuinely different situation.

### Identity propagation at query time

`semantic.py`'s query path already rebuilds the model from the table's identity
so queries use the model the table was built with. It must now also restore the
table's **prefixes**. Otherwise a query applies configured prefixes against
vectors embedded with different ones — the exact silent mismatch this work
exists to close.

## Components

### `chunker.py`

```python
HEADER_RESERVE_TOKENS = 32          # budget held back so headers can't overflow MAX_TOKENS
MAX_FILE_BYTES = 1_048_576          # 1 MB backstop
CONTENT_FORMAT = 1                  # bump when the stored chunk text shape changes

@dataclass(frozen=True)
class Chunk:
    ...                             # existing fields unchanged
    parent_name: str | None = None  # enclosing class, set by _split_class

def oversized_file_reason(size_bytes: int) -> str | None:
    """Checked before read_bytes(), so a huge file is never read to be rejected."""

def gitignored(repo_path: Path, rel_paths: list[str]) -> set[str]:
    """One batched `git check-ignore --stdin` for the whole repo."""
```

`chunk_file()` changes at two points: split decisions compare against
`MAX_TOKENS - HEADER_RESERVE_TOKENS`, and its single exit becomes
`return _apply_headers(_merge_small(chunks), rel_path)`.

`_merge_small()` gains one condition: only merge when `parent_name` matches.

**Deletion:** once `_split_class` stops prepending imports, `_collect_imports()`,
`_IMPORT_NODE_TYPES`, and `MAX_IMPORT_LINES` have no remaining callers — roughly
25 lines removed, including the Kotlin `import_list` special-casing.

### `embeddings.py`

```python
MODEL_PREFIXES: dict[str, tuple[str, str]] = {   # substring match → (query, doc)
    "bge-m3": ("", ""),
    "e5":     ("query: ", "passage: "),
    "nomic-embed": ("search_query: ", "search_document: "),
}

def prefixes(self) -> tuple[str, str]:
    """Env override wins, else the map, else ("", "")."""

def prefix_warning(self) -> str | None:
    """Message when the model is unlisted and no env override is set, naming
    the env var to set. Returns None otherwise. This module never prints —
    callers decide where to surface it."""
```

`embed_texts()` prepends the doc prefix; `embed_query()` prepends the query
prefix. `count_oversized()` measures the **prefixed** text, since that is what
reaches the encoder.

Environment variables: `CODEINTEL_EMBEDDING_QUERY_PREFIX`,
`CODEINTEL_EMBEDDING_DOC_PREFIX`. Each is independent — setting only one is
legal, since some models use asymmetric prefixes.

**Matching is substring-based, longest pattern first.** Substring matching is
required to handle vendor-prefixed names (`intfloat/multilingual-e5-large` must
match `e5`, `BAAI/bge-m3` must match `bge-m3`); longest-first makes the result
deterministic when two patterns both match. The accepted risk is a future model
whose name coincidentally contains a pattern and gets the wrong prefix — the env
override is the escape hatch for exactly that case, which is part of why the
map-plus-override design was chosen over a map alone.

### `semantic.py`

```python
@dataclass(frozen=True)
class TableIdentity:
    model_name: str
    model_revision: str
    query_prefix: str
    doc_prefix: str
    content_format: int

@dataclass(frozen=True)
class TokenStats:
    p50: int
    p90: int
    max: int
```

`SemanticStore.table_identity()` returns `TableIdentity | None`, reading the new
columns with `.get(key, default)` so an old table yields `content_format=0`.
Rows gain `query_prefix`, `doc_prefix`, `content_format`.
`SemanticIndexReport` gains `token_stats: TokenStats | None` (None when nothing
new was chunked).

`content_format` stays **out** of `EmbeddingModel.identity()` — it is a chunker
concern, not a model one. `TableIdentity` is where the two meet.

### `index_cli.py`

`_print_semantic_report` gains one line, printed only when `token_stats` is
present:

```
semantic: chunk tokens p50=180 p90=410 max=498
```

## Error handling and edge cases

**`git check-ignore` exit codes are not the usual convention:** 0 = some paths
ignored, 1 = none matched, 128 = real error. Treating non-zero as failure would
silently disable filtering on every repo that ignores nothing. Handling: exit 0
→ parse stdout; exit 1 → empty set; anything else, plus `FileNotFoundError` if
git is absent, plus a timeout guard → empty set. Failure degrades to indexing
*more*, never less.

**`_merge_small` must not merge across parents.** A class's last method sits
adjacent to the next top-level function; merging them yields a chunk stamped
`# in class: Foo` covering code that is not in `Foo` — a header that actively
lies.

**No printing from `embeddings.py`.** `semantic_search` runs inside the MCP
server, so a per-query warning would repeat on every call. `prefix_warning()`
returns the message; `index_semantic` folds it into the report (printed once per
index) and `semantic_search` appends it to its existing `warning` field, which
already carries this class of "config disagrees with the table" message.

| Case | Resolution |
|---|---|
| Non-git directory (the whole test suite) | `gitignored()` → empty set, indexing proceeds |
| Only one prefix env var set | Allowed and independent — asymmetric prefixes are legal |
| Old table missing new columns | `.get()` defaults → `content_format=0` → full rebuild, no `KeyError` |
| Empty file | `_fixed_windows` returns `[]`, no empty chunk reaches the header pass |
| `count_oversized` raises | Already guarded — `truncated=None` |

## Testing

Two regression tests carry the weight. Both cover hazards found during design,
not coverage gaps:

- **`test_stale_headerless_rows_not_carried_after_format_bump`** — index at
  `content_format=0`, bump to 1, reindex with byte-identical files, assert no
  header-less content survives.
- **`test_old_table_without_prefix_columns_forces_rebuild`** — write a table
  lacking the new columns, confirm it reads cleanly and triggers a full rebuild
  rather than raising.

**`test_chunker.py`**
- Header present on all three chunk shapes (top-level def, split method,
  fixed-window fallback).
- Header carries the path; carries the parent class for split methods; omits the
  redundant own-symbol line.
- Header not duplicated after a merge.
- Chunks with differing `parent_name` do not merge.
- `MAX_TOKENS` respected *after* the header is applied (proves the reserve works).
- `oversized_file_reason` threshold.
- `gitignored()` exit-1 handled as "nothing ignored"; non-git dir → empty set.

**`test_embeddings.py`**
- Prefix map resolution for bge-m3 / e5 / nomic.
- Env override wins over the map.
- Unlisted model → `prefix_warning()` returns a message, prefixes are `("", "")`.
- `embed_query` applies the query prefix; `embed_texts` applies the doc prefix.
- `count_oversized` measures prefixed text.

**`test_semantic.py`**
- `TableIdentity` round-trip.
- Query-time path restores the **table's** prefixes, not the configured ones.
- `token_stats` computed correctly.
- Oversized file skipped with its reason; gitignored file excluded.

**`test_index_cli.py`**
- Percentiles line printed when `token_stats` is present, omitted when None.

**Existing tests that must change** — they encode the behavior being removed:
`test_oversized_class_splits_into_methods_with_imports_and_class_header` and its
Kotlin equivalent both assert the imports blob is present. They are rewritten
against the new header, not deleted.

## Acceptance criteria

Invariants rather than counts. The previous spec's hardcoded chunk total went
stale the moment implementation added code to the tree being measured; these do
not have that failure mode.

1. Every chunk's `content` starts with `# file: `.
2. No chunk exceeds `MAX_TOKENS` after its header is applied.
3. Indexing the same repo twice in a row re-embeds nothing on the second run —
   carry-forward still works once formats match.
4. `codeintel index` on this repo reports a `chunk tokens p50=… p90=… max=…`
   line.

**Verified 2026-07-30:** all four criteria met — headers present on every chunk,
no chunk over `MAX_TOKENS`, a second consecutive index re-embeds 0 chunks, and
the percentiles line is emitted.

## Deferred

| Item | Why |
|---|---|
| Content-type routing (docs/config) | Scope expansion beyond code-only indexing; needs a product decision. |
| Stable FQN chunk IDs | Verified inert: `content_hash` drives dedup and the table is rebuilt wholesale each index. |
| Late chunking | 2-3x slower indexing; symbol-name collision is not a demonstrated problem here. |
| LLM-generated per-chunk context | Cost model does not fit a local-first tool. Static headers are the evidenced free approximation. |

## Unresolved questions

1. Is non-code indexing (markdown, config) ever in scope, or is code-only an
   intentional boundary given the SCIP/Zoekt focus?
2. The enrichment delta is unmeasured on this corpus. The mechanism is
   well-evidenced (see "Evidence for enrichment"), but nothing here measures
   whether retrieval on *these* repos improves, and the acceptance criteria are
   structural invariants rather than a quality metric. Building a retrieval eval
   set would settle it — deliberately out of scope, but the question stays open.
