"""Test-only helpers that build real zstd+protobuf occurrence/relationship
blobs via the vendored `scip_pb2` — the same generated module
`service/scip_decoder.py` uses to decode them, so encoder and decoder share
one source of truth (plan.md Design Decision 6/1).

Never shipped — imported only by tests/fixtures/synthetic_index.py and
tests/test_scip_decoder.py.
"""

from __future__ import annotations

import zstandard

from codeintel import scip_pb2

__all__ = ["encode_occurrences", "encode_relationships"]


def encode_occurrences(occurrences: list[scip_pb2.Occurrence]) -> bytes:
    """Build the exact `chunks.occurrences` blob shape: a `scip.Document`
    carrying only `occurrences`, zstd-compressed — matches
    `(*Chunk).toDBFormat` in the real v0.7.0 converter (`cmd/scip/convert.go`,
    verified 2026-07-11)."""
    document = scip_pb2.Document(occurrences=occurrences)
    return zstandard.ZstdCompressor().compress(document.SerializeToString())


def encode_relationships(relationships: list[scip_pb2.Relationship]) -> bytes:
    """Build a `global_symbols.relationships` blob: a bare serialized
    `scip.SymbolInformation` message, zstd-compressed. See
    `service/scip_decoder.py`'s module docstring — the real v0.7.0 converter
    never writes a non-NULL value for this column, so this framing is a
    forward-compatibility placeholder, not a verified-against-real-data
    convention."""
    symbol_info = scip_pb2.SymbolInformation(relationships=relationships)
    return zstandard.ZstdCompressor().compress(symbol_info.SerializeToString())
