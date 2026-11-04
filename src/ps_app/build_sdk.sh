#!/usr/bin/env bash
# Run in Ubuntu, never on the Zynq board. Does not install or alter the SDK.
set -e
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sdk_env=${1:-}
target=${2:-rf_serial_rx}
case "$target" in
    rf_serial_rx|pl_sample_replay|pl_live_rx|st7789_display|bci_app|bci_run) ;;
    *) printf 'Unknown build target: %s\n' "$target" >&2; exit 2 ;;
esac
if [[ -z "$sdk_env" ]]; then
    shopt -s nullglob
    candidates=(/opt/petalinux/2020.2/environment-setup-* "$HOME"/petalinux-sdk/2020.2/environment-setup-*)
    if [[ ${#candidates[@]} -ne 1 ]]; then
        printf '%s\n' 'Specify the installed SDK environment:' \
            '  bash build_sdk.sh /actual/sdk/environment-setup-cortexa9...'
        exit 2
    fi
    sdk_env=${candidates[0]}
fi
if [[ ! -f "$sdk_env" ]]; then
    printf 'SDK environment does not exist: %s\n' "$sdk_env" >&2
    exit 2
fi
# SDK scripts may depend on unset variables, so deliberately do not use set -u.
source "$sdk_env"
if [[ -z "${CC:-}" ]]; then
    printf '%s\n' 'The SDK did not export CC; compilation stopped.' >&2
    exit 2
fi
printf 'SDK compiler: %s\n' "$CC"
make -B "$target" CFLAGS="${CFLAGS:-} -O2 -Wall -Wextra -Wpedantic -std=c11"
if command -v readelf >/dev/null; then
    elf_header=$(LC_ALL=C readelf -h "$target")
    printf '%s\n' "$elf_header"
    if ! printf '%s\n' "$elf_header" | grep -Eq 'Class:[[:space:]]+ELF32'; then
        printf '%s\n' 'ERROR: expected ELF32.' >&2
        exit 1
    fi
    if ! printf '%s\n' "$elf_header" | grep -Eq 'Machine:[[:space:]]+ARM[[:space:]]*$'; then
        printf '%s\n' 'ERROR: expected ARM, not AArch64 or x86.' >&2
        exit 1
    fi
    LC_ALL=C readelf -l "$target" | grep 'interpreter' || true
else
    printf '%s\n' 'readelf is unavailable; verify ARM ELF32 architecture before deployment.'
    exit 1
fi
sha256sum "$target"
printf '%s\n' 'Build complete. Board runtime, libc compatibility and device I/O still need testing.'
