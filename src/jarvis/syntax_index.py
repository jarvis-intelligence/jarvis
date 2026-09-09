"""Immutable syntax/provider snapshot builder (spec TSI-03 "File manifest",
"Declaration contract", "Incremental reuse"; TSI-04 section 5 "Snapshot
storage/publication").

Three phases:

* **Capture** (`capture_sources`/`validate_sources`): `git ls-files
  --stage -z` is the single source of truth for "what belongs to this
  repo". Eligible tracked files up to 1 MiB are hashed and copied into a
  bounded scratch directory; gitlinks, symlinks, and files under
  `config.IGNORED_DIRS` are recorded as exclusions, never silently
  dropped. The manifest's `source_hash` digests ordered path/mode/
  admission/hash facts -- never filesystem mtimes -- so `validate_sources`
  can detect any addition/deletion/mutation of an eligible source between
  capture and publication.
* **Build** (`build_syntax_index`): walks the manifest once, extracting
  each in-scope supported file via `syntax.extract_file` unless a
  compatible row (same hash, same `grammar_identity`) already exists in a
  `previous` published snapshot, in which case its `syntax_files`/
  `syntax_symbols` rows are copied forward untouched.
* **Publish** (`copy_syntax_tables`/`finalize_snapshot`): merges the
  freshly built syntax tables into the converted SCIP database (never
  touching its own `documents`/`chunks`/`global_symbols`/`mentions`/
  `defn_enclosing_ranges` tables) and derives real per-file and
  per-snapshot SCIP provider-coverage flags from that data, writing the
  `jarvis_snapshot` singleton row.

Ownership boundary: this module owns `syntax_files`/`syntax_symbols`/
`jarvis_snapshot` end to end. It reuses `scip_decoder`'s public decoders
and `query.relationship_data_present` to inspect converter data --
`scip_decoder.py` stays the only module importing `scip_pb2`/`zstandard`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis import config
from jarvis.query import relationship_data_present
from jarvis.scip_decoder import decode_occurrences
from jarvis.symbols import Candidate, DescriptorKind, dotted_suffix_matches
from jarvis.syntax import (
    ParsedSyntax,
    ParserPool,
    Span,
    SyntaxSymbol,
    extract_file,
    grammar_identity,
    language_for_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Tree

# spec TSI-03 "File manifest": 1 MiB backstop for the syntax baseline.
# Distinct from -- and much smaller than -- any semantic-chunking limit.
MAX_SOURCE_BYTES = 1024 * 1024

FORMAT_VERSION = 1

_GITLINK_MODE = "160000"
_SYMLINK_MODE = "120000"

_STATE_KEYS = ("parsed", "partial", "failed", "skipped", "unsupported")


class SourceChangedError(RuntimeError):
    """A tracked eligible source was added, deleted, or mutated between
    `capture_sources` and a later `validate_sources` call."""


class SourceCaptureError(RuntimeError):
    """A tracked file could not be captured: not a git repository, an
    eligible file disappeared mid-capture, or an eligible read failed."""


class SnapshotCorruptionError(RuntimeError):
    """`jarvis_snapshot` exists but its singleton row is missing or its
    fields cannot be trusted (spec TSI-04 "never mutate published dbs" --
    a corrupt row must fail closed, not degrade to invented facts)."""


@dataclass(frozen=True)
class CapturedFile:
    file_path: str
    language: str | None
    file_hash: str | None
    source_path: Path | None
    in_scope: bool
    reason: str | None


@dataclass(frozen=True)
class SourceManifest:
    files: tuple[CapturedFile, ...]
    source_hash: str


@dataclass(frozen=True)
class CoverageCounts:
    parsed: int
    partial: int
    failed: int
    skipped: int
    unsupported: int


@dataclass(frozen=True)
class FileProviderCoverage:
    """One file's `syntax_files` row: parse outcome plus the per-file
    provider-coverage bits `finalize_snapshot` derives from real SCIP data
    (spec TSI-05 "Coverage precedence") -- the routing signal
    `query.py` needs to decide SCIP-vs-syntax per operation, per file."""

    state: str
    reason: str | None
    scip_outline: bool
    scip_definition: bool

@dataclass(frozen=True)
class SyntaxBuildReport:
    counts: CoverageCounts
    reused_files: int


@dataclass(frozen=True)
class SnapshotFacts:
    """Facts about one immutable published generation -- not registry
    last-run state."""

    generation: str | None
    commit_sha: str | None
    source_hash: str | None
    scip_state: str
    syntax_counts: CoverageCounts | None
    scip_outlines: bool
    scip_definitions: bool
    scip_references: bool
    scip_calls: bool
    scip_types: bool


# ---------------------------------------------------------------------------
# Capture (spec TSI-03 "File manifest")
# ---------------------------------------------------------------------------


def _list_tracked_stage0(repo_path: Path) -> list[tuple[str, str]]:
    """Repo-relative (mode, path) pairs for every stage-0 tracked entry.

    `git ls-files --stage -z`: each NUL-delimited record is
    `<mode> SP <object> SP <stage> TAB <path>`. Splitting at the first TAB
    (not further token splitting) keeps embedded tabs/newlines in `path`
    intact. A non-zero stage means an unmerged conflict entry, not a
    resolvable tracked file -- it is skipped rather than admitted."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "--stage", "-z"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SourceCaptureError(
            f"{repo_path} is not a git repository (git ls-files --stage: {result.stderr.strip()})"
        )
    entries: list[tuple[str, str]] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        header, sep, path = record.partition("\t")
        if not sep:
            continue
        parts = header.split(" ")
        if len(parts) != 3:
            continue
        mode, _object, stage = parts
        if stage != "0":
            continue
        entries.append((mode, path))
    return entries


