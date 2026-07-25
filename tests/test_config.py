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
