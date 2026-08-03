"""Real-schema test fixture — builds a small SQLite file against the ACTUAL
`scip expt-convert` v0.7.0 schema (documents/chunks/global_symbols/mentions/
defn_enclosing_ranges), with genuine zstd+protobuf occurrence blobs built via
tests/fixtures/scip_encoder.py.

DDL below is copied verbatim (`.schema` output, 2026-07-11) from a real index
produced against `acme-app` at
``.local-filestore/scip/acme-prod/acme-app/master/index-a32bf02baa47103108c7a69c321a67c9cfb19be9.db``
(gitignored, local-only — see plan.md "Verified Ground Truth").

Fixture content models a small deterministic TypeScript-like repo:

* ``toy/greeter.ts`` (2 chunks, to exercise chunk boundaries):
    - ``Greeter#`` class — definition + a `defn_enclosing_ranges` row (chunk 0).
    - ``Greeter#greet().`` method — definition + a `defn_enclosing_ranges` row,
      plus a `local 0` occurrence inside its body (chunk 1). Locals are
      never inserted into `mentions`/`global_symbols` by the real converter
      (`cmd/scip/convert.go`'s `insertOccurrenceData` explicitly skips
      `scip.IsLocalSymbol` occurrences) — this fixture mirrors that.
    - ``DEFAULT_NAME.`` const — definition occurrence with NO enclosing
      range (the 152-vs-964 gap: `insertEnclosingRangeData` skips
      occurrences whose `EnclosingRange` has fewer than 3 elements).
      Deliberately encoded with a COMBINED `symbol_roles` bitmask
      (Definition | Generated = 17, not a bare 1) — `mentions.role` is
      verified (2026-07-11, against both the real acme-app index and
      `convert.go`'s `insertOccurrenceData`) to be the RAW `SymbolRoles`
      bitmask value the converter saw for that (chunk, symbol) pair, not a
      normalized 0/1 boolean; acme-app happens to only ever produce 0 or 1
      because no occurrence there combines role bits, but the schema does
      not guarantee that. This row regression-tests that `get_definitions`
      filters `mentions.role` with a bitwise AND against the Definition
      bit, not exact equality (an exact `role = 1` filter would silently
      drop this row).
* ``toy/constants.ts`` (1 chunk) — a single top-level reference occurrence
  to ``Greeter#greet().`` (module scope, no enclosing range). This is a
  real "reference, not definition" usage site for find-references, and
  the "reference outside any enclosing range is dropped from
  incoming-calls" case (phase 4).
* ``toy/greeter.ts`` chunk 2 — a second method, ``Greeter#sayHi().``, whose
  body (lines 8-9) contains a real call-site reference to
  ``Greeter#greet().`` — gives call-hierarchy a non-empty, non-fabricated
  incoming/outgoing pair: `greet()`'s incoming calls include `sayHi()`'s
  call site, and `sayHi()`'s outgoing calls include that same reference to
  `greet()`.
* ``toy/animal.ts`` (document 3, 1 chunk) — an ``Animal#`` interface with
  its own `defn_enclosing_ranges` row, used purely as a resolvable
  supertype target: ``Greeter#``'s `global_symbols.relationships` blob
  carries a single `is_implementation` relationship pointing at it.

``global_symbols.signature``/``.relationships`` are NULL for every row
except `Greeter#` — verified from `convert.go` that v0.7.0's
`insertGlobalSymbols` INSERT statement has no column for either, so no
real index ever populates them (see `service/scip_decoder.py`'s module
docstring). `Greeter#`'s contrived non-NULL `relationships` blob (via
`scip_encoder.encode_relationships`) exists purely to exercise that decode
path end-to-end (real v0.7.0 output never populates it); `Animal#` is left
NULL so the fixture covers both the NULL and non-NULL cases in one pass.

Public entry points (`build_published_index`, `COMMIT_SHA`, `PUBLISHED_AT`,
`build_synthetic_index_db`) are unchanged from the pre-rewrite fixture so
`tests/conftest.py` and `tests/test_index_reader.py` don't need to change
their import lines.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codeintel import scip_pb2
from codeintel.scip_decoder import SymbolRoles
from tests.fixtures.scip_encoder import encode_occurrences, encode_relationships

COMMIT_SHA = "abc1234"
PUBLISHED_AT = "2026-07-08T12:00:00Z"

DOC_GREETER = "toy/greeter.ts"
DOC_CONSTANTS = "toy/constants.ts"
DOC_ANIMAL = "toy/animal.ts"

CLASS_SYMBOL = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#"
METHOD_SYMBOL = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#greet()."
SAY_HI_METHOD_SYMBOL = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#sayHi()."
CONST_SYMBOL = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/DEFAULT_NAME."
ANIMAL_SYMBOL = "scip-typescript npm @toy/pkg 0.0.1 src/`animal.ts`/Animal#"
LOCAL_SYMBOL = "local 0"

_SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    language TEXT,
    relative_path TEXT NOT NULL UNIQUE,
    position_encoding TEXT,
    text TEXT
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    occurrences BLOB NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);
CREATE TABLE global_symbols (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    display_name TEXT,
    kind INTEGER,
    documentation TEXT,
    signature BLOB,
    enclosing_symbol TEXT,
    relationships BLOB
);
CREATE TABLE mentions (
    chunk_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,
    role INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, symbol_id, role),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id),
    FOREIGN KEY (symbol_id) REFERENCES global_symbols(id)
);
CREATE TABLE defn_enclosing_ranges (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    start_char INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (symbol_id) REFERENCES global_symbols(id)
);
CREATE INDEX idx_chunks_line_range ON chunks(document_id, start_line, end_line);
CREATE INDEX idx_mentions_symbol_id_role ON mentions(symbol_id, role);
CREATE INDEX idx_defn_enclosing_ranges_symbol_id ON defn_enclosing_ranges(symbol_id);
CREATE INDEX idx_defn_enclosing_ranges_document ON defn_enclosing_ranges(document_id, start_line, end_line);
CREATE INDEX idx_chunks_doc_id ON chunks(document_id);
CREATE INDEX idx_global_symbols_symbol ON global_symbols(symbol);
"""


