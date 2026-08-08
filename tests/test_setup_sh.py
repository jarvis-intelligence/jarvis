"""Tests for setup.sh — the dependency bootstrapper.

Each test sources setup.sh with JARVIS_SETUP_SOURCED=1 (which suppresses
main()) and then calls one function, so functions are tested in isolation
without performing a real install.
"""

import hashlib
import re
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
    full_env = {"JARVIS_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
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
    assert "jarvis setup" not in result.stdout.lower()


# ----------------------------------------------- install dir / PATH wiring ----


def test_bin_dir_defaults_under_home():
    result = run_func('bin_dir', env={"HOME": "/tmp/fake-home"})
    assert result.stdout.strip() == "/tmp/fake-home/.jarvis/bin"


def test_bin_dir_respects_override():
    result = run_func('bin_dir', env={"JARVIS_BIN_DIR": "/custom/bin"})
    assert result.stdout.strip() == "/custom/bin"


def test_ensure_bin_dir_creates_directory(tmp_path):
    target = tmp_path / "nested" / "bin"
    result = run_func('ensure_bin_dir', env={"JARVIS_BIN_DIR": str(target)})
    assert result.returncode == 0
    assert target.is_dir()


def test_ensure_bin_dir_is_idempotent(tmp_path):
    target = tmp_path / "bin"
    env = {"JARVIS_BIN_DIR": str(target)}
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
        env={"HOME": str(home), "SHELL": "/bin/zsh", "JARVIS_BIN_DIR": str(bin_path)},
    )
    assert result.returncode == 0
    content = rc.read_text()
    assert "# existing content" in content, "must not clobber existing rc content"
    assert str(bin_path) in content
    # $PATH must remain LITERAL so it expands at shell startup. If it expanded
    # at install time, the rc would freeze today's PATH forever.
    assert 'export PATH="' in content
    assert ':$PATH"' in content, "$PATH must not be expanded when written"


def test_ensure_on_path_is_idempotent(tmp_path):
    """Running twice must not duplicate the export line."""
    home = tmp_path
    rc = home / ".zshrc"
    rc.write_text("")
    bin_path = tmp_path / "bin"
    env = {"HOME": str(home), "SHELL": "/bin/zsh", "JARVIS_BIN_DIR": str(bin_path)}
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
        "JARVIS_BIN_DIR": str(bin_path),
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
    f.write_bytes(b"jarvis")
    expected = hashlib.sha256(b"jarvis").hexdigest()
    result = run_func(f'sha256_of {f}')
    assert result.stdout.strip() == expected


def test_verify_sha256_accepts_correct_digest(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"jarvis")
    digest = hashlib.sha256(b"jarvis").hexdigest()
    result = run_func(f'verify_sha256 {f} {digest} && echo ok')
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_verify_sha256_rejects_wrong_digest(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"jarvis")
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
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
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
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode != 0
    assert not (bin_path / "mytool").exists(), "must not install an unverified binary"


