"""Unit tests for semantic store + RRF fusion."""
import sqlite3
import sys

import pytest

from jarvis.search import ZoektHit, ZoektSearchResult, ZoektUnavailableError
from jarvis.semantic import FusedHit, reciprocal_rank_fusion
from jarvis.symbol_search import SymbolHit
from jarvis.symbols import DescriptorKind


def _row(path, start, end, symbol="s", content="body"):
    return {"file_path": path, "start_line": start, "end_line": end,
            "symbol_name": symbol, "content": content}


def test_rrf_scores_are_hand_computed():
    vector_rows = [_row("a.py", 1, 10), _row("b.py", 1, 10)]
    zoekt_hits = [ZoektHit(repo="r", path="a.py", line_number=5, line_text="x")]
    fused = reciprocal_rank_fusion("r", vector_rows, zoekt_hits, k=60)
    by_path = {f.file_path: f for f in fused}
    # a.py: vector rank 1 + zoekt rank 1 = 1/61 + 1/61; b.py: vector rank 2 = 1/62
    assert by_path["a.py"].score == pytest.approx(2 / 61)
    assert by_path["b.py"].score == pytest.approx(1 / 62)
    assert fused[0].file_path == "a.py"  # sorted best-first
    assert by_path["a.py"].sources == ("vector", "zoekt")
    assert by_path["b.py"].sources == ("vector",)


def test_zoekt_only_hit_becomes_single_line_entry():
    zoekt_hits = [ZoektHit(repo="r", path="c.py", line_number=7, line_text="the line")]
    fused = reciprocal_rank_fusion("r", [], zoekt_hits)
    assert len(fused) == 1
    hit = fused[0]
    assert (hit.start_line, hit.end_line, hit.content) == (7, 7, "the line")
    assert hit.sources == ("zoekt",) and hit.symbol_name is None


def test_zoekt_hit_outside_chunk_range_does_not_merge():
    vector_rows = [_row("a.py", 1, 4)]
    zoekt_hits = [ZoektHit(repo="r", path="a.py", line_number=99, line_text="x")]
    fused = reciprocal_rank_fusion("r", vector_rows, zoekt_hits)
    assert len(fused) == 2


def test_content_truncated_to_500_chars():
    vector_rows = [_row("a.py", 1, 9, content="z" * 900)]
    fused = reciprocal_rank_fusion("r", vector_rows, [])
    assert len(fused[0].content) == 500


class FakeEmbedder:
    """Deterministic 3-dim embedder; counts embed calls for reuse assertions."""
    def __init__(self, name="fake-model", revision="rev1"):
        self._identity = (name, revision)
        self.embedded: list[str] = []

    def identity(self):
        return self._identity

    def embed_texts(self, texts):
        self.embedded.extend(texts)
        return [[float(len(t) % 97), 1.0, 0.0] for t in texts]

    def embed_query(self, query):
        return self.embed_texts([query])[0]

    def count_oversized(self, texts):
        return 0

    def prefixes(self):
        return ("", "")

    def prefix_warning(self):
        return None


FUNC = 'def f_{n}():\n    """{pad}"""\n    return {n}\n'


def _write_repo(tmp_path, n_files=2):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    for i in range(n_files):
        (repo / f"mod_{i}.py").write_text(FUNC.format(n=i, pad="p" * 1100))
    return repo


@pytest.fixture()
def lancedb_available():
    pytest.importorskip("lancedb")


def test_missing_lancedb_raises_install_hint(tmp_path, monkeypatch):
    from jarvis.embeddings import SemanticExtraMissingError
    from jarvis.semantic import SemanticStore
    from tests.conftest import BlockImportFinder
    monkeypatch.delitem(sys.modules, "lancedb", raising=False)
    monkeypatch.setattr(sys, "meta_path", [BlockImportFinder("lancedb"), *sys.meta_path])
    store = SemanticStore(tmp_path / "lancedb")
    # Escaped: `match` is a regex and [semantic] would read as a character class.
    with pytest.raises(
        SemanticExtraMissingError, match=r"jarvis-mcp\[semantic\]"
    ):
        store.table_identity("myrepo")


def test_index_semantic_writes_rows(tmp_path, lancedb_available):
    from jarvis.chunker import CONTENT_FORMAT
    from jarvis.semantic import SemanticStore, TableIdentity, index_semantic
    repo = _write_repo(tmp_path)
    embedder = FakeEmbedder()
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=embedder)
    assert report.rows == 2
    store = SemanticStore((tmp_path / "data") / "lancedb")
    assert store.table_identity("myrepo") == TableIdentity("fake-model", "rev1", "", "", CONTENT_FORMAT)
    rows = store.rows_by_path("myrepo")
    assert set(rows) == {"mod_0.py", "mod_1.py"}
    assert rows["mod_0.py"][0]["language"] == "python"


