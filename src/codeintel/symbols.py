"""SCIP symbol-string parsing and bare-name resolution.

The only module that knows the SCIP *symbol string* grammar, the same way
`scip_decoder.py` is the only module that knows the SCIP protobuf wire
format — a grammar change lands here and nowhere else.

Grammar (scip.proto, `Symbol`):

    symbol         ::= scheme ' ' manager ' ' package ' ' version ' ' descriptors
    descriptor     ::= namespace | type | term | method
                     | type-parameter | parameter | meta
    namespace      ::= name '/'
    type           ::= name '#'
    term           ::= name '.'
    method         ::= name '(' disambiguator ')' '.'
    parameter      ::= '(' name ')'
    type-parameter ::= '[' name ']'
    meta           ::= name ':'
    name           ::= identifier | '`' escaped '`'

A backtick-escaped name may contain any character; a doubled backtick is a
literal backtick. `local <id>` symbols carry no descriptors and are not
parseable.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from enum import StrEnum


class DescriptorKind(StrEnum):
    NAMESPACE = "NAMESPACE"
    TYPE = "TYPE"
    TERM = "TERM"
    METHOD = "METHOD"
    PARAMETER = "PARAMETER"
    TYPE_PARAMETER = "TYPE_PARAMETER"
    META = "META"


@dataclass(frozen=True)
class ParsedSymbol:
    package: str              # SCIP package field
    name: str                 # leaf descriptor name
    kind: DescriptorKind      # leaf descriptor kind
    parents: tuple[str, ...]  # enclosing descriptor names, outermost first

    @property
    def dotted_path(self) -> str:
        """Package first, then enclosing descriptors, then the leaf.

        The package is load-bearing, not decoration: scip-swift and
        scip-typescript both emit symbols whose descriptors are byte-identical
        across modules (measured: 1,403 such paths in one Swift repo, and
        `index.d.ts` once per npm package). Nothing inside the descriptors can
        separate those, so the package has to be part of the path.
        """
        return ".".join((self.package,) + self.parents + (self.name,))


_IDENTIFIER_CHARS = frozenset(string.ascii_letters + string.digits + "_+-$")

# Single-character suffixes that terminate a descriptor. '(' is absent on
# purpose: it means "method", which needs its disambiguator consumed first.
_SUFFIX_KINDS = {
    "/": DescriptorKind.NAMESPACE,
    "#": DescriptorKind.TYPE,
    ".": DescriptorKind.TERM,
    ":": DescriptorKind.META,
}


def _read_name(text: str, pos: int) -> tuple[str, int] | None:
    """Read one descriptor name starting at `pos`. Returns (name, next_pos),
    or None when no valid name starts there."""
    if pos < len(text) and text[pos] == "`":
        pos += 1
        chars: list[str] = []
        while pos < len(text):
            if text[pos] == "`":
                if pos + 1 < len(text) and text[pos + 1] == "`":
                    chars.append("`")  # doubled backtick: literal
                    pos += 2
                    continue
                return "".join(chars), pos + 1  # closing backtick
            chars.append(text[pos])
            pos += 1
        return None  # unterminated
    start = pos
    while pos < len(text) and text[pos] in _IDENTIFIER_CHARS:
        pos += 1
    return (text[start:pos], pos) if pos > start else None


def _parse_descriptors(tail: str) -> list[tuple[str, DescriptorKind]] | None:
    """Parse the descriptor sequence. Returns None on any malformed input —
    a bad symbol in an index must degrade, never raise."""
    out: list[tuple[str, DescriptorKind]] = []
    pos = 0
    while pos < len(tail):
        opener = tail[pos]

        # A descriptor starting with '(' or '[' is a parameter or type
        # parameter: bracketed, with no preceding name.
        if opener in "([":
            closer = ")" if opener == "(" else "]"
            kind = (
                DescriptorKind.PARAMETER
                if opener == "("
                else DescriptorKind.TYPE_PARAMETER
            )
            read = _read_name(tail, pos + 1)
            if read is None:
                return None
            name, pos = read
            if pos >= len(tail) or tail[pos] != closer:
                return None
            out.append((name, kind))
            pos += 1
            continue

        read = _read_name(tail, pos)
        if read is None:
            return None
        name, pos = read
        if pos >= len(tail):
            return None  # name with no suffix
        if tail[pos] == "(":
            # method: name '(' disambiguator ')' '.'
            close = tail.find(")", pos)
            if close == -1:
                return None
            pos = close + 1
            if pos >= len(tail) or tail[pos] != ".":
                return None
            out.append((name, DescriptorKind.METHOD))
            pos += 1
            continue
        kind = _SUFFIX_KINDS.get(tail[pos])
        if kind is None:
            return None
        out.append((name, kind))
        pos += 1
    return out or None


def parse_symbol(symbol: str) -> ParsedSymbol | None:
    """Parse a SCIP symbol string. Returns None for `local <id>` symbols and
    for anything malformed — never raises."""
    fields = symbol.split(" ", 4)
    if len(fields) < 5:
        return None  # 'local 0', truncated, or not a SCIP symbol
    descriptors = _parse_descriptors(fields[4])
    if descriptors is None:
        return None
    name, kind = descriptors[-1]
    return ParsedSymbol(
        package=fields[2],
        name=name,
        kind=kind,
        parents=tuple(n for n, _ in descriptors[:-1]),
    )
