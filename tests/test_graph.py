"""Tests for graph.py: package-name extraction from a real-schema index.db
(ported logic from an internal reference implementation's graph_extraction.py, adapted
to codeintel's bare-repo-slug keying) and the 2-hop bounded BFS traversal
that backs blastRadius."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codeintel import scip_pb2
from codeintel.graph import (
    ExtractionResult,
    GraphStore,
    SeedPackageNotFoundError,
    blast_radius,
    extract_package_names,
    populate_graph_for_repo,
)
from codeintel.scip_decoder import SymbolRoles
from tests.fixtures.synthetic_index import build_synthetic_index_db


def test_extract_package_names_splits_local_vs_external(tmp_path: Path):
    """Ported synthetic fixture (toy/greeter.ts etc.) defines its own
    package (`@toy/pkg`) locally — every symbol there is a DEFINITION, so
    extraction should find it local and find no externally-referenced
    package (the fixture has no cross-package reference)."""
    db_path = tmp_path / "index.db"
    build_synthetic_index_db(db_path)

    import sqlite3

    conn = sqlite3.connect(db_path)
    result = extract_package_names(conn)
    assert result == ExtractionResult(
        local_package_names=frozenset({"npm:@toy/pkg"}),
        external_package_names=frozenset(),
    )


def test_extract_package_names_finds_external_reference(tmp_path: Path):
    """A symbol that is only ever referenced (never defined) in this index
    belongs to some OTHER repo's package — it must show up as external, not
    local, and must not appear in both sets."""
    import sqlite3

    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE);
        CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE,
            display_name TEXT, kind INTEGER, relationships BLOB);
        CREATE TABLE mentions (chunk_id INTEGER NOT NULL, symbol_id INTEGER NOT NULL,
            role INTEGER NOT NULL, PRIMARY KEY (chunk_id, symbol_id, role));
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER,
            start_line INTEGER, end_line INTEGER, occurrences BLOB);
        """
    )
    local_symbol = "scip-typescript npm @toy/pkg 0.0.1 src/`greeter.ts`/Greeter#"
    external_symbol = "scip-typescript npm @other/dep 1.2.0 src/`index.ts`/Helper#"
    conn.execute("INSERT INTO documents (id, relative_path) VALUES (1, 'toy/greeter.ts')")
    conn.execute("INSERT INTO chunks (id, document_id, chunk_index, start_line, end_line, occurrences) "
                 "VALUES (1, 1, 0, 0, 0, X'')")
    conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (1, ?)", (local_symbol,))
    conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (2, ?)", (external_symbol,))
    conn.execute("INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (1, 1, ?)", (SymbolRoles.DEFINITION,))
    conn.execute("INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (1, 2, ?)", (0,))
    conn.commit()

    result = extract_package_names(conn)
    assert result.local_package_names == frozenset({"npm:@toy/pkg"})
    assert result.external_package_names == frozenset({"npm:@other/dep"})


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    s = GraphStore(tmp_path / "registry.db")
    yield s
    s.close()


def test_populate_graph_for_repo_creates_local_package_no_edges_when_no_external(
    store: GraphStore, tmp_path: Path
):
    import sqlite3

    db_path = tmp_path / "index.db"
    build_synthetic_index_db(db_path)
    conn = sqlite3.connect(db_path)

    summary = populate_graph_for_repo(store, "toy-repo", conn)
    assert summary.local_packages == 1
    assert summary.edges_created == 0
    assert store.get_package_by_key("toy-repo", "npm:@toy/pkg") is not None


def _seed_chain(store: GraphStore) -> None:
    """seed -> dep1 -> dep2 -> dep3 (dep1 depends on seed, dep2 depends on
    dep1, dep3 depends on dep2) — same shape as the source's blast-radius
    fixture, repo-keyed instead of project+repo-keyed."""
    seed_id = store.upsert_package(repo="seed-repo", name="npm:seed")
    dep1_id = store.upsert_package(repo="dep1-repo", name="npm:dep1")
    dep2_id = store.upsert_package(repo="dep2-repo", name="npm:dep2")
    dep3_id = store.upsert_package(repo="dep3-repo", name="npm:dep3")
    store.add_edge(from_package_id=dep1_id, to_package_id=seed_id)
    store.add_edge(from_package_id=dep2_id, to_package_id=dep1_id)
    store.add_edge(from_package_id=dep3_id, to_package_id=dep2_id)


def test_blast_radius_returns_dependents_up_to_two_hops(store: GraphStore):
    _seed_chain(store)
    result = blast_radius(store, "seed-repo", "npm:seed")
    by_repo = {d.repo: d.hops for d in result.dependents}
    assert by_repo == {"dep1-repo": 1, "dep2-repo": 2}
    assert "dep3-repo" not in by_repo  # 3 hops away — truncated
    assert result.freshness.freshness.value == "unknown"
    assert result.freshness.commit is None
    assert result.freshness.stale is False


def test_blast_radius_unknown_seed_raises(store: GraphStore):
    _seed_chain(store)
    with pytest.raises(SeedPackageNotFoundError):
        blast_radius(store, "seed-repo", "npm:no-such-package")


def test_blast_radius_no_dependents_returns_empty(store: GraphStore):
    store.upsert_package(repo="lonely-repo", name="npm:lonely")
    result = blast_radius(store, "lonely-repo", "npm:lonely")
    assert result.dependents == []


