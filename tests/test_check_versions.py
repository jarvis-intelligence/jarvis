"""Tests for scripts/check_versions.py -- the version-drift guard.

Each test builds a miniature repo under tmp_path rather than reading the real
files, so a failure here reflects the checker's logic and never whatever the
working tree happens to hold at the time.
"""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_versions.py"


def _load_checker():
    """Import the script by path -- scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_versions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_versions = _load_checker()


def _build(root: Path, *, version="0.2.1", plugin_version=None,
            codex_plugin_version=None, floor="0.2.1"):
    """Write the five version-bearing files plus .mcp.json into root."""
    plugin_version = plugin_version or version
    codex_plugin_version = codex_plugin_version or version
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "jarvis-mcp"\nversion = "{version}"\n'
    )
    (root / "server.json").write_text(
        json.dumps(
            {
                "version": version,
                "packages": [
                    {"identifier": "jarvis-mcp", "version": version}
                ],
            }
        )
    )
    plugin_dir = root / "plugin" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "jarvis", "version": plugin_version})
    )
    codex_plugin_dir = root / ".codex-plugin"
    codex_plugin_dir.mkdir(parents=True)
    (codex_plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "jarvis", "version": codex_plugin_version})
    )
    (root / "plugin" / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "jarvis": {
                        "command": "uvx",
                        "args": [
                            "--from",
                            f"jarvis-mcp>={floor}",
                            "jarvis-server",
                        ],
                    }
                }
            }
        )
    )


def test_consistent_versions_pass(tmp_path):
    _build(tmp_path)
    assert check_versions.check(tmp_path) == []


def test_plugin_version_drift_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", plugin_version="0.2.0")
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    # A bare "versions differ" would force the reader to diff four files by
    # hand, so the message must name the files and show both values.
    assert "plugin.json" in problems[0]
    assert "0.2.0" in problems[0] and "0.2.1" in problems[0]


def test_codex_plugin_version_drift_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", codex_plugin_version="0.2.0")
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    assert ".codex-plugin/plugin.json" in problems[0]
    assert "0.2.0" in problems[0] and "0.2.1" in problems[0]

def test_floor_ahead_of_release_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", floor="0.3.0")
    problems = check_versions.check(tmp_path)
    assert any("0.3.0" in p and "ahead" in p for p in problems)


def test_floor_behind_release_is_allowed(tmp_path):
    # The floor is the oldest tolerated package, not the current one, so it is
    # expected to lag. Flagging this would make the check useless.
    _build(tmp_path, version="0.3.0", floor="0.2.1")
    assert check_versions.check(tmp_path) == []


def test_package_constant_is_jarvis_mcp():
    """The checker asserts plugin/.mcp.json names the right distribution. If
    this constant drifts from pyproject's name, the check passes while the
    plugin installs nothing."""
    assert check_versions.PACKAGE == "jarvis-mcp"


def test_floor_is_read_from_the_jarvis_server_key(tmp_path):
    """The mcpServers key is the MCP server name users see, and the checker
    looks the floor up by it. A stale key raises KeyError, not a clear error."""
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / ".mcp.json").write_text(
        '{"mcpServers": {"jarvis": {"command": "uvx",'
        ' "args": ["--from", "jarvis-mcp>=0.5.0", "jarvis-server"]}}}'
    )
    assert check_versions.read_floor(tmp_path) == "0.5.0"