def _is_ignored_path(path: str) -> bool:
    return any(part in config.IGNORED_DIRS for part in Path(path).parts)


def _classify(mode: str, path: str) -> tuple[bool, str | None, str | None]:
    """(in_scope, exclusion_reason, language) for one tracked entry.

    `in_scope=False` means "never a syntax candidate at all" (gitlink,
    symlink-by-mode, or an ignored directory component); `in_scope=True`
    with `language=None` means "eligible but no grammar covers this
    extension" -- a distinct, later, classification."""
    if mode == _GITLINK_MODE:
        return False, "submodule", None
    if mode == _SYMLINK_MODE:
        return False, "symlink", None
    if _is_ignored_path(path):
        return False, "ignored directory", None
    return True, None, language_for_path(path)


def _resolve_safe(repo_path: Path, rel_path: str) -> Path:
    """Resolve `rel_path` under `repo_path`, rejecting any escape via `..`
    or a symlinked ancestor directory. The leaf itself is intentionally
    left unresolved (never dereferenced here) so a caller can `lstat` it
    to detect a working-tree symlink before ever following it."""
    candidate = repo_path / rel_path
    repo_real = repo_path.resolve()
    parent_real = candidate.parent.resolve()
    if parent_real != repo_real and repo_real not in parent_real.parents:
        raise SourceCaptureError(f"tracked path escapes repository: {rel_path!r}")
    return candidate


def _fact(path: str, mode: str, in_scope: bool, file_hash: str | None) -> str:
    return "\t".join((path, mode, "1" if in_scope else "0", file_hash or ""))