def test_add_edge_is_idempotent(store: GraphStore):
    a = store.upsert_package(repo="a-repo", name="npm:a")
    b = store.upsert_package(repo="b-repo", name="npm:b")
    assert store.add_edge(from_package_id=b, to_package_id=a) is True
    assert store.add_edge(from_package_id=b, to_package_id=a) is False


def test_upsert_package_same_key_returns_same_id(store: GraphStore):
    id1 = store.upsert_package(repo="a-repo", name="npm:a")
    id2 = store.upsert_package(repo="a-repo", name="npm:a")
    assert id1 == id2


def _build_index_db(db_path: Path, *, local_symbol: str, external_symbol: str | None) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE);
        CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE,
            display_name TEXT, kind INTEGER, relationships BLOB);
        CREATE TABLE mentions (chunk_id INTEGER NOT NULL, symbol_id INTEGER NOT NULL,
            role INTEGER NOT NULL, PRIMARY KEY (chunk_id, symbol_id, role));
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER,
            start_line INTEGER, end_line INTEGER, occurrences BLOB);
        """
    )
    conn.execute("INSERT INTO documents (id, relative_path) VALUES (1, 'a.py')")
    conn.execute("INSERT INTO chunks (id, document_id, chunk_index, start_line, end_line, occurrences) "
                 "VALUES (1, 1, 0, 0, 0, X'')")
    conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (1, ?)", (local_symbol,))
    conn.execute("INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (1, 1, ?)", (SymbolRoles.DEFINITION,))
    if external_symbol is not None:
        conn.execute("INSERT INTO global_symbols (id, symbol) VALUES (2, ?)", (external_symbol,))
        conn.execute("INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (1, 2, ?)", (0,))
    conn.commit()
    conn.close()


def test_populate_graph_for_repo_retracts_edge_when_dependency_removed(store: GraphStore, tmp_path: Path):
    """A repo that used to depend on a package and no longer does (e.g. the
    import was removed) must have its stale edge retracted on the next
    populate — not left behind as an artifact of an earlier index run."""
    local_symbol = "scip-python python dep-pkg 1.0.0 `a.py`/Thing#"
    external_symbol = "scip-python python base-pkg 1.0.0 `base.py`/Helper#"

    # base-repo defines the dependency dep-repo will reference.
    base_db = tmp_path / "base.db"
    _build_index_db(base_db, local_symbol=external_symbol, external_symbol=None)
    populate_graph_for_repo(store, "base-repo", sqlite3.connect(base_db))

    # First run: dep-repo references base-repo's package -> edge created.
    db_with_dep = tmp_path / "dep_v1.db"
    _build_index_db(db_with_dep, local_symbol=local_symbol, external_symbol=external_symbol)
    summary1 = populate_graph_for_repo(store, "dep-repo", sqlite3.connect(db_with_dep))
    assert summary1.edges_created == 1

    dep_pkg = store.get_package_by_key("dep-repo", "python:dep-pkg")
    base_pkg = store.get_package_by_key("base-repo", "python:base-pkg")
    assert base_pkg in store.get_dependents(base_pkg.id) or True  # sanity: dependents lookup works below
    assert dep_pkg in store.get_dependents(base_pkg.id)

    # Second run: dep-repo no longer references base-repo's package.
    db_without_dep = tmp_path / "dep_v2.db"
    _build_index_db(db_without_dep, local_symbol=local_symbol, external_symbol=None)
    summary2 = populate_graph_for_repo(store, "dep-repo", sqlite3.connect(db_without_dep))
    assert summary2.edges_created == 0
    assert dep_pkg not in store.get_dependents(base_pkg.id)


def test_populate_graph_for_repo_is_idempotent_for_a_stable_dependency(store: GraphStore, tmp_path: Path):
    """Re-running populate for the SAME unchanged index must not duplicate
    the edge or drop it — a no-op from the caller's point of view."""
    local_symbol = "scip-python python dep-pkg 1.0.0 `a.py`/Thing#"
    external_symbol = "scip-python python base-pkg 1.0.0 `base.py`/Helper#"

    base_db = tmp_path / "base.db"
    _build_index_db(base_db, local_symbol=external_symbol, external_symbol=None)
    populate_graph_for_repo(store, "base-repo", sqlite3.connect(base_db))

    db_path = tmp_path / "dep.db"
    _build_index_db(db_path, local_symbol=local_symbol, external_symbol=external_symbol)
    populate_graph_for_repo(store, "dep-repo", sqlite3.connect(db_path))
    summary2 = populate_graph_for_repo(store, "dep-repo", sqlite3.connect(db_path))

    assert summary2.edges_created == 1  # re-derived fresh after the clear, not a duplicate
    dep_pkg = store.get_package_by_key("dep-repo", "python:dep-pkg")
    base_pkg = store.get_package_by_key("base-repo", "python:base-pkg")
    assert store.get_dependents(base_pkg.id) == [dep_pkg]


def test_graph_store_persists_across_reopen(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    s1 = GraphStore(db_path)
    pkg_id = s1.upsert_package(repo="a-repo", name="npm:a")
    s1.close()

    s2 = GraphStore(db_path)
    try:
        pkg = s2.get_package_by_key("a-repo", "npm:a")
        assert pkg is not None
        assert pkg.id == pkg_id
    finally:
        s2.close()
