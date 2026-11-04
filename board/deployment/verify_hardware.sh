#!/bin/sh
set -eu
pkg=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Specify NEW output directory}
(cd "$pkg" && sha256sum -c source.sha256)
/etc/init.d/bci-ui stop
test ! -e "$out"
mkdir "$out"
"$pkg/pl_sample_replay" --bundle "$pkg/baseline_bundle" --hardware --core-base 0x43d00000 --fifo-base 0x43d10000 --out "$out/decisions.csv"
echo 'PASS BASELINE744_ON_FINAL_PLATFORM'
/etc/init.d/bci-ui start
