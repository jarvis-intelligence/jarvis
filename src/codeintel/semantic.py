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
        # open_table() directly rather than checking membership via
        # table_names()/list_tables() first: both paginate (default page
        # size 10), so with more than ~10 tables in this db_dir a slug
        # sorting past the first page would look "not found" even though
        # it exists. A missing table raises ValueError — treat that as None.
        try:
            return db.open_table(slug)
        except ValueError:
            return None

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
        # ignore_missing=True: same reasoning as _open — avoid a
        # table_names()/list_tables() membership check that paginates.
        db.drop_table(slug, ignore_missing=True)


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