def _scan(repo_path: Path, scratch_dir: Path | None, *, strict: bool) -> SourceManifest:
    """Shared walk for `capture_sources` (scratch_dir set, strict=True --
    real capture, I/O failures raise `SourceCaptureError`) and
    `validate_sources` (scratch_dir=None, strict=False -- a read failure
    just guarantees a digest mismatch, since a tracked-file disappearance
    between capture and validation is a content change, not a capture
    anomaly)."""
    entries = _list_tracked_stage0(repo_path)
    files: list[CapturedFile] = []
    digest: list[str] = []
    counter = 0

    for mode, path in entries:
        in_scope, reason, language = _classify(mode, path)
        if not in_scope:
            files.append(CapturedFile(path, None, None, None, False, reason))
            digest.append(_fact(path, mode, False, None))
            continue
        if language is None:
            files.append(CapturedFile(path, None, None, None, True, "unsupported extension"))
            digest.append(_fact(path, mode, True, None))
            continue

        full_path = _resolve_safe(repo_path, path)
        try:
            entry_stat = full_path.lstat()
        except OSError as exc:
            if strict:
                raise SourceCaptureError(f"tracked file disappeared: {path}") from exc
            digest.append(_fact(path, mode, True, "!missing"))
            continue

        if stat.S_ISLNK(entry_stat.st_mode):
            files.append(CapturedFile(path, None, None, None, False, "symlink"))
            digest.append(_fact(path, mode, False, None))
            continue

        if entry_stat.st_size > MAX_SOURCE_BYTES:
            files.append(CapturedFile(path, language, None, None, True, "file exceeds 1 MiB syntax limit"))
            digest.append(_fact(path, mode, True, None))
            continue

        try:
            data = full_path.read_bytes()
        except OSError as exc:
            if strict:
                raise SourceCaptureError(f"failed to read tracked file: {path}") from exc
            digest.append(_fact(path, mode, True, "!error"))
            continue

        if len(data) > MAX_SOURCE_BYTES:
            files.append(CapturedFile(path, language, None, None, True, "file exceeds 1 MiB syntax limit"))
            digest.append(_fact(path, mode, True, None))
            continue

        file_hash = hashlib.sha256(data).hexdigest()
        source_path = None
        if scratch_dir is not None:
            counter += 1
            source_path = scratch_dir / f"{counter:08d}.src"
            source_path.write_bytes(data)
        files.append(CapturedFile(path, language, file_hash, source_path, True, None))
        digest.append(_fact(path, mode, True, file_hash))

    source_hash = hashlib.sha256("\n".join(digest).encode("utf-8")).hexdigest()
    return SourceManifest(files=tuple(files), source_hash=source_hash)


