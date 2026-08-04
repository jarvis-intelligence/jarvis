"""Package-dependency graph: extraction from a SCIP index + a stdlib-sqlite3
store + 2-hop bounded BFS traversal (blastRadius).

Ported from an internal reference implementation's `service/graph_extraction.py` +
`repository/graph_store.py` + `service/graph_query_service.py`, collapsed
into one module and simplified for jarvis's single-user, single-tenant
shape:

* No `(project, repo)` pair — every package is keyed on a bare `repo` slug
  (see config.py's repo_slug), matching every other module in this codebase.
* No authorization filtering anywhere — jarvis has no multi-tenancy, so
  `_filter_by_repo_access` (and the `SeedNotAuthorizedError` it guarded
  against) simply doesn't exist here.
* Sync stdlib sqlite3 instead of SQLAlchemy async + asyncpg — matches
  registry.py's own simplification of registry_store.py, and lets the graph
  tables live in registry.db (a single RW database) exactly as the plan
  requires, without a second async engine to manage.

``mentions.role`` note (preserved from the source, load-bearing): it is the
RAW ``SymbolRoles`` bitmask the converter saw, so a package is "local" if
any of its symbols has a mention whose role has the Definition bit set — a
bitwise AND, never exact equality.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jarvis.models import Freshness
from jarvis.query import FreshnessSnapshot
from jarvis.scip_decoder import SymbolRoles, parse_symbol_package

__all__ = [
    "BlastRadiusResult",
    "DependentNode",
    "ExtractionResult",
    "GraphStore",
    "Package",
    "PopulationSummary",
    "SeedPackageNotFoundError",
    "blast_radius",
    "extract_package_names",
    "package_display_name",
    "populate_graph_for_repo",
]

# Hard cap — matches query.py's call_hierarchy "single level, no recursion"
# discipline: bounded, predictable response size, never an unbounded
# transitive walk over a potentially dense package graph.
_MAX_HOPS = 2


class SeedPackageNotFoundError(Exception):
    """No package named `name` is registered for `repo` — an honest
    "nothing to traverse from" result."""


@dataclass(frozen=True)
class ExtractionResult:
    local_package_names: frozenset[str]
    external_package_names: frozenset[str]


@dataclass(frozen=True)
class PopulationSummary:
    local_packages: int
    edges_created: int
    unresolved_external_names: int


@dataclass(frozen=True)
class Package:
    id: str
    repo: str
    name: str


@dataclass(frozen=True)
class DependentNode:
    repo: str
    name: str
    hops: int


@dataclass(frozen=True)
class BlastRadiusResult:
    dependents: list[DependentNode]
    freshness: FreshnessSnapshot


def package_display_name(manager: str, name: str) -> str:
    """`packages.name` convention: `"{manager}:{package_name}"` (e.g.
    `"npm:@acme-org/acme-app"`) — folds the manager into the string
    to avoid cross-ecosystem name collisions without a speculative column."""
    return f"{manager}:{name}"


_DEFINED_SYMBOLS_SQL = """
    SELECT DISTINCT gs.symbol
    FROM global_symbols gs
    JOIN mentions m ON m.symbol_id = gs.id
    WHERE (m.role & :definition_bit) != 0
"""

_ALL_MENTIONED_SYMBOLS_SQL = """
    SELECT DISTINCT gs.symbol
    FROM global_symbols gs
    JOIN mentions m ON m.symbol_id = gs.id
"""


def extract_package_names(conn: sqlite3.Connection) -> ExtractionResult:
    """Pure-SQL scan of one index.db's `mentions`/`global_symbols` tables —
    no zstd/protobuf blob decode required for package-edge derivation."""
    defined_symbols = {
        row[0] for row in conn.execute(_DEFINED_SYMBOLS_SQL, {"definition_bit": SymbolRoles.DEFINITION})
    }
    all_symbols = {row[0] for row in conn.execute(_ALL_MENTIONED_SYMBOLS_SQL)}
    referenced_only_symbols = all_symbols - defined_symbols

    local_names: set[str] = set()
    for symbol in defined_symbols:
        package = parse_symbol_package(symbol)
        if package is not None:
            local_names.add(package_display_name(package.manager, package.name))

    external_names: set[str] = set()
    for symbol in referenced_only_symbols:
        package = parse_symbol_package(symbol)
        if package is None:
            continue
        display_name = package_display_name(package.manager, package.name)
        if display_name not in local_names:
            external_names.add(display_name)

    return ExtractionResult(
        local_package_names=frozenset(local_names), external_package_names=frozenset(external_names)
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (repo, name)
);
CREATE TABLE IF NOT EXISTS edges (
    from_package_id TEXT NOT NULL,
    to_package_id TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (from_package_id, to_package_id),
    FOREIGN KEY (from_package_id) REFERENCES packages(id),
    FOREIGN KEY (to_package_id) REFERENCES packages(id)
);
CREATE INDEX IF NOT EXISTS idx_edges_to_package_id ON edges(to_package_id);
"""


