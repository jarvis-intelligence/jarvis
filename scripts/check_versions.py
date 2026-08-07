"""Assert every file declaring the release version agrees.

Three declarations carry the release version and must be identical:

    pyproject.toml                     [project] version
    server.json                        version
    server.json                        packages[0].version

The Claude Code and Codex plugins are NOT checked here: their source of
truth lives in jarvis-intelligence/jarvis-index (plugin/.claude-plugin/
plugin.json and .codex-plugin/plugin.json there), where they version
independently of the PyPI package. The plugin's .mcp.json floor is a
compatibility minimum against PyPI, maintained by hand in that repo.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_declared_versions(root: Path) -> dict[str, str]:
    """Map a human-readable location -> the version string declared there."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    server = json.loads((root / "server.json").read_text())
    return {
        "pyproject.toml [project] version": pyproject["project"]["version"],
        "server.json version": server["version"],
        "server.json packages[0].version": server["packages"][0]["version"],
    }


def check(root: Path) -> list[str]:
    """Return a list of problems. An empty list means everything agrees."""
    problems: list[str] = []

    declared = read_declared_versions(root)
    if len(set(declared.values())) > 1:
        detail = "\n".join(f"    {where}: {what}" for where, what in declared.items())
        problems.append(f"release version differs between files:\n{detail}")

    return problems


def main() -> int:
    problems = check(REPO_ROOT)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("versions consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
