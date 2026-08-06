#!/usr/bin/env sh
# jarvis dependency bootstrapper.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/jarvis-intelligence/jarvis-index/main/setup.sh | sh
#
# STRICTLY POSIX sh: `curl | sh` ignores the shebang above and runs under the
# system sh (dash on many Linux distros). No arrays, no [[ ]], no bashisms.

set -eu

# ------------------------------------------------------------- versions ------

# Pinned deliberately, never "latest": query.py targets the v0.7.0-era
# `scip expt-convert` SQLite schema. v0.9.0's schema was verified
# byte-identical to v0.7.0's before this pin was raised.
SCIP_VERSION="v0.9.0"
SCIP_REPO="scip-code/scip"

# Kept in sync with the repo-root ZOEKT_COMMIT file that CI builds from.
# tests/test_setup_sh.py asserts the two never drift.
ZOEKT_COMMIT_PIN="33f1f18af292"
JARVIS_REPO="phuongddx/jarvis"

# The zoekt binaries are published to a separate PUBLIC repo. jarvis's own
# repo is private, and GitHub serves release assets only to viewers of the
# owning repo -- an unauthenticated `curl` against a private repo's release
# 404s, which is every user running this script. Do not point this back at
# JARVIS_REPO; tests/test_setup_sh.py asserts the two differ.
ZOEKT_RELEASE_REPO="jarvis-intelligence/jarvis-index"

# v0.1.1 is the first release whose binary supports `scip-swift index …`, the
# form index_cli.py invokes. v0.1.0 predates that subcommand and cannot be
# driven by jarvis at all. v0.1.2 is the first whose xcodebuild backend
# disables code signing, without which repos containing signed app-extension
# targets fail during GatherProvisioningInputs before compiling anything.
SCIP_SWIFT_VERSION="v0.1.2"
SCIP_SWIFT_REPO="phuongddx/scip-swift"

# scip-java ships one self-contained launcher asset per release: a POSIX sh
# script with an embedded JAR. It runs on any JVM, so unlike scip-swift there
# is no os/arch gating.
#
# The scip-kotlinc plugin inside it is compiled against Kotlin 2.2.0 EXACTLY.
# Kotlin's compiler-plugin API is internal and unstable: 2.1.21 and 2.3.20 fail
# with AbstractMethodError, and even 2.2.20 fails with NoSuchMethodError. If
# this version is bumped, re-check which Kotlin the new release targets.
SCIP_JAVA_VERSION="v0.13.1"
SCIP_JAVA_REPO="scip-code/scip-java"
SCIP_JAVA_KOTLIN="2.2.0"

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

# ---------------------------------------------------------------- prompt -----

# Ask a y/n question. MUST read from /dev/tty, never stdin: under
# `curl … | sh` stdin is the piped script source, so reading stdin would
# consume script bytes or hit EOF instead of the user's answer.
# Returns non-zero (i.e. "no") when there is no tty, so non-interactive runs
# never hang and never silently opt in.
confirm() {
	# `[ -r /dev/tty ]` is NOT sufficient: the device node can exist and test as
	# readable while no controlling terminal is attached (CI, a piped
	# subprocess). Writing to it then fails with a raw "Device not configured"
	# error that looks like a broken installer. Probe it for real instead.
	#
	# The subshell is load-bearing. POSIX requires the shell to ABORT on a
	# redirection error against a special built-in, and `:` is one -- so a bare
	# `{ : >/dev/tty; }` kills the script (dash exits 2) instead of returning
	# false. Containing it in a subshell turns that abort into an exit status.
	if ! ( : >/dev/tty ) 2>/dev/null; then
		log_info "no terminal available — assuming no"
		return 1
	fi
	printf '%s [y/N] ' "$1" >/dev/tty 2>/dev/null || return 1
	read -r _answer </dev/tty 2>/dev/null || return 1
	case "$_answer" in
	y | Y | yes | YES) return 0 ;;
	*) return 1 ;;
	esac
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

# ------------------------------------------------------------ install dir ----

