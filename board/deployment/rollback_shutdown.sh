#!/bin/sh
set -eu
app=/root/bci/final
backup=/root/bci/shutdown_backup_20261103
test "$(id -u)" = 0
test -d "$backup/application"
test ! -e "$backup/shutdown_application"
sha256sum -c "$backup/halt.original.sha256"
/etc/init.d/bci-ui stop
cp -p "$backup/halt.original" /etc/init.d/halt.bci-restore-pending
mv /etc/init.d/halt.bci-restore-pending /etc/init.d/halt
mv "$app" "$backup/shutdown_application"
mv "$backup/application" "$app"
sync
/etc/init.d/bci-ui start
echo 'ROLLBACK_OK: original application and halt restored; logs retained.'
