#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Read-only device audit for the experimental A52XQ hybrid GKI 5.10 port.
# Run from Termux after granting root to Termux through SukiSU.
# Required Termux packages: bash, coreutils, findutils, grep, gzip, tar, python

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="/sdcard/Download/a52xq-hybrid-gki-audit-$STAMP"
REPORT="$OUT/report.txt"
ROOT_TMP="/data/local/tmp/a52xq-hybrid-gki-audit-$STAMP"

mkdir -p "$OUT"

if ! su -c id 2>/dev/null | grep -q 'uid=0'; then
  echo "Root access is required. Grant Termux root in SukiSU Manager." >&2
  exit 1
fi

su -c "rm -rf '$ROOT_TMP' && mkdir -p '$ROOT_TMP'"
trap 'su -c "rm -rf '\''$ROOT_TMP'\''" >/dev/null 2>&1 || true' EXIT

section() {
  printf '\n============================================================\n' | tee -a "$REPORT"
  printf '%s\n' "$1" | tee -a "$REPORT"
  printf '============================================================\n' | tee -a "$REPORT"
}

run_root() {
  local label="$1"
  shift
  section "$label"
  su -c "$*" 2>&1 | tee -a "$REPORT" || true
}

copy_root_file() {
  local src="$1"
  local dst="$2"
  su -c "test -r '$src' && cat '$src'" > "$dst" 2>/dev/null || true
}

printf 'A52XQ HYBRID GKI DEVICE AUDIT\n' | tee "$REPORT"
printf 'Created: %s\n' "$(date -Iseconds)" | tee -a "$REPORT"
printf 'Output: %s\n' "$OUT" | tee -a "$REPORT"

run_root "IDENTITY AND ANDROID PROPERTIES" '
for p in \
  ro.product.device ro.product.vendor.device ro.product.model \
  ro.board.platform ro.boot.hardware ro.hardware \
  ro.build.version.release ro.build.version.sdk \
  ro.product.first_api_level ro.vendor.api_level \
  ro.boot.slot_suffix ro.boot.header_version \
  ro.boot.dynamic_partitions ro.boot.super_partition \
  ro.boot.boot_devices ro.boot.verifiedbootstate \
  ro.boot.vbmeta.device_state ro.boot.flash.locked \
  ro.treble.enabled ro.apex.updatable; do
    printf "%s=%s\n" "$p" "$(getprop "$p")"
done
printf "fingerprint=%s\n" "$(getprop ro.build.fingerprint)"
'

run_root "KERNEL IDENTITY" '
uname -a
cat /proc/version
printf "kernel_release="; uname -r
printf "kernel_arch="; uname -m
'

run_root "DEVICE TREE IDENTITY" '
for f in model compatible qcom,msm-id qcom,board-id; do
  p="/proc/device-tree/$f"
  if [ -r "$p" ]; then
    printf "%s: " "$f"
    tr "\000" " " < "$p" 2>/dev/null || cat "$p"
    echo
  fi
done
'

run_root "KERNEL COMMAND LINE" '
cat /proc/cmdline
if [ -r /proc/bootconfig ]; then
  echo "--- /proc/bootconfig ---"
  cat /proc/bootconfig
fi
'

copy_root_file /proc/config.gz "$OUT/config.gz"
if [ -s "$OUT/config.gz" ]; then
  gzip -dc "$OUT/config.gz" > "$OUT/config.txt" || true
  section "IMPORTANT KERNEL CONFIG"
  grep -E "^(CONFIG_(ARM64|ARCH_QCOM|QCOM_|MSM_|MODULES|MODULE_UNLOAD|MODVERSIONS|KALLSYMS|IKCONFIG|IKCONFIG_PROC|DEVTMPFS|BLK_DEV_INITRD|OF|OF_OVERLAY|EFI|ACPI|SMP|PREEMPT|HIGH_RES_TIMERS|PSTORE|PSTORE_RAM|RAMOOPS|SERIAL_MSM|SERIAL_QCOM_GENI|MMC|SCSI_UFS|SCSI_UFS_QCOM|DRM|FB|USB|TYPEC|BPF|BPF_SYSCALL|CGROUP_BPF|KPROBES|KSU))=|# CONFIG_.* is not set)" "$OUT/config.txt" \
    | tee -a "$REPORT" || true
else
  section "KERNEL CONFIG"
  echo "/proc/config.gz is unavailable" | tee -a "$REPORT"
fi

run_root "PARTITION LINKS" '
for d in /dev/block/by-name /dev/block/platform/*/by-name; do
  [ -d "$d" ] || continue
  echo "BY_NAME=$d"
  ls -laZ "$d"
done
'

run_root "BLOCK DEVICES" '
cat /proc/partitions
for d in /dev/block/by-name /dev/block/platform/*/by-name; do
  [ -d "$d" ] || continue
  for n in boot boot_a boot_b vendor_boot vendor_boot_a vendor_boot_b init_boot init_boot_a init_boot_b dtbo dtbo_a dtbo_b recovery recovery_a recovery_b vbmeta vbmeta_a vbmeta_b; do
    [ -e "$d/$n" ] || continue
    printf "%s -> %s bytes\n" "$d/$n" "$(blockdev --getsize64 "$d/$n" 2>/dev/null || echo unknown)"
  done