# Where downloaded binaries go. JARVIS_BIN_DIR exists so tests can redirect
# writes away from the real home directory.
bin_dir() {
	if [ -n "${JARVIS_BIN_DIR:-}" ]; then
		echo "$JARVIS_BIN_DIR"
	else
		echo "${HOME}/.jarvis/bin"
	fi
}

ensure_bin_dir() {
	mkdir -p "$(bin_dir)"
}

# Where bash shims go. Keyed to JARVIS_DATA_DIR, NOT JARVIS_BIN_DIR:
# index_cli.py resolves this same path via config.shim_dir() -> data_dir(),
# which only honours JARVIS_DATA_DIR. A shim the reader cannot find is
# worse than no shim at all.
#
# A sibling of bin_dir rather than a subdirectory: bin/ holds pinned binaries
# we downloaded and own, shims/ holds symlinks to system tools we did not.
shim_dir() {
	echo "${JARVIS_DATA_DIR:-${HOME}/.jarvis}/shims"
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

	# SC2016 is intentional here: $PATH must stay LITERAL in the rc file so it
	# expands at shell-startup time. Expanding it now would bake today's PATH
	# permanently into the rc.
	# shellcheck disable=SC2016
	printf '\n# added by jarvis setup\nexport PATH="%s:$PATH"\n' "$_dir" >>"$_rc"
	log_info "added ${_dir} to ${_rc} — run 'exec \$SHELL' or open a new terminal"
}

# -------------------------------------------------------------- download -----

have_cmd() {
	command -v "$1" >/dev/null 2>&1
}