def _row_to_package(row: tuple) -> Package:
    pkg_id, repo, name = row
    return Package(id=pkg_id, repo=repo, name=name)


class GraphStore:
    """Sync stdlib-sqlite3 store for `packages` + `edges`. Lives in the same
    registry.db file `registry.Registry` manages (a single RW database,
    per the plan's non-functional requirement) but opens its own connection
    — SQLite supports multiple connections to one file, each with its own
    locking, and keeping the two tables' CRUD in a separate module mirrors
    the source's `registry_store.py` / `graph_store.py` split.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        # `jarvis watch`'s background reindex and a manual `jarvis
        # index`/`reindex` can legitimately race on this same file — a
        # busy_timeout makes SQLite retry for up to 5s instead of raising
        # "database is locked" on the first contended write.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert_package(self, *, repo: str, name: str) -> str:
        """Get-or-create a `packages` row keyed on `(repo, name)`."""
        existing = self.get_package_by_key(repo, name)
        if existing is not None:
            return existing.id
        package_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO packages (id, repo, name) VALUES (?, ?, ?)", (package_id, repo, name)
        )
        self._conn.commit()
        return package_id

    def get_package(self, package_id: str) -> Package | None:
        row = self._conn.execute(
            "SELECT id, repo, name FROM packages WHERE id = ?", (package_id,)
        ).fetchone()
        return _row_to_package(row) if row is not None else None

    def get_package_by_key(self, repo: str, name: str) -> Package | None:
        row = self._conn.execute(
            "SELECT id, repo, name FROM packages WHERE repo = ? AND name = ?", (repo, name)
        ).fetchone()
        return _row_to_package(row) if row is not None else None

    def find_packages_by_name(self, name: str) -> list[Package]:
        """All packages with this exact `name`, across every repo — the
        cross-repo resolution used by `populate_graph_for_repo` to turn an
        externally-referenced package name into a real edge target when
        (and only when) some other repo's own population run has already
        registered it. Returns an empty list, never raises, when nothing
        matches yet (rebuildable-graph semantics: an edge simply isn't
        recorded until both sides are known)."""
        rows = self._conn.execute("SELECT id, repo, name FROM packages WHERE name = ?", (name,)).fetchall()
        return [_row_to_package(row) for row in rows]

    def list_packages(self, repo: str | None = None) -> list[Package]:
        if repo is not None:
            rows = self._conn.execute(
                "SELECT id, repo, name FROM packages WHERE repo = ? ORDER BY repo, name", (repo,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT id, repo, name FROM packages ORDER BY repo, name").fetchall()
        return [_row_to_package(row) for row in rows]

    def add_edge(self, *, from_package_id: str, to_package_id: str) -> bool:
        """Idempotent edge insert. Returns `True` if a new edge row was
        created, `False` if it already existed — `discovered_at` records
        when the edge was FIRST observed, never bumped on rediscovery."""
        existing = self._conn.execute(
            "SELECT 1 FROM edges WHERE from_package_id = ? AND to_package_id = ?",
            (from_package_id, to_package_id),
        ).fetchone()
        if existing is not None:
            return False
        self._conn.execute(
            "INSERT INTO edges (from_package_id, to_package_id, discovered_at) VALUES (?, ?, ?)",
            (from_package_id, to_package_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()
        return True

    def clear_outgoing_edges(self, package_id: str) -> None:
        """Deletes every edge where `package_id` is the dependent side
        (`from_package_id`). Called before repopulating a repo's edges so a
        removed dependency's edge is retracted rather than left as a stale
        artifact of an earlier index run — never touches edges where
        `package_id` is the DEPENDENCY side (`to_package_id`), since those
        belong to some other repo's own population run and are corrected
        by that repo's own next reindex, not this one's."""
        self._conn.execute("DELETE FROM edges WHERE from_package_id = ?", (package_id,))
        self._conn.commit()

    def get_dependents(self, package_id: str) -> list[Package]:
        """Packages that directly depend on `package_id` (incoming edges) —
        single-hop only; multi-hop blast-radius traversal is `blast_radius`'s
        job, not this store's."""
        rows = self._conn.execute(
            "SELECT p.id, p.repo, p.name FROM packages p "
            "JOIN edges e ON e.from_package_id = p.id "
            "WHERE e.to_package_id = ? ORDER BY p.repo, p.name",
            (package_id,),
        ).fetchall()
        return [_row_to_package(row) for row in rows]

    def close(self) -> None:
        self._conn.close()


