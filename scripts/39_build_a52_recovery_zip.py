#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import zipfile
from pathlib import Path

INSTALLER = r'''#!/sbin/sh
OUTFD="$2"
ZIPFILE="$3"
ui_print() { echo "ui_print $1" > /proc/self/fd/$OUTFD; echo "ui_print" > /proc/self/fd/$OUTFD; }
abort() { ui_print "ERROR: $1"; exit 1; }

ui_print "A52 boot image installer"
ui_print "Package: __LABEL__"

DEVICE="$(getprop ro.product.device 2>/dev/null)"
MODEL="$(getprop ro.product.model 2>/dev/null)"
case "$DEVICE $MODEL" in
  *a52xq*|*SM-A526B*) ;;
  *) abort "Unsupported device: $DEVICE $MODEL" ;;
esac

BOOT=""
for CANDIDATE in \
  /dev/block/by-name/boot \
  /dev/block/bootdevice/by-name/boot \
  /dev/block/platform/*/by-name/boot; do
  [ -e "$CANDIDATE" ] && BOOT="$CANDIDATE" && break
done
[ -n "$BOOT" ] || abort "boot partition not found"

TMP=/tmp/a52-p1-installer
rm -rf "$TMP"
mkdir -p "$TMP" || abort "cannot create temp directory"
unzip -o "$ZIPFILE" boot.img -d "$TMP" >/dev/null 2>&1 || abort "cannot extract boot.img"
[ -s "$TMP/boot.img" ] || abort "boot.img missing or empty"

IMG_SIZE=$(wc -c < "$TMP/boot.img" 2>/dev/null)
[ "$IMG_SIZE" = "100663296" ] || abort "unexpected boot image size: $IMG_SIZE"

PART_SIZE=$(blockdev --getsize64 "$BOOT" 2>/dev/null)
[ -n "$PART_SIZE" ] || abort "cannot read boot partition size"
[ "$PART_SIZE" -ge "$IMG_SIZE" ] || abort "boot partition is too small"

ui_print "Target: $BOOT"
ui_print "Writing 96 MiB boot image..."
dd if="$TMP/boot.img" of="$BOOT" bs=4M conv=fsync 2>/dev/null || abort "dd failed"
sync
ui_print "Flash completed successfully"
exit 0
'''

UPDATER_SCRIPT = '#MAGISK\n'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def add_bytes(zf: zipfile.ZipFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--boot', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--notice', required=True)
    args = p.parse_args()

    if not args.boot.is_file():
        raise SystemExit(f'missing boot image: {args.boot}')
    if args.boot.stat().st_size != 100663296:
        raise SystemExit(f'unexpected boot image size: {args.boot.stat().st_size}')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    installer = INSTALLER.replace('__LABEL__', args.label)
    checksums = f"{sha256(args.boot)}  boot.img\n"
    notice = args.notice.strip() + '\n\nDevice: SM-A526B / a52xq\nPartition: boot\nImage bytes: 100663296\n'

    with zipfile.ZipFile(args.output, 'w', allowZip64=True) as zf:
        add_bytes(zf, 'META-INF/com/google/android/update-binary', installer.encode(), 0o755)
        add_bytes(zf, 'META-INF/com/google/android/updater-script', UPDATER_SCRIPT.encode())
        add_bytes(zf, 'boot.img', args.boot.read_bytes())
        add_bytes(zf, 'SHA256SUMS', checksums.encode())
        add_bytes(zf, 'README.txt', notice.encode())

    print(f'created {args.output} ({args.output.stat().st_size} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