def capture_sources(repo_path: Path, scratch_dir: Path) -> SourceManifest:
    """Capture git-tracked worktree bytes for every eligible source file
    into `scratch_dir` (created if needed) and return the manifest."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return _scan(repo_path, scratch_dir, strict=True)


def validate_sources(repo_path: Path, manifest: SourceManifest) -> None:
    """Re-scan `repo_path` and raise `SourceChangedError` if tracked-entry
    membership, admission, or any eligible file's bytes differ from what
    `manifest` recorded -- must be called before publishing anything
    extracted from `manifest`."""
    fresh = _scan(repo_path, None, strict=False)
    if fresh.source_hash != manifest.source_hash:
        raise SourceChangedError(f"tracked sources changed since capture in {repo_path}")


# ---------------------------------------------------------------------------
# Namespaced schema (spec TSI-04 section 5) -- verbatim DDL, run once per
# fresh destination connection, always before that connection's own data
# transaction (never inside a transaction whose atomicity must survive).
# ---------------------------------------------------------------------------

_SYNTAX_SCHEMA = """
CREATE TABLE syntax_files (
    file_path TEXT PRIMARY KEY,
    language TEXT,
    file_hash TEXT,
    parser_identity TEXT,
    in_scope INTEGER NOT NULL CHECK (in_scope IN (0, 1)),
    state TEXT NOT NULL CHECK (state IN ('parsed','partial','failed','skipped','unsupported')),
    reason TEXT,
    scip_outline INTEGER NOT NULL DEFAULT 0 CHECK (scip_outline IN (0, 1)),
    scip_definition INTEGER NOT NULL DEFAULT 0 CHECK (scip_definition IN (0, 1))
);
CREATE TABLE syntax_symbols (
    symbol TEXT PRIMARY KEY,
    file_path TEXT NOT NULL REFERENCES syntax_files(file_path),
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_symbol TEXT REFERENCES syntax_symbols(symbol) DEFERRABLE INITIALLY DEFERRED,
    declaration_start_byte INTEGER NOT NULL,
    declaration_end_byte INTEGER NOT NULL,
    declaration_start_line INTEGER NOT NULL,
    declaration_start_character INTEGER NOT NULL,
    declaration_end_line INTEGER NOT NULL,
    declaration_end_character INTEGER NOT NULL,
    selection_start_byte INTEGER NOT NULL,
    selection_end_byte INTEGER NOT NULL,
    selection_start_line INTEGER NOT NULL,
    selection_start_character INTEGER NOT NULL,
    selection_end_line INTEGER NOT NULL,
    selection_end_character INTEGER NOT NULL
);
CREATE INDEX syntax_symbols_file ON syntax_symbols(file_path, selection_start_byte);
CREATE INDEX syntax_symbols_name ON syntax_symbols(name);
CREATE INDEX syntax_symbols_qualified ON syntax_symbols(qualified_name);
CREATE TABLE jarvis_snapshot (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    published_at TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    scip_state TEXT NOT NULL,
    scip_outlines INTEGER NOT NULL,
    scip_definitions INTEGER NOT NULL,
    scip_references INTEGER NOT NULL,
    scip_calls INTEGER NOT NULL,
    scip_types INTEGER NOT NULL,
    syntax_counts TEXT NOT NULL
);
"""

_SYNTAX_SYMBOL_COLUMNS = (
    "symbol, file_path, name, qualified_name, kind, parent_symbol, "
    "declaration_start_byte, declaration_end_byte, declaration_start_line, declaration_start_character, "
    "declaration_end_line, declaration_end_character, "
    "selection_start_byte, selection_end_byte, selection_start_line, selection_start_character, "
    "selection_end_line, selection_end_character"
)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def has_syntax_tables(conn: sqlite3.Connection) -> bool:
    """Whether `conn` carries Task 3's namespaced syntax tables at all --
    False for a legacy pre-syntax-baseline snapshot, which has no per-file
    provider coverage data and must be served exactly as before (spec
    TSI-05 "detect capabilities before issuing provider-specific SQL")."""
    return _has_table(conn, "syntax_files")


def has_scip_tables(conn: sqlite3.Connection) -> bool:
    """Whether `conn` carries genuine converter-produced SCIP tables at
    all -- a syntax-only snapshot (SCIP disabled/unsupported/failed) has
    none, and provider-specific SQL against them must never run."""
    return _has_scip_conversion_tables(conn)


def _row_to_syntax_symbol(row: tuple) -> SyntaxSymbol:
    (
        symbol, file_path, name, qualified_name, kind, parent_symbol,
        d_sb, d_eb, d_sl, d_sc, d_el, d_ec,
        s_sb, s_eb, s_sl, s_sc, s_el, s_ec,
    ) = row
    return SyntaxSymbol(
        symbol=symbol, file_path=file_path, name=name, qualified_name=qualified_name, kind=DescriptorKind(kind),
        parent_symbol=parent_symbol,
        declaration=Span(d_sb, d_eb, d_sl, d_sc, d_el, d_ec),
        selection=Span(s_sb, s_eb, s_sl, s_sc, s_el, s_ec),
    )


# ---------------------------------------------------------------------------
# Build (spec TSI-03 "Incremental reuse")
# ---------------------------------------------------------------------------


def _insert_manifest_only(dest: sqlite3.Connection, captured: CapturedFile, state: str) -> None:
    dest.execute(
        "INSERT INTO syntax_files (file_path, language, file_hash, parser_identity, in_scope, state, reason, "
        "scip_outline, scip_definition) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
        (captured.file_path, captured.language, captured.file_hash, None,
         1 if captured.in_scope else 0, state, captured.reason),
    )


def _insert_parsed(dest: sqlite3.Connection, parsed: ParsedSyntax) -> None:
    dest.execute(
        "INSERT INTO syntax_files (file_path, language, file_hash, parser_identity, in_scope, state, reason, "
        "scip_outline, scip_definition) VALUES (?, ?, ?, ?, 1, ?, ?, 0, 0)",
        (parsed.file_path, parsed.language, parsed.file_hash, parsed.parser_identity, parsed.state, parsed.reason),
    )
    dest.executemany(
        f"INSERT INTO syntax_symbols ({_SYNTAX_SYMBOL_COLUMNS}) "
        f"VALUES ({', '.join('?' for _ in range(18))})",
        [
            (
                s.symbol, parsed.file_path, s.name, s.qualified_name, str(s.kind), s.parent_symbol,
                s.declaration.start_byte, s.declaration.end_byte,
                s.declaration.start_line, s.declaration.start_character,
                s.declaration.end_line, s.declaration.end_character,
                s.selection.start_byte, s.selection.end_byte,
                s.selection.start_line, s.selection.start_character,
                s.selection.end_line, s.selection.end_character,
            )
            for s in parsed.symbols
        ],
    )


def _previous_syntax_file_row(conn: sqlite3.Connection, file_path: str) -> tuple | None:
    return conn.execute(
        "SELECT file_path, language, file_hash, parser_identity, in_scope, state, reason "
        "FROM syntax_files WHERE file_path = ?", (file_path,)
    ).fetchone()


def _copy_previous_rows(previous: sqlite3.Connection, dest: sqlite3.Connection, row: tuple) -> None:
    dest.execute(
        "INSERT INTO syntax_files (file_path, language, file_hash, parser_identity, in_scope, state, reason, "
        "scip_outline, scip_definition) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
        row,
    )
    file_path = row[0]
    symbol_rows = previous.execute(
        f"SELECT {_SYNTAX_SYMBOL_COLUMNS} FROM syntax_symbols WHERE file_path = ?", (file_path,)
    ).fetchall()
    dest.executemany(
        f"INSERT INTO syntax_symbols ({_SYNTAX_SYMBOL_COLUMNS}) "
        f"VALUES ({', '.join('?' for _ in range(18))})",
        symbol_rows,
    )


def build_syntax_index(
    db_path: Path,
    manifest: SourceManifest,
    *,
    pool: ParserPool,
    previous: sqlite3.Connection | None = None,
    parse_for: frozenset[str] = frozenset(),
    on_parsed: Callable[[CapturedFile, bytes, Tree], None] | None = None,
) -> SyntaxBuildReport:
    """Build a fresh syntax-only database at `db_path` from `manifest`.

    A file whose captured hash and current `grammar_identity` both match a
    row in `previous` is copied forward without re-parsing; every other
    in-scope, supported, non-oversized file is extracted via
    `syntax.extract_file`. `previous=None` or a legacy snapshot with no
    `syntax_files` table is a normal first extraction. A reused file named
    in `parse_for` is still parsed once (for `on_parsed`) but its already
    -valid declaration rows are never re-extracted."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    dest = sqlite3.connect(db_path)
    try:
        dest.execute("PRAGMA busy_timeout = 5000")
        dest.executescript(_SYNTAX_SCHEMA)

        has_previous = previous is not None and _has_table(previous, "syntax_files")
        counts = dict.fromkeys(_STATE_KEYS, 0)
        reused = 0

        dest.execute("BEGIN")
        for captured in manifest.files:
            if not captured.in_scope:
                _insert_manifest_only(dest, captured, "skipped")
                continue
            if captured.language is None:
                _insert_manifest_only(dest, captured, "unsupported")
                counts["unsupported"] += 1
                continue
            if captured.source_path is None:
                _insert_manifest_only(dest, captured, "skipped")
                counts["skipped"] += 1
                continue

            identity = grammar_identity(captured.language)
            prev_row = _previous_syntax_file_row(previous, captured.file_path) if has_previous else None
            can_reuse = prev_row is not None and prev_row[2] == captured.file_hash and prev_row[3] == identity

            if can_reuse:
                _copy_previous_rows(previous, dest, prev_row)
                reused += 1
                counts[prev_row[5]] += 1
                if captured.file_path in parse_for and on_parsed is not None:
                    data = captured.source_path.read_bytes()
                    tree = pool.parse(captured.language, data)
                    on_parsed(captured, data, tree)
                continue

            data = captured.source_path.read_bytes()
            parsed = extract_file(captured.file_path, data, captured.language, pool=pool)
            _insert_parsed(dest, parsed)
            counts[parsed.state] += 1
            if captured.file_path in parse_for and on_parsed is not None:
                tree = parsed.tree if parsed.tree is not None else pool.parse(captured.language, data)
                on_parsed(captured, data, tree)
        dest.commit()
    finally:
        dest.close()

    return SyntaxBuildReport(
        counts=CoverageCounts(**counts),
        reused_files=reused,
    )


