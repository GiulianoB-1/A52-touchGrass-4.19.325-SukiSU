#!/system/bin/sh
# A52 recovery collector: freeze previous-boot evidence before recovery noise.
# Safe to run more than once. It never writes to pstore or raw ramoops.

set -u

STAMP="$(date +%Y%m%d_%H%M%S 2>/dev/null || echo unknown)"
EARLY_ROOT=/tmp/a52-early-boot-evidence
WORK=/tmp/A52_BOOT_EVIDENCE_${STAMP}

log() {
    echo "[A52-COLLECT] $*"
}

have() {
    command -v "$1" >/dev/null 2>&1
}

copy_tree() {
    src="$1"
    dst="$2"
    [ -d "$src" ] || return 0
    mkdir -p "$dst"
    cp -a "$src"/. "$dst"/ 2>/dev/null || cp -R "$src"/. "$dst"/ 2>/dev/null || true
}

snapshot_early() {
    mkdir -p "$EARLY_ROOT"

    if [ ! -f "$EARLY_ROOT/snapshot.started" ]; then
        date > "$EARLY_ROOT/snapshot.started" 2>/dev/null || true

        if [ -c /dev/a52_ramoops_raw ] || [ -e /dev/a52_ramoops_raw ]; then
            log "Copying frozen 1 MiB recovery-kernel snapshot"
            dd if=/dev/a52_ramoops_raw \
               of="$EARLY_ROOT/raw-ramoops-frozen-1MiB.bin" \
               bs=4096 count=256 2> "$EARLY_ROOT/raw-copy.stderr" || true
        fi

        DEBUG_DEV=""
        for cand in \
            /dev/block/by-name/debug \
            /dev/block/platform/*/by-name/debug; do
            [ -e "$cand" ] || continue
            DEBUG_DEV="$cand"
            break
        done
        if [ -n "$DEBUG_DEV" ]; then
            log "Copying Samsung debug partition before any experimental writes"
            echo "$DEBUG_DEV" > "$EARLY_ROOT/samsung-debug-device.txt"
            dd if="$DEBUG_DEV" \
               of="$EARLY_ROOT/samsung-debug-before.bin" \
               bs=4096 2> "$EARLY_ROOT/samsung-debug-copy.stderr" || true
            if have sha256sum && [ -f "$EARLY_ROOT/samsung-debug-before.bin" ]; then
                sha256sum "$EARLY_ROOT/samsung-debug-before.bin" \
                    > "$EARLY_ROOT/samsung-debug-before.sha256" 2>/dev/null || true
            fi
        else
            echo "debug partition not found" > "$EARLY_ROOT/samsung-debug-copy.stderr"
        fi

        cat /proc/iomem > "$EARLY_ROOT/recovery-proc-iomem.txt" 2>/dev/null || true

        if [ ! -d /sys/fs/pstore ]; then
            mkdir -p /sys/fs/pstore
        fi
        mount -t pstore pstore /sys/fs/pstore 2>/dev/null || true
        copy_tree /sys/fs/pstore "$EARLY_ROOT/pstore-first-view"

        cat /proc/cmdline > "$EARLY_ROOT/recovery-proc-cmdline.txt" 2>/dev/null || true
        cat /proc/bootconfig > "$EARLY_ROOT/recovery-proc-bootconfig.txt" 2>/dev/null || true
        date > "$EARLY_ROOT/snapshot.finished" 2>/dev/null || true
        sync
    fi
}

capture_command() {
    outfile="$1"
    shift
    {
        echo "COMMAND: $*"
        echo "DATE: $(date 2>/dev/null || true)"
        echo
        "$@"
    } > "$outfile" 2>&1 || true
}

capture_dt_runtime() {
    out="$1"
    node=/proc/device-tree/reserved-memory/a52-ramoops-reserved@b1b00000
    mkdir -p "$out"

    {
        echo "RAMOOPS DEVICE-TREE NODE: $node"
        if [ -d "$node" ]; then
            for p in compatible reg record-size console-size pmsg-size ftrace-size ecc-size; do
                echo "===== $p ====="
                if [ -f "$node/$p" ]; then
                    od -An -tx1 -v "$node/$p" 2>/dev/null || true
                    echo "32-bit cells:"
                    od -An -tx4 -v "$node/$p" 2>/dev/null || true
                else
                    echo MISSING
                fi
            done
        else
            echo NODE_MISSING
        fi
    } > "$out/device-tree-ramoops-decoded.txt" 2>&1

    if [ -d "$node" ]; then
        copy_tree "$node" "$out/device-tree-ramoops-raw"
    fi

    {
        for f in /sys/module/ramoops/parameters/* /sys/module/pstore/parameters/*; do
            [ -f "$f" ] || continue
            echo "===== $f ====="
            cat "$f" 2>/dev/null || true
        done
    } > "$out/module-parameters.txt" 2>&1
}

capture_reboot_reason() {
    out="$1"
    {
        echo "===== likely reboot/reset files ====="
        find /sys -type f \( \
            -iname '*restart*reason*' -o \
            -iname '*reboot*reason*' -o \
            -iname '*reset*reason*' -o \
            -iname '*pon*reason*' -o \
            -iname '*boot*reason*' \
        \) 2>/dev/null | sort

        echo
        echo "===== readable values ====="
        find /sys -type f \( \
            -iname '*restart*reason*' -o \
            -iname '*reboot*reason*' -o \
            -iname '*reset*reason*' -o \
            -iname '*pon*reason*' -o \
            -iname '*boot*reason*' \
        \) 2>/dev/null | sort | while read -r f; do
            echo "--- $f ---"
            cat "$f" 2>/dev/null || true
        done
    } > "$out" 2>&1
}

capture_android_logs() {
    root="$1"
    mkdir -p "$root"

    for src in \
        /data/tombstones \
        /data/vendor/tombstones \
        /data/anr \
        /data/system/dropbox \
        /data/misc/logd \
        /metadata/watchdog \
        /metadata/ota; do
        [ -e "$src" ] || continue
        safe="$(echo "$src" | tr '/' '_')"
        copy_tree "$src" "$root/$safe"
    done
}

make_manifest() {
    root="$1"
    find "$root" -type f -exec ls -ln {} \; > "$root/file-sizes-and-modes.txt" 2>&1 || true
    if have sha256sum; then
        find "$root" -type f ! -name SHA256SUMS -print0 2>/dev/null | \
            sort -z 2>/dev/null | xargs -0 sha256sum > "$root/SHA256SUMS" 2>/dev/null || true
    fi
}

choose_output_dir() {
    for d in /sdcard /data/media/0 /external_sd /usb-otg /tmp; do
        [ -d "$d" ] || continue
        if touch "$d/.a52-write-test" 2>/dev/null; then
            rm -f "$d/.a52-write-test"
            echo "$d"
            return
        fi
    done
    echo /tmp
}

snapshot_early

if [ "${1:-}" = "--snapshot-only" ]; then
    log "Early snapshot stored at $EARLY_ROOT"
    exit 0
fi

mkdir -p "$WORK"
copy_tree "$EARLY_ROOT" "$WORK/00-early-frozen-evidence"
copy_tree /sys/fs/pstore "$WORK/01-live-pstore-at-collection"

capture_command "$WORK/02-recovery-dmesg.txt" dmesg
if have logcat; then
    capture_command "$WORK/03-recovery-logcat-all.txt" logcat -b all -d -v threadtime
fi
if have getprop; then
    capture_command "$WORK/04-recovery-properties.txt" getprop
fi
capture_command "$WORK/05-proc-cmdline.txt" cat /proc/cmdline
capture_command "$WORK/06-proc-bootconfig.txt" cat /proc/bootconfig
capture_command "$WORK/07-mounts.txt" cat /proc/mounts
capture_command "$WORK/08-interrupts.txt" cat /proc/interrupts
capture_command "$WORK/09-meminfo.txt" cat /proc/meminfo
capture_command "$WORK/09b-proc-iomem.txt" cat /proc/iomem

mkdir -p "$WORK/09c-reserved-memory"
if [ -d /proc/device-tree/reserved-memory ]; then
    {
        echo "===== reserved-memory nodes ====="
        find /proc/device-tree/reserved-memory -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort
        echo
        echo "===== node reg/compatible/no-map/reusable ====="
        for n in /proc/device-tree/reserved-memory/*; do
            [ -d "$n" ] || continue
            echo "--- $n ---"
            for p in reg compatible no-map reusable; do
                if [ -e "$n/$p" ]; then
                    echo "[$p]"
                    od -An -tx1 -v "$n/$p" 2>/dev/null || true
                    if [ "$p" = compatible ]; then
                        tr '\000' '\n' < "$n/$p" 2>/dev/null || true
                    fi
                fi
            done
        done
    } > "$WORK/09c-reserved-memory/summary.txt" 2>&1
    copy_tree /proc/device-tree/reserved-memory "$WORK/09c-reserved-memory/raw"
fi

capture_dt_runtime "$WORK/10-active-ramoops-runtime"
capture_reboot_reason "$WORK/11-reboot-reason.txt"

mkdir -p "$WORK/12-recovery-logs"
for f in \
    /tmp/recovery.log \
    /cache/recovery/log \
    /cache/recovery/last_log \
    /cache/recovery/last_kmsg \
    /metadata/recovery/log; do
    [ -f "$f" ] || continue
    cp -a "$f" "$WORK/12-recovery-logs/$(echo "$f" | tr '/' '_')" 2>/dev/null || true
done

capture_android_logs "$WORK/13-android-persistent-logs"

{
    echo "collector_version=3-debug-partition-preflight"
    echo "collection_started=$STAMP"
    echo "raw_exporter_present=$([ -e /dev/a52_ramoops_raw ] && echo yes || echo no)"
    echo "early_snapshot_present=$([ -f "$EARLY_ROOT/snapshot.started" ] && echo yes || echo no)"
    echo "pstore_files=$(find /sys/fs/pstore -type f 2>/dev/null | wc -l)"
    echo "debug_partition_snapshot=$([ -f "$EARLY_ROOT/samsung-debug-before.bin" ] && echo yes || echo no)"
    echo "debug_partition_bytes=$([ -f "$EARLY_ROOT/samsung-debug-before.bin" ] && wc -c < "$EARLY_ROOT/samsung-debug-before.bin" || echo 0)"
    echo "data_mounted=$(grep -q ' /data ' /proc/mounts 2>/dev/null && echo yes || echo no)"
} > "$WORK/COLLECTION-STATUS.txt"

make_manifest "$WORK"
sync

DEST="$(choose_output_dir)"
BASE="A52_BOOT_EVIDENCE_${STAMP}"

if have zip; then
    (cd /tmp && zip -0 -r "$DEST/$BASE.zip" "$(basename "$WORK")" >/dev/null 2>&1)
    RESULT="$DEST/$BASE.zip"
elif have tar; then
    tar -C /tmp -czf "$DEST/$BASE.tar.gz" "$(basename "$WORK")" 2>/dev/null
    RESULT="$DEST/$BASE.tar.gz"
else
    RESULT="$WORK"
fi

sync
log "Collection complete: $RESULT"
echo "$RESULT"
