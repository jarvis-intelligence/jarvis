from __future__ import annotations

from pathlib import Path

import pytest

from jarvis import config


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


def test_swift_cache_dir_under_data_dir(monkeypatch, tmp_path):
    """Per-repo keyed (D-05): deterministic isolation so IndexStores from
    different repos never interfere with each other."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert config.swift_cache_dir("my-repo") == tmp_path / "cache" / "scip-swift" / "my-repo"


def test_swift_cache_dir_honors_explicit_root(tmp_path):
    """The root override wins over the env var, exactly like lancedb_dir."""
    assert config.swift_cache_dir("my-repo", tmp_path) == tmp_path / "cache" / "scip-swift" / "my-repo"


def test_ignored_dirs_shared_with_index_cli():
    from jarvis import index_cli
    assert index_cli._IGNORED_DIRS is config.IGNORED_DIRS
    assert "node_modules" in config.IGNORED_DIRS


def test_shim_dir_is_under_data_dir(tmp_path, monkeypatch):
    from jarvis import config

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert config.shim_dir() == tmp_path / "shims"


def test_shim_dir_honors_explicit_root(tmp_path):
    """The root override wins over the env var, matching data_dir()."""
    from jarvis import config

    assert config.shim_dir(tmp_path) == tmp_path / "shims"


def test_default_data_dir_is_dot_jarvis():
    """The data dir is user-visible and documented; a silent change would
    orphan every published index."""
    assert config.DEFAULT_DATA_DIR == Path.home() / ".jarvis"


def test_data_dir_honours_the_jarvis_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert config.data_dir() == tmp_path


def test_data_dir_ignores_the_old_codeintel_env_var(monkeypatch):
    """The old prefix must not keep working: a stale CODEINTEL_DATA_DIR left in
    a shell profile would silently point jarvis at the abandoned tree."""
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.setenv("CODEINTEL_DATA_DIR", "/tmp/should-be-ignored")
    assert config.data_dir() == config.DEFAULT_DATA_DIR



def test_removed_fallback_env_var_has_no_reader():
    """Superseded by reversible --scip/--no-scip (spec §12): the env tier
    is gone from config entirely, not merely ignored — no code path may
    resurrect a global fallback default."""
    assert not hasattr(config, "fallback_search_only_from_env")


def test_removed_fallback_env_var_triggers_only_a_note(capsys, monkeypatch):
    """main() warns once that the removed variable no longer controls
    anything, naming the replacement — never silently ignoring a variable
    a shell profile may still export."""
    from jarvis.index_cli import _warn_removed_env

    monkeypatch.setenv("JARVIS_FALLBACK_SEARCH_ONLY", "1")
    _warn_removed_env()
    err = capsys.readouterr().err
    assert "no longer read" in err
    assert "--scip" in err

