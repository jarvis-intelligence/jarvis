from __future__ import annotations

import pytest

from codeintel import config


def test_repo_slug_normalizes_case_and_unsafe_chars():
    assert config.repo_slug("My Repo!!") == "my-repo"


def test_repo_slug_rejects_bare_dot():
    with pytest.raises(ValueError):
        config.repo_slug(".")


def test_repo_slug_rejects_bare_dotdot():
    with pytest.raises(ValueError):
        config.repo_slug("..")


def test_repo_slug_rejects_empty_input():
    with pytest.raises(ValueError):
        config.repo_slug("   ")


def test_repo_slug_strips_path_separators_to_single_component():
    slug = config.repo_slug("../../etc")
    assert "/" not in slug
    assert "\\" not in slug


def test_lancedb_dir_under_data_dir(tmp_path):
    assert config.lancedb_dir(tmp_path) == tmp_path / "lancedb"


def test_ignored_dirs_shared_with_index_cli():
    from codeintel import index_cli
    assert index_cli._IGNORED_DIRS is config.IGNORED_DIRS
    assert "node_modules" in config.IGNORED_DIRS


def test_shim_dir_is_under_data_dir(tmp_path, monkeypatch):
    from codeintel import config

    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path))
    assert config.shim_dir() == tmp_path / "shims"


def test_shim_dir_honors_explicit_root(tmp_path):
    """The root override wins over the env var, matching data_dir()."""
    from codeintel import config

    assert config.shim_dir(tmp_path) == tmp_path / "shims"
