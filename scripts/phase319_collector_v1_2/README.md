# Phase319 Golden collector v1.2

Purpose: preserve the early Phase319 Golden `TG319F` observer records before the normal kernel printk ring buffer wraps.

## Capture design

- KernelSU launches `post-fs-data.sh` during early boot.
- The module opens `/dev/kmsg` and continuously copies it to persistent `/data/adb/a52_phase319_captures/early_<boot-id>/kmsg.log`.
- Each boot gets its own retained directory, so a later reboot does not destroy the previous capture.
- The live stream stops automatically after 120 seconds to prevent vendor printk spam from growing forever.
- `phase319-markers.txt` contains only exact `TG319F` records.
- Generic DSI/DRM/SDE timing context is stored separately in `display-context.txt`.
- The KernelSU Action exports every retained early capture plus current dmesg, logcat, pstore and boot identity.

## Phone procedure

1. Keep the currently flashed Phase319 Golden FDR boot image. Do not flash another kernel.
2. Install `A52_PHASE319_GOLDEN_COLLECTOR_V1.2.zip` in KernelSU.
3. Reboot once so `post-fs-data.sh` starts during early boot.
4. Let Android finish booting. Waiting at least 30 seconds is enough for the target display transaction. The collector itself stops at 120 seconds.
5. Open KernelSU, select this module and press **Action**.
6. Retrieve `/sdcard/Download/A52_Phase319/A52_Phase319_ALL_<timestamp>.tar` and send that archive for analysis.

## Success criterion

The exported manifest should report `TG319F_count` greater than zero. The decisive file is `phase319-markers.txt`, expected to contain the Phase319 ARM and q0/q1/q2 six-selector snapshots.
