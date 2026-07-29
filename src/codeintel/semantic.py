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
