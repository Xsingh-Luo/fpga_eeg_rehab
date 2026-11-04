#!/bin/bash
set -e
pkg=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sdk=${1:-/opt/petalinux/2020.2/environment-setup-cortexa9t2hf-neon-xilinx-linux-gnueabi}
for target in st7789_display bci_app bci_run pl_sample_replay; do
    bash "$pkg/ps_app/build_sdk.sh" "$sdk" "$target"
    cp "$pkg/ps_app/$target" "$pkg/$target"
    chmod +x "$pkg/$target"
done
(cd "$pkg" && find . -type f ! -path ./source.sha256 -print0 | sort -z | xargs -0 sha256sum > source.sha256)
echo 'FINAL_ARM_BUILD_READY: application, live worker, labelled replay and display built.'
