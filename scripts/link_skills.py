"""Symlink codeintel skills (.claude/skills/codeintel-*) into a target dir.

The skills live in .claude/skills/ (ck:skill-creator's required location, for
Claude Code auto-discovery). A ZCode agent loads from ~/.zcode/skills/ instead,
so this creates one symlink per codeintel skill there. The repo file stays the
single source of truth; the symlink is only a loader hook.

Idempotent: existing valid links are skipped; broken symlinks are replaced.

Usage:
    python scripts/link_skills.py [--target ~/.zcode/skills]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_PREFIX = "codeintel-"
DEFAULT_TARGET = Path.home() / ".zcode" / "skills"


def link_skills(repo_root: Path, target_dir: Path) -> list[str]:
    """Create one symlink per `.claude/skills/codeintel-*` dir in target_dir.

    Returns the paths of symlinks created on this run (empty if all already
    linked). Existing valid symlinks are skipped; broken symlinks are replaced.
    """
    skills_root = repo_root / ".claude" / "skills"
    if not skills_root.is_dir():
        raise FileNotFoundError(f"no .claude/skills/ dir at {repo_root}")

    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir() or not skill_dir.name.startswith(SKILL_PREFIX):
            continue
        link = target_dir / skill_dir.name
        # Skip an existing, valid symlink.
        if link.is_symlink() and link.exists():
            continue
        # Replace a broken symlink (or stale file) so re-runs self-heal.
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(skill_dir.resolve())
        created.append(str(link))

    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", type=Path, default=DEFAULT_TARGET,
        help=f"dir to symlink skills into (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent,
        help="repo root containing .claude/skills/ (default: this repo)",
    )
    args = parser.parse_args(argv)

    try:
        created = link_skills(args.repo_root, args.target)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if created:
        print(f"Linked {len(created)} skill(s) into {args.target}:")
        for path in created:
            print(f"  {path}")
    else:
        print(f"All codeintel skills already linked in {args.target}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
