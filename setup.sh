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