def test_unchanged_files_carry_over_without_reembedding(tmp_path, lancedb_available):
    from jarvis.semantic import index_semantic
    repo = _write_repo(tmp_path)
    first = FakeEmbedder()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=first)
    (repo / "mod_1.py").write_text(FUNC.format(n=99, pad="q" * 1100))
    second = FakeEmbedder()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=second)
    assert len(second.embedded) == 1  # only the changed file's chunk
    assert "f_99" in second.embedded[0]


def test_deleted_files_drop_out(tmp_path, lancedb_available):
    from jarvis.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    (repo / "mod_1.py").unlink()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    rows = SemanticStore((tmp_path / "data") / "lancedb").rows_by_path("myrepo")
    assert set(rows) == {"mod_0.py"}


def test_model_change_forces_full_reembed(tmp_path, lancedb_available):
    from jarvis.chunker import CONTENT_FORMAT
    from jarvis.semantic import SemanticStore, TableIdentity, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    changed = FakeEmbedder(revision="rev2")
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=changed)
    assert len(changed.embedded) == 2  # nothing reused across revisions
    identity = SemanticStore((tmp_path / "data") / "lancedb").table_identity("myrepo")
    assert identity == TableIdentity("fake-model", "rev2", "", "", CONTENT_FORMAT)


def test_duplicate_content_embedded_once(tmp_path, lancedb_available):
    """Two files with byte-identical bodies still embed separately, because
    the per-chunk header (`# file: <path>`) differs and content_hash covers
    the header. Dedup only applies to genuinely identical stored content —
    e.g. the same file re-chunked, or two chunks within one file that
    happen to produce identical text."""
    from jarvis.semantic import index_semantic
    repo = tmp_path / "repo"
    repo.mkdir()
    same = FUNC.format(n=1, pad="r" * 1100)
    (repo / "a.py").write_text(same)
    (repo / "b.py").write_text(same)
    embedder = FakeEmbedder()
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=embedder)
    assert report.rows == 2 and len(embedder.embedded) == 2


def _indexed(tmp_path):
    from jarvis.semantic import index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    return tmp_path / "data"


def test_semantic_search_returns_fused_results(tmp_path, lancedb_available, monkeypatch):
    from jarvis import semantic
    data = _indexed(tmp_path)
    from jarvis.search import ZoektSearchResult
    monkeypatch.setattr(semantic, "search_zoekt",
                        lambda url, q: ZoektSearchResult(
                            [ZoektHit(repo="myrepo", path="mod_0.py",
                                      line_number=1, line_text="def f_0():")], 1, 1))
    result = semantic.semantic_search("myrepo", "function zero", root=data,
                                      zoekt_base_url="http://x", model=FakeEmbedder())
    assert result["total"] >= 1 and "warning" not in result
    top = result["results"][0]
    assert top["filePath"] == "mod_0.py" and set(top["sources"]) == {"vector", "zoekt"}


def test_missing_table_raises_reindex_hint(tmp_path, lancedb_available):
    from jarvis.semantic import NoSemanticIndexError, semantic_search
    with pytest.raises(NoSemanticIndexError, match="jarvis reindex nosuch"):
        semantic_search("nosuch", "q", root=tmp_path, model=FakeEmbedder())


def test_query_model_mismatch_uses_table_model_and_warns(tmp_path, lancedb_available, monkeypatch):
    from jarvis import semantic
    data = _indexed(tmp_path)  # table identity: fake-model@rev1
    # semantic_search rebuilds a model from the table's identity; patch the
    # class so it never tries to download "fake-model" from HuggingFace.
    monkeypatch.setattr(
        semantic, "EmbeddingModel",
        lambda model_name, revision, query_prefix, doc_prefix:
            FakeEmbedder(name=model_name, revision=revision),
    )
    configured = FakeEmbedder(name="other-model", revision="rev9")
    result = semantic.semantic_search("myrepo", "query", root=data, model=configured)
    assert "reindex" in result["warning"]
    assert "content format" in result["warning"]
    assert configured.embedded == []  # configured model never used for the query


def test_zoekt_down_degrades_to_vector_only(tmp_path, lancedb_available, monkeypatch):
    from jarvis import semantic

    def _boom(url, q):
        raise ZoektUnavailableError("down")

    monkeypatch.setattr(semantic, "search_zoekt", _boom)
    data = _indexed(tmp_path)
    result = semantic.semantic_search("myrepo", "query", root=data,
                                      zoekt_base_url="http://x", model=FakeEmbedder())
    assert result["total"] >= 1
    assert all(h["sources"] == ["vector"] for h in result["results"])


