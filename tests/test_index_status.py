"""getIndexStatus: fresh -> commit -> stale transition, and the missing-repo
case — using a real temp git repo instead of a live hosted-git API call."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis import config
from jarvis.index_reader import IndexConnectionCache
from jarvis.models import Freshness
from jarvis.query import QueryService
from tests.fixtures.synthetic_index import COMMIT_SHA, build_published_index

REPO = "toy-repo"


def _init_git_repo(path: Path, commit_sha: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", commit_sha], cwd=path, check=True)


@pytest.fixture
def query_service(tmp_path: Path) -> QueryService:
    build_published_index(tmp_path / "data", config.PROJECT, REPO, config.BRANCH)
    return QueryService(IndexConnectionCache(str(tmp_path / "data")))


def test_index_status_missing_repo_returns_indexed_false(query_service: QueryService):
    indexed, freshness = query_service.get_index_status("never-published")
    assert indexed is False
    assert freshness.freshness == Freshness.UNKNOWN
    assert freshness.stale is False


def test_index_status_without_repo_path_reports_fresh(query_service: QueryService):
    """No `repo_path` given -> no live comparison possible; the published
    commit is reported as fresh (known, not yet checked), never stale."""
    indexed, freshness = query_service.get_index_status(REPO)
    assert indexed is True
    assert freshness.commit == COMMIT_SHA
    assert freshness.freshness == Freshness.FRESH
    assert freshness.stale is False


def test_index_status_fresh_when_head_matches_published_commit(query_service: QueryService, tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir, "unused")
    monkeypatch.setattr(
        "jarvis.query._git_head", lambda repo_path: COMMIT_SHA
    )

    indexed, freshness = query_service.get_index_status(REPO, repo_path=str(repo_dir))
    assert indexed is True
    assert freshness.stale is False
    assert freshness.freshness == Freshness.FRESH


def test_index_status_stale_after_new_commit(query_service: QueryService, tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir, "unused")
    monkeypatch.setattr(
        "jarvis.query._git_head", lambda repo_path: "some-other-commit-sha"
    )

    indexed, freshness = query_service.get_index_status(REPO, repo_path=str(repo_dir))
    assert indexed is True
    assert freshness.stale is True
    assert freshness.freshness == Freshness.STALE


def test_index_status_unresolvable_git_head_reports_unknown(query_service: QueryService, tmp_path: Path):
    indexed, freshness = query_service.get_index_status(REPO, repo_path=str(tmp_path / "not-a-git-repo"))
    assert indexed is True
    assert freshness.freshness == Freshness.UNKNOWN
    assert freshness.stale is False
