#!/bin/sh
# Runs in place of the final /sbin/halt command in the original init script.
# The real halt command and all its original arguments are preserved.
app=/root/bci/final
/etc/init.d/bci-ui stop || :
sync
# Normally init has already remounted root read-only. Retry if necessary.
mount -o remount,ro / || :
if ! "$app/st7789_display" --emio --shutdown-ready; then
    echo 'BCI: storage guard or LCD failed; no safe-to-cut message shown.' > /dev/console
fi
exec /sbin/halt "$@"
