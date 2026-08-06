# SCIP-Boosted Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third ranked signal — SCIP symbol-definition matches — to `semanticSearch`'s reciprocal rank fusion, so a query naming an identifier ranks that identifier's definition site above chunks that merely mention it.

**Architecture:** New `src/jarvis/symbol_search.py` turns an NL query into ranked `SymbolHit` definition locations by matching query tokens against the (cached) SCIP symbol name map and joining `defn_enclosing_ranges`. `semantic.py`'s existing RRF gains a third list; `server.py` passes an optional read-only SCIP connection. The signal is best-effort: any failure or absence degrades to today's two-signal behavior, byte-identical.

**Tech Stack:** Python 3.12+ (`uv`), stdlib `sqlite3` (parameterized, no ORM), frozen dataclasses, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-05-scip-boosted-retrieval-design.md`

## Global Constraints

- RRF formula and `RRF_K = 60` unchanged; symbol list enters **unweighted** (`1/(k+rank)`).
- `SYMBOL_TOP_K = 10`, defined in `symbol_search.py` (the module that applies it).
- Matching is **case-insensitive**; `symbols.resolve()` stays case-sensitive and untouched.
- Parameters/type-parameters excluded — candidates come from `symbols.py`'s name map, which already applies `_RESOLVABLE_KINDS`.
- Degradation contract: `scip_conn=None` or any exception inside the signal → results identical to today's two-signal output. The new code may only **add**.
- No MCP response-shape change: `sources` may now contain `"symbol"`; standalone symbol hits have `content=""` and `symbolName=dotted_path`.
- No index-time changes, no schema changes, no changes to `searchCode` or navigation tools.
- Style: `@dataclass(frozen=True)` results, modern type hints (`str | None`, `list[T]`), tests mirror source modules 1:1.
- Run tests with `uv run pytest tests/<file>.py -v` (unit tests need no external binaries).
- Out of scope (YAGNI, per spec): per-source RRF weights, trigrams, fuzzy/stemming, camelCase subtoken splitting, popularity signals.

---

### Task 1: Public accessors in `symbols.py` — name map + dotted-suffix matching

`symbol_search.py` needs two things `symbols.py` already builds privately: the leaf-name → candidates map (`_name_map()`, ~20 ms per 17K-symbol index, LRU-keyed by db path) and the dotted-suffix matching rule (`_matches()`) that `resolve()`'s rung 2 uses. Expose both via public accessors so no module reaches into another's privates. `_matches()` itself gains an optional `case_sensitive` flag — `resolve()`'s call keeps the default (`True`, unchanged behavior); the new public wrapper lets `symbol_search.py` request case-insensitive matching without duplicating the rule.

**Files:**
- Modify: `src/jarvis/symbols.py` (after `_name_map`, ~line 260)
- Test: `tests/test_symbols.py`

**Interfaces:**
- Consumes: existing `_name_map(conn)`, `_matches(name_map, query)`, `Candidate` (frozen dataclass: `symbol: str`, `dotted_path: str`, `kind: DescriptorKind`).
- Produces: `symbols.name_map(conn: sqlite3.Connection) -> dict[str, list[Candidate]]` and `symbols.dotted_suffix_matches(name_map: dict[str, list[Candidate]], query: str, *, case_sensitive: bool = True) -> list[Candidate]` — Task 3 calls both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_symbols.py` (constants `TS_METHOD`, `TS_TYPE`, `TS_ANIMAL_GREET` already exist at the top of the file; `_conn()` helper already exists):

```python
def test_public_name_map_buckets_by_leaf_name():
    """name_map() is the public accessor symbol_search composes with —
    same cached map resolve() uses, keyed by leaf descriptor name."""
    from jarvis.symbols import name_map

    conn = _conn(TS_METHOD, TS_TYPE)
    buckets = name_map(conn)
    assert {c.symbol for c in buckets["greet"]} == {TS_METHOD}
    assert {c.symbol for c in buckets["Greeter"]} == {TS_TYPE}


def test_dotted_suffix_matches_is_case_sensitive_by_default():
    """Default preserves resolve()'s existing rung-2 semantics exactly."""
    from jarvis.symbols import dotted_suffix_matches, name_map

    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    buckets = name_map(conn)
    assert {c.symbol for c in dotted_suffix_matches(buckets, "Greeter.greet")} == {TS_METHOD}
    assert dotted_suffix_matches(buckets, "greeter.greet") == []  # wrong case, no match


def test_dotted_suffix_matches_case_insensitive_when_requested():
    """symbol_search.py's use case: lowercased query against the map."""
    from jarvis.symbols import dotted_suffix_matches, name_map

    conn = _conn(TS_METHOD, TS_ANIMAL_GREET)
    buckets = name_map(conn)
    hits = dotted_suffix_matches(buckets, "greeter.greet", case_sensitive=False)
    assert {c.symbol for c in hits} == {TS_METHOD}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_symbols.py -v`
