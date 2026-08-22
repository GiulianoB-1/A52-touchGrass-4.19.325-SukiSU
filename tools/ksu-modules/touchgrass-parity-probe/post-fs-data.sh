#!/system/bin/sh
MODDIR=${0%/*}
export MODDIR
OUTROOT=/data/adb/tgprobe
mkdir -p "$OUTROOT"
chmod 0700 "$OUTROOT" 2>/dev/null

boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null | tr -cd 'A-Za-z0-9-')
[ -n "$boot_id" ] || boot_id=$(date +%s 2>/dev/null)
lock="$OUTROOT/.boot-start-$boot_id"

# One bounded capture per boot. Old boot locks do not block new boots.
if mkdir "$lock" 2>/dev/null; then
  find "$OUTROOT" -maxdepth 1 -type d -name '.boot-start-*' ! -name ".boot-start-$boot_id" \
    -mtime +2 -exec rm -rf {} \; 2>/dev/null || true
  "$MODDIR/bin/collect.sh" boot 150 0 >"$OUTROOT/boot-launch.log" 2>&1 &
fi
exit 0
