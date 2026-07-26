# `setup.sh` Dependency Bootstrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single curl-installable `setup.sh` that takes a bare macOS or Linux machine to a fully-working `codeintel` install by detecting and installing all 7 external binary dependencies.

**Architecture:** One POSIX-`sh` script at repo root with one isolated installer function per dependency, each independently failing-soft. Binaries land in `~/.codeintel/bin`, which the script idempotently adds to the user's shell rc. Because upstream `sourcegraph/zoekt` publishes no binaries at all, a new GitHub Actions workflow cross-compiles zoekt from a pinned commit and publishes it to codeintel's own releases, which `setup.sh` then downloads from.

**Tech Stack:** POSIX `sh`, `curl`, `shasum`/`sha256sum`, GitHub Actions, Go cross-compilation (`GOOS`/`GOARCH`, `CGO_ENABLED=0`), pytest (for testing the shell script from Python, matching repo convention).

## Global Constraints

- **Strictly POSIX `sh`.** No arrays, no `[[ ]]`, no `$'...'`, no process substitution, no `local -a`. `curl … | sh` ignores the shebang and runs under the system `sh` (`dash` on many Linux distros). A bashism fails only on Linux, where it goes unnoticed during macOS development.
- **Supported platforms:** macOS (arm64, amd64) and Linux (amd64, arm64) only. Windows is out of scope — exit with a clear message.
- **`scip` pinned to `v0.9.0`** via a single `SCIP_VERSION` variable. Never "latest". Schema verified byte-identical to the v0.7.0 that `query.py` targets.
- **`zoekt` pinned** to the commit in the repo-root `ZOEKT_COMMIT` file. Current known-good: `33f1f18af292`.
- **`scip-swift` is macOS-arm64 only.** Its only published asset is `scip-swift-v0.1.0-macos-arm64.tar.gz`. Every other OS/arch: print "not available" and skip. No build-from-source fallback.
- **`scip-java` is detect-only** and must ask for confirmation before doing anything.
- **Any interactive prompt must read from `/dev/tty`, never stdin** — under `curl | sh`, stdin is the piped script source.
- **Install dir:** `~/.codeintel/bin` (override via `CODEINTEL_BIN_DIR` for tests).
- **Failure isolation:** one dependency failing must never abort the run. Print a scoped error, continue to the next.
- **Exit code:** `0` when every requested dependency ends satisfied or explicitly-skipped-by-design; non-zero only on unexpected failure.

## File Structure

| File | Responsibility |
|---|---|
| `setup.sh` (create, repo root) | The entire bootstrapper: platform detection, shared download/verify helpers, 7 installer functions, orchestration, summary |
| `ZOEKT_COMMIT` (create, repo root) | Single line: pinned upstream `sourcegraph/zoekt` commit SHA. Changing this is what triggers a zoekt rebuild |
| `.github/workflows/build-zoekt.yml` (create) | Cross-compiles `zoekt-index` + `zoekt-webserver` for 4 os/arch pairs, smoke-tests, publishes to codeintel releases |
| `tests/test_setup_sh.py` (create) | pytest tests that source `setup.sh` with `CODEINTEL_SETUP_SOURCED=1` and assert individual function behavior |
| `README.md` (modify, install section ~lines 55-71) | Replace the manual "Required on PATH" table with the one-line curl install |

`setup.sh` stays a single file deliberately: it must be fetchable and runnable as one `curl` target, so splitting it across files would defeat its purpose.

---

### Task 1: Script skeleton — platform detection and testability seam

**Files:**
- Create: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces:
  - `detect_os()` → echoes `darwin` or `linux`; exits 1 with message on anything else
  - `detect_arch()` → echoes `arm64` or `amd64`; exits 1 with message on anything else
  - `log_info(msg)`, `log_warn(msg)`, `log_error(msg)` → echo prefixed messages (`warn`/`error` to stderr)
  - `main()` → orchestrator, currently just detects platform and prints it
  - Sourced-guard honoring `CODEINTEL_SETUP_SOURCED=1`

- [ ] **Step 1: Write the failing test**

Create `tests/test_setup_sh.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: FAIL — all tests error because `setup.sh` does not exist (`.  …/setup.sh: No such file or directory`).

- [ ] **Step 3: Write minimal implementation**

Create `setup.sh`:

```sh
#!/usr/bin/env sh
# codeintel dependency bootstrapper.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh
#
# STRICTLY POSIX sh: `curl | sh` ignores the shebang above and runs under the
# system sh (dash on many Linux distros). No arrays, no [[ ]], no bashisms.

set -eu

# ---------------------------------------------------------------- logging ----

log_info() {
	echo "  $1"
}

log_warn() {
	echo "warn: $1" >&2
}

log_error() {
	echo "error: $1" >&2
}

# ------------------------------------------------------ platform detection ---

# Echo the normalized OS name, or exit non-zero if unsupported.
detect_os() {
	os_raw=$(uname -s)
	case "$os_raw" in
	Darwin) echo "darwin" ;;
	Linux) echo "linux" ;;
	*)
		log_error "$os_raw is not supported (macOS and Linux only)"
		return 1
		;;
	esac
}

# Echo the normalized arch name, or exit non-zero if unsupported.
# Linux reports aarch64 where macOS reports arm64; both normalize to arm64.
detect_arch() {
	arch_raw=$(uname -m)
	case "$arch_raw" in
	arm64 | aarch64) echo "arm64" ;;
	x86_64 | amd64) echo "amd64" ;;
	*)
		log_error "$arch_raw is not supported (arm64 and amd64 only)"
		return 1
		;;
	esac
}