# ---------------------------------------------------------------------------
# Publish (spec TSI-04 section 5)
# ---------------------------------------------------------------------------


def copy_syntax_tables(source_path: Path, target: sqlite3.Connection) -> None:
    """Merge one `build_syntax_index` output database's Jarvis-owned rows
    into `target` (an already-open connection to the converted SCIP
    database). Creates the namespaced schema on `target` if absent; never
    touches converter tables."""
    target.execute("PRAGMA busy_timeout = 5000")
    if not _has_table(target, "syntax_files"):
        target.executescript(_SYNTAX_SCHEMA)
    target.execute("ATTACH DATABASE ? AS syntax_scratch", (str(source_path),))
    try:
        target.execute("BEGIN")
        target.execute("INSERT INTO syntax_files SELECT * FROM syntax_scratch.syntax_files")
        target.execute("INSERT INTO syntax_symbols SELECT * FROM syntax_scratch.syntax_symbols")
    except sqlite3.Error:
        target.rollback()
        raise
    else:
        target.commit()
    finally:
        target.execute("DETACH DATABASE syntax_scratch")


def _has_scip_conversion_tables(conn: sqlite3.Connection) -> bool:
    return _has_table(conn, "documents")


def _scip_capability_facts(
    conn: sqlite3.Connection,
) -> tuple[frozenset[str], frozenset[str], bool, bool, bool]:
    """(outline_paths, definition_paths, has_references, has_calls, has_types)
    derived from real converter data, decoding each chunk's occurrences
    blob exactly once.

    Outline coverage per file: a valid `defn_enclosing_ranges` row OR a
    decoded nonlocal definition occurrence. Definition coverage per file:
    a decoded nonlocal definition occurrence only -- mirrors what
    `get_definitions`'s `mentions`-role-bitmask query can actually return;
    an enclosing range alone is not "the definition query returns a
    location". Reference capability: any decoded nonlocal occurrence
    exists at all (matches `get_references`, which applies no role
    filter). Call capability additionally requires an enclosing range to
    exist (call-hierarchy resolves references onto their enclosing
    definition via `defn_enclosing_ranges`). Type capability reuses
    `query.relationship_data_present` verbatim."""
    if not _has_scip_conversion_tables(conn):
        return frozenset(), frozenset(), False, False, False

    enclosing_rows = conn.execute(
        "SELECT DISTINCT d.relative_path FROM defn_enclosing_ranges r "
        "JOIN documents d ON d.id = r.document_id"
    ).fetchall()
    outline_paths: set[str] = {row[0] for row in enclosing_rows}
    has_enclosing_ranges = len(enclosing_rows) > 0

    definition_paths: set[str] = set()
    has_nonlocal_occurrence = False
    chunk_rows = conn.execute(
        "SELECT d.relative_path, c.occurrences FROM chunks c JOIN documents d ON d.id = c.document_id"
    ).fetchall()
    for relative_path, blob in chunk_rows:
        for occ in decode_occurrences(blob):
            if occ.symbol.startswith("local "):
                continue
            has_nonlocal_occurrence = True
            if occ.is_definition():
                outline_paths.add(relative_path)
                definition_paths.add(relative_path)

    has_types = relationship_data_present(conn)
    has_references = has_nonlocal_occurrence
    has_calls = has_nonlocal_occurrence and has_enclosing_ranges
    return frozenset(outline_paths), frozenset(definition_paths), has_references, has_calls, has_types


