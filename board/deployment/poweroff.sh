#!/bin/sh
# Called only after the supervisor stops its inference child.
set -eu
test -x /sbin/poweroff
sync
# Use the regular init shutdown path; never use -f here.
exec /sbin/poweroff