Expected: FAIL with `ImportError: cannot import name 'name_map'` (and `dotted_suffix_matches`)

- [ ] **Step 3: Write minimal implementation**

In `src/jarvis/symbols.py`, change the existing `_matches` function to accept the new flag (this is the ONLY change to its body — the default preserves `resolve()`'s current behavior exactly, so `resolve()`'s call site below needs no edit):

```python
def _matches(name_map: dict[str, list[Candidate]], query: str, *, case_sensitive: bool = True) -> list[Candidate]:
    """Candidates whose dotted path equals `query` or ends with '.' + query.

    The leaf-name bucket is the fast path. It misses when the query's own
    last segment contains a dot — a backtick-escaped name like
    `greeter.ts`, which scip-typescript emits routinely — so fall back to a
    full scan rather than wrongly reporting not-found.
    """
    suffix = "." + query

    def hit(candidate: Candidate) -> bool:
        path = candidate.dotted_path if case_sensitive else candidate.dotted_path.lower()
        return path == query or path.endswith(suffix)

    bucketed = [c for c in name_map.get(query.rsplit(".", 1)[-1], ()) if hit(c)]
    if bucketed:
        return bucketed
    return [c for candidates in name_map.values() for c in candidates if hit(c)]
```

Then, directly after `_name_map`, add both public accessors:

```python
def name_map(conn: sqlite3.Connection) -> dict[str, list[Candidate]]:
    """Public accessor for the cached leaf-name -> candidates map.

    Exists for symbol_search.py, which builds its lowercased view on top —
    a public seam instead of a cross-module private reach. Treat the
    returned map as read-only: it is the cache entry itself, not a copy.
    """
    return _name_map(conn)


def dotted_suffix_matches(
    name_map: dict[str, list[Candidate]], query: str, *, case_sensitive: bool = True
) -> list[Candidate]:
    """Public accessor for the dotted-suffix matching rule `_matches`
    implements. `resolve()` keeps calling `_matches` directly (unaffected,
    default `case_sensitive=True`); this wrapper exists so symbol_search.py's
    case-insensitive dotted-token rung reuses the one rule instead of
    reimplementing it.
    """
    return _matches(name_map, query, case_sensitive=case_sensitive)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_symbols.py -v`
Expected: all PASS (new tests plus every existing test, including `test_parent_qualifier_disambiguates` which exercises `_matches` via `resolve()`, untouched)

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/symbols.py tests/test_symbols.py
git commit -m "feat: expose public name_map and dotted_suffix_matches accessors in symbols"
```

---

### Task 2: Token extraction in `symbol_search.py`

The query-side half of matching: NL query → lowercased identifier-ish tokens, stopwords and short tokens dropped, dotted tokens kept whole.

**Files:**
- Create: `src/jarvis/symbol_search.py`
- Create: `tests/test_symbol_search.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure function).
- Produces: `extract_tokens(query: str) -> list[str]` (deduped, order-preserving) and module constants `SYMBOL_TOP_K = 10`, `_STOPWORDS` — Task 3 builds on these in this same module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_symbol_search.py`:

```python
"""Unit tests for symbol_search: token extraction, matching, ranking,
location resolution. In-memory SQLite fixtures, no external binaries."""

from __future__ import annotations

from jarvis.symbol_search import extract_tokens


def test_extract_tokens_lowercases_and_drops_noise():
    tokens = extract_tokens("How does the ZoektLifecycle spawn it?")
    assert tokens == ["zoektlifecycle", "spawn"]


def test_extract_tokens_keeps_dotted_tokens_whole():
    tokens = extract_tokens("where is semantic.SemanticStore defined")
    assert "semantic.semanticstore" in tokens