def test_generated_file_is_skipped_and_reported(tmp_path, lancedb_available):
    from jarvis.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    (repo / "gen.py").write_text("# @generated\n" + FUNC.format(n=7, pad="g" * 1100))
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert [s.file_path for s in report.skipped] == ["gen.py"]
    assert report.skipped[0].reason == "banner:@generated"
    assert report.files == 2          # mod_0.py + mod_1.py, gen.py excluded
    rows = SemanticStore((tmp_path / "data") / "lancedb").rows_by_path("myrepo")
    assert "gen.py" not in rows


def test_force_include_keeps_generated_file(tmp_path, lancedb_available):
    from jarvis.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    (repo / "gen.py").write_text("# @generated\n" + FUNC.format(n=7, pad="g" * 1100))
    report = index_semantic(repo, "myrepo", root=tmp_path / "data",
                            model=FakeEmbedder(), include_prefixes=("gen.py",))
    assert report.skipped == ()
    rows = SemanticStore((tmp_path / "data") / "lancedb").rows_by_path("myrepo")
    assert "gen.py" in rows


def test_oversized_file_is_skipped_with_reason(tmp_path, lancedb_available, monkeypatch):
    from jarvis import chunker
    from jarvis.semantic import index_semantic
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(chunker, "MAX_FILE_BYTES", 10)
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert report.rows == 0
    assert all(s.reason.startswith("too-large:") for s in report.skipped)


def test_force_include_rescues_oversized_file(tmp_path, lancedb_available, monkeypatch):
    from jarvis import chunker
    from jarvis.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(chunker, "MAX_FILE_BYTES", 10)
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder(),
                            include_prefixes=("mod_0.py",))
    assert not any(s.file_path == "mod_0.py" for s in report.skipped)
    rows = SemanticStore((tmp_path / "data") / "lancedb").rows_by_path("myrepo")
    assert "mod_0.py" in rows


def test_stale_generated_rows_are_purged_on_reindex(tmp_path, lancedb_available, monkeypatch):
    """Regression test for the carry-forward trap. A generated file already
    in the table still hashes equal on reindex, so only skipping BEFORE the
    hash check removes it. Filtering after the carry would keep it forever."""
    from jarvis import semantic as semantic_module
    from jarvis.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    (repo / "gen.py").write_text("# @generated\n" + FUNC.format(n=7, pad="g" * 1100))

    # First index with admission disabled, mimicking a table built before
    # the filter existed.
    monkeypatch.setattr(semantic_module, "skip_reason", lambda *a, **k: None)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    store = SemanticStore((tmp_path / "data") / "lancedb")
    assert "gen.py" in store.rows_by_path("myrepo")

    # Second index with the real filter. gen.py is byte-identical, so its
    # file_hash still matches the carried rows.
    monkeypatch.undo()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert "gen.py" not in store.rows_by_path("myrepo")


def test_all_files_skipped_drops_the_table(tmp_path, lancedb_available):
    """An all-generated repo must leave no empty table behind — the
    existing overwrite() path drops it, and semanticSearch then raises
    NoSemanticIndexError while the skip report explains why."""
    from jarvis.semantic import SemanticStore, index_semantic
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gen.py").write_text("# @generated\n" + FUNC.format(n=1, pad="g" * 1100))
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert report.rows == 0 and len(report.skipped) == 1
    assert SemanticStore((tmp_path / "data") / "lancedb").table_identity("myrepo") is None


def test_truncated_is_none_when_counting_fails(tmp_path, lancedb_available):
    """Telemetry must never abort an otherwise-good index."""
    from jarvis.semantic import index_semantic

    class _BrokenCounter(FakeEmbedder):
        def count_oversized(self, texts):
            raise RuntimeError("tokenizer exploded")

    repo = _write_repo(tmp_path)
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=_BrokenCounter())
    assert report.truncated is None
    assert report.rows == 2          # the index itself still succeeded


def test_token_stats_reported_for_new_chunks(tmp_path, lancedb_available):
    from jarvis.semantic import index_semantic
    repo = _write_repo(tmp_path)
    report = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert report.token_stats is not None
    stats = report.token_stats
    assert 0 < stats.p50 <= stats.p90 <= stats.max


def test_token_stats_is_none_when_nothing_new(tmp_path, lancedb_available):
    from jarvis.semantic import index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    again = index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    assert again.token_stats is None


def test_table_identity_round_trips(tmp_path, lancedb_available):
    from jarvis.chunker import CONTENT_FORMAT
    from jarvis.semantic import SemanticStore, TableIdentity, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    identity = SemanticStore((tmp_path / "data") / "lancedb").table_identity("myrepo")
    assert identity == TableIdentity("fake-model", "rev1", "", "", CONTENT_FORMAT)


