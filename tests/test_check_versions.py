"""Tests for scripts/check_versions.py -- the version-drift guard.

Each test builds a miniature repo under tmp_path rather than reading the real
files, so a failure here reflects the checker's logic and never whatever the
working tree happens to hold at the time.

The Claude Code plugin is absent deliberately: its source of truth moved to
jarvis-intelligence/jarvis-index and it versions independently, so the checker
no longer reads plugin/ at all.
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


def _build(root: Path, *, version="0.2.1", codex_plugin_version=None):
    """Write the four version-bearing files into root."""
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
    codex_plugin_dir = root / ".codex-plugin"
    codex_plugin_dir.mkdir(parents=True)
    (codex_plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "jarvis", "version": codex_plugin_version})
    )


def test_consistent_versions_pass(tmp_path):
    _build(tmp_path)
    assert check_versions.check(tmp_path) == []


def test_codex_plugin_version_drift_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", codex_plugin_version="0.2.0")
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    # A bare "versions differ" would force the reader to diff the files by
    # hand, so the message must name the files and show both values.
    assert ".codex-plugin/plugin.json" in problems[0]
    assert "0.2.0" in problems[0] and "0.2.1" in problems[0]


def test_server_json_package_version_drift_is_reported(tmp_path):
    _build(tmp_path)
    server = json.loads((tmp_path / "server.json").read_text())
    server["packages"][0]["version"] = "0.2.0"
    (tmp_path / "server.json").write_text(json.dumps(server))
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    assert "packages[0].version" in problems[0]
