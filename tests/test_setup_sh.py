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

import pytest

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


# ------------------------------------------------------------ scip installer ----


@pytest.mark.parametrize(
    "os_name,arch,expected",
    [
        ("darwin", "arm64", "scip-darwin-arm64.tar.gz"),
        ("darwin", "amd64", "scip-darwin-amd64.tar.gz"),
        ("linux", "amd64", "scip-linux-amd64.tar.gz"),
        ("linux", "arm64", "scip-linux-arm64.tar.gz"),
    ],
)
def test_scip_asset_name_covers_all_supported_platforms(os_name, arch, expected):
    result = run_func(f'scip_asset_name {os_name} {arch}')
    assert result.stdout.strip() == expected


def test_scip_version_is_pinned_not_latest():
    """A floating 'latest' could silently swap the expt-convert schema."""
    result = run_func('echo "$SCIP_VERSION"')
    version = result.stdout.strip()
    assert version.startswith("v"), f"expected a pinned vX.Y.Z, got {version!r}"
    assert "latest" not in version


def test_install_scip_skips_when_already_present(tmp_path):
    """An existing scip on PATH must not be re-downloaded."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "scip"
    stub.write_text("#!/bin/sh\necho 'scip version v0.9.0'\n")
    stub.chmod(0o755)
    result = run_func(
        'install_scip darwin arm64',
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "CODEINTEL_BIN_DIR": str(tmp_path / "bin"),
        },
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined


# --------------------------------------------- scip-java detect-only / confirm ----


def test_confirm_returns_nonzero_when_no_tty_available(tmp_path):
    """Non-interactive runs (piped, CI) must default to "no", never hang."""
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\nconfirm "proceed?" && echo YES || echo NO'],
        capture_output=True,
        text=True,
        env={"CODEINTEL_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
    )
    assert "NO" in result.stdout


def test_confirm_does_not_read_from_stdin(tmp_path):
    """Feeding "y" on stdin must NOT be accepted — it must come from /dev/tty.

    This is the curl|sh correctness guard: stdin there is the script itself.
    """
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\nconfirm "proceed?" && echo YES || echo NO'],
        capture_output=True,
        text=True,
        env={"CODEINTEL_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin"},
        input="y\n",
    )
    assert "NO" in result.stdout, "confirm() must ignore stdin and use /dev/tty"


def test_install_scip_java_reports_and_returns_zero_without_docker(tmp_path):
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\ninstall_scip_java'],
        capture_output=True,
        text=True,
        env={"CODEINTEL_SETUP_SOURCED": "1", "PATH": f"{empty_bin}:/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "scip-java" in combined
    assert "docker" in combined or "jvm" in combined


def test_install_scip_java_never_pulls_without_confirmation(tmp_path):
    """With docker present but no tty, it must NOT pull the image."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "docker-called.txt"
    docker_stub = fake_bin / "docker"
    docker_stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    docker_stub.chmod(0o755)
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\ninstall_scip_java'],
        capture_output=True,
        text=True,
        env={"CODEINTEL_SETUP_SOURCED": "1", "PATH": f"{fake_bin}:/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0
    assert not log.exists(), "must not run docker pull without explicit confirmation"


# --------------------------------------------------------- npm-based indexers ----


def test_install_npm_indexer_warns_and_continues_without_npm(tmp_path):
    """Missing npm is a soft skip with instructions, not a hard failure."""
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    result = run_func(
        'install_npm_indexer scip-typescript @sourcegraph/scip-typescript',
        env={"PATH": f"{empty_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "npm" in combined


def test_install_npm_indexer_skips_when_binary_present(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "scip-typescript"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    result = run_func(
        'install_npm_indexer scip-typescript @sourcegraph/scip-typescript',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined


def test_install_npm_indexer_invokes_npm_with_correct_package(tmp_path):
    """Stub npm and assert the exact package name passed to it."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "npm-args.txt"
    npm_stub = fake_bin / "npm"
    npm_stub.write_text(f'#!/bin/sh\necho "$@" > {log}\n')
    npm_stub.chmod(0o755)
    result = run_func(
        'install_npm_indexer scip-python @sourcegraph/scip-python',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "@sourcegraph/scip-python" in log.read_text()
    assert "-g" in log.read_text()


def test_scip_typescript_wrapper_uses_sourcegraph_package(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "args.txt"
    npm_stub = fake_bin / "npm"
    npm_stub.write_text(f'#!/bin/sh\necho "$@" > {log}\n')
    npm_stub.chmod(0o755)
    result = run_func(
        'install_scip_typescript',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert "@sourcegraph/scip-typescript" in log.read_text()


# ----------------------------------------------------------- zoekt installer ----


@pytest.mark.parametrize(
    "os_name,arch,expected",
    [
        ("darwin", "arm64", "zoekt-darwin-arm64.tar.gz"),
        ("linux", "amd64", "zoekt-linux-amd64.tar.gz"),
    ],
)
def test_zoekt_asset_name(os_name, arch, expected):
    result = run_func(f'zoekt_asset_name {os_name} {arch}')
    assert result.stdout.strip() == expected


def test_zoekt_pin_matches_committed_file():
    """The in-script pin must not drift from the ZOEKT_COMMIT file CI reads."""
    on_disk = (Path(__file__).parent.parent / "ZOEKT_COMMIT").read_text().strip()
    in_script = run_func('echo "$ZOEKT_COMMIT_PIN"').stdout.strip()
    assert in_script == on_disk


def test_install_zoekt_skips_when_both_binaries_present(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    for name in ("zoekt-index", "zoekt-webserver"):
        stub = fake_bin / name
        stub.write_text("#!/bin/sh\ntrue\n")
        stub.chmod(0o755)
    result = run_func(
        'install_zoekt darwin arm64',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "CODEINTEL_BIN_DIR": str(tmp_path / "bin")},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined


def test_install_zoekt_extracts_both_binaries(tmp_path):
    """Verify both members land, using a local tarball over file://."""
    stage = tmp_path / "stage"
    stage.mkdir()
    for name in ("zoekt-index", "zoekt-webserver"):
        p = stage / name
        p.write_text("#!/bin/sh\ntrue\n")
    tar_path = tmp_path / "zoekt-darwin-arm64.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name in ("zoekt-index", "zoekt-webserver"):
            tf.add(stage / name, arcname=name)
    sha_path = tmp_path / "zoekt-darwin-arm64.tar.gz.sha256"
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    sha_path.write_text(f"{digest}  zoekt-darwin-arm64.tar.gz\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'ZOEKT_BASE_URL="file://{tmp_path}" install_zoekt darwin arm64',
        env={
            "CODEINTEL_BIN_DIR": str(bin_path),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "FORCE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert (bin_path / "zoekt-index").is_file()
    assert (bin_path / "zoekt-webserver").is_file()
    assert (bin_path / "zoekt-index").stat().st_mode & 0o111


# ------------------------------------------------------ scip-swift installer ----


def test_install_scip_swift_skips_on_linux_without_failing(tmp_path):
    """Swift indexing needs Xcode; a Linux skip is by design, not an error."""
    result = run_func(
        'install_scip_swift linux amd64',
        env={"CODEINTEL_BIN_DIR": str(tmp_path / "bin"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, "skip-by-design must not be a failure"
    combined = (result.stdout + result.stderr).lower()
    assert "not available" in combined or "macos" in combined


def test_install_scip_swift_skips_on_intel_mac(tmp_path):
    """Only an arm64 asset is published upstream."""
    result = run_func(
        'install_scip_swift darwin amd64',
        env={"CODEINTEL_BIN_DIR": str(tmp_path / "bin"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert "not available" in (result.stdout + result.stderr).lower()


def test_scip_swift_asset_name_uses_macos_not_darwin():
    """The upstream asset is scip-swift-v0.1.0-macos-arm64.tar.gz."""
    result = run_func('scip_swift_asset_name')
    name = result.stdout.strip()
    assert name == "scip-swift-v0.1.0-macos-arm64.tar.gz"


def test_install_scip_swift_skips_when_present(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "scip-swift"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    result = run_func(
        'install_scip_swift darwin arm64',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "CODEINTEL_BIN_DIR": str(tmp_path / "b")},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined
