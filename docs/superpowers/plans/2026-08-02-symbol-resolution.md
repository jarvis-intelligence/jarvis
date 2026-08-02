# Symbol Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the SCIP nav tools accept a bare symbol name (`search_zoekt`) instead of only the full SCIP symbol string, and return structured candidates instead of a silent empty result when a name is ambiguous.

**Architecture:** A new `symbols.py` owns the SCIP symbol-string grammar (parsing) and bare-name resolution (a two-rung ladder: verbatim passthrough, then dotted-suffix match against a path-keyed cache). `QueryService` calls it in each symbol-taking method; `server.py` maps two new exceptions into the existing `{"error": ...}` shape and adds `candidates` / `resolvedSymbol` keys.

**Tech Stack:** Python 3.12, `uv`, `pytest` (markers: `integration`), stdlib `sqlite3`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-02-symbol-resolution-design.md`

## Global Constraints

- Modern type-hint syntax throughout: `str | None`, `list[T]`, `dict[K, V]`. Never `Optional`/`List`.
- Result types are frozen dataclasses (`@dataclass(frozen=True)`), not Pydantic.
- Direct `sqlite3`, no ORM, always parameterized queries.
- `from __future__ import annotations` at the top of every module.
- Test files mirror source modules 1:1 — `symbols.py` ↔ `tests/test_symbols.py`.
- Unit tests must not require external binaries. Integration tests get `@pytest.mark.integration`.
- Published indexes are immutable — opened `mode=ro&immutable=1`, never mutated. Do not add tables or columns.
- No new MCP tool. No changes to `searchCode`, `semanticSearch`, `blastRadius`, or the index pipeline.
- Commit messages: conventional commit format, no AI references.
- Run `uv run pytest -m "not integration"` before every commit.

---

### Task 1: SCIP symbol-string parser

**Files:**
- Create: `src/codeintel/symbols.py`
- Create: `tests/test_symbols.py`

**Interfaces:**
- Consumes: nothing (pure text, no DB, no other codeintel module).
- Produces:
  - `class DescriptorKind(StrEnum)` with members `NAMESPACE`, `TYPE`, `TERM`, `METHOD`, `PARAMETER`, `TYPE_PARAMETER`, `META`
  - `@dataclass(frozen=True) class ParsedSymbol` with `package: str`, `name: str`, `kind: DescriptorKind`, `parents: tuple[str, ...]`, and a `dotted_path: str` property returning `".".join((package,) + parents + (name,))`
  - `def parse_symbol(symbol: str) -> ParsedSymbol | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_symbols.py`:

```python
"""Tests for symbols.py — the SCIP symbol-string grammar parser and the
bare-name resolution ladder. Parser tests are pure text with no fixtures;
resolution tests build an in-memory global_symbols table."""

from __future__ import annotations

import pytest

from codeintel.symbols import DescriptorKind, ParsedSymbol, parse_symbol

# Real symbols captured from live indexes: Python (codeintel itself) and
# TypeScript (tests/fixtures/synthetic_index.py).
PY_METHOD = (
    "scip-python python codeintel-navigation-mcp 0.3.1 "
    "`codeintel.search`/search_zoekt()."
)
PY_PARAM = (
    "scip-python python codeintel-navigation-mcp 0.3.1 "
    "`codeintel.search`/search_zoekt().(base_url)"
)
PY_FIELD = (
    "scip-python python codeintel-navigation-mcp 0.3.1 "
    "`codeintel.search`/ZoektHit#repo."
)
TS_TYPE = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#"
TS_METHOD = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet()."


def test_parses_python_method():
    parsed = parse_symbol(PY_METHOD)
    assert parsed == ParsedSymbol(
        package="codeintel-navigation-mcp",
        name="search_zoekt",
        kind=DescriptorKind.METHOD,
        parents=("codeintel.search",),
    )


def test_backtick_escaped_name_containing_dots_is_one_parent():
    """`codeintel.search` is a single descriptor whose name contains dots —
    not two namespace descriptors. Splitting on '.' would get this wrong."""
    parsed = parse_symbol(PY_METHOD)
    assert parsed.parents == ("codeintel.search",)


def test_dotted_path_leads_with_the_package():
    """The package is the outermost segment. It is the only thing separating
    symbols whose descriptors are byte-identical across modules — measured at
    1,403 such dotted paths in one Swift repo before this was added."""
    parsed = parse_symbol(PY_METHOD)
    assert parsed.dotted_path == (
        "codeintel-navigation-mcp.codeintel.search.search_zoekt"
    )


def test_package_separates_identical_descriptors_across_modules():
    swift_a = "scip-swift xcodebuild epost_comp_showcase_sdk . `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`."
    swift_b = "scip-swift xcodebuild ios_theme_ui . `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`."
    a, b = parse_symbol(swift_a), parse_symbol(swift_b)
    assert a.name == b.name and a.parents == b.parents  # descriptors identical
    assert a.dotted_path != b.dotted_path               # package separates them


