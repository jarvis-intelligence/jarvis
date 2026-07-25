"""Decode real `scip expt-convert` blob columns (zstd + SCIP protobuf).

This is the ONLY module that imports `scip_pb2`/`zstandard` — the rest of the
codebase works with the small local dataclasses below, staying protobuf-free
(phase-01's isolation-seam requirement).

Blob framing (verified against both the real luz_next index AND the v0.7.0
`expt-convert` source, 2026-07-11 — see plan.md "Verified Ground Truth" and
the phase-1 report):

* ``chunks.occurrences`` is zstd-compressed (magic ``28 B5 2F FD``); the
  decompressed bytes are a valid ``scip.Document`` protobuf message whose
  ``occurrences`` field (2) is exactly the chunk's occurrence list. Confirmed
  from source: `cmd/scip/convert.go`'s `(*Chunk).toDBFormat` does exactly
  `proto.Marshal(&scip.Document{Occurrences: c.Occurrences})` then
  zstd-compresses it.
* ``global_symbols.relationships`` (and `.signature`) are **never written**
  by the v0.7.0 converter — `insertGlobalSymbols`'s `INSERT INTO
  global_symbols` statement lists only `(symbol, display_name, kind,
  documentation, enclosing_symbol)`; there is no code path that populates
  `relationships` at all. This is a structural gap in v0.7.0's output, not
  an artifact of luz_next's content — every real v0.7.0 index has
  `relationships IS NULL` for every row. `decode_relationships` below
  therefore has no real blob to verify a framing against; it decodes a bare
  serialized `scip.SymbolInformation` message (its `relationships` field) —
  a reasonable, internally-consistent choice (encoder/decoder share this
  module's `scip_pb2` as single source of truth) kept only so the
  type-hierarchy code path has real decode logic to exercise via the
  synthetic fixture's contrived non-NULL case, not because any real index
  will ever call it in production.
"""

from __future__ import annotations

from dataclasses import dataclass

import zstandard

from codeintel import scip_pb2

__all__ = [
    "OccurrenceDecodeError",
    "ScipOccurrence",
    "ScipRelationship",
    "SymbolPackage",
    "SymbolRoles",
    "decode_occurrences",
    "decode_relationships",
    "kind_name",
    "parse_symbol_package",
    "scip_range_to_positions",
]


class OccurrenceDecodeError(Exception):
    """Raised for any zstd or protobuf failure while decoding a blob column.

    Never partial/fabricated data — callers must map this to a fail-closed
    503, never swallow it into an empty result (phase-03's contract).
    """


class SymbolRoles:
    """Re-export of `scip_pb2.SymbolRole` bitmask values (generated code —
    not hand-copied)."""

    UNSPECIFIED = scip_pb2.UnspecifiedSymbolRole
    DEFINITION = scip_pb2.Definition
    IMPORT = scip_pb2.Import
    WRITE_ACCESS = scip_pb2.WriteAccess
    READ_ACCESS = scip_pb2.ReadAccess
    GENERATED = scip_pb2.Generated
    TEST = scip_pb2.Test
    FORWARD_DEFINITION = scip_pb2.ForwardDefinition


@dataclass(frozen=True)
class ScipOccurrence:
    range: tuple[int, ...]
    symbol: str
    symbol_roles: int
    enclosing_range: tuple[int, ...]

    def is_definition(self) -> bool:
        return bool(self.symbol_roles & SymbolRoles.DEFINITION)


@dataclass(frozen=True)
class ScipRelationship:
    symbol: str
    is_reference: bool
    is_implementation: bool
    is_type_definition: bool
    is_definition: bool


def _decompress(blob: bytes) -> bytes:
    try:
        return zstandard.ZstdDecompressor().decompress(blob, max_output_size=100_000_000)
    except zstandard.ZstdError as exc:
        raise OccurrenceDecodeError(f"zstd decompression failed: {exc}") from exc


def decode_occurrences(blob: bytes) -> list[ScipOccurrence]:
    """Decompress + parse a ``chunks.occurrences`` blob into occurrences.

    Raises OccurrenceDecodeError on any zstd or protobuf failure — never
    returns partial results from a half-parsed message.
    """
    raw = _decompress(blob)
    try:
        document = scip_pb2.Document.FromString(raw)
    except Exception as exc:  # protobuf raises plain DecodeError/ValueError
        raise OccurrenceDecodeError(f"protobuf decode failed: {exc}") from exc

    return [
        ScipOccurrence(
            range=tuple(occ.range),
            symbol=occ.symbol,
            symbol_roles=occ.symbol_roles,
            enclosing_range=tuple(occ.enclosing_range),
        )
        for occ in document.occurrences
    ]


def decode_relationships(blob: bytes) -> list[ScipRelationship]:
    """Decompress + parse a ``global_symbols.relationships`` blob.

    See module docstring: the v0.7.0 converter never writes a non-NULL
    value for this column (confirmed from source), so this path is
    exercised only by the synthetic fixture's contrived non-NULL case, not
    by any real index. Framing: a bare serialized `scip.SymbolInformation`
    message, read via its `relationships` field. Raises
    OccurrenceDecodeError on any zstd/protobuf failure (fail-closed).
    """
    raw = _decompress(blob)
    try:
        symbol_info = scip_pb2.SymbolInformation.FromString(raw)
    except Exception as exc:
        raise OccurrenceDecodeError(f"protobuf decode failed: {exc}") from exc

    return [
        ScipRelationship(
            symbol=rel.symbol,
            is_reference=rel.is_reference,
            is_implementation=rel.is_implementation,
            is_type_definition=rel.is_type_definition,
            is_definition=rel.is_definition,
        )
        for rel in symbol_info.relationships
    ]