# ----------------------------------------------------------------- main ------

main() {
	echo "codeintel setup"
	OS=$(detect_os)
	ARCH=$(detect_arch)
	log_info "platform: ${OS}/${ARCH}"
}

# Testability seam: tests source this file with CODEINTEL_SETUP_SOURCED=1 to
# call individual functions without performing a real install.
if [ "${CODEINTEL_SETUP_SOURCED:-}" != "1" ]; then
	main "$@"
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS — all 8 tests green.

- [ ] **Step 5: Verify the real script runs end-to-end**

Run: `sh setup.sh`
Expected: prints `codeintel setup` then `platform: darwin/arm64`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add bootstrapper skeleton with platform detection"
```

---

### Task 2: Install directory and PATH wiring

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `log_info`, `log_warn` from Task 1
- Produces:
  - `bin_dir()` → echoes `$CODEINTEL_BIN_DIR` if set, else `$HOME/.codeintel/bin`
  - `ensure_bin_dir()` → creates the dir (`mkdir -p`), idempotent
  - `shell_rc_path()` → echoes the rc file path derived from `$SHELL` (`~/.zshrc` for zsh, `~/.bashrc` for bash, else empty)
  - `ensure_on_path()` → appends an export line to the rc file only if the dir isn't already on `PATH` and the line isn't already present; idempotent

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "bin_dir or shell_rc or on_path"`
Expected: FAIL — `bin_dir: not found`, `shell_rc_path: not found`, `ensure_on_path: not found`.

- [ ] **Step 3: Write minimal implementation**

In `setup.sh`, insert after the platform-detection section and before `main()`:

```sh
# ------------------------------------------------------------ install dir ----

# Where downloaded binaries go. CODEINTEL_BIN_DIR exists so tests can redirect
# writes away from the real home directory.
bin_dir() {
	if [ -n "${CODEINTEL_BIN_DIR:-}" ]; then
		echo "$CODEINTEL_BIN_DIR"
	else
		echo "${HOME}/.codeintel/bin"
	fi
}

ensure_bin_dir() {
	mkdir -p "$(bin_dir)"
}

# Echo the shell rc file to modify, or empty if the shell is unrecognized.
shell_rc_path() {
	case "${SHELL:-}" in
	*/zsh) echo "${HOME}/.zshrc" ;;
	*/bash) echo "${HOME}/.bashrc" ;;
	*) echo "" ;;
	esac
}

# Append the bin dir to the user's shell rc, unless it is already on PATH or
# the line is already present. Idempotent.
ensure_on_path() {
	_dir=$(bin_dir)

	# Already active in this environment: nothing to do.
	case ":${PATH}:" in
	*":${_dir}:"*)
		return 0
		;;
	esac

	_rc=$(shell_rc_path)
	if [ -z "$_rc" ]; then
		log_warn "unrecognized shell '${SHELL:-}'; add ${_dir} to PATH yourself"
		return 0
	fi

	# Already written on a previous run: don't duplicate.
	if [ -f "$_rc" ] && grep -qF "$_dir" "$_rc" 2>/dev/null; then
		return 0
	fi

	printf '\n# added by codeintel setup\nexport PATH="%s:$PATH"\n' "$_dir" >>"$_rc"
	log_info "added ${_dir} to ${_rc} — run 'exec \$SHELL' or open a new terminal"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS — all tests from Tasks 1 and 2 green.

- [ ] **Step 5: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add install dir resolution and idempotent PATH wiring"
```

---

### Task 3: Download, checksum-verify, and extract helper

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `log_info`, `log_error`, `bin_dir` from Tasks 1-2
- Produces:
  - `have_cmd(name)` → returns 0 if `name` is on `PATH`
  - `sha256_of(file)` → echoes the hex digest, using `shasum -a 256` or `sha256sum` (whichever exists)
  - `verify_sha256(file, expected_hex)` → returns 0 on match, 1 with an error message on mismatch
  - `download_to(url, dest)` → curl with `-fsSL --retry 3`; returns non-zero on HTTP failure
  - `install_tarball_binary(url, sha_url, member, dest_name)` → download tarball + its `.sha256`, verify, extract `member`, move to `bin_dir()/dest_name`, `chmod +x`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
import hashlib
import tarfile


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "have_cmd or sha256 or tarball"`
Expected: FAIL — `have_cmd: not found`, `sha256_of: not found`, etc.

- [ ] **Step 3: Write minimal implementation**

In `setup.sh`, insert after the install-dir section:

```sh
# -------------------------------------------------------------- download -----

have_cmd() {
	command -v "$1" >/dev/null 2>&1
}

# Echo the sha256 hex digest of a file. macOS ships shasum; Linux sha256sum.
sha256_of() {
	if have_cmd sha256sum; then
		sha256sum "$1" | cut -d' ' -f1
	elif have_cmd shasum; then
		shasum -a 256 "$1" | cut -d' ' -f1
	else
		log_error "neither sha256sum nor shasum found; cannot verify downloads"
		return 1
	fi
}

verify_sha256() {
	_file=$1
	_expected=$2
	_actual=$(sha256_of "$_file") || return 1
	if [ "$_actual" != "$_expected" ]; then
		log_error "checksum mismatch for ${_file}"
		log_error "  expected: ${_expected}"
		log_error "  actual:   ${_actual}"
		return 1
	fi
}

download_to() {
	curl -fsSL --retry 3 -o "$2" "$1"
}

