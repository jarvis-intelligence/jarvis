# SCIP-Boosted Retrieval — Design Spec

**Date:** 2026-08-05
**Status:** Approved (brainstorming session)
**Origin:** Retrieval-quality enhancement drawn from production indexing-pipeline
practices (Sourcegraph's "canonical-identifier graph" direction). Chosen as
Option B over an eval-harness-first approach (Option A) — the user accepted the
trade-off of tuning ahead of measurement; Option A remains the natural follow-up.

## Problem

`semanticSearch` fuses two signals — LanceDB vector hits and Zoekt lexical
hits — via reciprocal rank fusion (RRF). Neither signal knows what a
*definition* is: a query naming an identifier (`ZoektLifecycle spawn`) ranks
chunks that merely mention the name on par with the definition site the user
almost certainly wants. Jarvis already has compiler-grade symbol data (SCIP
`global_symbols` + `defn_enclosing_ranges`) sitting next to the search
indexes, unused by retrieval.

## Goal

Add a third ranked signal to `semanticSearch` — SCIP symbol-definition
matches — fused by the existing RRF. A query mentioning an identifier ranks
that identifier's definition site above chunks that merely mention it.

**Non-goal:** any change to `searchCode`, navigation tools, index-time
behavior, storage schemas, or the MCP response shape (the existing `sources`
field just gains a possible `"symbol"` value).

## Architecture

```
semanticSearch(repo, query)
        │
        ├─ vector:  embed(query) → LanceDB top-30          (unchanged)
        ├─ zoekt:   r:<slug> query → top-30                (unchanged)
        └─ symbol:  NEW — query tokens → name-map match →
                    defn_enclosing_ranges → ranked definition locations, top-10
        │
        ▼
reciprocal_rank_fusion(vector, zoekt, symbol)   ← same k=60 formula, 3 lists
        ▼
results; sources may now include "symbol"
```

### Component boundaries

| Module | Role | Change |
|---|---|---|
| `symbol_search.py` (new) | One concern: NL query + SCIP connection → ranked `SymbolHit(file_path, start_line, end_line, dotted_path, kind)` list. Composes the symbol name map (already built and LRU-cached in `symbols.py`, ~20 ms/17K symbols; exposed via a new public accessor) with the `defn_enclosing_ranges` location join | ~120 lines |
| `semantic.py` | `semantic_search()` gains optional `scip_conn=None` param (mirrors `zoekt_base_url=None`). `reciprocal_rank_fusion()` accepts the third list; symbol hits merge into overlapping vector chunks by line range exactly as Zoekt hits merge today | small |
| `server.py` | `semantic_search_tool` passes a connection from the existing nav-tool connection cache; `None` if unavailable | ~6 lines |

### Degradation contract (key invariant)

The symbol signal is best-effort, identical in spirit to Zoekt's:

- No SCIP index (search-only repo), a `partial` index (empty
  `defn_enclosing_ranges`), or **any exception** inside the signal → the
  signal contributes nothing and search returns exactly today's two-signal
  results.
- The new code can only **add** results, never break `semanticSearch`.

## Query→symbol matching

**Token extraction:** split the NL query on non-identifier characters. Drop
tokens shorter than 3 chars and a small fixed stopword set defined in
`symbol_search.py` (English function words: `the`, `how`, `does`, `for`,
`with`, and the like). No stemming, no fuzzy matching.

**Matching — three rungs, all against a lowercased leaf-name map**: a
`dict[str, list[Candidate]]` owned by `symbol_search.py`, derived from the
name map `symbols.py` already builds (exposed via a small public accessor
added there — no reaching into another module's privates), with its own
bounded cache keyed by db path, mirroring `_name_maps`:

1. **Verbatim token** — `zoektlifecycle` matches `ZoektLifecycle`.
2. **Adjacent-token concatenation (bigrams only)** — "zoekt lifecycle spawn"
   → `zoektlifecycle`, `lifecyclespawn`: catches identifiers written as
   separate words.
3. **Dotted path** — a token containing `.` (e.g. `semantic.SemanticStore`)
   goes through the existing suffix-match logic (`symbols._matches`).

Case-insensitive throughout: NL queries rarely preserve casing, and this is a
ranked context, not a resolving one — `resolve()` stays case-sensitive and
single-answer, unchanged.

**Kind filter:** parameters and type parameters are excluded, reusing the
same exclusions `resolve()` already makes (`_RESOLVABLE_KINDS`).

## Ranking within the symbol list (before RRF)

1. **Matched-token count** desc — a symbol hit by two query tokens outranks
   one hit by one. A bigram-concatenation match consumes two query tokens
   and counts as 2.
2. **Kind priority** — `TYPE > METHOD > TERM > NAMESPACE > META`.
3. **Shorter `dotted_path`** — a top-level definition beats a deeply nested
   same-name one.

Cap: `SYMBOL_TOP_K = 10` (constant beside `VECTOR_TOP_K`/`ZOEKT_TOP_K`). Each
surviving candidate resolves to its definition location via one
`defn_enclosing_ranges` query; candidates with no definition row (externals,
stdlib references) are silently dropped.

## Fusion

Symbol hits enter RRF **unweighted** — the same `1/(k+rank)` as the other two
lists. A definition matched by name AND semantically near AND lexically
present accumulates score from all three sources and rises naturally;
per-source weights without an eval harness would be guessing.

Merge rule: if a vector chunk's line range contains the definition's start
line, credit that chunk's key (identical to the Zoekt merge); otherwise the
symbol hit stands alone with `symbolName = dotted_path` and `content = ""` —
the SCIP db stores no source text, and the agent has the file/line to read.

## Error handling

One rule: *the symbol signal may only ever add.*

- The entire signal computation in `semantic_search()` is wrapped like
  Zoekt's: any exception → empty symbol list, no warning in the response
  (silent degradation, matching a Zoekt spawn failure today).
- `server.py` obtains the connection inside its own `try/except` → passes
  `scip_conn=None` on `IndexNotFoundError` (search-only repo) or any other
  failure. `semantic.py` treats `None` as "signal absent".
- `partial` indexes: every candidate drops at location resolution — correct
  behavior for free, no special case.
- No new error strings in the MCP response; `{"error": ...}` shape and
  `NoSemanticIndexError` behavior untouched.

## Testing

| File | Coverage |
|---|---|
| `tests/test_symbol_search.py` (new) | Unit: token extraction (stopwords, short tokens, dotted tokens); bigram concatenation matching; kind-priority + token-count ranking; case-insensitive lookup; candidates without definition rows dropped; empty `defn_enclosing_ranges` → empty list. In-memory SQLite fixtures like `test_symbols.py` |
| `tests/test_semantic.py` (extend) | Three-list RRF: symbol hit merges into overlapping vector chunk; standalone symbol hit gets `sources=("symbol",)`, `content=""`; unweighted accumulation across 3 sources; **`scip_conn=None` → byte-identical to today's two-signal result** (pins the degradation contract) |
| `tests/test_server.py` (extend) | Tool passes conn when index exists; passes `None` for search-only repo without erroring |
| `tests/test_index_cli.py` (integration, extend) | End-to-end on the Python fixture: query naming a known class ranks its definition file/line in top results with `"symbol"` among sources |

## Out of scope (YAGNI)

Per-source RRF weights, trigram concatenations, fuzzy/stemmed matching,
subtoken camelCase splitting, `mentions`-count popularity signals, any
index-time changes. Each is a measured-experiment candidate after an eval
harness (Option A) exists.