def populate_graph_for_repo(store: GraphStore, repo: str, conn: sqlite3.Connection) -> PopulationSummary:
    """Upserts this repo's own local package(s) and records dependency
    edges to any already-known package (by name). Rerun-safe both ways: an
    edge to a not-yet-indexed repo's package is simply skipped this run
    (not fabricated) and appears once that repo's own population run has
    registered its local package name(s); and a dependency this repo
    *used* to have is retracted, not left as a stale edge from an earlier
    index run — every one of this repo's own local packages has its
    outgoing edges cleared before being recomputed from this run's fresh
    extraction."""
    result = extract_package_names(conn)

    # Clear every package this repo has EVER registered locally — not just
    # the ones re-extracted this run — so a renamed/removed local package's
    # old outgoing edges don't linger once it's no longer part of the
    # fresh extraction either.
    for existing in store.list_packages(repo=repo):
        store.clear_outgoing_edges(existing.id)

    local_ids = [store.upsert_package(repo=repo, name=name) for name in sorted(result.local_package_names)]

    if not local_ids:
        return PopulationSummary(
            local_packages=0, edges_created=0, unresolved_external_names=len(result.external_package_names)
        )

    edges_created = 0
    unresolved_external_names = 0
    for name in sorted(result.external_package_names):
        matches = store.find_packages_by_name(name)
        targets = [pkg for pkg in matches if pkg.repo != repo]
        if not targets:
            unresolved_external_names += 1
            continue
        for from_id in local_ids:
            for target in targets:
                if store.add_edge(from_package_id=from_id, to_package_id=target.id):
                    edges_created += 1

    return PopulationSummary(
        local_packages=len(local_ids),
        edges_created=edges_created,
        unresolved_external_names=unresolved_external_names,
    )


def _freshness_snapshot() -> FreshnessSnapshot:
    """blastRadius's freshness contract is deliberately different from the
    5 SCIP nav tools': the `packages` table has no timestamp column at all,
    so there is no per-node commit/publish-time to report honestly for a
    multi-repo BFS result. `commit`/`generated_at` are always `None` and
    `freshness` is always `Freshness.UNKNOWN` — not a stub, an honest
    reflection of what this schema records. `checked_at` still records
    when this traversal ran."""
    return FreshnessSnapshot(
        commit=None, generated_at=None, stale=False, freshness=Freshness.UNKNOWN, checked_at=datetime.now(UTC)
    )


def blast_radius(store: GraphStore, repo: str, name: str) -> BlastRadiusResult:
    """2-hop bounded BFS over `GraphStore.get_dependents`, starting from the
    package `name` registered for `repo`. Raises `SeedPackageNotFoundError`
    if no such package is registered."""
    seed = store.get_package_by_key(repo, name)
    if seed is None:
        known = [pkg.name for pkg in store.list_packages(repo=repo)]
        hint = f"known packages for {repo!r}: {known}" if known else f"{repo!r} has no packages registered at all — has `jarvis index` been run for it?"
        raise SeedPackageNotFoundError(f"no package {name!r} registered for {repo!r} ({hint})")

    visited_ids = {seed.id}
    frontier = [seed]
    dependents: list[DependentNode] = []

    for hop in range(1, _MAX_HOPS + 1):
        next_frontier: list[Package] = []
        for pkg in frontier:
            for dependent in store.get_dependents(pkg.id):
                if dependent.id in visited_ids:
                    continue
                visited_ids.add(dependent.id)
                dependents.append(DependentNode(repo=dependent.repo, name=dependent.name, hops=hop))
                next_frontier.append(dependent)
        frontier = next_frontier
        if not frontier:
            break

    return BlastRadiusResult(dependents=dependents, freshness=_freshness_snapshot())
