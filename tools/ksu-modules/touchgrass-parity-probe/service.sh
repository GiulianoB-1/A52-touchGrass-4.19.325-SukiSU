#!/system/bin/sh
MODDIR=${0%/*}
export MODDIR
OUTROOT=/data/adb/tgprobe
mkdir -p "$OUTROOT"

i=0
while [ "$i" -lt 240 ]; do
  [ "$(getprop sys.boot_completed 2>/dev/null)" = "1" ] && break
  sleep 1
  i=$((i + 1))
done

"$MODDIR/bin/package-result.sh" all >"$OUTROOT/service-package.log" 2>&1
exit 0
