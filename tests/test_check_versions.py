"""Tests for scripts/check_versions.py -- the version-drift guard.

Each test builds a miniature repo under tmp_path rather than reading the real
files, so a failure here reflects the checker's logic and never whatever the
working tree happens to hold at the time.

The Claude Code and Codex plugins are absent deliberately: their source of
truth moved to jarvis-intelligence/jarvis-index and they version
independently, so the checker reads neither plugin/ nor .codex-plugin/.
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


def _build(root: Path, *, version="0.2.1", pyproject_version=None):
    """Write the version-bearing files into root."""
    pyproject_version = pyproject_version or version
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "jarvis-mcp"\nversion = "{pyproject_version}"\n'
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


def test_consistent_versions_pass(tmp_path):
    _build(tmp_path)
    assert check_versions.check(tmp_path) == []


def test_pyproject_version_drift_is_reported(tmp_path):
    _build(tmp_path, version="0.2.1", pyproject_version="0.2.0")
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    # A bare "versions differ" would force the reader to diff the files by
    # hand, so the message must name the files and show both values.
    assert "pyproject.toml" in problems[0]
    assert "0.2.0" in problems[0] and "0.2.1" in problems[0]


def test_server_json_package_version_drift_is_reported(tmp_path):
    _build(tmp_path)
    server = json.loads((tmp_path / "server.json").read_text())
    server["packages"][0]["version"] = "0.2.0"
    (tmp_path / "server.json").write_text(json.dumps(server))
    problems = check_versions.check(tmp_path)
    assert len(problems) == 1
    assert "packages[0].version" in problems[0]
