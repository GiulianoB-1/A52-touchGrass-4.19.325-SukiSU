#!/system/bin/sh
MODDIR=${0%/*}
ROOT=/data/local/tmp/tg_ksu_composer_reference
mkdir -p "$ROOT"
# KernelSU post-fs-data is blocking; launch only the detached read-only recorder.
# In late-load mode KernelSU replaces this stage, so service.sh is the fallback.
if [ "$KSU_LATE_LOAD" = "1" ]; then exit 0; fi
if command -v nohup >/dev/null 2>&1; then
    nohup sh "$MODDIR/recorder.sh" early > "$ROOT/early-launch.log" 2>&1 &
else
    sh "$MODDIR/recorder.sh" early > "$ROOT/early-launch.log" 2>&1 &
fi
exit 0
