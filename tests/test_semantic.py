"""Unit tests for semantic store + RRF fusion."""
import sys

import pytest

from codeintel.search import ZoektHit, ZoektUnavailableError
from codeintel.semantic import FusedHit, reciprocal_rank_fusion


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
    from codeintel.embeddings import SemanticExtraMissingError
    from codeintel.semantic import SemanticStore
    monkeypatch.setitem(sys.modules, "lancedb", None)
    store = SemanticStore(tmp_path / "lancedb")
    with pytest.raises(SemanticExtraMissingError, match="uv sync --extra semantic"):
        store.table_identity("myrepo")


def test_index_semantic_writes_rows(tmp_path, lancedb_available):
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    embedder = FakeEmbedder()
    count = index_semantic(repo, "myrepo", root=tmp_path / "data", model=embedder)
    assert count == 2
    store = SemanticStore((tmp_path / "data") / "lancedb")
    assert store.table_identity("myrepo") == ("fake-model", "rev1")
    rows = store.rows_by_path("myrepo")
    assert set(rows) == {"mod_0.py", "mod_1.py"}
    assert rows["mod_0.py"][0]["language"] == "python"


def test_unchanged_files_carry_over_without_reembedding(tmp_path, lancedb_available):
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    first = FakeEmbedder()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=first)
    (repo / "mod_1.py").write_text(FUNC.format(n=99, pad="q" * 1100))
    second = FakeEmbedder()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=second)
    assert len(second.embedded) == 1  # only the changed file's chunk
    assert "f_99" in second.embedded[0]


def test_deleted_files_drop_out(tmp_path, lancedb_available):
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    (repo / "mod_1.py").unlink()
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    rows = SemanticStore((tmp_path / "data") / "lancedb").rows_by_path("myrepo")
    assert set(rows) == {"mod_0.py"}


def test_model_change_forces_full_reembed(tmp_path, lancedb_available):
    from codeintel.semantic import SemanticStore, index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    changed = FakeEmbedder(revision="rev2")
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=changed)
    assert len(changed.embedded) == 2  # nothing reused across revisions
    identity = SemanticStore((tmp_path / "data") / "lancedb").table_identity("myrepo")
    assert identity == ("fake-model", "rev2")


def test_duplicate_content_embedded_once(tmp_path, lancedb_available):
    from codeintel.semantic import index_semantic
    repo = tmp_path / "repo"
    repo.mkdir()
    same = FUNC.format(n=1, pad="r" * 1100)
    (repo / "a.py").write_text(same)
    (repo / "b.py").write_text(same)
    embedder = FakeEmbedder()
    count = index_semantic(repo, "myrepo", root=tmp_path / "data", model=embedder)
    assert count == 2 and len(embedder.embedded) == 1


def _indexed(tmp_path):
    from codeintel.semantic import index_semantic
    repo = _write_repo(tmp_path)
    index_semantic(repo, "myrepo", root=tmp_path / "data", model=FakeEmbedder())
    return tmp_path / "data"


def test_semantic_search_returns_fused_results(tmp_path, lancedb_available, monkeypatch):
    from codeintel import semantic
    data = _indexed(tmp_path)
    monkeypatch.setattr(semantic, "search_zoekt",
                        lambda url, q: [ZoektHit(repo="myrepo", path="mod_0.py",
                                                 line_number=1, line_text="def f_0():")])
    result = semantic.semantic_search("myrepo", "function zero", root=data,
                                      zoekt_base_url="http://x", model=FakeEmbedder())
    assert result["total"] >= 1 and "warning" not in result
    top = result["results"][0]
    assert top["filePath"] == "mod_0.py" and set(top["sources"]) == {"vector", "zoekt"}


def test_missing_table_raises_reindex_hint(tmp_path, lancedb_available):
    from codeintel.semantic import NoSemanticIndexError, semantic_search
    with pytest.raises(NoSemanticIndexError, match="codeintel reindex nosuch"):
        semantic_search("nosuch", "q", root=tmp_path, model=FakeEmbedder())


def test_query_model_mismatch_uses_table_model_and_warns(tmp_path, lancedb_available, monkeypatch):
    from codeintel import semantic
    data = _indexed(tmp_path)  # table identity: fake-model@rev1
    # semantic_search rebuilds a model from the table's identity; patch the
    # class so it never tries to download "fake-model" from HuggingFace.
    monkeypatch.setattr(
        semantic, "EmbeddingModel",
        lambda model_name, revision: FakeEmbedder(name=model_name, revision=revision),
    )
    configured = FakeEmbedder(name="other-model", revision="rev9")
    result = semantic.semantic_search("myrepo", "query", root=data, model=configured)
    assert "reindex" in result["warning"]
    assert configured.embedded == []  # configured model never used for the query


def test_zoekt_down_degrades_to_vector_only(tmp_path, lancedb_available, monkeypatch):
    from codeintel import semantic

    def _boom(url, q):
        raise ZoektUnavailableError("down")

    monkeypatch.setattr(semantic, "search_zoekt", _boom)
    data = _indexed(tmp_path)
    result = semantic.semantic_search("myrepo", "query", root=data,
                                      zoekt_base_url="http://x", model=FakeEmbedder())
    assert result["total"] >= 1
    assert all(h["sources"] == ["vector"] for h in result["results"])