def _coverage_counts_from_syntax_files(conn: sqlite3.Connection) -> CoverageCounts:
    rows = conn.execute(
        "SELECT state, COUNT(*) FROM syntax_files WHERE in_scope = 1 GROUP BY state"
    ).fetchall()
    counts = dict.fromkeys(_STATE_KEYS, 0)
    for state, count in rows:
        if state in counts:
            counts[state] = count
    return CoverageCounts(**counts)


def finalize_snapshot(
    conn: sqlite3.Connection, *, generation: str, commit_sha: str, published_at: str,
    source_hash: str, scip_state: str,
) -> SnapshotFacts:
    """Reset every file's provider flags, re-derive them from actual SCIP
    data on `conn`, and write the `jarvis_snapshot` singleton row."""
    conn.execute("UPDATE syntax_files SET scip_outline = 0, scip_definition = 0")

    outline_paths, definition_paths, has_references, has_calls, has_types = _scip_capability_facts(conn)

    if outline_paths:
        conn.executemany(
            "UPDATE syntax_files SET scip_outline = 1 WHERE file_path = ?",
            [(path,) for path in outline_paths],
        )
    if definition_paths:
        conn.executemany(
            "UPDATE syntax_files SET scip_definition = 1 WHERE file_path = ?",
            [(path,) for path in definition_paths],
        )

    counts = _coverage_counts_from_syntax_files(conn)
    counts_json = json.dumps({key: getattr(counts, key) for key in _STATE_KEYS})

    conn.execute(
        "INSERT OR REPLACE INTO jarvis_snapshot "
        "(singleton, format_version, generation, commit_sha, published_at, source_hash, scip_state, "
        "scip_outlines, scip_definitions, scip_references, scip_calls, scip_types, syntax_counts) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            FORMAT_VERSION, generation, commit_sha, published_at, source_hash, scip_state,
            1 if outline_paths else 0, 1 if definition_paths else 0,
            1 if has_references else 0, 1 if has_calls else 0, 1 if has_types else 0,
            counts_json,
        ),
    )
    conn.commit()

    return SnapshotFacts(
        generation=generation, commit_sha=commit_sha, source_hash=source_hash, scip_state=scip_state,
        syntax_counts=counts,
        scip_outlines=bool(outline_paths), scip_definitions=bool(definition_paths),
        scip_references=has_references, scip_calls=has_calls, scip_types=has_types,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

# Bounded cache for expensive legacy-snapshot SCIP-capability derivation,
# keyed by immutable database path -- same FIFO/LRU-hybrid convention as
# `symbols._name_maps` (index_reader.py / symbols.py). An in-memory
# connection's path is "" and is never cached (it is neither immutable nor
# safely shareable across calls with different content).
_LEGACY_FACTS_CACHE_MAX_SIZE = 64
_legacy_facts_cache: OrderedDict[str, SnapshotFacts] = OrderedDict()


def _conn_db_path(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    return row[2] if row is not None else ""


def _legacy_snapshot_facts(conn: sqlite3.Connection) -> SnapshotFacts:
    path = _conn_db_path(conn)
    if path:
        cached = _legacy_facts_cache.get(path)
        if cached is not None:
            _legacy_facts_cache.move_to_end(path)
            return cached

    outline_paths, definition_paths, has_references, has_calls, has_types = _scip_capability_facts(conn)
    facts = SnapshotFacts(
        generation=None, commit_sha=None, source_hash=None, scip_state="legacy",
        syntax_counts=None,
        scip_outlines=bool(outline_paths), scip_definitions=bool(definition_paths),
        scip_references=has_references, scip_calls=has_calls, scip_types=has_types,
    )

    if path:
        _legacy_facts_cache[path] = facts
        _legacy_facts_cache.move_to_end(path)
        while len(_legacy_facts_cache) > _LEGACY_FACTS_CACHE_MAX_SIZE:
            _legacy_facts_cache.popitem(last=False)
    return facts


def _read_new_format_facts(conn: sqlite3.Connection) -> SnapshotFacts:
    row = conn.execute(
        "SELECT generation, commit_sha, source_hash, scip_state, scip_outlines, scip_definitions, "
        "scip_references, scip_calls, scip_types, syntax_counts FROM jarvis_snapshot WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise SnapshotCorruptionError("jarvis_snapshot table exists but has no singleton row")
    (
        generation, commit_sha, source_hash, scip_state, scip_outlines, scip_definitions,
        scip_references, scip_calls, scip_types, counts_json,
    ) = row
    if generation is None or commit_sha is None or source_hash is None or scip_state is None:
        raise SnapshotCorruptionError("jarvis_snapshot row is missing required fields")
    try:
        raw_counts = json.loads(counts_json)
        counts = CoverageCounts(**{key: int(raw_counts[key]) for key in _STATE_KEYS})
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SnapshotCorruptionError(f"jarvis_snapshot.syntax_counts is malformed: {exc}") from exc
    return SnapshotFacts(
        generation=generation, commit_sha=commit_sha, source_hash=source_hash, scip_state=scip_state,
        syntax_counts=counts,
        scip_outlines=bool(scip_outlines), scip_definitions=bool(scip_definitions),
        scip_references=bool(scip_references), scip_calls=bool(scip_calls), scip_types=bool(scip_types),
    )


def read_snapshot_facts(conn: sqlite3.Connection) -> SnapshotFacts:
    """New-format read from the `jarvis_snapshot` singleton; a legacy
    snapshot with no such table gets its SCIP-only facts derived (and
    cached) from the real converter tables instead."""
    if _has_table(conn, "jarvis_snapshot"):
        return _read_new_format_facts(conn)
    return _legacy_snapshot_facts(conn)


def file_symbols(conn: sqlite3.Connection, file_path: str) -> tuple[SyntaxSymbol, ...]:
    rows = conn.execute(
        f"SELECT {_SYNTAX_SYMBOL_COLUMNS} FROM syntax_symbols WHERE file_path = ? "
        "ORDER BY selection_start_byte",
        (file_path,),
    ).fetchall()
    return tuple(_row_to_syntax_symbol(row) for row in rows)


def get_syntax_symbol(conn: sqlite3.Connection, symbol: str) -> SyntaxSymbol | None:
    row = conn.execute(
        f"SELECT {_SYNTAX_SYMBOL_COLUMNS} FROM syntax_symbols WHERE symbol = ?", (symbol,)
    ).fetchone()
    return _row_to_syntax_symbol(row) if row is not None else None


def _syntax_name_map(conn: sqlite3.Connection, *, uncovered_only: bool) -> dict[str, list[Candidate]]:
    if uncovered_only:
        sql = (
            "SELECT sy.symbol, sy.name, sy.qualified_name, sy.kind "
            "FROM syntax_symbols sy JOIN syntax_files sf ON sf.file_path = sy.file_path "
            "WHERE sf.scip_definition = 0"
        )
    else:
        sql = "SELECT symbol, name, qualified_name, kind FROM syntax_symbols"
    buckets: dict[str, list[Candidate]] = {}
    for symbol, name, qualified_name, kind in conn.execute(sql).fetchall():
        buckets.setdefault(name, []).append(
            Candidate(symbol=symbol, dotted_path=qualified_name, kind=DescriptorKind(kind))
        )
    return buckets


def find_syntax_symbols(
    conn: sqlite3.Connection, query: str, *, uncovered_only: bool = True
) -> tuple[SyntaxSymbol, ...]:
    """Case-sensitive dotted-suffix lookup (the same rule `symbols.resolve`
    uses for SCIP symbols), scoped to declarations lacking real SCIP
    definition coverage when `uncovered_only` is True."""
    name_map = _syntax_name_map(conn, uncovered_only=uncovered_only)
    candidates = dotted_suffix_matches(name_map, query, case_sensitive=True)
    if not candidates:
        return ()
    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        f"SELECT {_SYNTAX_SYMBOL_COLUMNS} FROM syntax_symbols WHERE symbol IN ({placeholders})",
        tuple(c.symbol for c in candidates),
    ).fetchall()
    by_symbol = {row[0]: _row_to_syntax_symbol(row) for row in rows}
    return tuple(by_symbol[c.symbol] for c in candidates if c.symbol in by_symbol)


def file_coverage(conn: sqlite3.Connection, path: str) -> tuple[str, str | None]:
    """(state, reason) for one file's `syntax_files` row; `("unknown",
    None)` when the path was never recorded at all."""
    row = conn.execute("SELECT state, reason FROM syntax_files WHERE file_path = ?", (path,)).fetchone()
    if row is None:
        return "unknown", None
    return row[0], row[1]


def file_provider_coverage(conn: sqlite3.Connection, path: str) -> FileProviderCoverage | None:
    """One file's parse state plus its real per-file SCIP outline/
    definition coverage bits, or `None` when `path` was never recorded in
    `syntax_files` at all (untracked/uncaptured -- spec TSI-05 "not-indexed").
    Callers must check `has_syntax_tables(conn)` first: a legacy snapshot
    has no `syntax_files` table to query."""
    row = conn.execute(
        "SELECT state, reason, scip_outline, scip_definition FROM syntax_files WHERE file_path = ?", (path,)
    ).fetchone()
    if row is None:
        return None
    state, reason, scip_outline, scip_definition = row
    return FileProviderCoverage(
        state=state, reason=reason, scip_outline=bool(scip_outline), scip_definition=bool(scip_definition)
    )
