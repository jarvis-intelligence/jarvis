"""Tests for setup.sh — the dependency bootstrapper.

Each test sources setup.sh with CODEINTEL_SETUP_SOURCED=1 (which suppresses
main()) and then calls one function, so functions are tested in isolation
without performing a real install.
"""

import shutil
import subprocess
from pathlib import Path

SETUP_SH = Path(__file__).parent.parent / "setup.sh"

# Prefer dash over sh. macOS /bin/sh is bash in POSIX mode and still ACCEPTS
# bashisms -- verified: `[[ ]]` and `arr=(a b c)` both work under it. Testing
# with it would give false confidence. dash rejects both, matching what a
# Debian/Ubuntu user gets from `curl | sh`.
# Install with `brew install dash` if missing.
POSIX_SH = shutil.which("dash") or "sh"


def run_func(snippet: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source setup.sh, then run `snippet`. Returns the completed process."""
    full_env = {"CODEINTEL_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if env:
        full_env.update(env)
    return subprocess.run(
        [POSIX_SH, "-c", f". {SETUP_SH}\n{snippet}"],
        capture_output=True,
        text=True,
        env=full_env,
    )


def test_dash_is_available_for_honest_bashism_detection():
    """Guard the guard: if dash is missing, these tests silently weaken.

    macOS /bin/sh accepts [[ ]] and arrays, so falling back to it means
    bashisms pass locally and break only for Linux users.
    """
    assert POSIX_SH.endswith("dash"), (
        "dash not found -- run `brew install dash`. Without it these tests "
        "run under macOS /bin/sh, which accepts bashisms and cannot catch "
        "the Linux-only breakage this suite exists to prevent."
    )


def test_detect_os_maps_darwin():
    result = run_func('uname() { echo Darwin; }\ndetect_os')
    assert result.returncode == 0
    assert result.stdout.strip() == "darwin"


def test_detect_os_maps_linux():
    result = run_func('uname() { echo Linux; }\ndetect_os')
    assert result.returncode == 0
    assert result.stdout.strip() == "linux"


def test_detect_os_rejects_windows_with_message():
    result = run_func('uname() { echo MINGW64_NT-10.0; }\ndetect_os')
    assert result.returncode != 0
    assert "not supported" in (result.stdout + result.stderr).lower()


def test_detect_arch_maps_apple_silicon():
    result = run_func('uname() { echo arm64; }\ndetect_arch')
    assert result.returncode == 0
    assert result.stdout.strip() == "arm64"


def test_detect_arch_maps_x86_64_to_amd64():
    result = run_func('uname() { echo x86_64; }\ndetect_arch')
    assert result.returncode == 0
    assert result.stdout.strip() == "amd64"


def test_detect_arch_maps_aarch64_to_arm64():
    """Linux reports aarch64 where macOS reports arm64."""
    result = run_func('uname() { echo aarch64; }\ndetect_arch')
    assert result.returncode == 0
    assert result.stdout.strip() == "arm64"


def test_detect_arch_rejects_unknown():
    result = run_func('uname() { echo riscv64; }\ndetect_arch')
    assert result.returncode != 0
    assert "not supported" in (result.stdout + result.stderr).lower()


def test_sourcing_does_not_run_main():
    """The guard must prevent a real install when the script is sourced."""
    result = run_func('echo sourced-ok')
    assert result.returncode == 0
    assert "sourced-ok" in result.stdout
    # main() would print a banner; it must not appear
    assert "codeintel setup" not in result.stdout.lower()