def test_parses_parameter_descriptor():
    parsed = parse_symbol(PY_PARAM)
    assert parsed.name == "base_url"
    assert parsed.kind is DescriptorKind.PARAMETER
    assert parsed.parents == ("codeintel.search", "search_zoekt")


def test_parses_term_descriptor():
    parsed = parse_symbol(PY_FIELD)
    assert parsed.name == "repo"
    assert parsed.kind is DescriptorKind.TERM
    assert parsed.parents == ("codeintel.search", "ZoektHit")


def test_parses_typescript_type():
    parsed = parse_symbol(TS_TYPE)
    assert parsed.name == "Greeter"
    assert parsed.kind is DescriptorKind.TYPE
    assert parsed.parents == ("src", "greeter.ts")


def test_parses_typescript_method_nested_under_type():
    parsed = parse_symbol(TS_METHOD)
    assert parsed.name == "greet"
    assert parsed.kind is DescriptorKind.METHOD
    assert parsed.dotted_path == "@toy/pkg.src.greeter.ts.Greeter.greet"


def test_parses_type_parameter():
    parsed = parse_symbol("scip-java maven com.example 1.0 com/example/Box#[T]")
    assert parsed.name == "T"
    assert parsed.kind is DescriptorKind.TYPE_PARAMETER


def test_parses_meta_descriptor():
    parsed = parse_symbol("scip-python python pkg 1.0 `mod`/thing:")
    assert parsed.name == "thing"
    assert parsed.kind is DescriptorKind.META


def test_parses_method_with_disambiguator():
    parsed = parse_symbol("scip-java maven com.example 1.0 com/example/C#m(+1).")
    assert parsed.name == "m"
    assert parsed.kind is DescriptorKind.METHOD


def test_doubled_backtick_is_a_literal_backtick():
    parsed = parse_symbol("scip-python python pkg 1.0 `we``ird`/x.")
    assert parsed.parents == ("we`ird",)
    assert parsed.name == "x"


def test_local_symbol_returns_none():
    assert parse_symbol("local 0") is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "too few fields",
        "scip-python python pkg 1.0 ",              # empty descriptors
        "scip-python python pkg 1.0 noSuffix",      # descriptor with no suffix
        "scip-python python pkg 1.0 `unterminated", # unclosed backtick
        "scip-python python pkg 1.0 (unclosed",     # unclosed parameter
    ],
)
def test_malformed_returns_none_never_raises(bad: str):
    assert parse_symbol(bad) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_symbols.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeintel.symbols'`

- [ ] **Step 3: Write the parser**

Create `src/codeintel/symbols.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_symbols.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Confirm grammar coverage against every local index**

The spec's figures were already produced by this exact parser during planning (46,914 symbols,
0 unparsed, across Python/TypeScript/Swift). This step confirms your implementation reproduces
them — a mismatch means a transcription error, not a new discovery.

```bash
uv run python -c "
import collections, os, sqlite3
from codeintel.symbols import parse_symbol, DescriptorKind
skip = {DescriptorKind.PARAMETER, DescriptorKind.TYPE_PARAMETER}
base = os.path.expanduser('~/.codeintel/scip/_')
for slug in sorted(os.listdir(base)):
    pointer = os.path.join(base, slug, '_', 'current')
    if not os.path.exists(pointer):
        continue
    db = os.path.join(base, slug, '_', open(pointer).read().strip())
    conn = sqlite3.connect(f'file:{db}?mode=ro&immutable=1', uri=True)
    rows = [r[0] for r in conn.execute('SELECT symbol FROM global_symbols')]
    unparsed = 0
    by_name, by_path = collections.defaultdict(list), collections.Counter()
    for s in rows:
        p = parse_symbol(s)
        if p is None:
            unparsed += 1
            continue
        if p.kind in skip:
            continue
        by_name[p.name].append(p.dotted_path)
        by_path[p.dotted_path] += 1
    if not by_name:
        continue
    total = len(by_name)
    unique = sum(1 for v in by_name.values() if len(v) == 1)
    pkg_unique = sum(1 for c in by_path.values() if c == 1)
    print(f'{slug:28} rows={len(rows):6d} unparsed={unparsed:4d} '
          f'names={total:6d} bare={unique*100//total:3d}% '
          f'full-path={pkg_unique*100//len(by_path):3d}%')
