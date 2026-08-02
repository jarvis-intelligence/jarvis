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

import sqlite3
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


# ---------------------------------------------------------------------------
# Bare-name resolution
# ---------------------------------------------------------------------------

CANDIDATE_LIMIT = 10

# Parameters and type parameters are deliberately not resolution targets.
# They dominate name collisions (measured: tmp_path 205, self 79,
# monkeypatch 71 in this repo alone) and nobody navigates to them. Callers
# who genuinely want one pass the full SCIP symbol, which rung 1 honours.
_RESOLVABLE_KINDS = frozenset(
    {
        DescriptorKind.NAMESPACE,
        DescriptorKind.TYPE,
        DescriptorKind.TERM,
        DescriptorKind.METHOD,
        DescriptorKind.META,
    }
)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    dotted_path: str
    kind: DescriptorKind


class SymbolNotFoundError(Exception):
    """No symbol in the index matches the query."""


class AmbiguousSymbolError(Exception):
    """More than one symbol matches; the caller must qualify further."""

    def __init__(self, query: str, candidates: tuple[Candidate, ...], total: int) -> None:
        super().__init__(f"{query!r} is ambiguous ({total} matches)")
        self.query = query
        self.candidates = candidates
        self.total = total


# Keyed by db file path. Published indexes are immutable (`index-<sha>.db`,
# opened mode=ro&immutable=1), so an entry can never go stale and a reindex
# produces a new sha -> a new key. No invalidation, no TTL.
_name_maps: dict[str, dict[str, list[Candidate]]] = {}


def _db_path(conn: sqlite3.Connection) -> str:
    """Absolute path of the connection's main database, or "" for an
    in-memory or temporary db — which must never be cached, since distinct
    connections would collide on the same empty key."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return row[2] if row is not None else ""


def _build_name_map(conn: sqlite3.Connection) -> dict[str, list[Candidate]]:
    """Bucket every resolvable symbol by its leaf descriptor name. Cost is
    linear in symbol count — measured 20 ms for 17,422 symbols — and is paid
    once per index, not once per query."""
    buckets: dict[str, list[Candidate]] = {}
    for (symbol,) in conn.execute("SELECT symbol FROM global_symbols"):
        parsed = parse_symbol(symbol)
        if parsed is None or parsed.kind not in _RESOLVABLE_KINDS:
            continue
        buckets.setdefault(parsed.name, []).append(
            Candidate(symbol=symbol, dotted_path=parsed.dotted_path, kind=parsed.kind)
        )
    return buckets


def _name_map(conn: sqlite3.Connection) -> dict[str, list[Candidate]]:
    path = _db_path(conn)
    if not path:
        return _build_name_map(conn)
    cached = _name_maps.get(path)
    if cached is None:
        cached = _name_maps[path] = _build_name_map(conn)
    return cached


def _matches(name_map: dict[str, list[Candidate]], query: str) -> list[Candidate]:
    """Candidates whose dotted path equals `query` or ends with '.' + query.

    The leaf-name bucket is the fast path. It misses when the query's own
    last segment contains a dot — a backtick-escaped name like
    `greeter.ts`, which scip-typescript emits routinely — so fall back to a
    full scan rather than wrongly reporting not-found.
    """
    suffix = "." + query

    def hit(candidate: Candidate) -> bool:
        return candidate.dotted_path == query or candidate.dotted_path.endswith(suffix)

    bucketed = [c for c in name_map.get(query.rsplit(".", 1)[-1], ()) if hit(c)]
    if bucketed:
        return bucketed
    return [c for candidates in name_map.values() for c in candidates if hit(c)]


def _not_found_message(conn: sqlite3.Connection, query: str) -> str:
    """Distinguish "no such name" from "that name is only ever a parameter".
    Today both produce an identical empty result, which is the bug this
    whole module exists to remove. Only runs on the failure path."""
    suffix = "." + query
    for (symbol,) in conn.execute("SELECT symbol FROM global_symbols"):
        parsed = parse_symbol(symbol)
        if parsed is None or parsed.kind in _RESOLVABLE_KINDS:
            continue
        if parsed.dotted_path == query or parsed.dotted_path.endswith(suffix):
            return (
                f"{query!r} matches only parameters or type parameters, which are "
                "not resolution targets; pass the full SCIP symbol string to "
                "navigate to one"
            )
    return f"no symbol named {query!r} in this index"


def resolve(conn: sqlite3.Connection, query: str) -> str:
    """Resolve `query` to exactly one SCIP symbol string.

    Rung 1: verbatim passthrough — an already-full SCIP symbol returns
    unchanged via one indexed lookup, which also makes resolve() idempotent.
    Rung 2: dotted-suffix match on `'.'.join((package,) + parents + (name,))`.
    """
    row = conn.execute(
        "SELECT symbol FROM global_symbols WHERE symbol = ?", (query,)
    ).fetchone()
    if row is not None:
        return row[0]

    matches = _matches(_name_map(conn), query)
    if len(matches) == 1:
        return matches[0].symbol
    if not matches:
        raise SymbolNotFoundError(_not_found_message(conn, query))
    ordered = sorted(matches, key=lambda c: c.dotted_path)
    raise AmbiguousSymbolError(query, tuple(ordered[:CANDIDATE_LIMIT]), len(ordered))
