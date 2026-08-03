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

Figures below were produced by the **conforming grammar parser** (the one this spec specifies),
run against four live indexes spanning three languages. Parameters and type-parameters are
excluded throughout, per the rule below.

**Grammar coverage: 46,914 symbols, 0 unparsed.** Every symbol in all four indexes parsed cleanly.

| repo | language | symbols | distinct names | bare name | + parent qualifier | + package |
| --- | --- | --- | --- | --- | --- | --- |
| `codeintel` | Python | 2,180 | 1,056 | 93% | 99% | **100%** |
| `polaris-ui` | TypeScript | 9,920 | 5,472 | 78% | 87% | **99%** |
| `epost-ios-theme-showcase` | Swift | 17,422 | 15,907 | 91% | 91% | **100%** |
| `post-shell-app` | Swift | 17,392 | 15,883 | 91% | 91% | **100%** |

Three things this shows, none of which a single-repo measurement would have:

**1. Bare-name resolution alone is not enough — 78% at worst, not 93%.**

**2. The parent qualifier is worthless in some repos** (Swift: 91% → 91%). The colliding symbols
have byte-identical descriptors and differ *only* in the package field:

```
scip-swift xcodebuild epost_comp_showcase_sdk . `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`.
scip-swift xcodebuild ios_theme_ui            . `c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor`.
```

No amount of parent qualification can separate those. TypeScript has the same shape — `index.d.ts`
appears 8 times, once per npm package (`@types/react`, `@types/js-yaml`, …). This is why the
package is part of the dotted path.

**3. With the package folded in, resolution is 99–100% everywhere.** The only residue is 76 names
in `polaris-ui` (0.8%), which fall through to the candidate list.

In this repo the collisions excluded by the parameter rule were `tmp_path` (205), `self` (79),
`monkeypatch` (71) — pytest fixtures and parameters, none of them navigation targets. The heaviest
remaining collision is `__init__` (92), one per class, resolved by a parent qualifier.

### Bare-name resolution does not help Swift

**100% of scip-swift symbol names are clang USR strings** — 17,422 of 17,422 in
`epost-ios-theme-showcase`, with zero readable names:

```
c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor
```

Nobody types that, so the feature has no practical value in a Swift repo even though resolution
succeeds at 100% there. TypeScript is the opposite — 100% readable (`cacheLife`, `revalidate`,
`stale`), and Python likewise.

This bounds the feature rather than breaking it: Python, TypeScript, Java and Kotlin get the
benefit; Swift does not. Recovering readable names from USRs is viable but is scip-swift-specific
parsing and belongs in its own spec — recorded under *Unresolved questions*.

### Fast enough for query time

Largest indexed repo, `epost-ios-theme-showcase` (17,422 symbols):

| step | cost |
| --- | --- |
| `SELECT symbol FROM global_symbols` | 5.3 ms |
| parse + bucket all rows | 14.6 ms |

20 ms for a full cold build settles the index-time-vs-query-time question: query-time
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
    package: str              # SCIP package field -- outermost path segment
    name: str                 # leaf descriptor name
    kind: DescriptorKind      # StrEnum
    parents: tuple[str, ...]  # enclosing descriptor names, outermost first

    @property
    def dotted_path(self) -> str:
        return ".".join((self.package,) + self.parents + (self.name,))

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
`'.'.join((package,) + parents + (name,))`, then match when `full == query` or
`full.endswith('.' + query)`.

That one rule covers every query form. For
`codeintel-navigation-mcp.codeintel.search.search_zoekt`:

| query | matches via |
| --- | --- |
| `search_zoekt` | `.endswith('.search_zoekt')` |
| `SemanticStore.__init__` | `.endswith('.SemanticStore.__init__')` |
| `ios_theme_ui.UIView.centerXAnchor` | `.endswith('.ios_theme_ui.UIView.centerXAnchor')` |
| full dotted path | exact |

The package is the outermost segment, so it is available as a qualifier precisely when parent
qualification cannot help — the Swift and TypeScript cases above — without changing the rule.

Requiring the `.` boundary prevents partial-identifier matches: `zoekt` does not match
`search_zoekt`. It also handles backtick-escaped names containing dots — `` `codeintel.search` ``
is one parent, not two — which a naive `query.split('.')` comparison against `parents` gets wrong.

**Leaf-bucket fast path with a full-scan fallback.** The name map is keyed by leaf descriptor name,
so the common query is one dict lookup. That key is wrong when the query's own last segment
contains a dot — `` `greeter.ts` `` is a single descriptor name that scip-typescript emits
routinely, and `rsplit('.')` would key it as `ts`. On a bucket miss, scan all candidates against
the same suffix rule. Correct in every case, fast in the common one.

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

**How `server.py` learns the resolved value.** The nav methods return `(locations, freshness)` and
`server.py` holds no connection, so it cannot see what resolution produced. `QueryService` gains a
public `resolve_symbol(repo, symbol) -> str`; each tool calls it first and passes the result down.

The nav methods still resolve internally, so direct `QueryService` callers (tests, CLI, future
code) keep the behaviour without going through `server.py`. Double resolution is safe by
construction: rung 1 is verbatim passthrough, so resolving an already-resolved symbol is one
indexed lookup returning the same string. Idempotent, and cheaper than threading a new return
value through four signatures and their existing tests.

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
| `tests/test_server_tools.py` | `candidates` / `candidateTotal` payload; `resolvedSymbol` present only when it differs. |
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
4. `findReferences(repo="codeintel", symbol="tmp_path")` returns a not-found error naming the
   parameter-exclusion rule, not `[]`.

   Use `tmp_path` (a pytest fixture, parameter-only — verified), **not** `base_url`: that is a real
   method on `ZoektLifecycle` (`search.py:121`) and correctly resolves to it. A parameter-exclusion
   test needs a name that is only ever a parameter. `monkeypatch`, `self`, `timeout_seconds` and
   `include_prefixes` also qualify.
5. `documentSymbols` returns non-null `displayName` / `kind`.
6. A symbol resolvable at package version `0.3.1` stays resolvable at `0.4.0` with no caller change.

## Risks

- **Constructor names dominate ambiguity in every language.** `__init__`, `init`, `<init>`,
  `constructor`. The error message must lead with the qualifier hint.
- **Verbose `dottedPath`.** Including the package makes candidate paths long
  (`codeintel-navigation-mcp.codeintel.search.search_zoekt`). Accepted: it is the disambiguator,
  and it is the only thing that separates the Swift and TypeScript collisions.
- **Cost is linear in symbol count** — 20 ms at 17K symbols, so roughly 600 ms at 500K, paid once
  per index. Acceptable; worth a comment in the cache code so it is not mistaken for per-query
  cost.

## Unresolved questions

None blocking. Three deferred by choice:

1. **Readable names for Swift.** scip-swift emits clang USRs for 100% of symbol names, so bare-name
   resolution has no practical value in a Swift repo. Extracting the trailing readable identifier
   (`c:@CM@UIKit@@objc(cs)UIView(im)centerXAnchor` → `centerXAnchor`) would fix it, but that is
   scip-swift-specific parsing and belongs in its own spec.
2. **A standalone `lookupSymbol` tool** — YAGNI while candidates ride the error path.
3. **Cross-repo resolution** — blocked on the hardcoded `PROJECT="_"` / `BRANCH="_"` index layout,
   a separate and larger change.