def test_extract_tokens_drops_short_and_stopword_tokens():
    assert extract_tokens("in a of it") == []


def test_extract_tokens_dedupes_preserving_order():
    assert extract_tokens("spawn spawn Spawn") == ["spawn"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_symbol_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.symbol_search'`

- [ ] **Step 3: Write minimal implementation**

Create `src/jarvis/symbol_search.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_symbol_search.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/symbol_search.py tests/test_symbol_search.py
git commit -m "feat: add query token extraction for symbol search"
```

---

### Task 3: Matching, ranking, and location resolution in `symbol_search.py`

The index-side half: lowercased name map (cached like `symbols._name_maps`), three matching rungs (verbatim token, adjacent-token bigram, dotted suffix), ranking (matched-token count desc → kind priority → shorter dotted path), then one `defn_enclosing_ranges` lookup per surviving candidate.

**Files:**
- Modify: `src/jarvis/symbol_search.py`
- Test: `tests/test_symbol_search.py`

**Interfaces:**
- Consumes: `symbols.name_map(conn)` and `symbols.dotted_suffix_matches(name_map, query, *, case_sensitive)` (both Task 1), `extract_tokens` (Task 2), `Candidate`, `DescriptorKind`.
- Produces: `SymbolHit` (frozen dataclass: `file_path: str`, `start_line: int`, `end_line: int`, `dotted_path: str`, `kind: DescriptorKind`) and `search_symbols(conn: sqlite3.Connection, query: str, limit: int = SYMBOL_TOP_K) -> list[SymbolHit]` — Tasks 4/5 consume both.

- [ ] **Step 1: Write the failing tests**

First, add `import sqlite3` to the existing import block at the top of `tests/test_symbol_search.py` (Task 2 didn't need it; this task's fixtures do). Then append to `tests/test_symbol_search.py`:

```python
from jarvis.symbol_search import SymbolHit, search_symbols

# Realistic scip-python symbol strings: leaf 'ZoektLifecycle' is a TYPE,
# 'spawn' a METHOD, 'render' exists as both TYPE and METHOD to test
# kind-priority, 'external_thing' has no definition row.
_PKG = "scip-python python mypkg 0.1 "
CLASS_SYM = _PKG + "search/ZoektLifecycle#"
METHOD_SYM = _PKG + "search/ZoektLifecycle#spawn()."
RENDER_TYPE_SYM = _PKG + "views/Render#"
RENDER_METHOD_SYM = _PKG + "widget/render()."
EXTERNAL_SYM = _PKG + "vendor/external_thing#"

_DDL = """
CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE);
CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE);
CREATE TABLE defn_enclosing_ranges (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL, start_line INTEGER NOT NULL, start_char INTEGER NOT NULL,
    end_line INTEGER NOT NULL, end_char INTEGER NOT NULL);
"""


def _index_conn(*symbols_with_defs: tuple[str, str, int, int],
                orphans: tuple[str, ...] = ()) -> sqlite3.Connection:
    """In-memory index db. Each entry is (symbol, path, start_line, end_line);
    `orphans` get a global_symbols row but no definition range."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    doc_ids: dict[str, int] = {}
    for i, (sym, path, start, end) in enumerate(symbols_with_defs, start=1):
        if path not in doc_ids:
            doc_ids[path] = len(doc_ids) + 1
            conn.execute("INSERT INTO documents (id, relative_path) VALUES (?, ?)",
                         (doc_ids[path], path))
        conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (?, ?)", (i, sym))
        conn.execute(
            "INSERT INTO defn_enclosing_ranges "
            "(document_id, symbol_id, start_line, start_char, end_line, end_char) "
            "VALUES (?, ?, ?, 0, ?, 0)",
            (doc_ids[path], i, start, end),
        )
    next_id = len(symbols_with_defs) + 1
    for j, sym in enumerate(orphans):
        conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (?, ?)",
                     (next_id + j, sym))
    conn.commit()
    return conn


def test_verbatim_token_finds_definition():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    hits = search_symbols(conn, "ZoektLifecycle")
    assert hits == [SymbolHit(file_path="search.py", start_line=10, end_line=40,
                              dotted_path="mypkg.search.ZoektLifecycle",
                              kind=DescriptorKind.TYPE)]


def test_match_is_case_insensitive():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    assert search_symbols(conn, "zoektlifecycle") != []


def test_bigram_concatenation_matches_split_identifier():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    hits = search_symbols(conn, "zoekt lifecycle spawning")
    assert [h.dotted_path for h in hits] == ["mypkg.search.ZoektLifecycle"]


def test_bigram_counts_two_tokens_and_outranks_single_token_match():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40),
                       (METHOD_SYM, "search.py", 12, 20))
    hits = search_symbols(conn, "zoekt lifecycle spawn")
    # spawn matches METHOD_SYM with 1 token; the bigram 'zoektlifecycle'
    # matches CLASS_SYM with 2 -> class first.
    assert [h.dotted_path for h in hits] == [
        "mypkg.search.ZoektLifecycle",
        "mypkg.search.ZoektLifecycle.spawn",
    ]


def test_kind_priority_ranks_type_over_method_on_tie():
    conn = _index_conn((RENDER_METHOD_SYM, "widget.py", 5, 9),
                       (RENDER_TYPE_SYM, "views.py", 1, 50))
    hits = search_symbols(conn, "render")
    assert [h.kind for h in hits] == [DescriptorKind.TYPE, DescriptorKind.METHOD]


def test_dotted_token_suffix_matches():
    conn = _index_conn((METHOD_SYM, "search.py", 12, 20))
    hits = search_symbols(conn, "ZoektLifecycle.spawn")
    assert [h.dotted_path for h in hits] == ["mypkg.search.ZoektLifecycle.spawn"]


def test_candidates_without_definition_rows_are_dropped():
    conn = _index_conn(orphans=(EXTERNAL_SYM,))
    assert search_symbols(conn, "external_thing") == []


def test_empty_defn_enclosing_ranges_yields_empty_list():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (1, ?)", (CLASS_SYM,))
    conn.commit()
    assert search_symbols(conn, "ZoektLifecycle") == []


def test_no_matching_tokens_yields_empty_list():
    conn = _index_conn((CLASS_SYM, "search.py", 10, 40))
    assert search_symbols(conn, "how does it work") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_symbol_search.py -v`
Expected: new tests FAIL with `ImportError: cannot import name 'SymbolHit'`; Task 2's four still PASS

- [ ] **Step 3: Write the implementation**

First, add the imports this task needs to the top of `src/jarvis/symbol_search.py` (Task 2 deliberately left these out — it didn't use them yet):

```python
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass

from jarvis import symbols
from jarvis.symbols import Candidate, DescriptorKind
```

Then append to `src/jarvis/symbol_search.py`:

```python
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
        hits.append(SymbolHit(file_path=row[0], start_line=row[1], end_line=row[2],
                              dotted_path=candidate.dotted_path, kind=candidate.kind))
        if len(hits) >= limit:
            break
    return hits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_symbol_search.py tests/test_symbols.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/symbol_search.py tests/test_symbol_search.py
git commit -m "feat: match and rank symbol definitions for NL queries"
```

---

### Task 4: Three-list RRF in `semantic.py`

`reciprocal_rank_fusion()` accepts the symbol list. Symbol hits merge into an overlapping vector chunk exactly the way Zoekt hits do (containment of the definition's start line); standalone hits carry `symbol_name=dotted_path`, `content=""`.

**Files:**
- Modify: `src/jarvis/semantic.py` (`reciprocal_rank_fusion`, ~lines 44–80; imports at top)
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `SymbolHit` (Task 3).
- Produces: `reciprocal_rank_fusion(repo, vector_rows, zoekt_hits, symbol_hits=(), *, k=RRF_K) -> list[FusedHit]` — Task 5 passes the fourth argument. Existing two-list callers keep working (defaulted arg).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_semantic.py` (it already imports `reciprocal_rank_fusion` and `ZoektHit`; add the `SymbolHit` import at the top of the file):

```python
from jarvis.symbol_search import SymbolHit
from jarvis.symbols import DescriptorKind


def _class_hit() -> SymbolHit:
    return SymbolHit(file_path="a.py", start_line=10, end_line=40,
                     dotted_path="pkg.mod.Thing", kind=DescriptorKind.TYPE)


def test_symbol_hit_merges_into_overlapping_vector_chunk():
    vector_rows = [{"file_path": "a.py", "start_line": 8, "end_line": 42,
                    "symbol_name": "Thing", "content": "class Thing: ..."}]
    fused = reciprocal_rank_fusion("r", vector_rows, [], [_class_hit()])
    assert len(fused) == 1
    assert set(fused[0].sources) == {"vector", "symbol"}
    # Merged entry keeps the vector chunk's coordinates and content.
    assert fused[0].start_line == 8 and fused[0].content == "class Thing: ..."


def test_standalone_symbol_hit_has_empty_content_and_dotted_name():
    fused = reciprocal_rank_fusion("r", [], [], [_class_hit()])
    assert len(fused) == 1
    assert fused[0].sources == ("symbol",)
    assert fused[0].symbol_name == "pkg.mod.Thing"
    assert fused[0].content == ""
    assert (fused[0].start_line, fused[0].end_line) == (10, 40)


def test_three_source_hit_accumulates_unweighted_rrf_score():
    vector_rows = [{"file_path": "a.py", "start_line": 8, "end_line": 42,
                    "symbol_name": "Thing", "content": "class Thing: ..."}]
    zoekt_hits = [ZoektHit(repo="r", path="a.py", line_number=10, line_text="class Thing")]
    fused = reciprocal_rank_fusion("r", vector_rows, zoekt_hits, [_class_hit()])
    assert len(fused) == 1
    assert set(fused[0].sources) == {"vector", "zoekt", "symbol"}
    # Each source contributed rank 1: score is exactly 3/(k+1).
    assert abs(fused[0].score - 3 / 61) < 1e-9


def test_fusion_without_symbol_list_is_unchanged():
    """Two-list callers (and the scip_conn=None path) are byte-identical
    to the pre-symbol behavior — pins the degradation contract."""
    vector_rows = [{"file_path": "a.py", "start_line": 1, "end_line": 5,
                    "symbol_name": None, "content": "x"}]
    zoekt_hits = [ZoektHit(repo="r", path="b.py", line_number=2, line_text="y")]
    assert (reciprocal_rank_fusion("r", vector_rows, zoekt_hits)
            == reciprocal_rank_fusion("r", vector_rows, zoekt_hits, []))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: new tests FAIL with `TypeError: reciprocal_rank_fusion() takes 3 positional arguments but 4 were given`; existing tests PASS

- [ ] **Step 3: Write the implementation**

In `src/jarvis/semantic.py`: add the import near the existing `search` import:

```python
from jarvis.symbol_search import SymbolHit
```

Change `reciprocal_rank_fusion`'s signature and add the symbol loop after the Zoekt loop (before `fused = [...]`):

```python
def reciprocal_rank_fusion(repo: str, vector_rows: list[dict],
                           zoekt_hits: list[ZoektHit],
                           symbol_hits: list[SymbolHit] = [],
                           *, k: int = RRF_K) -> list[FusedHit]:
```

(The mutable-default lint concern doesn't apply — the list is never mutated — but use `symbol_hits: tuple[SymbolHit, ...] | list[SymbolHit] = ()` to match codebase style.)

```python
    for rank, sym in enumerate(symbol_hits, start=1):
        # Same containment rule as the Zoekt merge above: credit the vector
        # chunk that contains the definition's start line, else stand alone.
        merged_key = next(
            ((r["file_path"], r["start_line"], r["end_line"]) for r in vector_rows
             if r["file_path"] == sym.file_path
             and r["start_line"] <= sym.start_line <= r["end_line"]),
            None,
        )
        if merged_key is not None:
            _add(merged_key, rank, "symbol", None)
        else:
            # No content: the SCIP db stores no source text, and the agent
            # has file/lines to read. symbol_name carries the dotted path.
            _add((sym.file_path, sym.start_line, sym.end_line), rank, "symbol",
                 {"file_path": sym.file_path, "start_line": sym.start_line,
                  "end_line": sym.end_line, "symbol_name": sym.dotted_path,
                  "content": ""})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/semantic.py tests/test_semantic.py
git commit -m "feat: fuse symbol-definition hits into semantic search RRF"
```

---

### Task 5: `semantic_search(scip_conn=...)` with the degradation contract

`semantic_search()` computes the symbol list from an optional connection, swallowing every failure — mirroring how Zoekt unavailability already degrades.

**Files:**
- Modify: `src/jarvis/semantic.py` (`semantic_search`, ~lines 286–340)
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `search_symbols(conn, query)` (Task 3), three-list fusion (Task 4).
- Produces: `semantic_search(slug, query, limit=10, *, root=None, zoekt_base_url=None, model=None, scip_conn: sqlite3.Connection | None = None) -> dict` — Task 6 passes `scip_conn`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_semantic.py`. Follow the file's existing pattern for stubbing `SemanticStore`/`EmbeddingModel` (reuse its existing fakes/fixtures if present — check the top of the file; the tests below assume a `_patched_search` helper style where `store.search` returns fixed rows and the model embeds trivially; adapt names to the file's existing fixtures):

```python
def test_semantic_search_with_none_scip_conn_matches_previous_behavior(monkeypatch):
    """scip_conn=None must be byte-identical to the two-signal result."""
    from jarvis import semantic as semantic_mod

    rows = [{"file_path": "a.py", "start_line": 1, "end_line": 5,
             "symbol_name": None, "content": "x", "_distance": 0.1}]
    monkeypatch.setattr(semantic_mod.SemanticStore, "table_identity",
                        lambda self, slug: semantic_mod.TableIdentity(
                            "m", "r", "", "", semantic_mod.CONTENT_FORMAT))
    monkeypatch.setattr(semantic_mod.SemanticStore, "search",
                        lambda self, slug, vector, limit: rows)
    monkeypatch.setattr(semantic_mod, "default_model", lambda: _FakeModel())

    result = semantic_mod.semantic_search("repo", "find x", scip_conn=None)
    assert [h["sources"] for h in result["results"]] == [["vector"]]


def test_semantic_search_symbol_signal_failure_degrades_silently(monkeypatch):
    """Any exception inside the signal -> two-signal result, no error, no warning."""
    from jarvis import semantic as semantic_mod

    rows = [{"file_path": "a.py", "start_line": 1, "end_line": 5,
             "symbol_name": None, "content": "x", "_distance": 0.1}]
    monkeypatch.setattr(semantic_mod.SemanticStore, "table_identity",
                        lambda self, slug: semantic_mod.TableIdentity(
                            "m", "r", "", "", semantic_mod.CONTENT_FORMAT))
    monkeypatch.setattr(semantic_mod.SemanticStore, "search",
                        lambda self, slug, vector, limit: rows)
    monkeypatch.setattr(semantic_mod, "default_model", lambda: _FakeModel())

    def _boom(conn, query):
        raise RuntimeError("index went away")

    monkeypatch.setattr(semantic_mod, "search_symbols", _boom)
    result = semantic_mod.semantic_search(
        "repo", "find x", scip_conn=sqlite3.connect(":memory:"))
    assert [h["sources"] for h in result["results"]] == [["vector"]]
    assert "error" not in result


class _FakeModel:
    """Minimal EmbeddingModel stand-in for semantic_search wiring tests."""
    def identity(self):
        return ("m", "r")

    def prefixes(self):
        return ("", "")

    def embed_query(self, text):
        return [0.0]

    def prefix_warning(self):
        return None
```

(If `tests/test_semantic.py` already defines a fake model or store fixture, reuse that instead of `_FakeModel` — do not duplicate an existing helper. Add `import sqlite3` to the file's imports if absent.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: new tests FAIL with `TypeError: semantic_search() got an unexpected keyword argument 'scip_conn'`

- [ ] **Step 3: Write the implementation**

In `src/jarvis/semantic.py`: extend the import from Task 4:

```python
from jarvis.symbol_search import SymbolHit, search_symbols
```

Add `import sqlite3` to the module imports. Change `semantic_search`'s signature:

```python
def semantic_search(slug: str, query: str, limit: int = 10, *, root: Path | None = None,
                    zoekt_base_url: str | None = None,
                    model: EmbeddingModel | None = None,
                    scip_conn: sqlite3.Connection | None = None) -> dict:
```

After the `zoekt_hits` block, before the fusion call:

```python
    symbol_hits: list[SymbolHit] = []
    if scip_conn is not None:
        try:
            symbol_hits = search_symbols(scip_conn, query)
        except Exception:
            # Best-effort by contract: the symbol signal may only ever add.
            # Silent like the Zoekt degradation above; sources reflect it.
            pass
```

And change the fusion call:

```python
    fused = reciprocal_rank_fusion(slug, vector_rows, zoekt_hits, symbol_hits)[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic.py tests/test_symbol_search.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/semantic.py tests/test_semantic.py
git commit -m "feat: wire optional scip connection into semantic_search"
```

---

### Task 6: Server wiring — `QueryService.connection()` + `_scip_conn_or_none()`

The MCP tool passes the repo's cached read-only index connection when one exists, `None` otherwise (search-only repos, unpublished indexes, any failure).

**Files:**
- Modify: `src/jarvis/query.py` (inside `class QueryService`, after `resolve_symbol`, ~line 375)
- Modify: `src/jarvis/server.py` (`semantic_search_tool`, ~lines 334–347)
- Test: `tests/test_query.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `config.get_connection(cache, repo)` (existing), `semantic_search(..., scip_conn=...)` (Task 5).
- Produces: `QueryService.connection(repo: str) -> sqlite3.Connection` (raises `IndexNotFoundError` when unpublished); `server._scip_conn_or_none(repo: str) -> sqlite3.Connection | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query.py` (the `query_service` fixture and `REPO` constant already exist):

```python
def test_query_service_exposes_repo_connection(query_service: QueryService):
    """connection() hands semanticSearch the same cached read-only conn the
    nav tools use; symbol data must be readable through it."""
    conn = query_service.connection(REPO)
    assert conn.execute("SELECT COUNT(*) FROM global_symbols").fetchone()[0] > 0


def test_query_service_connection_raises_for_unpublished_repo(query_service: QueryService):
    from jarvis.index_reader import IndexNotFoundError

    with pytest.raises(IndexNotFoundError):
        query_service.connection("no-such-repo")
```

Append to `tests/test_server.py` (adapt import style to the file's existing pattern):

```python
def test_scip_conn_or_none_returns_none_when_no_index(monkeypatch):
    """Search-only repos (IndexNotFoundError) and any other failure both
    degrade to None — semanticSearch must not error over a missing SCIP index."""
    from jarvis import server

    class _Raising:
        def connection(self, repo):
            raise server.IndexNotFoundError("no index published")

    monkeypatch.setattr(server, "_query_service", _Raising())
    assert server._scip_conn_or_none("search-only-repo") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query.py -v -k connection && uv run pytest tests/test_server.py -v -k scip_conn`
Expected: FAIL with `AttributeError: 'QueryService' object has no attribute 'connection'` / `AttributeError: module 'jarvis.server' has no attribute '_scip_conn_or_none'`

- [ ] **Step 3: Write the implementation**

In `src/jarvis/query.py`, inside `QueryService` after `resolve_symbol`:

```python
    def connection(self, repo: str) -> sqlite3.Connection:
        """The repo's cached read-only index connection.

        Exists for semanticSearch's symbol signal (symbol_search.py), which
        needs raw table access rather than a nav operation. Raises
        IndexNotFoundError when no index is published — callers treating
        the connection as optional catch that and pass None.
        """
        conn, _ = get_connection(self._cache, repo)
        return conn
```

In `src/jarvis/server.py`, next to `_zoekt_base_url_or_none`:

```python
def _scip_conn_or_none(repo: str):
    """The symbol signal wants a SCIP index but must not require one — a
    search-only repo (or any failure) degrades semanticSearch to the
    vector+zoekt signals, mirroring _zoekt_base_url_or_none."""
    try:
        return _service().connection(repo)
    except Exception:
        return None
```

And in `semantic_search_tool`, change the call:

```python
        return semantic.semantic_search(repo, query, limit,
                                        zoekt_base_url=_zoekt_base_url_or_none(),
                                        scip_conn=_scip_conn_or_none(repo))
```

Note: `_scip_conn_or_none` calls `_service()`, which reads the module-level `_query_service` global — the monkeypatch in the server test relies on that, matching how existing server tests stub `_zoekt_lifecycle`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_query.py tests/test_server.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full unit suite**

Run: `uv run pytest -m "not integration"`
Expected: all PASS — no regression anywhere

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/query.py src/jarvis/server.py tests/test_query.py tests/test_server.py
git commit -m "feat: pass scip connection to semanticSearch symbol signal"
```

---

### Task 7: Integration test against a real index

End-to-end proof on the Python fixture: index it with real `scip-python` + `scip`, open the published db, and confirm `search_symbols` returns the known class's definition. Scoped to the symbol signal deliberately — full `semantic_search` needs the `semantic` extra and a model download, which the integration environment doesn't guarantee; the fusion path is already pinned by Task 4/5 unit tests.

**Files:**
- Modify: `tests/test_index_cli.py` (append; follows the file's existing integration pattern)

**Interfaces:**
- Consumes: `index_repo` (existing), `search_symbols` (Task 3), fixture `tests/fixtures/mini_py_repo` (contains `class Greeter` with method `say_hi` in `greeter.py`).
- Produces: nothing — terminal verification.

- [ ] **Step 1: Write the test**

Append to `tests/test_index_cli.py`:

```python
@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_symbol_search_finds_definitions_in_real_index(tmp_path: Path):
    """The semanticSearch symbol signal, end-to-end against a real published
    index: an NL query naming the fixture's class returns its definition."""
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"
    slug = index_repo(repo_dir, root=data_root)

    target_dir = config.index_dir(slug, data_root)
    pointer = (target_dir / "current").read_text(encoding="utf-8").strip()
    conn = sqlite3.connect(f"file:{target_dir / pointer}?mode=ro&immutable=1", uri=True)
    try:
        from jarvis.symbol_search import search_symbols

        hits = search_symbols(conn, "where is the Greeter class defined")
        assert hits, "expected at least one symbol hit for 'Greeter'"
        top = hits[0]
        assert top.file_path == "greeter.py"
        assert top.dotted_path.endswith("Greeter")

        # A method query resolves too, proving defn_enclosing_ranges depth.
        method_hits = search_symbols(conn, "say_hi")
        assert any(h.dotted_path.endswith("say_hi") for h in method_hits)
    finally:
        conn.close()
```

- [ ] **Step 2: Run the integration test**

Run: `uv run pytest tests/test_index_cli.py::test_symbol_search_finds_definitions_in_real_index -v -m integration`
Expected: PASS (or SKIP if `scip-python`/`scip`/`zoekt-git-index` are absent — a skip is acceptable in that environment, but run it on a machine with the binaries before calling the feature done)

- [ ] **Step 3: Run the complete suite**

Run: `uv run pytest`
Expected: all PASS (integration tests skip cleanly where binaries are missing)

- [ ] **Step 4: Commit**

```bash
git add tests/test_index_cli.py
git commit -m "test: integration-verify symbol search against a real index"
```

---

## Self-Review Notes

- **Spec coverage:** token extraction + three matching rungs (Tasks 2–3), ranking rules incl. bigram-counts-2 (Task 3), `SYMBOL_TOP_K=10` (Task 2), three-list unweighted RRF + merge rule + `content=""` (Task 4), degradation contract with the load-bearing `scip_conn=None` test (Task 5), server wiring incl. search-only repos (Task 6), integration (Task 7), public accessor instead of private reach (Task 1). Out-of-scope list carried into Global Constraints.
- **Deviation from spec, by explicit decision (pre-flight scan):** the spec's reference implementation reimplemented the dotted-suffix rule inside `symbol_search.py` to avoid reaching into `symbols._matches` (private). Presented to the human partner as a DRY-vs-isolation trade-off before dispatch; the ruling was to generalize `_matches` with a `case_sensitive` flag and expose it publicly (`symbols.dotted_suffix_matches`), mirroring Task 1's `name_map()` pattern. Task 1 and Task 3 reflect this — zero duplication, `resolve()`'s call site and behavior are untouched (flag defaults to the prior behavior).
- **Type consistency check:** `SymbolHit` fields (`file_path/start_line/end_line/dotted_path/kind`) used identically in Tasks 3, 4, 7; `search_symbols(conn, query, limit=SYMBOL_TOP_K)` consistent across Tasks 3, 5, 7; `scip_conn` kwarg consistent across Tasks 5, 6.
- **Known adaptation point:** Task 5's `_FakeModel`/monkeypatch style must be reconciled with whatever fakes `tests/test_semantic.py` already defines — the task text says to reuse existing helpers if present rather than duplicate.

## Unresolved Questions

- None blocking. If `test_semantic.py`'s existing fixtures make the Task 5 stubs redundant, prefer the existing fixtures (noted in the task).