done
'

run_root "LOADED MODULES" '
cat /proc/modules 2>/dev/null || true
'

run_root "MODULE DIRECTORIES" '
for d in /vendor/lib/modules /vendor_dlkm/lib/modules /odm/lib/modules /odm_dlkm/lib/modules /system/lib/modules /system_dlkm/lib/modules; do
  [ -d "$d" ] || continue
  echo "--- $d ---"
  find "$d" -maxdepth 2 -type f -printf "%p %s bytes\n" 2>/dev/null | sort
  for f in modules.load modules.dep modules.alias modules.softdep modules.blocklist; do
    [ -r "$d/$f" ] || continue
    echo "### $d/$f"
    cat "$d/$f"
  done
done
'

run_root "MOUNTS AND FILESYSTEMS" '
cat /proc/filesystems
mount
'

run_root "RAMOOPS AND PSTORE" '
ls -laZ /sys/fs/pstore 2>/dev/null || true
find /sys/fs/pstore -maxdepth 1 -type f -print -exec sh -c "echo --- {}; cat {}" \; 2>/dev/null || true
grep -iE "ramoops|pstore" /proc/iomem 2>/dev/null || true
'

run_root "BOOT-CRITICAL HARDWARE LOGS" '
dmesg | grep -iE "Machine model|OF:|dtb|dtbo|ufs|scsi|regulator|clock|clk|pinctrl|rpmh|remoteproc|subsys|display|dsi|panel|usb|typec|charger|battery|wlan|wifi|bluetooth|firmware|modem|adsp|cdsp|slpi|ramoops|pstore" | tail -2000
'

# Save complete logs separately without flooding report.txt.
su -c 'dmesg' > "$OUT/dmesg.txt" 2>/dev/null || true
su -c 'getprop' > "$OUT/getprop.txt" 2>/dev/null || true

# Copy current boot-chain partitions read-only. The script selects the active slot
# when a slotted partition exists and falls back to an unsuffixed partition.
SLOT="$(su -c 'getprop ro.boot.slot_suffix' 2>/dev/null | tr -d '\r')"
BYNAME="$(su -c '
for d in /dev/block/by-name /dev/block/platform/*/by-name; do
  [ -d "$d" ] && { echo "$d"; break; }
done
' 2>/dev/null | head -n1 | tr -d '\r')"

if [ -n "$BYNAME" ]; then
  for part in boot vendor_boot init_boot dtbo recovery vbmeta vbmeta_system; do
    node=""
    if [ -n "$SLOT" ] && su -c "test -e '$BYNAME/${part}${SLOT}'"; then
      node="$BYNAME/${part}${SLOT}"
    elif su -c "test -e '$BYNAME/$part'"; then
      node="$BYNAME/$part"
    fi
    [ -n "$node" ] || continue
    echo "Copying $node read-only..."
    su -c "dd if='$node' of='$ROOT_TMP/$part.img' bs=4M status=none && chmod 0644 '$ROOT_TMP/$part.img'"
    cp "$ROOT_TMP/$part.img" "$OUT/$part.img"
  done
fi

# Parse Android boot and vendor_boot headers without external Android tools.
python - "$OUT" <<'PY' > "$OUT/boot-header-report.txt" 2>&1 || true
from pathlib import Path
import struct
import sys

out = Path(sys.argv[1])
for path in sorted(out.glob("*.img")):
    data = path.read_bytes()[:4096]
    print(f"===== {path.name} =====")
    print(f"size={path.stat().st_size}")
    if data.startswith(b"ANDROID!") and len(data) >= 44:
        kernel_size, ramdisk_size = struct.unpack_from("<II", data, 8)
        header_version = struct.unpack_from("<I", data, 40)[0]
        print("type=android_boot")
        print(f"header_version={header_version}")
        print(f"kernel_size={kernel_size}")
        print(f"ramdisk_size={ramdisk_size}")
        if header_version <= 2:
            page_size = struct.unpack_from("<I", data, 36)[0]
            print(f"page_size={page_size}")
        else:
            header_size = struct.unpack_from("<I", data, 20)[0]
            print(f"header_size={header_size}")
    elif data.startswith(b"VNDRBOOT") and len(data) >= 16:
        header_version, page_size = struct.unpack_from("<II", data, 8)
        print("type=vendor_boot")
        print(f"header_version={header_version}")
        print(f"page_size={page_size}")
    else:
        print("type=other_or_signed_image")
    print()
PY

cat "$OUT/boot-header-report.txt" | tee -a "$REPORT"

(
  cd "$OUT"
  sha256sum ./* > SHA256SUMS.txt
)

ARCHIVE="/sdcard/Download/a52xq-hybrid-gki-audit-$STAMP.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"

printf '\nAudit complete.\nDirectory: %s\nArchive: %s\n' "$OUT" "$ARCHIVE"
printf 'Upload the .tar.gz archive for the hybrid GKI feasibility analysis.\n'
