"""Tests for scip_decoder.py — zstd + vendored-protobuf occurrence/relationship
decoding. Round-trips are built directly with `scip_pb2` (not the fixture
encoder in tests/fixtures/, which is exercised by tests/test_query_service.py
and friends) to keep this module's tests independent of the fixture."""

from __future__ import annotations

import pytest
import zstandard

from codeintel import scip_pb2
from codeintel.scip_decoder import (
    OccurrenceDecodeError,
    ScipOccurrence,
    SymbolRoles,
    decode_occurrences,
    decode_relationships,
    kind_name,
    scip_range_to_positions,
)


def _compress(data: bytes) -> bytes:
    return zstandard.ZstdCompressor().compress(data)


def _occurrences_blob(occurrences: list[scip_pb2.Occurrence]) -> bytes:
    document = scip_pb2.Document(occurrences=occurrences)
    return _compress(document.SerializeToString())


def test_decode_occurrences_round_trip():
    occ1 = scip_pb2.Occurrence(
        range=[1, 0, 1, 9],
        symbol="scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet().",
        symbol_roles=SymbolRoles.DEFINITION,
        enclosing_range=[1, 0, 3, 1],
    )
    occ2 = scip_pb2.Occurrence(
        range=[5, 4, 5, 9],
        symbol="scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet().",
        symbol_roles=0,
        enclosing_range=[],
    )
    blob = _occurrences_blob([occ1, occ2])

    decoded = decode_occurrences(blob)

    assert decoded == [
        ScipOccurrence(
            range=(1, 0, 1, 9),
            symbol="scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet().",
            symbol_roles=SymbolRoles.DEFINITION,
            enclosing_range=(1, 0, 3, 1),
        ),
        ScipOccurrence(
            range=(5, 4, 5, 9),
            symbol="scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet().",
            symbol_roles=0,
            enclosing_range=(),
        ),
    ]
    assert decoded[0].is_definition() is True
    assert decoded[1].is_definition() is False


def test_decode_occurrences_empty_list_returns_empty():
    blob = _occurrences_blob([])
    assert decode_occurrences(blob) == []


def test_decode_occurrences_truncated_zstd_raises_decode_error():
    with pytest.raises(OccurrenceDecodeError):
        decode_occurrences(b"\x28\xb5\x2f\xfd\x00\x01\x02")


def test_decode_occurrences_non_zstd_input_raises_decode_error():
    with pytest.raises(OccurrenceDecodeError):
        decode_occurrences(b"not zstd at all, just plain bytes")


def test_decode_occurrences_corrupt_protobuf_after_valid_zstd_raises_decode_error():
    garbage = _compress(b"\xff\xff\xff not a valid Document message \xff")
    with pytest.raises(OccurrenceDecodeError):
        decode_occurrences(garbage)


def test_scip_range_to_positions_four_element():
    assert scip_range_to_positions((3, 4, 3, 9)) == (3, 4, 3, 9)


def test_scip_range_to_positions_three_element_same_line_shorthand():
    assert scip_range_to_positions((3, 4, 9)) == (3, 4, 3, 9)


def test_scip_range_to_positions_rejects_negative():
    with pytest.raises(OccurrenceDecodeError):
        scip_range_to_positions((-1, 0, 0, 5))


def test_scip_range_to_positions_rejects_bad_length():
    with pytest.raises(OccurrenceDecodeError):
        scip_range_to_positions((1, 2))


def test_decode_relationships_round_trip():
    symbol_info = scip_pb2.SymbolInformation(
        symbol="scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#",
        relationships=[
            scip_pb2.Relationship(
                symbol="scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Animal#",
                is_reference=False,
                is_implementation=True,
                is_type_definition=False,
                is_definition=False,
            )
        ],
    )
    blob = _compress(symbol_info.SerializeToString())

    decoded = decode_relationships(blob)

    assert len(decoded) == 1
    assert decoded[0].symbol == "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Animal#"
    assert decoded[0].is_implementation is True
    assert decoded[0].is_reference is False


def test_decode_relationships_empty_returns_empty():
    symbol_info = scip_pb2.SymbolInformation(symbol="x")
    blob = _compress(symbol_info.SerializeToString())
    assert decode_relationships(blob) == []


def test_decode_relationships_corrupt_input_raises_decode_error():
    with pytest.raises(OccurrenceDecodeError):
        decode_relationships(b"garbage-not-zstd")


def test_kind_name_known_int():
    assert kind_name(scip_pb2.SymbolInformation.Class) == "Class"


def test_kind_name_unknown_int_returns_none():
    assert kind_name(999_999) is None


def test_kind_name_none_input_returns_none():
    assert kind_name(None) is None


def test_only_scip_decoder_and_test_fixtures_import_scip_pb2():
    """Isolation-seam check (phase-01 success criterion): scip_pb2 must not
    leak into the query/API layers — only the decoder module and test
    fixture/encoder code may import it."""
    import pathlib
    import re

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    allowed = {
        repo_root / "src" / "codeintel" / "scip_decoder.py",
    }
    pattern = re.compile(r"^\s*(import scip_pb2|from codeintel import.*\bscip_pb2\b)", re.MULTILINE)

    offenders = []
    for path in (repo_root / "src").rglob("*.py"):
        if path in allowed or path.name == "scip_pb2.py":
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path))

    assert offenders == [], f"scip_pb2 imported outside the decoder module: {offenders}"


def test_scip_pb2_exposes_typed_range_oneof():
    """scip.proto gained a typed_range oneof; the old v0.7.0 gencode lacks it.

    Without these fields the decoder cannot see ranges produced by any modern
    indexer (scip-swift writes single_line_range and never the deprecated
    repeated-int32 range), so every occurrence looks position-less.
    """
    from codeintel import scip_pb2

    field_names = {f.name for f in scip_pb2.Occurrence.DESCRIPTOR.fields}
    assert "single_line_range" in field_names
    assert "multi_line_range" in field_names
    # The deprecated field must remain readable for older indexes.
    assert "range" in field_names

    oneof_names = {o.name for o in scip_pb2.Occurrence.DESCRIPTOR.oneofs}
    assert "typed_range" in oneof_names


def test_single_line_range_message_shape():
    from codeintel import scip_pb2

    r = scip_pb2.SingleLineRange()
    r.line = 4
    r.start_character = 2
    r.end_character = 9
    assert (r.line, r.start_character, r.end_character) == (4, 2, 9)