def test_install_raw_binary_installs_and_marks_executable(tmp_path):
    """scip-java ships a bare launcher, not a tarball — no extraction step."""
    payload = tmp_path / "launcher"
    payload.write_text("#!/bin/sh\necho hello-from-launcher\n")
    sha_path = tmp_path / "launcher.sha256"
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    sha_path.write_text(f"{digest}  launcher\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'install_raw_binary file://{payload} file://{sha_path} mytool',
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert result.returncode == 0, result.stderr
    installed = bin_path / "mytool"
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111, "must be executable"
    assert "hello-from-launcher" in installed.read_text()


def test_install_raw_binary_refuses_on_checksum_mismatch(tmp_path):
    payload = tmp_path / "launcher"
    payload.write_text("#!/bin/sh\ntrue\n")
    sha_path = tmp_path / "launcher.sha256"
    sha_path.write_text(f"{'0' * 64}  launcher\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'install_raw_binary file://{payload} file://{sha_path} mytool',
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
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


def test_scip_pin_matches_committed_file():
    """The in-script pin must not drift from the SCIP_COMMIT file CI builds
    from: a drifted pin downloads a release build-scip.yml never published,
    which 404s for every user.

    The non-empty and format assertions guard against the vacuous pass:
    two empty (or two 'latest') values would compare equal while producing
    a download URL that resolves for nobody."""
    on_disk = (Path(__file__).parent.parent / "SCIP_COMMIT").read_text().strip()
    in_script = run_func('echo "$SCIP_COMMIT_PIN"').stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{7,40}", on_disk), f"SCIP_COMMIT is not a commit hash: {on_disk!r}"
    assert in_script == on_disk


def test_install_scip_reinstalls_over_unpatched_upstream_binary(tmp_path):
    """The upgrade path for the installed base: existing users hold upstream
    v0.9.0, whose --version carries no fork commit. A bare presence check
    would strand them on broken typeHierarchy forever, so a version mismatch
    must fall through to the download path (asserted here via its failure
    against an unreachable URL, not 'already installed, skipping')."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    scip = fake_bin / "scip"
    scip.write_text('#!/bin/sh\necho "scip version v0.9.0"\n')
    scip.chmod(0o755)
    result = run_func(
        # Shadow download_to so the fall-through is observable without
        # network access: reaching the download step proves the version
        # gate declined to skip.
        'download_to() { return 1; }\ninstall_scip darwin arm64',
        env={"JARVIS_BIN_DIR": str(fake_bin)},
    )
    assert "already installed" not in result.stdout
    assert "installing fork build" in result.stdout


def test_scip_release_repo_is_set():
    """The scip binaries come from a dedicated public repo, not this one."""
    assert run_func('echo "$SCIP_RELEASE_REPO"').stdout.strip() == "jarvis-intelligence/jarvis-index"


def test_scip_release_repo_is_not_the_private_repo():
    """Same invariant as the zoekt variant above: GitHub serves release
    assets only to viewers of the owning repo, so pointing scip downloads at
    the private development repo 404s for every real user.

    The non-empty assertion comes first deliberately: without it, an unset
    SCIP_RELEASE_REPO makes `"" != "jarvis-intelligence/jarvis"` true and the test
    passes vacuously, guarding nothing."""
    release_repo = run_func('echo "$SCIP_RELEASE_REPO"').stdout.strip()
    private_repo = run_func('echo "$JARVIS_REPO"').stdout.strip()
    assert release_repo, "SCIP_RELEASE_REPO is unset"
    assert private_repo, "JARVIS_REPO is unset"
    assert release_repo != private_repo


def test_already_installed_finds_binary_in_bin_dir_not_on_path(tmp_path):
    """Idempotency must not depend on the install dir being on PATH.

    setup.sh writes to ~/.jarvis/bin and only appends it to the shell rc --
    so during the very run that creates it (and any re-run in the same shell)
    the dir is NOT yet on PATH. A PATH-only presence check re-downloads
    everything on every re-run.
    """
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    stub = bin_path / "scip"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    result = run_func(
        'already_installed scip && echo FOUND || echo MISSING',
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin"},
    )
    assert "FOUND" in result.stdout


def test_already_installed_falls_back_to_path_lookup(tmp_path):
    """npm-installed indexers land in npm's global bin, not ours."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "scip-typescript"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    result = run_func(
        'already_installed scip-typescript && echo FOUND || echo MISSING',
        env={
            "JARVIS_BIN_DIR": str(tmp_path / "empty-bin"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
    )
    assert "FOUND" in result.stdout


def test_already_installed_reports_missing_when_truly_absent(tmp_path):
    result = run_func(
        'already_installed scip && echo FOUND || echo MISSING',
        env={"JARVIS_BIN_DIR": str(tmp_path / "empty"), "PATH": "/usr/bin:/bin"},
    )
    assert "MISSING" in result.stdout


def test_install_scip_skips_when_present_only_in_bin_dir(tmp_path):
    """Regression: CI caught setup.sh re-downloading on every re-run.

    The stub reports the pinned fork commit because the skip is
    version-gated: after a real install the binary genuinely stamps the pin,
    so re-runs skip, while a silent or upstream binary must NOT skip (see
    test_install_scip_reinstalls_over_unpatched_upstream_binary)."""
    pin = (Path(__file__).parent.parent / "SCIP_COMMIT").read_text().strip()
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    stub = bin_path / "scip"
    stub.write_text(f'#!/bin/sh\necho "SHA: {pin}0000000000000000000000000000"\n')
    stub.chmod(0o755)
    result = run_func(
        'install_scip linux amd64',
        env={"JARVIS_BIN_DIR": str(bin_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined
    assert "installing" not in combined, "must not re-download"


def test_install_scip_skips_when_already_present(tmp_path):
    """A pin-stamped scip on PATH must not be re-downloaded."""
    pin = (Path(__file__).parent.parent / "SCIP_COMMIT").read_text().strip()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "scip"
    stub.write_text(f'#!/bin/sh\necho "SHA: {pin}0000000000000000000000000000"\n')
    stub.chmod(0o755)
    result = run_func(
        'install_scip darwin arm64',
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "JARVIS_BIN_DIR": str(tmp_path / "bin"),
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
        env={"JARVIS_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
    )
    assert "NO" in result.stdout


def test_confirm_emits_no_raw_shell_errors_without_a_tty(tmp_path):
    """`[ -r /dev/tty ]` alone is not enough.

    The device node can exist and test as readable while no controlling
    terminal is attached (CI, a piped subprocess). Writing to it then fails
    with a raw "Device not configured" / "No such device" error, which looks
    like a broken installer. confirm() must probe the tty for real and stay
    quiet.
    """
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\nconfirm "proceed?" && echo YES || echo NO'],
        capture_output=True,
        text=True,
        env={"JARVIS_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
    )
    assert "NO" in result.stdout
    combined = result.stdout + result.stderr
    for noise in ("/dev/tty", "Device not configured", "No such device"):
        assert noise not in combined, f"leaked raw shell error: {combined!r}"


def test_confirm_does_not_read_from_stdin(tmp_path):
    """Feeding "y" on stdin must NOT be accepted — it must come from /dev/tty.

    This is the curl|sh correctness guard: stdin there is the script itself.
    """
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\nconfirm "proceed?" && echo YES || echo NO'],
        capture_output=True,
        text=True,
        env={"JARVIS_SETUP_SOURCED": "1", "PATH": "/usr/bin:/bin"},
        input="y\n",
    )
    assert "NO" in result.stdout, "confirm() must ignore stdin and use /dev/tty"


def test_install_scip_java_warns_and_returns_zero_without_java(tmp_path):
    """The launcher is inert without a JVM, so a missing `java` is a soft skip."""
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    result = subprocess.run(
        [POSIX_SH, "-c", f'. {SETUP_SH}\ninstall_scip_java'],
        capture_output=True,
        text=True,
        env={
            "JARVIS_SETUP_SOURCED": "1",
            "PATH": f"{empty_bin}",
            "JARVIS_BIN_DIR": str(tmp_path / "bin"),
        },
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "scip-java" in combined
    assert "java" in combined


def test_install_scip_java_never_mentions_docker(tmp_path):
    """Docker was never the install path — the image entrypoint is jshell."""
    assert "SCIP_JAVA_IMAGE" not in SETUP_SH.read_text()
    assert "docker pull" not in SETUP_SH.read_text()


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


# ------------------------------------------------------- jarvis-mcp installer ----


def test_install_jarvis_mcp_warns_and_continues_without_uv():
    """Missing uv is a soft skip with instructions, not a hard failure."""
    result = run_func("install_jarvis_mcp")
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "uv" in combined


def test_install_jarvis_mcp_skips_when_already_present(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "jarvis-server"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    result = run_func(
        "install_jarvis_mcp",
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined


def test_install_jarvis_mcp_invokes_uv_tool_install(tmp_path):
    """Stub uv and assert the exact package name passed to it."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "uv-args.txt"
    uv_stub = fake_bin / "uv"
    uv_stub.write_text(f'#!/bin/sh\necho "$@" > {log}\n')
    uv_stub.chmod(0o755)
    result = run_func(
        "install_jarvis_mcp",
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    args = log.read_text()
    assert "tool" in args
    assert "install" in args
    assert "jarvis-mcp" in args
    assert "--force" not in args


def test_install_jarvis_mcp_forces_reinstall_when_forced(tmp_path):
    """FORCE=1 both bypasses the already-installed skip and passes --force to uv.

    FORCE is set as a shell variable in the snippet, not via the subprocess
    env: sourcing setup.sh resets FORCE=0 at top level (parse_args is what
    normally sets it, from main()'s --force flag), so an env-supplied FORCE
    would be clobbered before install_jarvis_mcp ever sees it.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "jarvis-server"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    log = tmp_path / "uv-args.txt"
    uv_stub = fake_bin / "uv"
    uv_stub.write_text(f'#!/bin/sh\necho "$@" > {log}\n')
    uv_stub.chmod(0o755)
    result = run_func(
        "FORCE=1\ninstall_jarvis_mcp",
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "--force" in log.read_text()


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
    for name in ("zoekt-git-index", "zoekt-webserver"):
        stub = fake_bin / name
        stub.write_text("#!/bin/sh\ntrue\n")
        stub.chmod(0o755)
    result = run_func(
        'install_zoekt darwin arm64',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "JARVIS_BIN_DIR": str(tmp_path / "bin")},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined


def test_install_zoekt_extracts_both_binaries(tmp_path):
    """Verify both members land, using a local tarball over file://."""
    stage = tmp_path / "stage"
    stage.mkdir()
    for name in ("zoekt-git-index", "zoekt-webserver"):
        p = stage / name
        p.write_text("#!/bin/sh\ntrue\n")
    tar_path = tmp_path / "zoekt-darwin-arm64.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name in ("zoekt-git-index", "zoekt-webserver"):
            tf.add(stage / name, arcname=name)
    sha_path = tmp_path / "zoekt-darwin-arm64.tar.gz.sha256"
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    sha_path.write_text(f"{digest}  zoekt-darwin-arm64.tar.gz\n")

    bin_path = tmp_path / "bin"
    result = run_func(
        f'ZOEKT_BASE_URL="file://{tmp_path}" install_zoekt darwin arm64',
        env={
            "JARVIS_BIN_DIR": str(bin_path),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "FORCE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert (bin_path / "zoekt-git-index").is_file()
    assert (bin_path / "zoekt-webserver").is_file()
    assert (bin_path / "zoekt-git-index").stat().st_mode & 0o111


# ------------------------------------------------------ scip-swift installer ----


def test_install_scip_swift_skips_on_linux_without_failing(tmp_path):
    """Swift indexing needs Xcode; a Linux skip is by design, not an error."""
    result = run_func(
        'install_scip_swift linux amd64',
        env={"JARVIS_BIN_DIR": str(tmp_path / "bin"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, "skip-by-design must not be a failure"
    combined = (result.stdout + result.stderr).lower()
    assert "not available" in combined or "macos" in combined


def test_install_scip_swift_skips_on_intel_mac(tmp_path):
    """Only an arm64 asset is published upstream."""
    result = run_func(
        'install_scip_swift darwin amd64',
        env={"JARVIS_BIN_DIR": str(tmp_path / "bin"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    assert "not available" in (result.stdout + result.stderr).lower()


def test_scip_swift_repo_is_the_org_not_the_personal_owner():
    """The repo moved owners and GitHub serves no redirect for the old path.

    While this pointed at `phuongddx/scip-swift` the download URL 404'd, so
    `--only scip-swift` failed on every macOS arm64 host and Swift indexing was
    unavailable to all users. Nothing else caught it: no other test reads this
    variable, and setup-smoke.yml did not exercise the scip-swift install.
    """
    repo = run_func('echo "$SCIP_SWIFT_REPO"').stdout.strip()
    assert repo == "jarvis-intelligence/scip-swift", f"wrong owner: {repo}"


def test_scip_swift_asset_name_uses_macos_not_darwin():
    """The asset says "macos", not "darwin" — unlike scip's own assets."""
    result = run_func('scip_swift_asset_name')
    name = result.stdout.strip()
    assert name == "scip-swift-v0.1.2-macos-arm64.tar.gz"


def test_scip_swift_pin_is_at_least_v0_1_2():
    """Two independent reasons the pin must never slip backwards.

    v0.1.0's binary lacks the `index` subcommand `index_cli.py` invokes, so
    jarvis cannot drive it at all. v0.1.1's xcodebuild backend passes no
    code-signing overrides, so any repo with signed app-extension targets fails
    during GatherProvisioningInputs before compiling anything.
    """
    version = run_func('echo "$SCIP_SWIFT_VERSION"').stdout.strip()
    parts = version.lstrip("v").split(".")
    assert tuple(int(p) for p in parts) >= (0, 1, 2), f"too old: {version}"


def test_install_scip_swift_skips_when_present(tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "scip-swift"
    stub.write_text("#!/bin/sh\ntrue\n")
    stub.chmod(0o755)
    result = run_func(
        'install_scip_swift darwin arm64',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "JARVIS_BIN_DIR": str(tmp_path / "b")},
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "already" in combined or "skip" in combined


# ------------------------------------------------ orchestration / flags / summary ----


def test_help_flag_exits_zero_and_prints_usage():
    result = subprocess.run(
        [POSIX_SH, str(SETUP_SH), "--help"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "--only" in result.stdout


def test_parse_args_sets_only():
    result = run_func('parse_args --only scip; echo "ONLY=$ONLY"')
    assert "ONLY=scip" in result.stdout


def test_parse_args_sets_force():
    result = run_func('parse_args --force; echo "FORCE=$FORCE"')
    assert "FORCE=1" in result.stdout


def test_parse_args_rejects_unknown_flag():
    result = run_func('parse_args --bogus || echo rejected')
    assert "rejected" in result.stdout


def test_record_and_print_summary_roundtrip():
    result = run_func(
        'SUMMARY=""; record scip installed; record zoekt skipped; print_summary'
    )
    assert "scip" in result.stdout
    assert "installed" in result.stdout
    assert "zoekt" in result.stdout
    assert "skipped" in result.stdout


def test_only_flag_runs_single_installer(tmp_path):
    """--only scip must not attempt npm installers."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    npm_log = tmp_path / "npm-called.txt"
    npm_stub = fake_bin / "npm"
    npm_stub.write_text(f'#!/bin/sh\necho called >> {npm_log}\n')
    npm_stub.chmod(0o755)
    # Pre-place a scip stub so no download happens.
    scip_stub = fake_bin / "scip"
    scip_stub.write_text("#!/bin/sh\ntrue\n")
    scip_stub.chmod(0o755)

    result = subprocess.run(
        [POSIX_SH, str(SETUP_SH), "--only", "scip"],
        capture_output=True, text=True,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "JARVIS_BIN_DIR": str(tmp_path / "bin"),
            "SHELL": "/bin/zsh",
        },
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert not npm_log.exists(), "--only scip must not run npm installers"


def _fake_bash(tmp_path, name: str, version_line: str) -> str:
    """A stand-in bash that answers --version and nothing else."""
    path = tmp_path / name
    path.write_text(f'#!/bin/sh\n[ "$1" = "--version" ] && echo "{version_line}"\n')
    path.chmod(0o755)
    return str(path)


def test_shim_dir_defaults_under_jarvis_home():
    result = run_func("shim_dir", env={"HOME": "/home/someone"})
    assert result.stdout.strip() == "/home/someone/.jarvis/shims"


def test_shim_dir_follows_data_dir_not_bin_dir(tmp_path):
    """Must key off JARVIS_DATA_DIR: config.shim_dir() reads that one, and a
    shim the Python side cannot find is worse than no shim."""
    result = run_func(
        "shim_dir",
        env={"HOME": "/home/someone",
             "JARVIS_DATA_DIR": str(tmp_path),
             "JARVIS_BIN_DIR": "/should/be/ignored"},
    )
    assert result.stdout.strip() == f"{tmp_path}/shims"


def test_bash_at_least_44_accepts_bash_5(tmp_path):
    fake = _fake_bash(tmp_path, "bash5", "GNU bash, version 5.3.15(1)-release (arm64-apple-darwin25)")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode == 0


def test_bash_at_least_44_accepts_exactly_44(tmp_path):
    fake = _fake_bash(tmp_path, "bash44", "GNU bash, version 4.4.0(1)-release")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode == 0


def test_bash_at_least_44_rejects_macos_bash_32(tmp_path):
    fake = _fake_bash(tmp_path, "bash32", "GNU bash, version 3.2.57(1)-release (arm64-apple-darwin25)")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode != 0


def test_bash_at_least_44_rejects_unparseable_version(tmp_path):
    fake = _fake_bash(tmp_path, "weird", "not a version string at all")
    assert run_func(f'bash_at_least_44 "{fake}"').returncode != 0


def test_bash_at_least_44_rejects_missing_binary(tmp_path):
    assert run_func(f'bash_at_least_44 "{tmp_path}/nope"').returncode != 0


def test_install_bash_shim_is_a_noop_on_linux(tmp_path):
    result = run_func(
        'install_bash_shim linux',
        env={"HOME": str(tmp_path), "JARVIS_DATA_DIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert not (tmp_path / "shims" / "bash").exists()


def test_install_bash_shim_links_default_bash_when_it_is_already_modern(tmp_path):
    """A modern default bash must still be linked into the shim dir -- setup.sh's
    own PATH at install time is not necessarily the PATH the indexer subprocess
    will inherit later (a GUI-launched MCP server, launchd, a stripped-env shell),
    so "already modern here, skip" leaves those contexts with no shim and the
    original bug. Baking the resolved default into the shim dir removes the
    PATH-context dependency entirely."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    modern = _fake_bash(bindir, "bash", "GNU bash, version 5.3.15(1)-release")
    result = run_func(
        'install_bash_shim darwin',
        env={"HOME": str(tmp_path), "JARVIS_DATA_DIR": str(tmp_path),
             "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    link = tmp_path / "shims" / "bash"
    assert link.is_symlink()
    assert link.resolve() == Path(modern).resolve()


def test_install_bash_shim_links_candidate_when_default_is_old(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_bash(bindir, "bash", "GNU bash, version 3.2.57(1)-release")
    modern = _fake_bash(tmp_path, "modern-bash", "GNU bash, version 5.3.15(1)-release")
    result = run_func(
        f'BASH_SHIM_CANDIDATES="{modern}"\ninstall_bash_shim darwin',
        env={"HOME": str(tmp_path), "JARVIS_DATA_DIR": str(tmp_path),
             "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0
    link = tmp_path / "shims" / "bash"
    assert link.is_symlink()
    assert link.resolve() == Path(modern).resolve()


def test_install_bash_shim_advises_install_when_no_modern_bash(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_bash(bindir, "bash", "GNU bash, version 3.2.57(1)-release")
    result = run_func(
        f'BASH_SHIM_CANDIDATES="{tmp_path}/absent"\ninstall_bash_shim darwin',
        env={"HOME": str(tmp_path), "JARVIS_DATA_DIR": str(tmp_path),
             "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, "must not fail setup for users who never index Java"
    assert "brew install bash" in result.stdout + result.stderr
    assert not (tmp_path / "shims" / "bash").exists()


def test_zoekt_release_repo_is_set():
    """The zoekt binaries come from a dedicated public repo, not this one."""
    assert run_func('echo "$ZOEKT_RELEASE_REPO"').stdout.strip() == "jarvis-intelligence/jarvis-index"

def test_zoekt_release_repo_is_not_the_private_repo():
    """The invariant, not just the value: GitHub serves release assets only to
    viewers of the owning repo, so pointing zoekt downloads at the private
    development repo 404s for every real user. This test is the guard against
    that regression -- it is how the original outage would have been caught.

    The non-empty assertion comes first deliberately: without it, an unset
    ZOEKT_RELEASE_REPO makes `"" != "jarvis-intelligence/jarvis"` true and the test
    passes vacuously, guarding nothing."""
    release_repo = run_func('echo "$ZOEKT_RELEASE_REPO"').stdout.strip()
    private_repo = run_func('echo "$JARVIS_REPO"').stdout.strip()
    assert release_repo, "ZOEKT_RELEASE_REPO is unset"
    assert private_repo, "JARVIS_REPO is unset"
    assert release_repo != private_repo
