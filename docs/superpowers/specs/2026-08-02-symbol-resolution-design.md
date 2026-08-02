# Symbol resolution: bare names for the SCIP nav tools — Design

## Problem

Four of the five SCIP nav tools take a `symbol` argument and require the **full SCIP symbol
string**. A bare identifier returns an empty result with no error:

```
findReferences(repo="codeintel", symbol="search_zoekt")
  -> {"references": []}

findReferences(repo="codeintel",
               symbol="scip-python python codeintel-navigation-mcp 0.3.1 `codeintel.search`/search_zoekt().")
  -> 9 references
```

Both were run against the live index during investigation. The first is the call an agent
naturally makes, and it produces a confident wrong answer: "this symbol has no references."
That is worse than an error, because nothing signals that anything went wrong.

Affected: `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`.
`documentSymbols` takes a `path` and is unaffected by resolution.

### The full symbol string is not a stable thing to ask for

SCIP symbols embed the package version. The same function, across two indexes of this repo taken
hours apart:

```
scip-python python codeintel-navigation-mcp 0.2.1 `codeintel.search`/search_zoekt().
scip-python python codeintel-navigation-mcp 0.3.1 `codeintel.search`/search_zoekt().
```

Any symbol string a caller records — in a prompt, a skill, a cached plan — silently stops
resolving at the next release. Requiring callers to supply these strings is therefore not merely
inconvenient; it is a correctness hazard that grows with time.

### There is no name column to look them up by

`global_symbols` declares `display_name` and `kind`, but `scip expt-convert` never populates
either. Measured on the live index (2180 rows):

| column | populated |
| --- | --- |
| `symbol` | 2180 |
| `display_name` | **0** |
| `kind` | **0** |

