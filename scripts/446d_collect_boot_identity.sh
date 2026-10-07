#!/sbin/sh
# Phase446d recovery-side boot identity helper.
# Usage:
#   sh 446d_collect_boot_identity.sh [output.txt] [optional-flashed-image.img]

OUT="${1:-/sdcard/phase446d-boot-identity.txt}"
IMAGE="${2:-}"

BOOT=""
for C in \
  /dev/block/by-name/boot \
  /dev/block/bootdevice/by-name/boot \
  /dev/block/platform/*/by-name/boot; do
  [ -e "$C" ] && BOOT="$C" && break
done

hash_one() {
  P="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$P"
  elif command -v toybox >/dev/null 2>&1; then
    toybox sha256sum "$P"
  else
    echo "sha256sum unavailable: $P"
  fi
}

{
  echo "phase=446d"
  echo "date=$(date 2>/dev/null)"
  echo "device=$(getprop ro.product.device 2>/dev/null)"
  echo "model=$(getprop ro.product.model 2>/dev/null)"
  echo "boot_path=$BOOT"
  if [ -n "$BOOT" ]; then
    echo "boot_sha256:"
    hash_one "$BOOT"
    blockdev --getsize64 "$BOOT" 2>/dev/null | sed 's/^/boot_bytes=/'
  else
    echo "boot_error=partition-not-found"
  fi
  if [ -n "$IMAGE" ] && [ -f "$IMAGE" ]; then
    echo "flashed_image_path=$IMAGE"
    echo "flashed_image_sha256:"
    hash_one "$IMAGE"
  fi
} > "$OUT"

cat "$OUT"
