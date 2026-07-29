# Semantic/Vector Search Design

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

Add semantic (natural-language) code search to codeintel, fused with the existing
Zoekt lexical search via Reciprocal Rank Fusion (RRF). A user asks "where is the
authentication logic?" and gets ranked `file:line` results drawn from both a vector
index and Zoekt. Explicitly out of scope: cross-encoder reranking and LLM answer
synthesis — the MCP client (Claude) does its own synthesis.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Embeddings | Self-hosted `nomic-ai/nomic-embed-code` via `sentence-transformers` | Fully offline, matches local-first ethos; no API key or per-token cost |
| Vector storage | LanceDB, embedded, one table per repo | No server process; per-repo isolation mirrors `index-<sha>.db` pattern |
| Chunking | tree-sitter, AST-aware at function/class boundaries | Verified best practice (~40-50% recall gain over fixed windows) |
| Pipeline | Third stage inside existing `codeintel index` | One command, one registry status; no separate embed step |
| Fusion | RRF with k=60 over top-30 vector + top-30 Zoekt hits | Standard, parameter-light, no training required |

All new dependencies live under an optional `semantic` extra
(`uv sync --extra semantic`), mirroring the existing `watch` extra. Base install
is unchanged.

## Module Layout

```
src/codeintel/
├── chunker.py        # NEW — tree-sitter AST chunking
├── embeddings.py     # NEW — self-hosted embedding model wrapper
├── semantic.py       # NEW — LanceDB storage + vector search + RRF fusion
├── index_cli.py      # MODIFIED — add embedding stage to pipeline
├── server.py         # MODIFIED — add semanticSearch MCP tool
└── config.py         # MODIFIED — lancedb path + model env vars
```

New dependencies (under `[project.optional-dependencies] semantic`):
`lancedb>=0.20`, `sentence-transformers>=3.0`, `tree-sitter>=0.25`,
`tree-sitter-language-pack>=0.1`.

## Chunking (`chunker.py`)

1. Walk the repo reusing `index_cli.py`'s `_IGNORED_DIRS`; detect language per file
   by extension (reuse `_LANGUAGE_INDEXERS` mapping).
2. Parse with tree-sitter; chunk at function/class/method declaration nodes per
   language (Python: `function_definition`/`class_definition`; TS/JS:
   `function_declaration`/`class_declaration`/`method_definition`; Java/Kotlin:
   `function_declaration`/`class_declaration`; Swift: adds `protocol_declaration`).
3. Target 256–512 tokens per chunk, estimated as `len(content) // 4` (no tokenizer
   dependency). A class over 512 tokens is split into per-method chunks, each
   prefixed with a context header: the file's import lines (collected from
   tree-sitter import nodes, capped at the first 10 lines to protect the token
   budget) plus the class definition line. Chunks under 256 tokens merge with
   adjacent siblings.
4. Files tree-sitter cannot parse fall back to fixed 512-token windows with
   50-token overlap.
5. Each chunk carries `content_hash = sha256(content)` for deduplication and
   `file_hash = sha256(file_bytes)` for file-level change detection.

```python
@dataclass(frozen=True)
class Chunk:
    file_path: str          # relative to repo root
    start_line: int         # 1-indexed
    end_line: int           # 1-indexed, inclusive
    content: str
    symbol_name: str | None # None for fixed-window chunks
    content_hash: str
    file_hash: str
    language: str
```

## Embeddings (`embeddings.py`)

- Model: `nomic-ai/nomic-embed-code` (768-dim), loaded via `sentence-transformers`.
- Lazy singleton — loaded on first use, never at MCP server startup (same pattern
  as `ZoektLifecycle`).
- Pinned by HuggingFace revision hash. Model identity (`model_name` +
  `model_revision`) is stored per row and as table-level metadata; vectors from
  different models never coexist in a table (see the model-identity rule under
  Storage & Search).
- Batch size 32. Before embedding, chunks are grouped by `content_hash` and only
  unique hashes are embedded.
- Vectors are L2-normalized at encode time (`normalize_embeddings=True`), the
  standard hubness mitigation; paired with cosine distance at query time (see
  Storage & Search), ranking is well-defined and metric-consistent.
- Missing `semantic` extra raises:
  `"semantic search requires the 'semantic' extra: uv sync --extra semantic"`.
- Env vars: `CODEINTEL_EMBEDDING_MODEL` (default `nomic-ai/nomic-embed-code`),
  `CODEINTEL_EMBEDDING_BATCH_SIZE` (default 32).

## Storage & Search (`semantic.py`)

**Layout:** `~/.codeintel/lancedb/` holds one table per repo, named by slug. This
is the fourth storage layer next to `registry.db`, per-repo `index-<sha>.db`, and
Zoekt shards.

