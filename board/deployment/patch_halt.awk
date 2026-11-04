# Replace exactly one direct halt invocation, preserving its arguments/indent.
# Reject unknown scripts instead of replacing their shutdown sequence.
BEGIN { count=0 }
/BCI_SHUTDOWN_HOOK_20261103/ { exit 2 }
{
    if ($0 ~ /^[ \t]*(exec[ \t]+)?(\/sbin\/)?halt[ \t]+/) {
        count++
        sub(/(\/sbin\/)?halt[ \t]+/, "/root/bci/final/halt_with_screen.sh ")
        print "# BCI_SHUTDOWN_HOOK_20261103: protected LCD notice before original halt"
    }
    print
}
END { if (count != 1) exit 2 }
