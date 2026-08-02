"""Assert every file declaring the release version agrees.

Five files carry the release version and must be identical:

    pyproject.toml                     [project] version
    server.json                        version
    server.json                        packages[0].version
    plugin/.claude-plugin/plugin.json  version
    .codex-plugin/plugin.json          version

.claude-plugin/marketplace.json deliberately omits a version for its plugin
entry. The field is optional, and leaving it out removes a sixth place to drift.

plugin/.mcp.json carries something different in kind: the OLDEST package the
plugin tolerates, in its `--from` specifier. That is a compatibility floor, not
a release version, so it is checked with <= rather than ==. Auto-syncing it to
the current version would force needless upgrades on users and would make this
check assert nothing.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGE = "codeintel-navigation-mcp"


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse a plain X.Y.Z version for ordering.

    Deliberately does not handle pre-release or local segments: this project
    ships plain semver, and an int tuple avoids depending on `packaging`, which
    is only ever present transitively here. A non-numeric segment raises rather
    than silently comparing wrong.
    """
    return tuple(int(part) for part in version.split("."))


def read_declared_versions(root: Path) -> dict[str, str]:
    """Map a human-readable location -> the version string declared there."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    server = json.loads((root / "server.json").read_text())
    plugin = json.loads(
        (root / "plugin" / ".claude-plugin" / "plugin.json").read_text()
    )
    codex_plugin = json.loads(
        (root / ".codex-plugin" / "plugin.json").read_text()
    )
    return {
        "pyproject.toml [project] version": pyproject["project"]["version"],
        "server.json version": server["version"],
        "server.json packages[0].version": server["packages"][0]["version"],
        "plugin/.claude-plugin/plugin.json version": plugin["version"],
        ".codex-plugin/plugin.json version": codex_plugin["version"],
    }


def read_floor(root: Path) -> str:
    """Extract the >= floor from plugin/.mcp.json's `--from` specifier."""
    mcp = json.loads((root / "plugin" / ".mcp.json").read_text())
    args = mcp["mcpServers"]["codeintel"]["args"]
    spec = args[args.index("--from") + 1]
    name, separator, floor = spec.partition(">=")
    if not separator:
        raise ValueError(f"--from spec {spec!r} declares no >= floor")
    if name != PACKAGE:
        raise ValueError(f"--from names {name!r}, expected {PACKAGE!r}")
    return floor


def check(root: Path) -> list[str]:
    """Return a list of problems. An empty list means everything agrees."""
    problems: list[str] = []

    declared = read_declared_versions(root)
    if len(set(declared.values())) > 1:
        detail = "\n".join(f"    {where}: {what}" for where, what in declared.items())
        problems.append(f"release version differs between files:\n{detail}")

    version = declared["pyproject.toml [project] version"]
    floor = read_floor(root)
    if _version_tuple(floor) > _version_tuple(version):
        problems.append(
            f"plugin/.mcp.json floor {floor} is ahead of the released version "
            f"{version}: the plugin would resolve to nothing installable"
        )

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
