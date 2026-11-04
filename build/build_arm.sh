#!/bin/bash
set -e
base=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
sdk=${1:-/opt/petalinux/2020.2/environment-setup-cortexa9t2hf-neon-xilinx-linux-gnueabi}
for target in bci_app bci_run st7789_display pl_sample_replay; do
 bash "$base/src/ps_app/build_sdk.sh" "$sdk" "$target"
done
echo ARM_BUILD_READY
