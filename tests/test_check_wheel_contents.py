import importlib.util
import zipfile
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_wheel_contents",
    Path(__file__).resolve().parent.parent / "scripts" / "check_wheel_contents.py",
)
check_wheel_contents = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_wheel_contents)


def _wheel(tmp_path, names):
    path = tmp_path / "jarvis_mcp-0.0.0-cp312-cp312-macosx_11_0_arm64.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return str(path)


def test_compiled_wheel_with_allowlisted_sources_passes(tmp_path):
    wheel = _wheel(tmp_path, [
        "jarvis/__init__.py",
        "jarvis/scip_pb2.py",
        "jarvis/query.cpython-312-darwin.so",
        "jarvis_mcp-0.0.0.dist-info/RECORD",
    ])
    assert check_wheel_contents.check(wheel) == []


def test_leaked_module_source_is_reported(tmp_path):
    wheel = _wheel(tmp_path, [
        "jarvis/__init__.py",
        "jarvis/scip_pb2.py",
        "jarvis/query.cpython-312-darwin.so",
        "jarvis/query.py",
    ])
    problems = check_wheel_contents.check(wheel)
    assert any("jarvis/query.py" in p for p in problems)


def test_cython_c_artifact_is_reported(tmp_path):
    wheel = _wheel(tmp_path, [
        "jarvis/__init__.py",
        "jarvis/scip_pb2.py",
        "jarvis/query.cpython-312-darwin.so",
        "jarvis/query.c",
    ])
    problems = check_wheel_contents.check(wheel)
    assert any("jarvis/query.c" in p for p in problems)


def test_pure_python_fallback_wheel_is_reported(tmp_path):
    wheel = _wheel(tmp_path, ["jarvis/__init__.py", "jarvis/scip_pb2.py"])
    problems = check_wheel_contents.check(wheel)
    assert any("no compiled" in p for p in problems)


def test_missing_allowlisted_files_is_reported(tmp_path):
    wheel = _wheel(tmp_path, ["jarvis/query.cpython-312-darwin.so"])
    problems = check_wheel_contents.check(wheel)
    assert any("jarvis/__init__.py" in p for p in problems)
    assert any("jarvis/scip_pb2.py" in p for p in problems)