def scip_range_to_positions(range_values: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Convert a SCIP packed range (3 or 4 ints) to (start_line, start_char,
    end_line, end_char). The 3-element form is same-line shorthand:
    [line, startChar, endChar]."""
    if len(range_values) == 4:
        start_line, start_char, end_line, end_char = range_values
    elif len(range_values) == 3:
        start_line, start_char, end_char = range_values
        end_line = start_line
    else:
        raise OccurrenceDecodeError(f"unexpected range length {len(range_values)}: {range_values}")

    if start_line < 0 or start_char < 0 or end_line < 0 or end_char < 0:
        raise OccurrenceDecodeError(f"negative position in range: {range_values}")

    return start_line, start_char, end_line, end_char


@dataclass(frozen=True)
class SymbolPackage:
    """The `<manager> <name> <version>` triple carried by a non-local SCIP
    symbol string — see `parse_symbol_package` below for the grammar and the
    real-index evidence this is built against (phase-04's Spike Result)."""

    manager: str
    name: str
    version: str


# SCIP symbols for a package-less/ambient context (e.g. TypeScript's global
# `lib.dom.d.ts` typings, which ship with the compiler, not a package.json)
# use "." as a placeholder for both name and version — verified in the real
# luz_next index (e.g. ``scip-typescript npm . . `index.d.ts`/...``). These
# carry no real package identity and must not become a package-graph node.
_UNSPECIFIED_PACKAGE_TOKEN = "."


def _split_backtick_aware_tokens(symbol: str, max_tokens: int) -> list[str]:
    """Splits the leading ``max_tokens`` space-separated components off a SCIP
    symbol string, honoring the SCIP symbol grammar's rule that any component
    containing a literal space must be wrapped in a matching pair of
    backticks (e.g. `` `some file.ts` ``). Stops after ``max_tokens`` are
    collected and ignores everything after — callers that only need the
    package prefix (scheme/manager/name/version) never need to parse the
    remaining descriptor, which may itself contain nested backtick groups and
    ``/`` separators.

    Returns fewer than ``max_tokens`` entries if the string is exhausted
    first (caller must check the length before use).
    """
    tokens: list[str] = []
    i = 0
    n = len(symbol)
    while len(tokens) < max_tokens and i < n:
        while i < n and symbol[i] == " ":
            i += 1
        if i >= n:
            break
        if symbol[i] == "`":
            end = symbol.find("`", i + 1)
            if end == -1:
                # Malformed (no closing backtick) — treat the remainder as a
                # single final token rather than raising; this is a parsing
                # convenience for a graph-population sweep, not a decode
                # path with a fail-closed contract like decode_occurrences.
                tokens.append(symbol[i:])
                break
            tokens.append(symbol[i + 1 : end])
            i = end + 1
        else:
            start = i
            while i < n and symbol[i] != " ":
                i += 1
            tokens.append(symbol[start:i])
    return tokens


def parse_symbol_package(symbol: str) -> SymbolPackage | None:
    """Extracts the package identity (`manager`, `name`, `version`) from a
    `global_symbols.symbol` string, or `None` if the symbol carries no real
    package identity.

    Real SCIP symbols (verified against a real luz_next index, see
    phase-04-dependency-graph-foundation.md's "Spike Result" section) follow
    the generic, language-agnostic grammar `<scheme> <manager> <name>
    <version> <descriptor>` — e.g.
    ``scip-typescript npm @dnd-kit/core 6.3.1 dist/`index.d.ts`/...``. This
    is documented as part of the SCIP protocol itself (not a
    scip-typescript-specific convention), so the same 4-token prefix applies
    to scip-java's `maven` and scip-python's `pip` symbols too — not
    independently verified against a real Java/Python index in this
    environment (only a TypeScript index was available to inspect), a
    documented limitation, not an assumption papered over.

    Returns `None` for:
    - SCIP-local symbols (``local <n>``, function-local variables) — never
      inserted into `global_symbols`/`mentions` by the real converter (see
      this module's top docstring), but guarded here defensively anyway.
    - Symbols with fewer than 4 space-separated leading tokens (malformed or
      unrecognized scheme).
    - Symbols whose package name is the "unspecified" placeholder (`.`) —
      an ambient/no-manifest context (e.g. TypeScript's built-in `lib.*.d.ts`
      typings), not a real package dependency edge.
    """
    if symbol.startswith("local "):
        return None
    tokens = _split_backtick_aware_tokens(symbol, max_tokens=4)
    if len(tokens) < 4:
        return None
    _scheme, manager, name, version = tokens
    if name == _UNSPECIFIED_PACKAGE_TOKEN:
        return None
    return SymbolPackage(manager=manager, name=name, version=version)


def kind_name(kind: int | None) -> str | None:
    """Map a `SymbolInformation.Kind` int to its name, `None` for unknown
    ints or a `None` input (global_symbols.kind is NULL in luz_next today —
    an honest gap in the converter's output, not a decode failure)."""
    if kind is None:
        return None
    try:
        return scip_pb2.SymbolInformation.Kind.Name(kind)
    except ValueError:
        return None
