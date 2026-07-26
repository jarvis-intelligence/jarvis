"""Tests for setup.sh — the dependency bootstrapper.

Each test sources setup.sh with CODEINTEL_SETUP_SOURCED=1 (which suppresses
main()) and then calls one function, so functions are tested in isolation
without performing a real install.
"""

import hashlib
import shutil
import subprocess
import tarfile
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


# ----------------------------------------------- install dir / PATH wiring ----


def test_bin_dir_defaults_under_home():
    result = run_func('bin_dir', env={"HOME": "/tmp/fake-home"})
    assert result.stdout.strip() == "/tmp/fake-home/.codeintel/bin"


def test_bin_dir_respects_override():
    result = run_func('bin_dir', env={"CODEINTEL_BIN_DIR": "/custom/bin"})
    assert result.stdout.strip() == "/custom/bin"


def test_ensure_bin_dir_creates_directory(tmp_path):
    target = tmp_path / "nested" / "bin"
    result = run_func('ensure_bin_dir', env={"CODEINTEL_BIN_DIR": str(target)})
    assert result.returncode == 0
    assert target.is_dir()


def test_ensure_bin_dir_is_idempotent(tmp_path):
    target = tmp_path / "bin"
    env = {"CODEINTEL_BIN_DIR": str(target)}
    assert run_func('ensure_bin_dir', env=env).returncode == 0
    assert run_func('ensure_bin_dir', env=env).returncode == 0
    assert target.is_dir()


def test_shell_rc_path_picks_zshrc_for_zsh():
    result = run_func('shell_rc_path', env={"SHELL": "/bin/zsh", "HOME": "/tmp/h"})
    assert result.stdout.strip() == "/tmp/h/.zshrc"


def test_shell_rc_path_picks_bashrc_for_bash():
    result = run_func('shell_rc_path', env={"SHELL": "/bin/bash", "HOME": "/tmp/h"})
    assert result.stdout.strip() == "/tmp/h/.bashrc"


def test_shell_rc_path_empty_for_unknown_shell():
    result = run_func('shell_rc_path', env={"SHELL": "/usr/bin/fish", "HOME": "/tmp/h"})
    assert result.stdout.strip() == ""


def test_ensure_on_path_appends_export_line(tmp_path):
    home = tmp_path
    rc = home / ".zshrc"
    rc.write_text("# existing content\n")
    bin_path = tmp_path / "bin"
    result = run_func(
        'ensure_on_path',
        env={"HOME": str(home), "SHELL": "/bin/zsh", "CODEINTEL_BIN_DIR": str(bin_path)},
    )
    assert result.returncode == 0
    content = rc.read_text()
    assert "# existing content" in content, "must not clobber existing rc content"
    assert str(bin_path) in content


def test_ensure_on_path_is_idempotent(tmp_path):
    """Running twice must not duplicate the export line."""
    home = tmp_path
    rc = home / ".zshrc"
    rc.write_text("")
    bin_path = tmp_path / "bin"
    env = {"HOME": str(home), "SHELL": "/bin/zsh", "CODEINTEL_BIN_DIR": str(bin_path)}
    run_func('ensure_on_path', env=env)
    run_func('ensure_on_path', env=env)
    assert rc.read_text().count(str(bin_path)) == 1


def test_ensure_on_path_skips_when_already_on_path(tmp_path):
    """If the dir is already on PATH, don't touch the rc file at all."""
    home = tmp_path
    rc = home / ".zshrc"
    rc.write_text("")
    bin_path = tmp_path / "bin"
    env = {
        "HOME": str(home),
        "SHELL": "/bin/zsh",
        "CODEINTEL_BIN_DIR": str(bin_path),
        "PATH": f"{bin_path}:/usr/bin:/bin",
    }
    run_func('ensure_on_path', env=env)
    assert rc.read_text() == ""


# ------------------------------------------------- download / verify helper ----


def test_have_cmd_true_for_existing_binary():
    assert run_func('have_cmd sh && echo yes').stdout.strip() == "yes"


def test_have_cmd_false_for_missing_binary():
    result = run_func('have_cmd definitely-not-a-real-binary-xyz && echo yes || echo no')
    assert result.stdout.strip() == "no"


def test_sha256_of_matches_hashlib(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"codeintel")
    expected = hashlib.sha256(b"codeintel").hexdigest()
    result = run_func(f'sha256_of {f}')
    assert result.stdout.strip() == expected


def test_verify_sha256_accepts_correct_digest(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"codeintel")
    digest = hashlib.sha256(b"codeintel").hexdigest()
    result = run_func(f'verify_sha256 {f} {digest} && echo ok')
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_verify_sha256_rejects_wrong_digest(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"codeintel")
    result = run_func(f'verify_sha256 {f} {"0" * 64} || echo rejected')
    assert "rejected" in result.stdout
    assert "checksum" in (result.stdout + result.stderr).lower()


def test_install_tarball_binary_extracts_and_marks_executable(tmp_path):
    """End-to-end on a locally built tarball served over file:// — no network."""
    payload = tmp_path / "mytool"
    payload.write_text("#!/bin/sh\necho hello-from-mytool\n")
    tar_path = tmp_path / "mytool.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(payload, arcname="mytool")
    sha_path = tmp_path / "mytool.tar.gz.sha256"
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    # Upstream .sha256 files use the "<digest>  <filename>" format.
    sha_path.write_text(f"{digest}  mytool.tar.gz\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'install_tarball_binary file://{tar_path} file://{sha_path} mytool mytool',
        env={"CODEINTEL_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode == 0, result.stderr
    installed = bin_path / "mytool"
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111, "must be executable"


def test_install_tarball_binary_refuses_on_checksum_mismatch(tmp_path):
    payload = tmp_path / "mytool"
    payload.write_text("#!/bin/sh\ntrue\n")
    tar_path = tmp_path / "mytool.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(payload, arcname="mytool")
    sha_path = tmp_path / "mytool.tar.gz.sha256"
    sha_path.write_text(f"{'0' * 64}  mytool.tar.gz\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'install_tarball_binary file://{tar_path} file://{sha_path} mytool mytool',
        env={"CODEINTEL_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode != 0
    assert not (bin_path / "mytool").exists(), "must not install an unverified binary"