# Download a .tar.gz plus its .sha256 sidecar, verify, extract one member,
# and install it into bin_dir() under dest_name.
#
#   install_tarball_binary <tar_url> <sha_url> <member> <dest_name>
install_tarball_binary() {
	_tar_url=$1
	_sha_url=$2
	_member=$3
	_dest_name=$4

	_tmp=$(mktemp -d)
	# Clean up the temp dir on every exit path, including failure.
	# shellcheck disable=SC2064
	trap "rm -rf '$_tmp'" EXIT

	if ! download_to "$_tar_url" "${_tmp}/archive.tar.gz"; then
		log_error "download failed: ${_tar_url}"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	if ! download_to "$_sha_url" "${_tmp}/archive.sha256"; then
		log_error "checksum download failed: ${_sha_url}"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	# Sidecar format is "<digest>  <filename>"; take the first field.
	_expected=$(cut -d' ' -f1 <"${_tmp}/archive.sha256")
	if ! verify_sha256 "${_tmp}/archive.tar.gz" "$_expected"; then
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	if ! tar -xzf "${_tmp}/archive.tar.gz" -C "$_tmp" "$_member" 2>/dev/null; then
		log_error "could not extract '${_member}' from archive"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	ensure_bin_dir
	mv "${_tmp}/${_member}" "$(bin_dir)/${_dest_name}"
	chmod +x "$(bin_dir)/${_dest_name}"

	rm -rf "$_tmp"
	trap - EXIT
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS — all tests through Task 3 green.

- [ ] **Step 5: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add checksum-verified download and extract helper"
```

---

### Task 4: `scip` installer

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `install_tarball_binary`, `have_cmd`, `log_info` from Tasks 1-3; `OS`/`ARCH` set by `main()`
- Produces:
  - `SCIP_VERSION` variable, value `v0.9.0`
  - `scip_asset_name(os, arch)` → echoes e.g. `scip-darwin-arm64.tar.gz`
  - `install_scip(os, arch)` → skips if already present (unless `FORCE=1`), else downloads and installs

Note: upstream asset names use the same `darwin`/`linux` + `amd64`/`arm64` vocabulary this script normalizes to, so the mapping is a direct interpolation. All 4 combinations exist as published assets (verified during design).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
import pytest


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "scip_asset or scip_version or install_scip"`
Expected: FAIL — `scip_asset_name: not found`, `SCIP_VERSION` empty.

- [ ] **Step 3: Write minimal implementation**

Near the top of `setup.sh`, after `set -eu`, add the pinned-version block:

```sh
# ------------------------------------------------------------- versions ------

# Pinned deliberately, never "latest": query.py targets the v0.7.0-era
# `scip expt-convert` SQLite schema. v0.9.0's schema was verified
# byte-identical to v0.7.0's before this pin was raised.
SCIP_VERSION="v0.9.0"
SCIP_REPO="scip-code/scip"
```

Then add an installers section before `main()`:

```sh
# ------------------------------------------------------------ installers -----

scip_asset_name() {
	echo "scip-$1-$2.tar.gz"
}

install_scip() {
	_os=$1
	_arch=$2

	if [ "${FORCE:-0}" != "1" ] && have_cmd scip; then
		log_info "scip: already installed, skipping"
		return 0
	fi

	_asset=$(scip_asset_name "$_os" "$_arch")
	_base="https://github.com/${SCIP_REPO}/releases/download/${SCIP_VERSION}"

	log_info "scip: installing ${SCIP_VERSION}"
	if install_tarball_binary "${_base}/${_asset}" "${_base}/${_asset}.sha256" scip scip; then
		log_info "scip: installed"
	else
		log_error "scip: install failed — see https://github.com/${SCIP_REPO}/releases"
		return 1
	fi
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS.

- [ ] **Step 5: Verify against the real release (network)**

Run:
```bash
rm -rf /tmp/ci-scip-test
CODEINTEL_SETUP_SOURCED=1 CODEINTEL_BIN_DIR=/tmp/ci-scip-test FORCE=1 \
  sh -c '. ./setup.sh; install_scip darwin arm64'
/tmp/ci-scip-test/scip --version
```
(`CODEINTEL_SETUP_SOURCED=1` is required so sourcing doesn't trigger `main`.)
Expected: prints `scip version v0.9.0`.

- [ ] **Step 6: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add pinned scip installer"
```

---

### Task 5: zoekt cross-compile CI workflow

**Files:**
- Create: `ZOEKT_COMMIT`
- Create: `.github/workflows/build-zoekt.yml`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent CI concern)
- Produces: GitHub release assets named `zoekt-<os>-<arch>.tar.gz` (each containing both `zoekt-index` and `zoekt-webserver`) plus a `.sha256` sidecar per tarball, on a release tagged `zoekt-<short-sha>`. Task 6's installer depends on exactly these names.

Background: `sourcegraph/zoekt` publishes zero releases and zero tags, so there is no upstream binary to download on any platform. Cross-compilation with `CGO_ENABLED=0` was verified feasible during design (produced a statically-linked Linux ELF from macOS arm64).

- [ ] **Step 1: Create the pinned commit file**

Create `ZOEKT_COMMIT` containing exactly one line:

```
33f1f18af292
```

This is the commit the currently-working local binaries were built from (pseudo-version `v0.0.0-20260709064101-33f1f18af292`). Changing this file is the only thing that triggers a rebuild.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/build-zoekt.yml`:

```yaml
# Cross-compiles zoekt for the platforms setup.sh supports and publishes the
# binaries to this repo's releases.
#
# Why this exists: upstream sourcegraph/zoekt publishes no releases and no tags,
# so there is no prebuilt zoekt-index/zoekt-webserver to download on any
# platform. setup.sh downloads from *our* releases instead.
#
# Runs only when the pinned commit changes, or on manual dispatch — never on a
# schedule, so upstream breakage can't land unannounced.
name: build-zoekt