**Table columns:** `chunk_id` (UUID pk), `content_hash`, `file_hash`,
`file_path`, `start_line`, `end_line`, `symbol_name`, `language`, `content`,
`vector` (768-dim), `model_name`, `model_revision`. Table-level metadata records
the authoritative `model_name` + `model_revision` for the whole table. LanceDB's
default IVF_PQ ANN index applies; no manual tuning.

**Model-identity rule:** a table only ever contains vectors from one model at
one revision. Reuse and querying are both conditioned on it:

- **Reuse:** the previous table's vectors are eligible for reuse only when its
  table-level `model_name`/`model_revision` match the currently configured
  model. On mismatch the old table is ignored entirely and every chunk is
  re-embedded — there is no migration path between vector spaces.
- **Query:** `semanticSearch` embeds the query with the model recorded in the
  table, not the currently configured one. If configuration and table disagree,
  the query still runs correctly (table's model wins) and the result carries a
  `"warning"` field advising a reindex.

**Write path (during `codeintel index`):** hash each file in the current file
tree → files whose `file_hash` matches the previous table are not re-parsed;
their chunk rows (and vectors) carry over wholesale → changed/new files are
chunked, deduped by `content_hash`, and only unique new hashes are embedded
(subject to the model-identity rule) → build the replacement table from
carried-over + new rows → atomic swap. Deleted files drop out naturally because
carry-over is driven by walking the current file tree — rebuild-not-accumulate,
matching `populate_graph_for_repo()`.

**Query path (`semanticSearch` tool):** embed query with the table's model →
LanceDB top-30 using **cosine distance** (never LanceDB's default L2 — vectors
are L2-normalized at encode time, so cosine ranking is exact) → Zoekt top-30
via existing `search_zoekt()` → RRF merge:

```python
def reciprocal_rank_fusion(vector_hits, zoekt_hits, k: int = 60) -> list[FusedHit]:
    """RRF_score(doc) = sum(1 / (k + rank_i(doc))).
    Results appearing in both lists (same file + overlapping lines) sum scores."""
```

**Result shape:** list of `{repo, filePath, startLine, endLine, symbolName,
content (truncated to 500 chars), score, sources: ["vector"|"zoekt", ...]}` plus
`query` and `total`.

## Pipeline Integration (`index_cli.py`)

```
detect language → SCIP indexer → scip expt-convert → populate graph
→ zoekt-index → [NEW] chunk + embed + write LanceDB table → atomic publish
```

- The semantic stage is optional and non-fatal: without the `semantic` extra,
  `codeintel index` logs one skip line and continues — zero behavior change for
  existing users.
- The LanceDB write completes before the `current` pointer flip, preserving the
  atomic-publish guarantee: an embedding failure leaves the previous SCIP index
  and previous LanceDB table live.
- Registry gains one nullable column `semantic_indexed_at`, surfaced by
  `codeintel status`. `reindex` and `watch` run the semantic stage automatically
  when the extra is installed.

## MCP Tool (`server.py`)

One new tool: `semanticSearch(repo, query, limit=10)`. Wrapped in the same
per-tool try/except returning `{"error": "..."}` — the stdio server never crashes.

## Error Handling

| Failure | Behavior |
|---|---|
| `semantic` extra not installed | Index continues without embedding; `semanticSearch` returns install-hint error |
| Model download fails (offline first run) | Embedding stage aborts; SCIP/Zoekt index still publishes |
| tree-sitter cannot parse a file | Fixed-window fallback for that file only |
| LanceDB table missing at query time | `{"error": "no semantic index for <slug> — run codeintel reindex <slug>"}` |
| Configured model ≠ table model at query time | Query runs with the table's model; result carries a `"warning"` advising reindex |
| Zoekt down during hybrid query | Return vector-only results; `sources` reflects it |

## Testing

Mirrors the existing 1:1 test-file convention:

- `test_chunker.py` — unit: real tree-sitter parsing on fixture strings (pure
  library, nothing to mock); oversized-class split with imports+class context
  header, tiny-sibling merge, unparseable-file fallback.
- `test_embeddings.py` — unit: mocked model; batching, dedup-before-embed,
  missing-extra error message.
- `test_semantic.py` — unit: RRF math against hand-computed scores; LanceDB on a
  tmp-dir table (embedded, real I/O acceptable); Zoekt side mocked; unchanged
  files carry rows over while changed files re-embed; model-revision mismatch
  triggers full re-embed instead of reuse; query-time model mismatch surfaces
  the `"warning"` field.
- `test_index_cli.py` — integration (extended): full pipeline with semantic stage
  on the existing Python fixture; skip-cleanly-when-extra-missing path.
