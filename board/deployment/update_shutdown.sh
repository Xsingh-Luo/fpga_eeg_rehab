#!/bin/sh
set -eu
pkg=$(CDPATH= cd -- "${1:?Specify compiled bci_final folder}" && pwd)
app=/root/bci/final
stage=/root/bci/final_shutdown_pending_20261103
backup=/root/bci/shutdown_backup_20261103
test "$(id -u)" = 0
test -d "$app" && test -x /etc/init.d/bci-ui
test -f /etc/init.d/halt && test -x /sbin/halt && test -x /sbin/poweroff
test ! -e "$stage" && test ! -e "$backup"
test "$pkg" != "$app"
(cd "$pkg" && sha256sum -c source.sha256)
test "$(sha256sum "$pkg/image.ub" | awk '{print $1}')" = "$(sha256sum "$app/image.ub" | awk '{print $1}')"
for mode in live dataset; do
    for name in ff_sos.txt ff_csp.txt ff_svmw.txt ff_svmb.txt; do
        test "$(sha256sum "$pkg/models/$mode/$name" | awk '{print $1}')" = "$(sha256sum "$app/models/$mode/$name" | awk '{print $1}')"
    done
done
for name in bci_app bci_run st7789_display pl_sample_replay; do test -s "$pkg/$name"; done
cp -R "$pkg" "$stage"
chmod +x "$stage/bci_app" "$stage/bci_run" "$stage/st7789_display" "$stage/pl_sample_replay"
chmod +x "$stage/halt_with_screen.sh"
(cd "$stage" && sha256sum -c source.sha256)
"$stage/bci_app" --version
"$stage/bci_run" --version
"$stage/st7789_display" --preview "$stage/arm_preview.ppm" --state menu-power --demo
awk -f "$stage/patch_halt.awk" /etc/init.d/halt > "$stage/halt.patched"
test -s "$stage/halt.patched"
mkdir "$backup"
cp -p /etc/init.d/halt "$backup/halt.original"
sha256sum "$backup/halt.original" > "$backup/halt.original.sha256"
old_moved=0
new_moved=0
rollback() {
    code=$?
    trap - EXIT HUP INT TERM
    if test "$code" -ne 0; then
        if test "$new_moved" = 1; then
            /etc/init.d/bci-ui stop || :
            mv "$app" "$backup/failed_application"
        fi
        cp -p "$backup/halt.original" /etc/init.d/halt.bci-restore-pending
        mv /etc/init.d/halt.bci-restore-pending /etc/init.d/halt
        if test "$old_moved" = 1; then mv "$backup/application" "$app"; fi
        sync
        /etc/init.d/bci-ui start || :
        echo "UPDATE_FAILED: original application/halt restored; inspect $backup" >&2
    fi
    exit "$code"
}
trap rollback EXIT
trap 'exit 1' HUP INT TERM
/etc/init.d/bci-ui stop
mv "$app" "$backup/application"
old_moved=1
mv "$stage" "$app"
new_moved=1
cp "$app/halt.patched" /etc/init.d/halt.bci-pending
chmod 755 /etc/init.d/halt.bci-pending
mv /etc/init.d/halt.bci-pending /etc/init.d/halt
sync
/etc/init.d/bci-ui start
sleep 2
/etc/init.d/bci-ui status
trap - EXIT HUP INT TERM
echo "SHUTDOWN_UPDATE_INSTALLED: application and guarded halt hook; originals saved in $backup. Boot image/models unchanged."
echo 'Check the new menu and cancel path first; then test one safe shutdown while the serial terminal is still connected.'
