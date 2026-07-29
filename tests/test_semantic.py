"""Unit tests for semantic store + RRF fusion."""
import pytest

from codeintel.search import ZoektHit
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
