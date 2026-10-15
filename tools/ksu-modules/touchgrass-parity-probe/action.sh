#!/system/bin/sh
MODDIR=${0%/*}
export MODDIR

echo "A52 TouchGrass Runtime Parity Probe v1.1"
echo
echo "A bounded 90-second deep capture will run."
echo "The display will turn off for three seconds and then turn on."
echo "The archive can contain logs, properties, package names and device IDs."
echo "Review it before sharing publicly."
echo

"$MODDIR/bin/collect.sh" action 90 1
rc=$?

echo
if [ "$rc" -eq 0 ]; then
  echo "Capture complete."
  echo "Look in /sdcard/Download for TouchGrass-Parity-Probe-v1.1-*.tar.gz"
else
  echo "Capture completed with status $rc."
  echo "Raw data remains under /data/adb/tgprobe."
fi
exit "$rc"