def test_stale_rows_not_carried_after_content_format_bump(tmp_path, lancedb_available, monkeypatch):
    """Unchanged files are matched by file_hash, not content_hash -- so without
    a content_format in the identity, a file whose bytes never changed would
    carry its old-format rows forward forever."""
    from jarvis import semantic as semantic_module
    from jarvis.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(semantic_module, "CONTENT_FORMAT", 0)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    store = SemanticStore((tmp_path / "data") / "lancedb")
    assert all(r["content_format"] == 0 for rows in store.rows_by_path("myrepo").values()
               for r in rows)

    monkeypatch.undo()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    rows = store.rows_by_path("myrepo")
    assert rows and all(r["content_format"] != 0 for rs in rows.values() for r in rs)


def test_old_table_without_new_columns_forces_rebuild(tmp_path, lancedb_available):
    """An index written before these columns existed must read cleanly and
    trigger a full rebuild rather than raising KeyError."""
    import lancedb
    from jarvis.semantic import SemanticStore
    db_dir = (tmp_path / "data") / "lancedb"
    db_dir.mkdir(parents=True)
    lancedb.connect(str(db_dir)).create_table("myrepo", data=[{
        "chunk_id": "x", "content_hash": "h", "file_hash": "fh", "file_path": "a.py",
        "start_line": 1, "end_line": 2, "symbol_name": "", "language": "python",
        "content": "def a(): pass", "vector": [0.0, 1.0, 0.0],
        "model_name": "fake-model", "model_revision": "rev1",
    }], mode="overwrite")
    identity = SemanticStore(db_dir).table_identity("myrepo")
    assert identity is not None and identity.content_format == 0


def test_query_uses_the_tables_prefixes_not_the_configured_ones(tmp_path, lancedb_available, monkeypatch):
    """A prefix mismatch must rebuild the query model from the TABLE's
    identity. EmbeddingModel is patched because the real one would try to
    download 'fake-model' from HuggingFace on reconstruction."""
    from jarvis import semantic as semantic_module
    from jarvis.semantic import index_semantic, semantic_search
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    monkeypatch.setattr(semantic_module, "search_zoekt",
                        lambda *a, **k: ZoektSearchResult([], 0, 0))

    rebuilt: list[tuple] = []

    def _fake_ctor(model_name=None, revision=None, query_prefix=None, doc_prefix=None, **kw):
        rebuilt.append((model_name, revision, query_prefix, doc_prefix))
        return FakeEmbedder(name=model_name, revision=revision)

    monkeypatch.setattr(semantic_module, "EmbeddingModel", _fake_ctor)

    class _PrefixedEmbedder(FakeEmbedder):
        def prefixes(self):
            return ("WRONG: ", "WRONG: ")

    result = semantic_search("myrepo", "auth", root=tmp_path / "data",
                             model=_PrefixedEmbedder())
    assert "warning" in result and "prefix" in result["warning"].lower()
    # Rebuilt with the table's empty prefixes, not the configured "WRONG: ".
    assert rebuilt == [("fake-model", "rev1", "", "")]


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


def test_symbol_hit_at_definition_line_merges_into_its_own_chunk():
    """Regression: a chunker-derived chunk for a definition starts ON the
    definition's own line (chunker.py's start_line is 1-indexed at the
    node's own first line) -- the symbol hit for that same definition must
    merge into it, not stand alone one line off."""
    definition_line = 9
    vector_rows = [{"file_path": "a.py", "start_line": definition_line, "end_line": 40,
                    "symbol_name": "Thing", "content": "class Thing: ..."}]
    hit = SymbolHit(file_path="a.py", start_line=definition_line, end_line=40,
                    dotted_path="pkg.mod.Thing", kind=DescriptorKind.TYPE)
    fused = reciprocal_rank_fusion("r", vector_rows, [], [hit])
    assert len(fused) == 1
    assert set(fused[0].sources) == {"vector", "symbol"}


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


def test_semantic_search_with_none_scip_conn_matches_previous_behavior(tmp_path, lancedb_available):
    from jarvis import semantic
    data = _indexed(tmp_path)
    result = semantic.semantic_search("myrepo", "function zero", root=data,
                                      model=FakeEmbedder(), scip_conn=None)
    assert result["total"] >= 1
    assert result["results"][0]["sources"] == ["vector"]


def test_semantic_search_symbol_signal_failure_degrades_silently(tmp_path, lancedb_available, monkeypatch):
    from jarvis import semantic
    data = _indexed(tmp_path)

    def _boom(conn, query):
        raise RuntimeError("index went away")

    monkeypatch.setattr(semantic, "search_symbols", _boom)
    result = semantic.semantic_search("myrepo", "function zero", root=data,
                                      model=FakeEmbedder(), scip_conn=sqlite3.connect(":memory:"))
    assert result["total"] >= 1
    assert result["results"][0]["sources"] == ["vector"]
    assert "error" not in result