on:
  push:
    branches: [main]
    paths: ["ZOEKT_COMMIT", ".github/workflows/build-zoekt.yml"]
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-go@v5
        with:
          go-version: "1.25"

      - name: Read pinned commit
        id: pin
        run: |
          commit=$(tr -d '[:space:]' < ZOEKT_COMMIT)
          if [ -z "$commit" ]; then
            echo "ZOEKT_COMMIT is empty" >&2
            exit 1
          fi
          echo "commit=$commit" >> "$GITHUB_OUTPUT"

      - name: Cross-compile all platforms
        env:
          ZOEKT_COMMIT: ${{ steps.pin.outputs.commit }}
        run: |
          set -eux
          mkdir -p dist work
          cd work
          go mod init zoekt-build
          # Resolve the pinned commit to a module version and fetch it.
          GOFLAGS=-mod=mod go get "github.com/sourcegraph/zoekt@${ZOEKT_COMMIT}"

          for target in darwin/arm64 darwin/amd64 linux/amd64 linux/arm64; do
            os=${target%/*}
            arch=${target#*/}
            stage="../dist/stage-${os}-${arch}"
            mkdir -p "$stage"
            for cmd in zoekt-index zoekt-webserver; do
              GOFLAGS=-mod=mod GOOS="$os" GOARCH="$arch" CGO_ENABLED=0 \
                go build -trimpath -o "${stage}/${cmd}" \
                "github.com/sourcegraph/zoekt/cmd/${cmd}"
            done
            tar -czf "../dist/zoekt-${os}-${arch}.tar.gz" -C "$stage" zoekt-index zoekt-webserver
            rm -rf "$stage"
          done

      - name: Generate checksums
        run: |
          set -eu
          cd dist
          for f in *.tar.gz; do
            # Match the "<digest>  <filename>" sidecar format setup.sh parses.
            sha256sum "$f" > "${f}.sha256"
          done
          ls -la

      - name: Smoke-test the native binary
        run: |
          set -eux
          mkdir -p smoke
          tar -xzf dist/zoekt-linux-amd64.tar.gz -C smoke
          # zoekt-index has no -version flag; -h exits non-zero but prints usage.
          smoke/zoekt-index -h 2>&1 | grep -q "zoekt-index"
          smoke/zoekt-webserver -version

      - name: Publish release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ZOEKT_COMMIT: ${{ steps.pin.outputs.commit }}
        run: |
          set -eu
          tag="zoekt-${ZOEKT_COMMIT}"
          if gh release view "$tag" >/dev/null 2>&1; then
            gh release upload "$tag" dist/*.tar.gz dist/*.sha256 --clobber
          else
            gh release create "$tag" dist/*.tar.gz dist/*.sha256 \
              --title "zoekt @ ${ZOEKT_COMMIT}" \
              --notes "zoekt-index and zoekt-webserver cross-compiled from sourcegraph/zoekt@${ZOEKT_COMMIT}. Consumed by setup.sh."
          fi
```

- [ ] **Step 3: Validate the workflow file parses**

Run: `uv run python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/build-zoekt.yml').read_text()); print('valid yaml')"`
Expected: prints `valid yaml`.

If PyYAML isn't available, run: `uvx --from pyyaml python -c "..."` or skip — CI will surface a parse error on push.

- [ ] **Step 4: Verify the build steps locally before relying on CI**

Run:
```bash
rm -rf /tmp/zoekt-local && mkdir -p /tmp/zoekt-local && cd /tmp/zoekt-local
go mod init zoekt-build
GOFLAGS=-mod=mod go get "github.com/sourcegraph/zoekt@$(tr -d '[:space:]' < ~/Projects/codeintel/ZOEKT_COMMIT)"
GOFLAGS=-mod=mod GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -o zi github.com/sourcegraph/zoekt/cmd/zoekt-index
file zi
```
Expected: `zi: ELF 64-bit LSB executable, x86-64, ..., statically linked`.

- [ ] **Step 5: Commit**

```bash
git add ZOEKT_COMMIT .github/workflows/build-zoekt.yml
git commit -m "ci: cross-compile and publish zoekt binaries from pinned commit"
```

- [ ] **Step 6: Push and confirm the workflow produces a release**

```bash
git push
gh run watch
gh release view "zoekt-$(tr -d '[:space:]' < ZOEKT_COMMIT)"
```
Expected: the release exists with 4 `.tar.gz` assets and 4 `.sha256` assets.

Task 6 cannot be verified against the network until this release exists.

---

### Task 6: zoekt installer

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `install_tarball_binary`, `have_cmd` from Task 3; the release assets published by Task 5
- Produces:
  - `ZOEKT_COMMIT_PIN` variable (the commit string, kept in sync with the `ZOEKT_COMMIT` file)
  - `zoekt_asset_name(os, arch)` → echoes e.g. `zoekt-darwin-arm64.tar.gz`
  - `install_zoekt(os, arch)` → installs *both* `zoekt-index` and `zoekt-webserver` from one tarball

The tarball holds two binaries, so this cannot reuse `install_tarball_binary` (single-member) directly; it extracts both members.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "zoekt"`
Expected: FAIL — `zoekt_asset_name: not found`, `ZOEKT_COMMIT_PIN` empty.

- [ ] **Step 3: Write minimal implementation**

Add to the versions block near the top of `setup.sh`:

```sh
# Kept in sync with the repo-root ZOEKT_COMMIT file that CI builds from.
# tests/test_setup_sh.py asserts the two never drift.
ZOEKT_COMMIT_PIN="33f1f18af292"
CODEINTEL_REPO="phuongddx/codeintel"
```

Add to the installers section:

```sh
zoekt_asset_name() {
	echo "zoekt-$1-$2.tar.gz"
}

# zoekt ships as one tarball containing both binaries. Upstream
# sourcegraph/zoekt publishes no releases at all, so these come from
# codeintel's own releases (see .github/workflows/build-zoekt.yml).
install_zoekt() {
	_os=$1
	_arch=$2

	if [ "${FORCE:-0}" != "1" ] && have_cmd zoekt-index && have_cmd zoekt-webserver; then
		log_info "zoekt: already installed, skipping"
		return 0
	fi

	_asset=$(zoekt_asset_name "$_os" "$_arch")
	# ZOEKT_BASE_URL is overridable so tests can serve a local tarball.
	_base="${ZOEKT_BASE_URL:-https://github.com/${CODEINTEL_REPO}/releases/download/zoekt-${ZOEKT_COMMIT_PIN}}"

	log_info "zoekt: installing (pinned ${ZOEKT_COMMIT_PIN})"

	_tmp=$(mktemp -d)
	# shellcheck disable=SC2064
	trap "rm -rf '$_tmp'" EXIT

	if ! download_to "${_base}/${_asset}" "${_tmp}/z.tar.gz"; then
		log_error "zoekt: download failed (${_base}/${_asset})"
		rm -rf "$_tmp"; trap - EXIT; return 1
	fi
	if ! download_to "${_base}/${_asset}.sha256" "${_tmp}/z.sha256"; then
		log_error "zoekt: checksum download failed"
		rm -rf "$_tmp"; trap - EXIT; return 1
	fi
	_expected=$(cut -d' ' -f1 <"${_tmp}/z.sha256")
	if ! verify_sha256 "${_tmp}/z.tar.gz" "$_expected"; then
		rm -rf "$_tmp"; trap - EXIT; return 1
	fi
	if ! tar -xzf "${_tmp}/z.tar.gz" -C "$_tmp" zoekt-index zoekt-webserver 2>/dev/null; then
		log_error "zoekt: archive did not contain both binaries"
		rm -rf "$_tmp"; trap - EXIT; return 1
	fi

	ensure_bin_dir
	for _b in zoekt-index zoekt-webserver; do
		mv "${_tmp}/${_b}" "$(bin_dir)/${_b}"
		chmod +x "$(bin_dir)/${_b}"
	done

	rm -rf "$_tmp"; trap - EXIT
	log_info "zoekt: installed"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS.

- [ ] **Step 5: Verify against the real published release (requires Task 5 pushed)**

Run:
```bash
rm -rf /tmp/ci-zoekt-test
CODEINTEL_SETUP_SOURCED=1 CODEINTEL_BIN_DIR=/tmp/ci-zoekt-test FORCE=1 \
  sh -c '. ./setup.sh; install_zoekt darwin arm64'
/tmp/ci-zoekt-test/zoekt-webserver -version
```
Expected: the binary runs. If the release isn't published yet, this step blocks on Task 5 Step 6.

- [ ] **Step 6: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add zoekt installer sourced from codeintel releases"
```

---

### Task 7: `scip-swift` installer (macOS arm64 only)

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `install_tarball_binary`, `have_cmd` from Task 3
- Produces:
  - `SCIP_SWIFT_VERSION` variable (`v0.1.0`), `SCIP_SWIFT_REPO` (`phuongddx/scip-swift`)
  - `scip_swift_asset_name()` → echoes `scip-swift-v0.1.0-macos-arm64.tar.gz`
  - `install_scip_swift(os, arch)` → installs on `darwin/arm64`; on anything else logs "not available" and returns 0 (skip-by-design, not a failure)

The only published asset is `scip-swift-v0.1.0-macos-arm64.tar.gz`. Note the asset uses `macos`, not `darwin` — do not reuse the `scip` naming pattern.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "scip_swift"`
Expected: FAIL — `install_scip_swift: not found`.

- [ ] **Step 3: Write minimal implementation**

Add to the versions block:

```sh
SCIP_SWIFT_VERSION="v0.1.0"
SCIP_SWIFT_REPO="phuongddx/scip-swift"
```

Add to the installers section:

```sh
# Note: the asset says "macos", not "darwin" — different vocabulary from
# scip's own assets. Only arm64 is published.
scip_swift_asset_name() {
	echo "scip-swift-${SCIP_SWIFT_VERSION}-macos-arm64.tar.gz"
}

install_scip_swift() {
	_os=$1
	_arch=$2

	# Swift indexing reads an Xcode-produced IndexStore, so it is inherently
	# macOS-only; and only an arm64 binary is published. Skipping is expected
	# behavior on other platforms, not a failure.
	if [ "$_os" != "darwin" ] || [ "$_arch" != "arm64" ]; then
		log_info "scip-swift: not available for ${_os}/${_arch} (macOS arm64 only) — skipping"
		return 0
	fi

	if [ "${FORCE:-0}" != "1" ] && have_cmd scip-swift; then
		log_info "scip-swift: already installed, skipping"
		return 0
	fi

	_asset=$(scip_swift_asset_name)
	_base="https://github.com/${SCIP_SWIFT_REPO}/releases/download/${SCIP_SWIFT_VERSION}"

	log_info "scip-swift: installing ${SCIP_SWIFT_VERSION}"
	if install_tarball_binary "${_base}/${_asset}" "${_base}/${_asset}.sha256" scip-swift scip-swift; then
		log_info "scip-swift: installed"
	else
		log_error "scip-swift: install failed — build from source: https://github.com/${SCIP_SWIFT_REPO}"
		return 1
	fi
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm the upstream release actually has a `.sha256` sidecar**

Run: `gh release view v0.1.0 --repo phuongddx/scip-swift --json assets --jq '.assets[].name'`
Expected: lists `scip-swift-v0.1.0-macos-arm64.tar.gz`.

**If no `.sha256` asset is listed**, `install_tarball_binary` will fail on the checksum download. In that case, either (a) publish a `.sha256` sidecar to that release, or (b) add a `SKIP_SHA` path to `install_tarball_binary` for this one dependency and log a warning that the download is unverified. Prefer (a) — you own that repo.

- [ ] **Step 6: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add scip-swift installer for macOS arm64"
```

---

### Task 8: npm-based installers (`scip-typescript`, `scip-python`)

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `have_cmd`, `log_info`, `log_warn` from Tasks 1-3
- Produces:
  - `install_npm_indexer(binary_name, npm_package)` → shared helper; skips if present, warns and returns 0 if `npm` missing, else `npm install -g`
  - `install_scip_typescript()` → delegates with `scip-typescript` / `@sourcegraph/scip-typescript`
  - `install_scip_python()` → delegates with `scip-python` / `@sourcegraph/scip-python`

Both packages are plain npm globals whose `bin` name matches the binary (verified: `@sourcegraph/scip-typescript` → `scip-typescript`, `@sourcegraph/scip-python` → `scip-python`), so one helper covers both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "npm or typescript"`
Expected: FAIL — `install_npm_indexer: not found`.

- [ ] **Step 3: Write minimal implementation**

Add to the installers section:

```sh
# scip-typescript and scip-python are plain npm globals whose bin name matches
# the binary, so one helper covers both.
#
#   install_npm_indexer <binary_name> <npm_package>
install_npm_indexer() {
	_bin=$1
	_pkg=$2

	if [ "${FORCE:-0}" != "1" ] && have_cmd "$_bin"; then
		log_info "${_bin}: already installed, skipping"
		return 0
	fi

	if ! have_cmd npm; then
		log_warn "${_bin}: npm not found — skipping. Install Node.js, then: npm install -g ${_pkg}"
		return 0
	fi

	log_info "${_bin}: installing via npm"
	if npm install -g "$_pkg" >/dev/null 2>&1; then
		log_info "${_bin}: installed"
	else
		log_error "${_bin}: npm install failed — try manually: npm install -g ${_pkg}"
		return 1
	fi
}

install_scip_typescript() {
	install_npm_indexer scip-typescript @sourcegraph/scip-typescript
}

install_scip_python() {
	install_npm_indexer scip-python @sourcegraph/scip-python
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add npm-based scip-typescript and scip-python installers"
```

---

### Task 9: `scip-java` detect-only with `/dev/tty` confirmation

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: `have_cmd`, `log_info`, `log_warn` from Tasks 1-3
- Produces:
  - `confirm(prompt)` → reads a y/n answer from `/dev/tty` (never stdin); returns 0 on yes, 1 otherwise; returns 1 immediately if `/dev/tty` is unavailable (non-interactive run)
  - `install_scip_java()` → detects `docker`/JVM, explains, asks `confirm`, and only then pulls the Docker image; always returns 0 (declining or absence is by design)

Reading from `/dev/tty` rather than stdin is mandatory: under `curl … | sh` stdin is the piped script text, so a `read` from stdin would consume script bytes or hit EOF instead of the user's answer.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "confirm or scip_java"`
Expected: FAIL — `confirm: not found`, `install_scip_java: not found`.

- [ ] **Step 3: Write minimal implementation**

Add a prompt helper after the logging section:

```sh
# ---------------------------------------------------------------- prompt -----

# Ask a y/n question. MUST read from /dev/tty, never stdin: under
# `curl … | sh` stdin is the piped script source, so reading stdin would
# consume script bytes or hit EOF instead of the user's answer.
# Returns non-zero (i.e. "no") when there is no tty, so non-interactive runs
# never hang and never silently opt in.
confirm() {
	if [ ! -r /dev/tty ]; then
		log_info "no terminal available — assuming no"
		return 1
	fi
	printf '%s [y/N] ' "$1" >/dev/tty
	read -r _answer </dev/tty || return 1
	case "$_answer" in
	y | Y | yes | YES) return 0 ;;
	*) return 1 ;;
	esac
}
```

Add to the installers section:

```sh
SCIP_JAVA_IMAGE="ghcr.io/scip-code/scip-java:latest"

# Detect-only by design. Upstream ships scip-java as a Docker image or a
# JVM launcher — not a standalone binary — so auto-provisioning a container
# runtime or JDK is deliberately out of scope. Always returns 0: neither a
# missing runtime nor a declined prompt is a failure.
install_scip_java() {
	if [ "${FORCE:-0}" != "1" ] && have_cmd scip-java; then
		log_info "scip-java: already installed, skipping"
		return 0
	fi

	# Use `if`, not `have_cmd docker && _has_docker=1`. The && form happens to
	# survive `set -e` on both dash and bash-posix, but the intent is clearer
	# and the exit-status semantics are unambiguous this way.
	_has_docker="no"
	_has_jvm="no"
	if have_cmd docker; then _has_docker="yes"; fi
	if have_cmd java; then _has_jvm="yes"; fi

	log_info "scip-java: detect-only (upstream ships a Docker image or JVM launcher, not a binary)"
	log_info "scip-java:   docker present: ${_has_docker}"
	log_info "scip-java:   java present:   ${_has_jvm}"

	if [ "$_has_docker" != "yes" ]; then
		log_info "scip-java: no docker — to index Java/Kotlin later, install Docker then run:"
		log_info "scip-java:   docker pull ${SCIP_JAVA_IMAGE}"
		return 0
	fi

	log_info "scip-java: the image is large (bundles JDK 17, 21, and 25)"
	if confirm "scip-java: pull ${SCIP_JAVA_IMAGE} now?"; then
		if docker pull "$SCIP_JAVA_IMAGE"; then
			log_info "scip-java: image pulled"
		else
			log_warn "scip-java: docker pull failed — pull it manually when needed"
		fi
	else
		log_info "scip-java: skipped — pull it later with: docker pull ${SCIP_JAVA_IMAGE}"
	fi
	return 0
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add setup.sh tests/test_setup_sh.py
git commit -m "feat(setup): add scip-java detection with tty-based confirmation"
```

---

### Task 10: Orchestration, flags, summary, README, and end-to-end verification

**Files:**
- Modify: `setup.sh`
- Modify: `README.md` (install section, ~lines 55-71)
- Test: `tests/test_setup_sh.py`

**Interfaces:**
- Consumes: every installer from Tasks 4, 6, 7, 8, 9
- Produces:
  - `parse_args()` → sets `ONLY` and `FORCE` from `--only <name>` / `--force`; `--help` prints usage and exits 0
  - `usage()` → prints the help text
  - `record(name, status)` → appends a `name:status` line to `$SUMMARY` (newline-delimited string, not an array — POSIX)
  - `print_summary()` → prints the collected results
  - `should_run(name)` → returns 0 when `ONLY` is empty or equals `name`
  - `run_one(name, cmd...)` → runs one installer, records `ok`/`FAILED`, sets `EXIT_CODE=1` on failure without aborting
  - `main()` → full orchestration with failure isolation and the documented exit-code contract

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_setup_sh.py`:

```python
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
            "CODEINTEL_BIN_DIR": str(tmp_path / "bin"),
            "SHELL": "/bin/zsh",
        },
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert not npm_log.exists(), "--only scip must not run npm installers"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -v -k "help or parse_args or record or only_flag"`
Expected: FAIL — `parse_args: not found`, `record: not found`.

- [ ] **Step 3: Write the implementation**

Replace `main()` in `setup.sh` with the full orchestrator, and add the arg/summary helpers above it:

```sh
# ------------------------------------------------------------ orchestration --

ONLY=""
FORCE=0
# POSIX: no arrays, so the summary is a newline-delimited string.
SUMMARY=""
EXIT_CODE=0

usage() {
	cat <<'EOF'
Usage: setup.sh [options]

Installs codeintel's external binary dependencies into ~/.codeintel/bin.

Options:
  --only <name>   Install just one dependency. One of:
                  scip, zoekt, scip-swift, scip-typescript,
                  scip-python, scip-java
  --force         Reinstall even if already present
  --help          Show this message

Environment:
  CODEINTEL_BIN_DIR   Override the install directory
EOF
}

parse_args() {
	while [ $# -gt 0 ]; do
		case "$1" in
		--only)
			if [ $# -lt 2 ]; then
				log_error "--only requires a value"
				return 1
			fi
			ONLY=$2
			shift 2
			;;
		--force)
			FORCE=1
			shift
			;;
		--help | -h)
			usage
			exit 0
			;;
		*)
			log_error "unknown option: $1"
			usage >&2
			return 1
			;;
		esac
	done
}

record() {
	SUMMARY="${SUMMARY}$1:$2
"
}

print_summary() {
	echo ""
	echo "summary"
	printf '%s' "$SUMMARY" | while IFS=: read -r _name _status; do
		[ -n "$_name" ] || continue
		printf '  %-16s %s\n' "$_name" "$_status"
	done
}

# Run one installer, isolating failure so a single bad dependency never
# aborts the whole run.
run_one() {
	_name=$1
	shift
	if "$@"; then
		record "$_name" "ok"
	else
		record "$_name" "FAILED"
		EXIT_CODE=1
	fi
}

should_run() {
	[ -z "$ONLY" ] || [ "$ONLY" = "$1" ]
}

main() {
	parse_args "$@" || exit 2

	echo "codeintel setup"
	OS=$(detect_os) || exit 1
	ARCH=$(detect_arch) || exit 1
	log_info "platform: ${OS}/${ARCH}"
	log_info "install dir: $(bin_dir)"
	echo ""

	ensure_bin_dir

	# `if` form rather than `should_run X && run_one …`: unambiguous exit-status
	# semantics under `set -e` across dash and bash-posix.
	if should_run scip; then run_one scip install_scip "$OS" "$ARCH"; fi
	if should_run zoekt; then run_one zoekt install_zoekt "$OS" "$ARCH"; fi
	if should_run scip-swift; then run_one scip-swift install_scip_swift "$OS" "$ARCH"; fi
	if should_run scip-typescript; then run_one scip-typescript install_scip_typescript; fi
	if should_run scip-python; then run_one scip-python install_scip_python; fi
	if should_run scip-java; then run_one scip-java install_scip_java; fi

	ensure_on_path
	print_summary
	exit "$EXIT_CODE"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py -v`
Expected: PASS — every test from Tasks 1-10 green.

- [ ] **Step 5: Confirm no bashisms slipped in**

Run: `command -v shellcheck >/dev/null && shellcheck -s sh setup.sh || echo "shellcheck not installed - install with: brew install shellcheck"`
Expected: no errors. Fix any `SC2039`/`SC3xxx` (non-POSIX construct) findings — those are exactly the Linux-only breakages the POSIX constraint exists to prevent.

- [ ] **Step 6: Update the README install section**

In `README.md`, replace the block from `## Install` through the `\* scip-swift ...` footnote (currently ~lines 54-71) with:

```markdown
## Install

```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh
uv sync
```

`setup.sh` installs every external binary codeintel needs into `~/.codeintel/bin`
and adds it to your shell rc. macOS and Linux; Windows is not supported.

| Purpose | Binary | Source |
|---------|--------|--------|
| SCIP → SQLite conversion | `scip` | prebuilt, pinned `v0.9.0` |
| Lexical search | `zoekt-index` · `zoekt-webserver` | cross-compiled by [our CI](.github/workflows/build-zoekt.yml) — upstream publishes no binaries |
| TypeScript indexing | `scip-typescript` | `npm install -g` |
| Python indexing | `scip-python` | `npm install -g` |
| Swift indexing | `scip-swift` | prebuilt, macOS arm64 only |
| Java/Kotlin indexing | `scip-java` | detect-only — Docker image, asks before pulling |

Options: `--only <name>` to install one dependency, `--force` to reinstall,
`--help` for usage. Re-running is safe: anything already present is skipped.

Swift caveat: `scip-swift` indexes and populates the symbol table, but its
occurrences carry no source ranges yet, so per-file nav returns empty on Swift
repos. See [`docs/project-roadmap.md`](docs/project-roadmap.md).
```

- [ ] **Step 7: End-to-end verification on a clean install dir**

Run:
```bash
rm -rf /tmp/codeintel-e2e
CODEINTEL_BIN_DIR=/tmp/codeintel-e2e sh setup.sh
ls -la /tmp/codeintel-e2e
/tmp/codeintel-e2e/scip --version
/tmp/codeintel-e2e/zoekt-webserver -version
/tmp/codeintel-e2e/scip-swift --version
```
Expected: summary shows `ok` for scip, zoekt, scip-swift; each binary runs. `scip-java` reports detect-only.

- [ ] **Step 8: Verify the pinned `scip v0.9.0` actually works with codeintel**

This is the empirical check the spec requires — the schema was proven identical to v0.7.0, but v0.9.0 had not been executed against codeintel.

Run:
```bash
PATH="/tmp/codeintel-e2e:$PATH" uv run pytest -m integration -v
```
Expected: PASS.

**If it fails on a schema/conversion error**, change `SCIP_VERSION` in `setup.sh` to `v0.7.0` (the known-good version), update the comment above it and the README table, re-run this step, and note the rollback in `docs/project-roadmap.md`.

- [ ] **Step 9: Verify on Linux in a container**

The spec requires Linux verification, and it is the platform most likely to break
(different `sh`, `sha256sum` instead of `shasum`, `aarch64`/`x86_64` arch strings). Run the
real script against a real Debian userland:

```bash
docker run --rm -v "$PWD:/w" -w /w debian:stable-slim sh -c '
  apt-get update -qq && apt-get install -y -qq curl ca-certificates tar >/dev/null
  CODEINTEL_BIN_DIR=/tmp/ci-bin sh setup.sh --only scip
  /tmp/ci-bin/scip --version
'
```
Expected: platform detected as `linux/amd64` (or `linux/arm64` on Apple Silicon Docker),
`scip` installs and reports `v0.9.0`.

Then confirm zoekt on Linux too:
```bash
docker run --rm -v "$PWD:/w" -w /w debian:stable-slim sh -c '
  apt-get update -qq && apt-get install -y -qq curl ca-certificates tar >/dev/null
  CODEINTEL_BIN_DIR=/tmp/ci-bin sh setup.sh --only zoekt
  /tmp/ci-bin/zoekt-webserver -version
'
```
Expected: the cross-compiled Linux binary from Task 5 runs.

If `docker` isn't available, note it and rely on the dash-based unit tests plus CI; but do not
mark this step complete without either running it or explicitly recording that it was skipped.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS — no regressions in the existing 11 test modules.

- [ ] **Step 11: Commit**

```bash
git add setup.sh tests/test_setup_sh.py README.md
git commit -m "feat(setup): add orchestration, flags, summary, and install docs"
```

---

## Notes for the implementer

**Why POSIX `sh` and not bash:** `curl -fsSL … | sh` runs the script under the system `sh`, ignoring the shebang. On Debian/Ubuntu that is `dash`, which has no arrays, no `[[ ]]`, and no `local -a`. A bashism here works perfectly on the macOS dev machine and breaks only for Linux users. `tests/test_setup_sh.py` deliberately invokes `sh`, not `bash`, so these slip-ups surface in CI rather than in a bug report.

**Why `/dev/tty` for prompts:** same root cause — under `curl … | sh`, stdin is the script text itself. A `read` from stdin would consume script bytes or immediately hit EOF. Two tests guard this specifically.

**Task 6 depends on Task 5 being pushed.** The zoekt installer cannot be verified against the network until the CI workflow has published a release. Its unit tests use a local `file://` tarball and pass without the network; only Task 6 Step 5 needs the real release.

**Known limitation carried forward, not introduced here:** `index_cli.py` invokes `zoekt-index` without passing a repo name, so Zoekt names shards by directory basename rather than by codeintel's `--slug`. When a slug differs from the directory name, `searchCode(repo=<slug>)`'s `r:` filter silently matches nothing. This is a pre-existing codeintel bug, out of scope for this plan, and worth a separate fix.