This is the same class of gap as the unpopulated `relationships` column that makes `typeHierarchy`
unanswerable ([scip#464](https://github.com/scip-code/scip/issues/464)). Consequence for this
design: resolution cannot read a name column. It must parse the SCIP symbol string.

Consequence for `documentSymbols`: it returns `displayName: null, kind: null` for every symbol it
has ever returned. This design fixes that as a by-product, since the parser produces both.

## Measured feasibility

All figures below come from the live `codeintel` index (`index-bdd789b3…db`, 2180 symbols) using a
**heuristic** parser — the last identifier in the descriptor string. A conforming grammar parser
will shift them, most likely upward, because the heuristic mishandles backtick-escaped names
containing punctuation. **Re-measure during implementation and update this section.**

Bucketing symbols by their leaf descriptor name:

| corpus | distinct names | resolve to exactly 1 | ambiguous |
| --- | --- | --- | --- |
| all symbols | 1187 | 1046 (88%) | 141 (11%) |
| excluding parameters / type-parameters | 1055 | **990 (93%)** | 65 (6%) |

The collisions are dominated by things nobody navigates to:

| all symbols | count | | excluding parameters | count |
| --- | --- | --- | --- | --- |
| `tmp_path` | 205 | | `__init__` | 92 |
| `__init__` | 92 | | `symbol` | 6 |
| `self` | 79 | | `name` | 5 |
| `monkeypatch` | 71 | | `repo` | 5 |
| `repo` | 35 | | `_boom` | 5 |
| `symbol` | 30 | | `__all__` | 4 |

`tmp_path`, `self`, `monkeypatch` are pytest fixtures and parameters. Excluding parameters and
type-parameters removes the entire long tail and leaves `__init__` — one per class, expected — as
the only heavy collision.

Adding a single parent qualifier resolves nearly all of the remainder:

| rung | resolves |
| --- | --- |
| bare leaf name (`search_zoekt`) | 990 / 1055 (93%) |
| + one parent qualifier (`SemanticStore.__init__`) | 63 of the remaining 65 (96%) |
| **combined** | **1053 / 1055 (99.8%)** |
| irreducible | 2 — `tokenizer`, `__call__` |

Ambiguity is therefore an edge case, not the main path. This design should not be built around it.

### Not a Python artifact, and fast enough

Largest indexed repo, `epost-ios-theme-showcase` (Swift, 17,422 symbols):

| step | cost |
| --- | --- |
| `SELECT symbol FROM global_symbols` | 5.3 ms |
| parse + bucket all rows | 14.6 ms |
| distinct names / unique | 14,266 / 12,468 (**87%**) |

87% on Swift against 88% on Python — the collision profile is a property of code, not of language.
And 20 ms for a full cold build settles the index-time-vs-query-time question: query-time
resolution needs no schema change and no pipeline stage. Precomputing a lookup table would mean
extending the vendored `scip expt-convert` schema — a permanent maintenance seam — for no
measurable gain.

## Design

### Module boundary

New `src/codeintel/symbols.py`, owning SCIP symbol-string handling end to end. Parsing is pure
text; only resolution needs a connection. Keeping both in one module mirrors how `scip_decoder.py`
isolates the SCIP protobuf format: one module per externally-defined format, so a grammar change
lands in exactly one file.

```python
@dataclass(frozen=True)
class ParsedSymbol:
    name: str                 # leaf descriptor name
    kind: DescriptorKind      # StrEnum
    parents: tuple[str, ...]  # enclosing descriptor names, outermost first

def parse_symbol(symbol: str) -> ParsedSymbol | None
def resolve(conn: sqlite3.Connection, query: str) -> str
```

`resolve` raises `AmbiguousSymbolError(candidates)` or `SymbolNotFoundError(query)`.

### Parser

Operates on the descriptor tail — everything after the fifth space-delimited field (scheme,
manager, package, version).

| descriptor | syntax | `DescriptorKind` |
| --- | --- | --- |
| namespace | `name/` | `NAMESPACE` |
| type | `name#` | `TYPE` |
| term | `name.` | `TERM` |
| method | `name().` or `name(disambiguator).` | `METHOD` |
| parameter | `(name)` | `PARAMETER` |
| type parameter | `[name]` | `TYPE_PARAMETER` |
| meta | `name:` | `META` |

Names may be backtick-escaped (`` `codeintel.search` ``), with a doubled backtick as the literal
escape.

`local <id>` symbols and unparseable input return `None`, never an exception — a malformed symbol
in an index must not take down a query.

### Resolution ladder

First rung yielding exactly one symbol wins.

**Rung 1 — verbatim passthrough.** If `query` equals a `global_symbols.symbol` exactly, return it.
One indexed lookup via `idx_global_symbols_symbol`. Full SCIP strings keep working with zero
behaviour change, and no grammar sniffing is needed to decide which form the caller used.

**Rung 2 — dotted-suffix match.** Build each symbol's dotted path as
`'.'.join(parents + (name,))`, then match when `full == query` or `full.endswith('.' + query)`.

That one rule covers both query forms:

| query | matches `codeintel.search.search_zoekt` via |
| --- | --- |
| `search_zoekt` | `.endswith('.search_zoekt')` |
| `SemanticStore.__init__` | `.endswith('.SemanticStore.__init__')` |
| `codeintel.search.search_zoekt` | exact |

Requiring the `.` boundary prevents partial-identifier matches: `zoekt` does not match
`search_zoekt`. It also handles backtick-escaped names containing dots — `` `codeintel.search` ``
is one parent, not two — which a naive `query.split('.')` comparison against `parents` gets wrong.

No fuzzy or case-insensitive fallback. Discovery-by-approximation is what `searchCode` is for;
adding it here would reintroduce wrong-answer risk into the one layer whose entire value is
precision.

### Parameters are not resolution targets

Parameters and type-parameters are excluded from the searchable map entirely.

The alternative — prefer non-parameters when both match — was rejected. Preferring is a silent
heuristic pick, which is the exact failure mode this spec exists to eliminate. Exclusion is a
stated rule with an explicit error. Callers who genuinely want a parameter pass the full SCIP
symbol, which rung 1 honours.

### Cache

Module-level `dict[str, dict[str, list[str]]]` keyed by the db file path.

Published indexes are immutable — `index-<sha>.db`, opened `mode=ro&immutable=1`, never mutated in
place. An entry therefore cannot go stale, and a reindex publishes a new sha, producing a new key.
No invalidation, no TTL. The 20 ms build is paid once per index rather than once per query.

### Wiring into `QueryService`

One private helper, called explicitly by each of the four symbol-taking methods:

```python
def _resolved(self, conn, symbol: str) -> str:
    return symbols.resolve(conn, symbol)

def get_definitions(self, repo, symbol):
    conn, metadata = get_connection(self._cache, repo)
    symbol = self._resolved(conn, symbol)      # new
    ...
```

Explicit over a decorator: this codebase is consistently explicit (raw SQL, no ORM, plain
dataclasses), and hiding resolution behind method magic would make misfires hard to trace — a bad
trait for the layer whose job is to stop silent wrong answers.

**Ordering in `type_hierarchy`:** the existing `relationship_data_present(conn)` check runs
*before* resolution. On every real index that check is False, so the tool returns its explicit
"cannot answer" explanation. Resolving first would replace that accurate message with an
ambiguity or not-found error about a symbol whose hierarchy is unanswerable regardless — a less
informative failure. The other three methods resolve immediately after `get_connection`.

### Error contract

`_error_payload(repo, exc)` (`server.py:82`) already special-cases one exception type and passes
everything else through. Add one branch for `AmbiguousSymbolError`:

```json
{
  "error": "'__init__' is ambiguous in codeintel (92 matches). Retry with a qualifier, e.g. 'SemanticStore.__init__'.",
  "candidates": [
    {
      "symbol": "scip-python python codeintel-navigation-mcp 0.3.1 `codeintel.semantic`/SemanticStore#__init__().",
      "dottedPath": "codeintel.semantic.SemanticStore.__init__",
      "kind": "METHOD"
    }
  ],
  "candidateTotal": 92
}
```

`candidates` is a structured list, not prose inside `error`, so the caller can act on it without
parsing English. Sorted by dotted path, capped at 10, with `candidateTotal` reporting the true
count. No join against `defn_enclosing_ranges` for a file path: for the dominant collision
(`__init__`) the dotted path *is* the disambiguator.

`SymbolNotFoundError` distinguishes two cases in its message — no symbol at all, versus matched
only parameters/type-parameters, with the hint to pass the full SCIP symbol. Today those two
produce identical empty results.

### Success path — additive only

`"symbol"` keeps echoing the caller's input. A `"resolvedSymbol"` key appears only when resolution
changed it.

Redefining `"symbol"` to carry the resolved value would be cleaner, but the plugin skills and
existing agent prompts read that field. Additive delivers the canonical form to the caller without
breaking a published contract.

### `documentSymbols` backfill

`get_document_symbols` populates `displayName` and `kind` from the parser instead of from the
always-NULL columns.

**This `kind` is derived from descriptor syntax** (`#`→TYPE, `().`→METHOD), not from the indexer's
semantic classification, which is what the NULL `kind` INTEGER column was meant to carry. It must
not be presented as SCIP's `SymbolKind`. If a future converter populates the real column, prefer
it — the same self-healing property `relationship_data_present` already has.

## Scope

**In:** `symbols.py`; resolution in the four nav methods; `_error_payload` branch;
`resolvedSymbol`; `documentSymbols` backfill.

**Out:** no new MCP tool. Candidates ride the error path, so a `lookupSymbol` tool would be a
second way to do the same thing. Unchanged: `searchCode`, `semanticSearch`, `blastRadius`, the
index pipeline, the DB schema.

## Testing

| file | content |
| --- | --- |
| `tests/test_symbols.py` *(new, mirrors `symbols.py`)* | Parser units, zero fixtures: every descriptor kind, backtick-escaped names with embedded dots, doubled-backtick escape, `local <id>` → `None`, malformed → `None`. Resolution ladder against an in-memory `global_symbols`. |
| `tests/test_query.py` | Resolution wired into all four nav methods; both exceptions propagate. |
| `tests/test_server.py` | `candidates` / `candidateTotal` payload; `resolvedSymbol` present only when it differs. |
| `tests/test_index_cli.py` | `@pytest.mark.integration` — resolve against a real built index. |

Fixtures must include at least one non-Python symbol set. The parser is language-agnostic and the
collision profile was validated on Python and Swift, but TS/Java/Kotlin produce different
descriptor shapes.

### Acceptance criteria

The calls that fail today:

1. `findReferences(repo="codeintel", symbol="search_zoekt")` returns the 9 references it currently
   returns only for the full SCIP string.
2. The same call still works when passed the full SCIP string (rung 1).
3. `findReferences(repo="codeintel", symbol="__init__")` returns `candidates`, not `[]`.
4. `findReferences(repo="codeintel", symbol="base_url")` returns a not-found error naming the
   parameter-exclusion rule, not `[]`.
5. `documentSymbols` returns non-null `displayName` / `kind`.
6. A symbol resolvable at package version `0.3.1` stays resolvable at `0.4.0` with no caller change.

## Risks

- **Heuristic-derived figures.** Every percentage in *Measured feasibility* came from the
  last-identifier heuristic, not a grammar parser. Re-measure and update.
- **Constructor names dominate ambiguity in every language.** `__init__`, `init`, `<init>`,
  `constructor`. The error message must lead with the qualifier hint.
- **Cost is linear in symbol count** — 20 ms at 17K symbols, so roughly 600 ms at 500K, paid once
  per index. Acceptable; worth a comment in the cache code so it is not mistaken for per-query
  cost.

## Unresolved questions

None blocking. Two deferred by choice: a standalone `lookupSymbol` tool (YAGNI while candidates
ride the error path) and cross-repo resolution (blocked on the hardcoded `PROJECT="_"` /
`BRANCH="_"` index layout, a separate change).
