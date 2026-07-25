from __future__ import annotations

from pathlib import Path

from codeintel.registry import Registry


def test_upsert_then_get_roundtrips(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    repo = reg.get("my-repo")
    assert repo is not None
    assert repo.path == "/repos/my-repo"
    assert repo.language == "python"
    assert repo.commit_sha == "abc123"
    assert repo.status == "indexed"
    reg.close()


def test_upsert_same_slug_updates_in_place(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    reg.upsert("my-repo", "/repos/my-repo", "python", "def456", "indexed")
    assert len(reg.list()) == 1
    assert reg.get("my-repo").commit_sha == "def456"
    reg.close()


def test_mark_status_transition(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", None, "indexing")
    reg.mark_status("my-repo", "failed")
    assert reg.get("my-repo").status == "failed"
    reg.close()


def test_get_unknown_slug_returns_none(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    assert reg.get("nope") is None
    reg.close()


def test_list_orders_by_slug(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("zeta", "/z", "python", None, "indexed")
    reg.upsert("alpha", "/a", "python", None, "indexed")
    assert [r.slug for r in reg.list()] == ["alpha", "zeta"]
    reg.close()


def test_forget_removes_row_and_reports_whether_it_existed(tmp_path: Path):
    reg = Registry(tmp_path / "registry.db")
    reg.upsert("my-repo", "/repos/my-repo", "python", None, "indexed")
    assert reg.forget("my-repo") is True
    assert reg.get("my-repo") is None
    assert reg.forget("my-repo") is False
    reg.close()


def test_registry_persists_across_reopen(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    Registry(db_path).upsert("my-repo", "/repos/my-repo", "python", "abc123", "indexed")
    reopened = Registry(db_path)
    assert reopened.get("my-repo").commit_sha == "abc123"
    reopened.close()
