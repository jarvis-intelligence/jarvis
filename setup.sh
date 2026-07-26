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
