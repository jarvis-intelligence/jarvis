"""NL query -> ranked SCIP symbol-definition hits.

The third semanticSearch signal (spec:
docs/superpowers/specs/2026-08-05-scip-boosted-retrieval-design.md).
Composes symbols.name_map() with the defn_enclosing_ranges location join.
Best-effort by contract: callers treat any failure as "signal absent" —
nothing in this module is allowed to break a search that would have
succeeded without it.
"""

from __future__ import annotations

import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass

from jarvis import symbols
from jarvis.symbols import Candidate, DescriptorKind

SYMBOL_TOP_K = 10

# English function words that appear in NL queries but never name symbols.
# Deliberately small: a false stopword hides a real identifier, a missed
# one only adds a harmless empty bucket lookup.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "what", "where", "when",
    "why", "who", "which", "from", "into", "are", "was", "can", "not",
    "all", "any", "its", "has", "have", "how", "does", "you", "your",
})

# Mirrors symbols._IDENTIFIER_CHARS plus '.' so dotted paths survive whole.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.$+-]+")

_MIN_TOKEN_LEN = 3


def extract_tokens(query: str) -> list[str]:
    """Lowercased identifier-ish tokens, deduped preserving order.

    Tokens shorter than _MIN_TOKEN_LEN or in _STOPWORDS are dropped —
    they only produce noise buckets. Dotted tokens are kept whole;
    the dotted-path rung of matching handles them.
    """
    out: dict[str, None] = {}
    for raw in _TOKEN_RE.findall(query):
        token = raw.strip(".").lower()
        if len(token) < _MIN_TOKEN_LEN or token in _STOPWORDS:
            continue
        out.setdefault(token)
    return list(out)


@dataclass(frozen=True)
class SymbolHit:
    """One symbol-definition location, ready for RRF fusion."""
    file_path: str
    start_line: int
    end_line: int
    dotted_path: str
    kind: DescriptorKind


# Lower kinds rank first on matched-token ties: types and functions are what
# people search for. Parameters/type-parameters never appear — the name map
# already excludes them (symbols._RESOLVABLE_KINDS).
_KIND_PRIORITY = {
    DescriptorKind.TYPE: 0,
    DescriptorKind.METHOD: 1,
    DescriptorKind.TERM: 2,
    DescriptorKind.NAMESPACE: 3,
    DescriptorKind.META: 4,
}

# How many ranked candidates to try resolving to a definition before giving
# up: a short common token can match hundreds of externals that all lack
# definition rows, and each miss costs one indexed query.
_CANDIDATE_SCAN_CAP = 200

# Bounded cache of lowercased name maps, keyed by db file path — the exact
# pattern of symbols._name_maps, and safe for the same reason: published
# index dbs are immutable, a reindex publishes under a new path.
_LOWER_MAP_CACHE_MAX_SIZE = 64
_lower_maps: OrderedDict[str, dict[str, list[Candidate]]] = OrderedDict()


def _db_path(conn: sqlite3.Connection) -> str:
    """Mirrors symbols._db_path: "" (never cached) for in-memory dbs."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return row[2] if row is not None else ""


def _build_lower_map(conn: sqlite3.Connection) -> dict[str, list[Candidate]]:
    lowered: dict[str, list[Candidate]] = {}
    for name, candidates in symbols.name_map(conn).items():
        lowered.setdefault(name.lower(), []).extend(candidates)
    return lowered


def _lower_map(conn: sqlite3.Connection) -> dict[str, list[Candidate]]:
    path = _db_path(conn)
    if not path:
        return _build_lower_map(conn)
    cached = _lower_maps.get(path)
    if cached is not None:
        _lower_maps.move_to_end(path)
        return cached
    cached = _lower_maps[path] = _build_lower_map(conn)
    _lower_maps.move_to_end(path)
    while len(_lower_maps) > _LOWER_MAP_CACHE_MAX_SIZE:
        _lower_maps.popitem(last=False)
    return cached


def _ranked_candidates(
    lower_map: dict[str, list[Candidate]], tokens: list[str]
) -> list[Candidate]:
    """Match tokens against the map; rank by matched-token count desc, then
    kind priority, then shorter dotted path (ties broken lexically for
    determinism). A bigram match consumes two query tokens and counts as 2.

    The dotted-token rung calls symbols.dotted_suffix_matches() rather than
    reimplementing the rule — lower_map's keys and values are already
    lowercased (_build_lower_map), so case_sensitive=False against it is
    exactly the case-insensitive version of resolve()'s rung-2 rule."""
    scores: dict[Candidate, int] = {}

    def _add(candidates: list[Candidate], weight: int) -> None:
        for candidate in candidates:
            scores[candidate] = scores.get(candidate, 0) + weight

    for token in tokens:
        if "." in token:
            _add(symbols.dotted_suffix_matches(lower_map, token, case_sensitive=False), 1)
        else:
            _add(lower_map.get(token, []), 1)
    for first, second in zip(tokens, tokens[1:]):
        if "." in first or "." in second:
            continue
        _add(lower_map.get(first + second, []), 2)

    return sorted(
        scores,
        key=lambda c: (-scores[c], _KIND_PRIORITY.get(c.kind, 99),
                       len(c.dotted_path), c.dotted_path),
    )


_DEFINITION_SQL = (
    "SELECT d.relative_path, r.start_line, r.end_line "
    "FROM defn_enclosing_ranges r "
    "JOIN global_symbols s ON s.id = r.symbol_id "
    "JOIN documents d ON d.id = r.document_id "
    "WHERE s.symbol = ? ORDER BY r.id LIMIT 1"
)


def search_symbols(conn: sqlite3.Connection, query: str,
                   limit: int = SYMBOL_TOP_K) -> list[SymbolHit]:
    """Ranked symbol-definition hits for an NL query.

    Candidates without a definition range (externals, stdlib references,
    `partial` indexes) are silently dropped — for a `partial` index every
    candidate drops and the signal is correctly empty, no special case.
    """
    tokens = extract_tokens(query)
    if not tokens:
        return []
    hits: list[SymbolHit] = []
    for candidate in _ranked_candidates(_lower_map(conn), tokens)[:_CANDIDATE_SCAN_CAP]:
        row = conn.execute(_DEFINITION_SQL, (candidate.symbol,)).fetchone()
        if row is None:
            continue
        # defn_enclosing_ranges stores 0-based SCIP line numbers; SymbolHit /
        # FusedHit use 1-based (matching chunker.py and Zoekt) -- convert
        # here, the one seam where SCIP coordinates cross into jarvis's
        # 1-based space.
        hits.append(SymbolHit(file_path=row[0], start_line=row[1] + 1, end_line=row[2] + 1,
                              dotted_path=candidate.dotted_path, kind=candidate.kind))
        if len(hits) >= limit:
            break
    return hits