"
```

Expected: `unparsed=0` for every repo, `bare` between 78% and 93%, `full-path` at 99% or 100%.

Any non-zero `unparsed` is a grammar gap — stop and investigate before continuing. If `full-path`
drops below 99%, the package is not being included in `dotted_path`.

- [ ] **Step 6: Commit**

```bash
git add src/codeintel/symbols.py tests/test_symbols.py docs/superpowers/specs/2026-08-02-symbol-resolution-design.md
git commit -m "feat(symbols): add SCIP symbol-string grammar parser"
```

---

### Task 2: Bare-name resolution ladder

**Files:**
- Modify: `src/codeintel/symbols.py` (append; do not change Task 1's code)
- Modify: `tests/test_symbols.py` (append)

**Interfaces:**
- Consumes: `parse_symbol`, `ParsedSymbol`, `DescriptorKind` from Task 1.
- Produces:
  - `@dataclass(frozen=True) class Candidate` with `symbol: str`, `dotted_path: str`, `kind: DescriptorKind`
  - `class SymbolNotFoundError(Exception)`
  - `class AmbiguousSymbolError(Exception)` with attributes `query: str`, `candidates: tuple[Candidate, ...]`, `total: int`
  - `def resolve(conn: sqlite3.Connection, query: str) -> str`
  - `CANDIDATE_LIMIT = 10`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_symbols.py`:

```python
import sqlite3

from codeintel.symbols import (
    AmbiguousSymbolError,
    CANDIDATE_LIMIT,
    SymbolNotFoundError,
    resolve,
)

TS_SAY_HI = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#sayHi()."
TS_ANIMAL_GREET = "scip-typescript npm @toy/pkg 0.0.1 src/`animal.ts`/Animal#greet()."


def _conn(*symbols: str) -> sqlite3.Connection:
    """In-memory global_symbols holding just the columns resolution reads."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE)")
    conn.executemany("INSERT INTO global_symbols (symbol) VALUES (?)", [(s,) for s in symbols])
    return conn


def test_full_scip_symbol_passes_through_verbatim():
    conn = _conn(TS_METHOD)
    assert resolve(conn, TS_METHOD) == TS_METHOD


def test_bare_leaf_name_resolves():
    conn = _conn(TS_METHOD, TS_TYPE)
    assert resolve(conn, "greet") == TS_METHOD


def test_parent_qualifier_disambiguates():
    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    assert resolve(conn, "Greeter.greet") == TS_METHOD
    assert resolve(conn, "Animal.greet") == TS_ANIMAL_GREET


def test_full_dotted_path_resolves():
    conn = _conn(TS_METHOD)
    assert resolve(conn, "@toy/pkg.src.greeter.ts.Greeter.greet") == TS_METHOD


def test_package_qualifier_disambiguates_identical_descriptors():
    """The Swift/TypeScript case parent qualification cannot touch: same
    descriptors, different module. Only the package separates them."""
    swift_a = "scip-swift xcodebuild epost_comp_showcase_sdk . `UIView(im)centerXAnchor`."
    swift_b = "scip-swift xcodebuild ios_theme_ui . `UIView(im)centerXAnchor`."
    conn = _conn(swift_a, swift_b)
    with pytest.raises(AmbiguousSymbolError):
        resolve(conn, "UIView(im)centerXAnchor")
    assert resolve(conn, "ios_theme_ui.UIView(im)centerXAnchor") == swift_b


def test_dot_boundary_prevents_partial_identifier_match():
    """'zoekt' must not match 'search_zoekt' — suffix matching requires a
    '.' boundary, otherwise every substring would resolve."""
    conn = _conn(PY_METHOD)
    with pytest.raises(SymbolNotFoundError):
        resolve(conn, "zoekt")


def test_name_containing_dots_resolves_via_full_scan_fallback():
    """A leaf descriptor name may itself contain dots — scip-typescript emits
    `greeter.ts` as one backtick-escaped namespace name. Under a naive
    rsplit('.') its bucket key is 'ts', which indexes nothing, so only the
    fallback scan makes the spec's suffix rule actually hold."""
    ts_namespace = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/"
    conn = _conn(ts_namespace, TS_TYPE)
    assert resolve(conn, "greeter.ts") == ts_namespace


def test_ambiguous_name_raises_with_sorted_candidates():
    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    with pytest.raises(AmbiguousSymbolError) as excinfo:
        resolve(conn, "greet")
    error = excinfo.value
    assert error.query == "greet"
    assert error.total == 2
    assert [c.dotted_path for c in error.candidates] == [
        "@toy/pkg.src.animal.ts.Animal.greet",
        "@toy/pkg.src.greeter.ts.Greeter.greet",
    ]


def test_candidates_are_capped_but_total_is_honest():
    symbols = [
        f"scip-typescript npm @toy/pkg 0.0.1 src/`m{i}.ts`/C#dup()."
        for i in range(CANDIDATE_LIMIT + 5)
    ]
    conn = _conn(*symbols)
    with pytest.raises(AmbiguousSymbolError) as excinfo:
        resolve(conn, "dup")
    assert len(excinfo.value.candidates) == CANDIDATE_LIMIT
    assert excinfo.value.total == CANDIDATE_LIMIT + 5


def test_parameters_are_not_resolution_targets():
    conn = _conn(PY_PARAM)
    with pytest.raises(SymbolNotFoundError) as excinfo:
        resolve(conn, "base_url")
    assert "parameter" in str(excinfo.value).lower()


def test_a_method_wins_over_a_same_named_parameter_elsewhere():
    """Exclusion drops parameters from the map; it must not shadow a real
    method that happens to share a parameter's name. `base_url` is both a
    parameter of search_zoekt and a method on ZoektLifecycle in this repo."""
    py_method_base_url = (
        "scip-python python codeintel-navigation-mcp 0.3.1 "
        "`codeintel.search`/ZoektLifecycle#base_url()."
    )
    conn = _conn(PY_PARAM, py_method_base_url)
    assert resolve(conn, "base_url") == py_method_base_url


def test_parameter_still_reachable_by_full_symbol():
    conn = _conn(PY_PARAM)
    assert resolve(conn, PY_PARAM) == PY_PARAM


def test_unknown_name_message_says_not_found():
    conn = _conn(TS_TYPE)
    with pytest.raises(SymbolNotFoundError) as excinfo:
        resolve(conn, "NoSuchThing")
    assert "no symbol named" in str(excinfo.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_symbols.py -v -k "resolve or ambiguous or parameter or boundary or passthrough or qualifier or candidates or unknown"`