# A dependency counts as present if it is already in our install dir OR
# anywhere on PATH.
#
# The bin_dir check is load-bearing: setup.sh only *appends* its install dir to
# the shell rc, so that dir is not on PATH during the run that creates it, nor
# on any re-run in the same shell. A PATH-only check therefore re-downloads
# every binary on every re-run -- which CI caught.
already_installed() {
	if [ -x "$(bin_dir)/$1" ]; then
		return 0
	fi
	have_cmd "$1"
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

# Download a bare (non-archive) binary plus its .sha256 sidecar, verify it, and
# install it into bin_dir() under dest_name. Separate from
# install_tarball_binary rather than a refactor of it: the only difference is
# the missing extraction step, and four callers already depend on the tarball
# helper's behavior.
#
#   install_raw_binary <url> <sha_url> <dest_name>
install_raw_binary() {
	_url=$1
	_sha_url=$2
	_dest_name=$3

	_tmp=$(mktemp -d)
	# Clean up the temp dir on every exit path, including failure.
	# shellcheck disable=SC2064
	trap "rm -rf '$_tmp'" EXIT

	if ! download_to "$_url" "${_tmp}/binary"; then
		log_error "download failed: ${_url}"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	if ! download_to "$_sha_url" "${_tmp}/binary.sha256"; then
		log_error "checksum download failed: ${_sha_url}"
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	# Sidecar format is "<digest>  <filename>"; take the first field.
	_expected=$(cut -d' ' -f1 <"${_tmp}/binary.sha256")
	if ! verify_sha256 "${_tmp}/binary" "$_expected"; then
		rm -rf "$_tmp"
		trap - EXIT
		return 1
	fi

	ensure_bin_dir
	mv "${_tmp}/binary" "$(bin_dir)/${_dest_name}"
	chmod +x "$(bin_dir)/${_dest_name}"

	rm -rf "$_tmp"
	trap - EXIT
}

# ------------------------------------------------------------ installers -----

scip_asset_name() {
	echo "scip-$1-$2.tar.gz"
}

# Overridable so tests can point at fixtures instead of the real filesystem.
BASH_SHIM_CANDIDATES="${BASH_SHIM_CANDIDATES:-/opt/homebrew/bin/bash /usr/local/bin/bash}"

# True when $1 is a bash >= 4.4. Below that, `set -u` plus an empty
# "${arr[@]}" is an error -- which is exactly how scip-java's generated javac
# wrapper dies on macOS's stock bash 3.2.
#
# Parses `bash --version` rather than $BASH_VERSINFO so a test fixture can be a
# plain sh script. First line looks like:
#   GNU bash, version 5.3.15(1)-release (aarch64-apple-darwin25.4.0)
bash_at_least_44() {
	_bin=$1
	[ -n "$_bin" ] || return 1
	[ -x "$_bin" ] || return 1
	_line=$("$_bin" --version 2>/dev/null | head -n 1) || return 1
	_ver=${_line#*version }
	_ver=${_ver%%[!0-9.]*}
	_major=${_ver%%.*}
	_rest=${_ver#*.}
	_minor=${_rest%%.*}
	case "$_major" in '' | *[!0-9]*) return 1 ;; esac
	case "$_minor" in '' | *[!0-9]*) return 1 ;; esac
	if [ "$_major" -gt 4 ]; then return 0; fi
	if [ "$_major" -eq 4 ] && [ "$_minor" -ge 4 ]; then return 0; fi
	return 1
}

# scip-java's generated javac wrapper is `#!/usr/bin/env bash` with `set -eu`
# and an unguarded "${LAUNCHER_ARGS[@]}", so it needs bash >= 4.4 on PATH.
# macOS ships only 3.2, which breaks every Maven-built Java repo. Linux ships
# >= 4.4, so this is a no-op there.
#
# Never runs `brew`: installing a shell is the user's call, and setup.sh
# otherwise only downloads pinned release binaries into its own bin dir.
#
# Always symlinks a known-good bash into the shim dir on darwin, even when
# `command -v bash` here is already modern: this is setup.sh's OWN PATH at
# install time, not necessarily the PATH the indexer subprocess inherits
# later (a GUI-launched MCP server, launchd, a stripped-env shell). Baking
# the resolved path into the shim dir removes that PATH-context dependency --
# `_java_indexer_env()` only checks whether the shim file exists on disk.
install_bash_shim() {
	_os=$1
	if [ "$_os" != "darwin" ]; then
		record bash-shim "not needed (linux ships bash >= 4.4)"
		return 0
	fi

	_default=$(command -v bash 2>/dev/null) || _default=""
	if bash_at_least_44 "$_default"; then
		mkdir -p "$(shim_dir)"
		ln -sf "$_default" "$(shim_dir)/bash"
		log_info "bash-shim: linked default bash (${_default})"
		record bash-shim "ok"
		return 0
	fi

	# SC2086 intentional: BASH_SHIM_CANDIDATES is a space-separated list and
	# must word-split. POSIX sh has no arrays, which is why it is a string.
	# shellcheck disable=SC2086
	for _cand in $BASH_SHIM_CANDIDATES; do
		if bash_at_least_44 "$_cand"; then
			mkdir -p "$(shim_dir)"
			ln -sf "$_cand" "$(shim_dir)/bash"
			log_info "bash-shim: linked ${_cand}"
			record bash-shim "ok"
			return 0
		fi
	done

	log_warn "bash-shim: no bash >= 4.4 found. Maven-built Java repos cannot be SCIP-indexed until you run: brew install bash"
	record bash-shim "skipped (run: brew install bash)"
	return 0
}

install_scip() {
	_os=$1
	_arch=$2

	if [ "${FORCE:-0}" != "1" ] && already_installed scip; then
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

zoekt_asset_name() {
	echo "zoekt-$1-$2.tar.gz"
}

# zoekt ships as one tarball containing both binaries. Upstream
# sourcegraph/zoekt publishes no releases at all, so these come from our own
# releases in the public jarvis-intelligence/jarvis-index repo -- NOT from jarvis's own
# repo, which is private and would 404 (see build-zoekt.yml, and
# ZOEKT_RELEASE_REPO above).
#
# zoekt-git-index, not zoekt-index: jarvis indexes from the git tree so
# gitignored content never enters the index. Nothing calls zoekt-index any
# more, and it is deliberately not installed as a fallback — falling back
# would silently reintroduce junk indexing.
install_zoekt() {
	_os=$1
	_arch=$2

	if [ "${FORCE:-0}" != "1" ] && already_installed zoekt-git-index && already_installed zoekt-webserver; then
		log_info "zoekt: already installed, skipping"
		return 0
	fi

	_asset=$(zoekt_asset_name "$_os" "$_arch")
	# ZOEKT_BASE_URL is overridable so tests can serve a local tarball.
	_base="${ZOEKT_BASE_URL:-https://github.com/${ZOEKT_RELEASE_REPO}/releases/download/zoekt-${ZOEKT_COMMIT_PIN}}"

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
	if ! tar -xzf "${_tmp}/z.tar.gz" -C "$_tmp" zoekt-git-index zoekt-webserver 2>/dev/null; then
		log_error "zoekt: archive did not contain both binaries"
		rm -rf "$_tmp"; trap - EXIT; return 1
	fi

	ensure_bin_dir
	for _b in zoekt-git-index zoekt-webserver; do
		mv "${_tmp}/${_b}" "$(bin_dir)/${_b}"
		chmod +x "$(bin_dir)/${_b}"
	done

	rm -rf "$_tmp"; trap - EXIT
	log_info "zoekt: installed"
}

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

	if [ "${FORCE:-0}" != "1" ] && already_installed scip-swift; then
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

# scip-typescript and scip-python are plain npm globals whose bin name matches
# the binary, so one helper covers both.
#
#   install_npm_indexer <binary_name> <npm_package>
install_npm_indexer() {
	_bin=$1
	_pkg=$2

	if [ "${FORCE:-0}" != "1" ] && already_installed "$_bin"; then
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

# Installs upstream's single-file launcher. Unattended, like scip/zoekt/
# scip-swift: the old confirm() prompt existed because the docker image is
# 6.75GB, and confirm() returns false without a TTY, which would make
# `curl | sh` silently skip scip-java.
install_scip_java() {
	if [ "${FORCE:-0}" != "1" ] && already_installed scip-java; then
		log_info "scip-java: already installed, skipping"
		return 0
	fi

	# The launcher is a JAR bootstrap: without a JVM it cannot run at all.
	# A soft skip with instructions, matching install_npm_indexer's missing-npm
	# branch — not a hard failure that would abort the whole setup run.
	if ! have_cmd java; then
		log_warn "scip-java: java not found — skipping. Install a JDK, then: ./setup.sh --only scip-java"
		return 0
	fi

	_asset="scip-java-${SCIP_JAVA_VERSION}"
	_base="https://github.com/${SCIP_JAVA_REPO}/releases/download/${SCIP_JAVA_VERSION}"

	log_info "scip-java: installing ${SCIP_JAVA_VERSION} (~86MB launcher)"
	if install_raw_binary "${_base}/${_asset}" "${_base}/${_asset}.sha256" scip-java; then
		log_info "scip-java: installed"
		log_info "scip-java: Kotlin repos must use Kotlin ${SCIP_JAVA_KOTLIN} exactly; Java is unrestricted"
	else
		log_error "scip-java: install failed — see https://github.com/${SCIP_JAVA_REPO}"
		return 1
	fi
}

# ------------------------------------------------------------ orchestration --

ONLY=""
FORCE=0
# POSIX: no arrays, so the summary is a newline-delimited string.
SUMMARY=""
EXIT_CODE=0

usage() {
	cat <<'EOF'
Usage: setup.sh [options]

Installs jarvis's external binary dependencies into ~/.jarvis/bin.

Options:
  --only <name>   Install just one dependency. One of:
                  scip, zoekt, scip-swift, scip-typescript,
                  scip-python, scip-java, bash-shim
  --force         Reinstall even if already present
  --help          Show this message

Environment:
  JARVIS_BIN_DIR   Override the install directory
  JARVIS_DATA_DIR  Override where the bash shim is created (default ~/.jarvis)
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

# ----------------------------------------------------------------- main ------

main() {
	parse_args "$@" || exit 2

	echo "jarvis setup"
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
	if should_run bash-shim; then install_bash_shim "$OS"; fi

	ensure_on_path
	print_summary
	exit "$EXIT_CODE"
}

# Testability seam: tests source this file with JARVIS_SETUP_SOURCED=1 to
# call individual functions without performing a real install.
if [ "${JARVIS_SETUP_SOURCED:-}" != "1" ]; then
	main "$@"
fi
