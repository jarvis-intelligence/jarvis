"""Tests for scripts/link_skills.py — symlink helper for codeintel skills."""
from pathlib import Path

import importlib.util
import sys


def _load_link_skills():
    """Load scripts/link_skills.py as a module (it's not a package)."""
    spec = importlib.util.spec_from_file_location(
        "link_skills", Path(__file__).resolve().parent.parent / "scripts" / "link_skills.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["link_skills"] = module
    spec.loader.exec_module(module)
    return module


def _make_skill(repo_root: Path, name: str) -> None:
    skill_dir = repo_root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n# {name}\n")


def test_creates_symlink_for_each_codeintel_skill(tmp_path):
    link_skills = _load_link_skills()
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    for name in ("codeintel-setup", "codeintel-use", "codeintel-issues"):
        _make_skill(repo_root, name)
    # a non-codeintel skill must be ignored
    _make_skill(repo_root, "other-skill")

    created = link_skills.link_skills(repo_root, target)

    assert sorted(Path(p).name for p in created) == [
        "codeintel-issues",
        "codeintel-setup",
        "codeintel-use",
    ]
    for name in ("codeintel-setup", "codeintel-use", "codeintel-issues"):
        link = target / name
        assert link.is_symlink()
        assert (link / "SKILL.md").exists()


def test_idempotent_skips_existing_links(tmp_path):
    link_skills = _load_link_skills()
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    _make_skill(repo_root, "codeintel-setup")

    first = link_skills.link_skills(repo_root, target)
    second = link_skills.link_skills(repo_root, target)

    assert len(first) == 1
    assert second == []  # nothing new created on second run
    assert (target / "codeintel-setup" / "SKILL.md").exists()  # still linked


def test_replaces_broken_symlink(tmp_path):
    link_skills = _load_link_skills()
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    _make_skill(repo_root, "codeintel-setup")
    # pre-create a broken symlink at the target
    target.mkdir()
    (target / "codeintel-setup").symlink_to(tmp_path / "does-not-exist")

    created = link_skills.link_skills(repo_root, target)

    assert len(created) == 1
    link = target / "codeintel-setup"
    assert link.is_symlink()
    assert (link / "SKILL.md").exists()  # now resolves