Expected: FAIL — `ImportError: cannot import name 'resolve' from 'codeintel.symbols'`

- [ ] **Step 3: Append the resolver**

Append to `src/codeintel/symbols.py`:

```python
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
```

Add `import sqlite3` to the module's import block (after `import string`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_symbols.py -v`
Expected: PASS (all tests, Task 1's included)

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/symbols.py tests/test_symbols.py
git commit -m "feat(symbols): resolve bare names to SCIP symbols"
```

---

### Task 3: Wire resolution into QueryService

**Files:**
- Modify: `src/codeintel/query.py` (imports; `QueryService` methods at lines 337, 346, 405, 413)
- Modify: `tests/test_query.py` (append)

**Interfaces:**
- Consumes: `resolve`, `AmbiguousSymbolError`, `SymbolNotFoundError` from Task 2.
- Produces: `QueryService.resolve_symbol(repo: str, symbol: str) -> str` — public, used by `server.py` in Task 4 to learn the resolved value.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query.py`:

```python
from codeintel.symbols import AmbiguousSymbolError, SymbolNotFoundError


def test_get_definitions_accepts_a_bare_name(query_service: QueryService):
    """The regression this whole feature exists for: a bare name used to
    return [] with no error."""
    locations, _ = query_service.get_definitions(REPO, "Greeter")
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER


def test_get_definitions_still_accepts_a_full_scip_symbol(query_service: QueryService):
    locations, _ = query_service.get_definitions(REPO, CLASS_SYMBOL)
    assert len(locations) == 1
    assert locations[0].path == DOC_GREETER


def test_find_references_accepts_a_bare_name(query_service: QueryService):
    by_bare, _ = query_service.find_references(REPO, "greet")
    by_full, _ = query_service.find_references(REPO, METHOD_SYMBOL)
    assert by_bare == by_full
    assert by_bare


def test_call_hierarchy_accepts_a_bare_name(query_service: QueryService):
    bare_in, bare_out, _ = query_service.call_hierarchy(REPO, "greet")
    full_in, full_out, _ = query_service.call_hierarchy(REPO, METHOD_SYMBOL)
    assert bare_in == full_in
    assert bare_out == full_out


def test_unknown_name_raises_instead_of_returning_empty(query_service: QueryService):
    with pytest.raises(SymbolNotFoundError):
        query_service.get_definitions(REPO, "NoSuchSymbol")


def test_resolve_symbol_returns_the_full_scip_string(query_service: QueryService):
    assert query_service.resolve_symbol(REPO, "Greeter") == CLASS_SYMBOL


def test_resolve_symbol_is_idempotent(query_service: QueryService):
    """server.py resolves, then the nav method resolves the result again.
    Rung 1 makes the second pass a no-op."""
    once = query_service.resolve_symbol(REPO, "Greeter")
    assert query_service.resolve_symbol(REPO, once) == once


def test_type_hierarchy_reports_unavailable_before_resolving(query_service: QueryService):
    """The fixture carries no relationships data, so typeHierarchy cannot
    answer. That explanation is more useful than a resolution error, so the
    availability check must run first — even for a nonsense symbol."""
    supertypes, subtypes, _, available = query_service.type_hierarchy(REPO, "NoSuchSymbol")
    assert available is False
    assert supertypes == []
    assert subtypes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query.py -v -k "bare_name or resolve_symbol or unknown_name or unavailable_before"`
Expected: FAIL — `SymbolNotFoundError` not raised (bare names currently return `[]`), and `AttributeError: 'QueryService' object has no attribute 'resolve_symbol'`

- [ ] **Step 3: Wire resolution in**

In `src/codeintel/query.py`, add to the import block:

```python
from codeintel import symbols
```

Add the helper and public method to `QueryService`, directly after `__init__`:

```python
    def _resolved(self, conn: sqlite3.Connection, symbol: str) -> str:
        """Accept either a bare name or a full SCIP symbol. Explicit at each
        call site rather than a decorator: this codebase is consistently
        explicit, and hiding resolution would make misfires hard to trace."""
        return symbols.resolve(conn, symbol)

    def resolve_symbol(self, repo: str, symbol: str) -> str:
        """Public resolution for callers that need the canonical symbol
        string itself — `server.py` reports it as `resolvedSymbol`."""
        conn, _ = get_connection(self._cache, repo)
        return self._resolved(conn, symbol)
```

In `get_definitions` (line 337), `find_references` (line 346), and `call_hierarchy` (line 405), insert one line immediately after the `get_connection` call:

```python
        conn, metadata = get_connection(self._cache, repo)
        symbol = self._resolved(conn, symbol)
```

In `type_hierarchy` (line 413), resolve **after** the availability check:

```python
        conn, metadata = get_connection(self._cache, repo)
        available = relationship_data_present(conn)
        if not available:
            return [], [], _freshness_snapshot(metadata), False
        symbol = self._resolved(conn, symbol)          # after the check
        supertypes = _type_hierarchy_supertypes(conn, symbol)
```

Ordering rationale: `relationship_data_present` is False on every real index, so the tool returns its explicit "cannot answer" explanation. Resolving first would replace that accurate message with an error about a symbol whose hierarchy is unanswerable regardless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_query.py -v`
Expected: PASS (all tests, including the pre-existing ones — they pass full SCIP symbols, which rung 1 returns unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/query.py tests/test_query.py
git commit -m "feat(query): accept bare symbol names in nav methods"
```

---

### Task 4: Error contract and resolvedSymbol in server.py

**Files:**
- Modify: `src/codeintel/server.py` (`_error_payload` at line 82; tools at lines 109, 120, 131, 147)
- Modify: `tests/test_server_tools.py` (append)

**Interfaces:**
- Consumes: `AmbiguousSymbolError` from Task 2; `QueryService.resolve_symbol` from Task 3.
- Produces: no new Python API — only the JSON tool contract (`candidates`, `candidateTotal`, `resolvedSymbol`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_tools.py`:

```python
from codeintel.symbols import AmbiguousSymbolError, Candidate, DescriptorKind


def test_error_payload_renders_ambiguous_candidates(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    candidates = (
        Candidate(symbol="sym-a", dotted_path="a.C.dup", kind=DescriptorKind.METHOD),
        Candidate(symbol="sym-b", dotted_path="b.D.dup", kind=DescriptorKind.METHOD),
    )
    payload = server._error_payload(REPO, AmbiguousSymbolError("dup", candidates, 7))
    assert payload["candidateTotal"] == 7
    assert payload["candidates"] == [
        {"symbol": "sym-a", "dottedPath": "a.C.dup", "kind": "METHOD"},
        {"symbol": "sym-b", "dottedPath": "b.D.dup", "kind": "METHOD"},
    ]
    assert "ambiguous" in payload["error"]
    assert "a.C.dup" in payload["error"]  # leads with the qualifier hint


def test_go_to_definition_reports_resolved_symbol_for_a_bare_name():
    result = server.go_to_definition(REPO, "Greeter")
    assert result["symbol"] == "Greeter"
    assert result["resolvedSymbol"] == CLASS_SYMBOL
    assert result["definitions"]


def test_go_to_definition_omits_resolved_symbol_when_input_was_already_full():
    result = server.go_to_definition(REPO, CLASS_SYMBOL)
    assert "resolvedSymbol" not in result
    assert result["definitions"]


def test_find_references_reports_resolved_symbol_for_a_bare_name():
    result = server.find_references(REPO, "greet")
    assert result["resolvedSymbol"] == METHOD_SYMBOL


def test_unknown_symbol_returns_error_not_empty_references():
    result = server.find_references(REPO, "NoSuchSymbol")
    assert "no symbol named" in result["error"]
    assert "references" not in result
```

Add `CLASS_SYMBOL` and `METHOD_SYMBOL` to the existing `tests.fixtures.synthetic_index` import at the top of the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_tools.py -v -k "ambiguous or resolved_symbol or unknown_symbol"`
Expected: FAIL — `KeyError: 'candidateTotal'` and `KeyError: 'resolvedSymbol'`

- [ ] **Step 3: Add the error branch and resolvedSymbol**

In `src/codeintel/server.py`, add to the import block:

```python
from codeintel.symbols import AmbiguousSymbolError
```

In `_error_payload` (line 82), add a branch before the final `return`:

```python
    if isinstance(exc, AmbiguousSymbolError):
        hint = exc.candidates[0].dotted_path if exc.candidates else exc.query
        return {
            "error": (
                f"{exc.query!r} is ambiguous in {repo} ({exc.total} matches). "
                f"Retry with a qualifier, e.g. {hint!r}."
            ),
            # A structured list, not prose inside `error`, so the caller can
            # act on it without parsing English.
            "candidates": [
                {"symbol": c.symbol, "dottedPath": c.dotted_path, "kind": str(c.kind)}
                for c in exc.candidates
            ],
            "candidateTotal": exc.total,
        }
```

Add a module-level helper next to `_error_payload`:

```python
def _resolved_fields(symbol: str, resolved: str) -> dict[str, Any]:
    """`resolvedSymbol` appears only when resolution changed the input, so
    callers passing full SCIP symbols see an unchanged response shape."""
    return {} if resolved == symbol else {"resolvedSymbol": resolved}
```

Then in each of the four symbol-taking tools, resolve first and merge the field. `goToDefinition` becomes:

```python
@mcp.tool(name="goToDefinition")
def go_to_definition(repo: str, symbol: str) -> dict[str, Any]:
    """Resolve `symbol`'s definition location(s) within `repo`. `symbol` may
    be a bare name (`Greeter`), a qualified name (`Greeter.greet`), or a full
    SCIP symbol string."""
    try:
        resolved = _service().resolve_symbol(repo, symbol)
        locations, freshness = _service().get_definitions(repo, resolved)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return _error_payload(repo, exc)
    return {
        "symbol": symbol,
        **_resolved_fields(symbol, resolved),
        "definitions": [_json_safe(asdict(loc)) for loc in locations],
        **_freshness_fields(freshness),
    }
```

Apply the same three changes to `find_references` (line 120), `call_hierarchy` (line 131), and `type_hierarchy` (line 147): call `resolve_symbol` first, pass `resolved` to the service method, merge `_resolved_fields(symbol, resolved)` into the response.

For `type_hierarchy` only, resolution must not pre-empt the unavailable message. Keep the existing early-return path intact by resolving inside the same `try` but tolerating failure:

```python
    try:
        resolved = symbol
        supertypes, subtypes, freshness, available = _service().type_hierarchy(repo, symbol)
        if available:
            resolved = _service().resolve_symbol(repo, symbol)
    except Exception as exc:
        return _error_payload(repo, exc)
```

Update the three other tool docstrings the same way as `goToDefinition`'s, so the accepted forms are discoverable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_tools.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full unit suite**

Run: `uv run pytest -m "not integration"`
Expected: PASS — no regressions in the other 17 test modules.

- [ ] **Step 6: Commit**

```bash
git add src/codeintel/server.py tests/test_server_tools.py
git commit -m "feat(server): return symbol candidates and resolvedSymbol"
```

---

### Task 5: Backfill documentSymbols displayName and kind

**Files:**
- Modify: `src/codeintel/query.py` (`_symbol_display_and_kind` at line 172, `_symbol_info` at line 179, `get_document_symbols` at lines 370-379 and 386-400)
- Modify: `tests/test_query.py` (append)

**Interfaces:**
- Consumes: `parse_symbol`, `ParsedSymbol` from Task 1.
- Produces: `_display_and_kind(symbol: str, display_name: str | None, kind: int | None) -> tuple[str | None, str | None]` — module-private in `query.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query.py`:

```python
def test_document_symbols_populate_display_name_and_kind(query_service: QueryService):
    """scip expt-convert leaves global_symbols.display_name and .kind NULL
    for every row, so these were always null before the parser fallback."""
    entries, _ = query_service.get_document_symbols(REPO, DOC_GREETER)
    by_symbol = {e.symbol: e for e in entries}
    greeter = by_symbol[CLASS_SYMBOL]
    assert greeter.displayName == "Greeter"
    assert greeter.kind == "TYPE"
    method = by_symbol[METHOD_SYMBOL]
    assert method.displayName == "greet"
    assert method.kind == "METHOD"


def test_display_name_prefers_a_populated_column_over_the_parser():
    """Self-healing: if a future converter starts populating the real
    columns, they win over the syntax-derived fallback."""
    from codeintel.query import _display_and_kind

    display_name, kind = _display_and_kind(CLASS_SYMBOL, "FromColumn", 5)
    assert display_name == "FromColumn"
    assert kind != "TYPE"  # kind_name(5) came from the column, not the parser


def test_display_and_kind_falls_back_for_unparseable_symbols():
    from codeintel.query import _display_and_kind

    assert _display_and_kind("local 0", None, None) == (None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query.py -v -k "display_name or display_and_kind"`
Expected: FAIL — `assert None == 'Greeter'`

- [ ] **Step 3: Add the fallback**

In `src/codeintel/query.py`, add after `_symbol_display_and_kind` (line 176):

```python
def _display_and_kind(
    symbol: str, display_name: str | None, kind: int | None
) -> tuple[str | None, str | None]:
    """Resolve a symbol's display name and kind, preferring the DB columns.

    `scip expt-convert` declares `global_symbols.display_name` and `.kind`
    but never populates either (measured: 0 of 2180 rows on a real index),
    so both are always NULL today and every documentSymbols response
    carried nulls. Falling back to the symbol string fixes that.

    The fallback kind is derived from *descriptor syntax* ('#' -> TYPE,
    '().' -> METHOD), not from the indexer's semantic classification, which
    is what the integer `kind` column was meant to carry. It is therefore
    not a SCIP `SymbolKind` value. Same self-healing property as
    `relationship_data_present`: a converter that starts populating the real
    columns immediately takes precedence, with no code change here.
    """
    column_kind = kind_name(kind)
    if display_name is not None and column_kind is not None:
        return display_name, column_kind
    parsed = symbols.parse_symbol(symbol)
    if parsed is None:
        return display_name, column_kind
    return display_name or parsed.name, column_kind or str(parsed.kind)
```

Rewrite `_symbol_info` (line 179) to use it:

```python
def _symbol_info(conn: sqlite3.Connection, symbol: str) -> SymbolInfo:
    display_name, kind = _symbol_display_and_kind(conn, symbol)
    display_name, kind_label = _display_and_kind(symbol, display_name, kind)
    return SymbolInfo(symbol=symbol, displayName=display_name, kind=kind_label)
```

In `get_document_symbols`, replace the two `DocumentSymbolEntry` constructions. The outline loop (line 370) becomes:

```python
        for symbol, display_name, kind, start_line, start_char, end_line, end_char in outline_rows:
            entry_name, entry_kind = _display_and_kind(symbol, display_name, kind)
            entries[symbol] = DocumentSymbolEntry(
                symbol=symbol,
                displayName=entry_name,
                kind=entry_kind,
                range=Range(
                    start=Position(line=start_line, character=start_char),
                    end=Position(line=end_line, character=end_char),
                ),
            )
```

And the occurrence loop (line 390) becomes:

```python
                display_name, kind = _symbol_display_and_kind(conn, occ.symbol)
                entry_name, entry_kind = _display_and_kind(occ.symbol, display_name, kind)
                start_line, start_char, end_line, end_char = scip_range_to_positions(occ.range)
                entries[occ.symbol] = DocumentSymbolEntry(
                    symbol=occ.symbol,
                    displayName=entry_name,
                    kind=entry_kind,
                    range=Range(
                        start=Position(line=start_line, character=start_char),
                        end=Position(line=end_line, character=end_char),
                    ),
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_query.py tests/test_server_tools.py -v`
Expected: PASS. If a pre-existing test asserted `displayName is None` or `kind is None`, update it — the nulls were the bug, and note the change in the commit body.

- [ ] **Step 5: Commit**

```bash
git add src/codeintel/query.py tests/test_query.py
git commit -m "fix(query): populate documentSymbols displayName and kind"
```

---

### Task 6: Integration test against a real index

**Files:**
- Modify: `tests/test_index_cli.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: nothing.

- [ ] **Step 1: Find the existing integration pattern**

Run: `uv run grep -n "pytest.mark.integration" -A15 tests/test_index_cli.py | head -60`

Reuse whatever fixture builds and publishes a real index (it shells out to a real language indexer plus `scip expt-convert`). Do not invent a new one.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_index_cli.py`, adapting the fixture name and slug to what Step 1 found:

```python
@pytest.mark.integration
def test_bare_name_resolution_against_a_real_index(indexed_repo):
    """End-to-end: a bare name resolves through a genuinely converted SCIP
    index, not a hand-built fixture. Guards against grammar assumptions that
    hold for synthetic symbols but not for real indexer output."""
    from codeintel import config
    from codeintel.query import QueryService

    slug, root = indexed_repo
    service = QueryService(config.new_connection_cache(root))

    entries, _ = service.get_document_symbols(slug, _any_indexed_path(entries_root=root, slug=slug))
    assert entries, "fixture repo produced no document symbols"
    target = entries[0]

    parsed_name = target.displayName
    assert parsed_name, "displayName should be populated from the parser"

    resolved = service.resolve_symbol(slug, parsed_name)
    assert resolved == target.symbol

    # Idempotence: rung 1 returns a full symbol unchanged.
    assert service.resolve_symbol(slug, resolved) == resolved
```

Replace the `_any_indexed_path(...)` call with however the existing integration tests obtain a known indexed file path — a module-level constant in the fixture, most likely. If the fixture exposes no path, pick one with:

```python
    conn, _ = config.get_connection(config.new_connection_cache(root), slug)
    path = conn.execute("SELECT relative_path FROM documents LIMIT 1").fetchone()[0]
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_index_cli.py -m integration -v -k bare_name`
Expected: PASS, or SKIP if the required binaries are absent from `PATH`. A skip is acceptable; a failure is not.

- [ ] **Step 4: Run the full suite both ways**

Run: `uv run pytest -m "not integration"`
Expected: PASS

Run: `uv run pytest -m integration`
Expected: PASS or clean skips.

- [ ] **Step 5: Verify the spec's acceptance criteria by hand**

Restart the MCP server first — it is currently down from an unrelated `searchCode` crash. Then confirm each criterion:

```bash
uv run codeintel reindex codeintel
```

Then via the MCP client (or `QueryService` directly):

1. `findReferences(repo="codeintel", symbol="search_zoekt")` → references, not `[]`
2. same call with the full SCIP string → identical result
3. `findReferences(repo="codeintel", symbol="__init__")` → `candidates` + `candidateTotal`
4. `findReferences(repo="codeintel", symbol="tmp_path")` → error naming the parameter rule.
   Do **not** use `base_url` here — it is a real method on `ZoektLifecycle` (`search.py:121`) and
   correctly resolves. Parameter-only names verified against the live index: `tmp_path`,
   `monkeypatch`, `self`, `timeout_seconds`, `include_prefixes`.
5. `documentSymbols` → non-null `displayName` / `kind`
6. Resolution survives a version bump — covered by criterion 1 passing after the reindex above, since the index now carries a different package version than the symbol strings quoted in the spec

- [ ] **Step 6: Commit**

```bash
git add tests/test_index_cli.py
git commit -m "test: verify bare-name resolution against a real index"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: parser → 1; ladder, parameter exclusion, cache → 2; `QueryService` wiring and `type_hierarchy` ordering → 3; error contract, `resolvedSymbol`, `resolve_symbol` → 3+4; `documentSymbols` backfill → 5; testing table and all six acceptance criteria → 6. The spec's grammar-coverage claim is re-confirmed by Task 1 Step 5.

**Two spec errors found and fixed while planning:**
1. The spec named `tests/test_server.py`; the file is `tests/test_server_tools.py`. Corrected in the spec.
2. The spec required a `resolvedSymbol` key but the nav methods return `(locations, freshness)` and `server.py` holds no connection, so it could not learn the resolved value. Added `QueryService.resolve_symbol` to the spec with the idempotence argument.

**Deviation from the spec worth flagging:** `_matches` adds a full-scan fallback the spec did not describe. The spec's rule is "match on the dotted path", but indexing by leaf name — needed for speed — misses queries whose last segment contains a dot, which scip-typescript emits routinely (`` `greeter.ts` ``). Without the fallback the implementation would be faster than the spec but wrong. Covered by `test_name_containing_dots_resolves_via_full_scan_fallback`.

**Type consistency.** `Candidate.dotted_path` (snake_case in Python) serializes as `dottedPath` in JSON, matching the existing camelCase tool contract (`displayName`, `lineNumber`). `DescriptorKind` is a `StrEnum`, so `str(c.kind)` yields `"METHOD"`. `_display_and_kind` returns `tuple[str | None, str | None]` — a *label*, distinct from `_symbol_display_and_kind`'s `tuple[str | None, int | None]` raw column values; the two names are deliberately different and used in that order.

**Known weak point.** Task 6 Step 2 cannot be written fully concretely because the integration fixture's name and shape are unknown until Step 1 inspects it. The step says so and gives a fallback query. This is the one place the plan asks the implementer to adapt rather than transcribe.

## Validation performed while planning

Tasks 1 and 2 were prototyped and run before this plan was finalised, so the code blocks in them are
executed code rather than sketches:

- **Parser: 17/17 grammar cases pass** — every descriptor kind, backtick names with embedded dots,
  doubled-backtick escape, `local 0`, and six malformed inputs returning `None`.
- **Grammar coverage: 46,914 real symbols across 4 indexes, 0 unparsed** (Python, TypeScript, Swift).
- **Resolver: 16/16 ladder cases pass** — passthrough, bare leaf, parent qualifier, package
  qualifier, full path, dot-boundary rejection, full-scan fallback, ambiguity ordering and cap,
  parameter exclusion, parameter-by-full-symbol, not-found messages.
- **Acceptance criteria 1, 2, 3, 6 verified against the live `codeintel` index.**

Three defects were found and fixed this way rather than at implementation time:

1. **The package field was missing from the design.** Parent qualification cannot separate symbols
   with byte-identical descriptors across modules, which is 1,403 dotted paths in one Swift repo.
   Bare-name resolution measured 78% in TypeScript, not the 93% the spec claimed. Folding the
   package in takes every measured repo to 99–100%. Spec and plan both updated.
2. **Acceptance criterion 4 used a wrong example.** `base_url` is a real method on `ZoektLifecycle`
   (`search.py:121`), so it resolves correctly and cannot demonstrate parameter exclusion.
   Replaced with `tmp_path`, verified parameter-only. A regression test now covers the case where a
   method and a parameter share a name.
3. **The leaf-bucket index needed a full-scan fallback.** Keying on `rsplit('.')` misses descriptor
   names that contain dots — `` `greeter.ts` ``, which scip-typescript emits routinely.

## Unresolved questions

None blocking. Deferred by choice, per the spec: a standalone `lookupSymbol` tool, and cross-repo resolution (blocked on the hardcoded `PROJECT="_"` / `BRANCH="_"` layout).