def _mentions_for_chunk(occurrences: list[scip_pb2.Occurrence]) -> set[tuple[str, int]]:
    """Mirrors `convert.go`'s `insertOccurrenceData`: group a chunk's
    non-local occurrences by symbol, and emit one (symbol, role) pair per
    DISTINCT raw `symbol_roles` value seen for that symbol in this chunk —
    not a single normalized role per symbol."""
    pairs: set[tuple[str, int]] = set()
    for occ in occurrences:
        if occ.symbol.startswith("local "):
            continue
        pairs.add((occ.symbol, occ.symbol_roles))
    return pairs


class _Builder:
    """Accumulates rows for one index.db, assigning ids the same way
    SQLite's INTEGER PRIMARY KEY autoincrement would (1-based, insertion
    order) so foreign keys can be wired up before the executescript call."""

    def __init__(self) -> None:
        self.documents: list[tuple[int, str]] = []  # (id, relative_path)
        self.chunks: list[tuple[int, int, int, int, int, bytes]] = []  # (id, doc_id, index, start, end, blob)
        self.global_symbols: list[tuple[int, str, str | None, int | None, bytes | None]] = (
            []
        )  # (id, symbol, display_name, kind, relationships)
        self.mentions: set[tuple[int, int, int]] = set()  # (chunk_id, symbol_id, role)
        self.defn_enclosing_ranges: list[tuple[int, int, int, int, int, int]] = []  # (doc_id, symbol_id, sl, sc, el, ec)
        self._symbol_ids: dict[str, int] = {}

    def add_document(self, relative_path: str) -> int:
        doc_id = len(self.documents) + 1
        self.documents.append((doc_id, relative_path))
        return doc_id

    def add_symbol(
        self,
        symbol: str,
        display_name: str | None,
        kind: int | None,
        relationships: list[scip_pb2.Relationship] | None = None,
    ) -> int:
        if symbol in self._symbol_ids:
            return self._symbol_ids[symbol]
        symbol_id = len(self.global_symbols) + 1
        relationships_blob = encode_relationships(relationships) if relationships else None
        self.global_symbols.append((symbol_id, symbol, display_name, kind, relationships_blob))
        self._symbol_ids[symbol] = symbol_id
        return symbol_id

    def add_chunk(
        self, doc_id: int, chunk_index: int, start_line: int, end_line: int, occurrences: list[scip_pb2.Occurrence]
    ) -> int:
        chunk_id = len(self.chunks) + 1
        blob = encode_occurrences(occurrences)
        self.chunks.append((chunk_id, doc_id, chunk_index, start_line, end_line, blob))
        for symbol, role in _mentions_for_chunk(occurrences):
            symbol_id = self._symbol_ids[symbol]
            self.mentions.add((chunk_id, symbol_id, role))
        return chunk_id

    def add_defn_enclosing_range(
        self, doc_id: int, symbol: str, start_line: int, start_char: int, end_line: int, end_char: int
    ) -> None:
        symbol_id = self._symbol_ids[symbol]
        self.defn_enclosing_ranges.append((doc_id, symbol_id, start_line, start_char, end_line, end_char))

    def write(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(_SCHEMA)
            conn.executemany(
                "INSERT INTO documents (id, relative_path) VALUES (?, ?)", self.documents
            )
            conn.executemany(
                "INSERT INTO global_symbols (id, symbol, display_name, kind, relationships) VALUES (?, ?, ?, ?, ?)",
                self.global_symbols,
            )
            conn.executemany(
                "INSERT INTO chunks (id, document_id, chunk_index, start_line, end_line, occurrences) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                self.chunks,
            )
            conn.executemany(
                "INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (?, ?, ?)",
                sorted(self.mentions),
            )
            conn.executemany(
                "INSERT INTO defn_enclosing_ranges "
                "(document_id, symbol_id, start_line, start_char, end_line, end_char) VALUES (?, ?, ?, ?, ?, ?)",
                self.defn_enclosing_ranges,
            )
            conn.commit()
        finally:
            conn.close()


def build_synthetic_index_db(db_path: Path) -> None:
    """Create the real-schema fixture index.db described in the module
    docstring."""
    b = _Builder()

    doc_greeter = b.add_document(DOC_GREETER)
    doc_constants = b.add_document(DOC_CONSTANTS)
    doc_animal = b.add_document(DOC_ANIMAL)

    b.add_symbol(ANIMAL_SYMBOL, "Animal", scip_pb2.SymbolInformation.Interface)
    # Greeter implements Animal — the only non-NULL relationships blob in
    # this fixture (real v0.7.0 output never populates this column; see
    # module docstring). `kind` is left NULL, matching the real acme-app
    # index (see scip_decoder.kind_name's docstring) and exercising
    # query.py's parser-based fallback for documentSymbols.
    b.add_symbol(
        CLASS_SYMBOL,
        "Greeter",
        None,
        relationships=[scip_pb2.Relationship(symbol=ANIMAL_SYMBOL, is_implementation=True)],
    )
    b.add_symbol(METHOD_SYMBOL, "greet", None)
    b.add_symbol(SAY_HI_METHOD_SYMBOL, "sayHi", scip_pb2.SymbolInformation.Method)
    b.add_symbol(CONST_SYMBOL, "DEFAULT_NAME", None)

    # toy/greeter.ts, chunk 0: `class Greeter {` definition (line 0), body
    # spans lines 0-5 (the method + const declared after it).
    b.add_chunk(
        doc_greeter,
        chunk_index=0,
        start_line=0,
        end_line=0,
        occurrences=[
            scip_pb2.Occurrence(
                range=[0, 6, 13],
                symbol=CLASS_SYMBOL,
                symbol_roles=SymbolRoles.DEFINITION,
                enclosing_range=[0, 0, 5, 1],
            ),
        ],
    )
    b.add_defn_enclosing_range(doc_greeter, CLASS_SYMBOL, start_line=0, start_char=0, end_line=5, end_char=1)

    # toy/greeter.ts, chunk 1: `greet()` method definition (line 1, body
    # lines 1-3, containing a local var use with no mentions row), then a
    # top-level const `DEFAULT_NAME` (line 6) with NO enclosing range.
    b.add_chunk(
        doc_greeter,
        chunk_index=1,
        start_line=1,
        end_line=6,
        occurrences=[
            scip_pb2.Occurrence(
                range=[1, 2, 7],
                symbol=METHOD_SYMBOL,
                symbol_roles=SymbolRoles.DEFINITION,
                enclosing_range=[1, 2, 3, 3],
            ),
            scip_pb2.Occurrence(
                range=[2, 4, 8],
                symbol=LOCAL_SYMBOL,
                symbol_roles=0,
                enclosing_range=[],
            ),
            scip_pb2.Occurrence(
                range=[6, 6, 18],
                symbol=CONST_SYMBOL,
                # Deliberately combined bitmask (Definition | Generated) —
                # see module docstring's mentions.role note.
                symbol_roles=SymbolRoles.DEFINITION | SymbolRoles.GENERATED,
                enclosing_range=[],
            ),
        ],
    )
    b.add_defn_enclosing_range(doc_greeter, METHOD_SYMBOL, start_line=1, start_char=2, end_line=3, end_char=3)

    # toy/constants.ts, chunk 0: a bare top-level reference to greet() —
    # no enclosing range (module scope).
    b.add_chunk(
        doc_constants,
        chunk_index=0,
        start_line=0,
        end_line=0,
        occurrences=[
            scip_pb2.Occurrence(
                range=[0, 0, 20],
                symbol=METHOD_SYMBOL,
                symbol_roles=0,
                enclosing_range=[],
            ),
        ],
    )

    # toy/greeter.ts, chunk 2: `sayHi()` method definition (line 8, body
    # lines 8-9) whose body contains a real call-site reference to
    # `greet()` (line 9) — gives call-hierarchy a non-fabricated
    # incoming/outgoing pair.
    b.add_chunk(
        doc_greeter,
        chunk_index=2,
        start_line=8,
        end_line=9,
        occurrences=[
            scip_pb2.Occurrence(
                range=[8, 2, 7],
                symbol=SAY_HI_METHOD_SYMBOL,
                symbol_roles=SymbolRoles.DEFINITION,
                enclosing_range=[8, 2, 9, 3],
            ),
            scip_pb2.Occurrence(
                range=[9, 4, 9],
                symbol=METHOD_SYMBOL,
                symbol_roles=0,
                enclosing_range=[],
            ),
        ],
    )
    b.add_defn_enclosing_range(doc_greeter, SAY_HI_METHOD_SYMBOL, start_line=8, start_char=2, end_line=9, end_char=3)

    # toy/animal.ts, chunk 0: `interface Animal { ... }` definition — a
    # resolvable supertype target for Greeter's relationships blob.
    b.add_chunk(
        doc_animal,
        chunk_index=0,
        start_line=0,
        end_line=2,
        occurrences=[
            scip_pb2.Occurrence(
                range=[0, 10, 16],
                symbol=ANIMAL_SYMBOL,
                symbol_roles=SymbolRoles.DEFINITION,
                enclosing_range=[0, 0, 2, 1],
            ),
        ],
    )
    b.add_defn_enclosing_range(doc_animal, ANIMAL_SYMBOL, start_line=0, start_char=0, end_line=2, end_char=1)

    b.write(db_path)


def build_published_index(filestore_root: Path, project: str, repo: str, branch: str) -> Path:
    """Lay out {filestore_root}/scip/{project}/{repo}/{branch}/ with a
    versioned db, its metadata.json, and the `current` pointer — matching
    Phase 2's publish.sh GCS_PREFIX convention. Returns the index directory.
    """
    index_dir = filestore_root / "scip" / project / repo / branch
    index_dir.mkdir(parents=True, exist_ok=True)

    db_filename = f"index-{COMMIT_SHA}.db"
    build_synthetic_index_db(index_dir / db_filename)

    metadata = {
        "project": project,
        "repo": repo,
        "branch": branch,
        "commit_sha": COMMIT_SHA,
        "published_at": PUBLISHED_AT,
        "indexer": {"name": "scip-typescript", "version": "0.6.6"},
        "scip_converter_version": "v0.7.0",
        "zoekt_version": "REPLACE_WITH_VERIFIED_COMMIT_SHA",
        "duration_seconds": 12,
        "sizes": {"index_scip_bytes": 0, "index_db_bytes": 0},
    }
    (index_dir / f"index-{COMMIT_SHA}.metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (index_dir / "current").write_text(db_filename, encoding="utf-8")
    return index_dir
