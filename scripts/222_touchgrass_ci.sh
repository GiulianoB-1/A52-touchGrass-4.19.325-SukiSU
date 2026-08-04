#!/usr/bin/env bash
set -Eeuo pipefail

TOUCHGRASS_SHA="${TOUCHGRASS_SHA:-6bf351bdf18bdb228db79e66f14a7a9c0178e5d7}"
ANYKERNEL_SHA="${ANYKERNEL_SHA:-1c9a500dd4aa8081952523126e97eb155aed941b}"
DEFCONFIG="${DEFCONFIG:-a52xq_defconfig}"
OUT="$PWD/artifacts/touchgrass-phase222-reference"
BUILD="$PWD/workspace/touchgrass-phase222-out"

rm -rf "$OUT" "$BUILD" touchgrass anykernel
mkdir -p "$OUT/logs" "$OUT/stage" "$OUT/config" "$OUT/package" "$BUILD"

python3 - <<'PY'
import base64, hashlib, zlib
from pathlib import Path
source = Path('scripts/222_apply_sg_boot_progress_trace.py.z64')
encoded = source.read_text(encoding='ascii').strip()
assert hashlib.sha256(encoded.encode('ascii')).hexdigest() == \
    'c1e40f30e91cc8779b4addeb1832ca64efcb1a7b2cbe47f6fe88f2ed00a1a933'
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
assert hashlib.sha256(raw).hexdigest() == \
    '5a9839f7fd3602855bfeb6ba6e658435f6822fdc29e70a0aa8230b2e4777d9ab'
target = Path('scripts/222_apply_sg_boot_progress_trace.py')
target.write_bytes(raw)
target.chmod(0o755)
PY
python3 -m py_compile scripts/222_apply_sg_boot_progress_trace.py
python3 scripts/222_apply_sg_boot_progress_trace.py --self-test

git init touchgrass
git -C touchgrass remote add origin \
  https://github.com/micr0softstore/samsung_android_kernel_a52xq.git
git -C touchgrass fetch --depth=1 origin "$TOUCHGRASS_SHA"
git -C touchgrass checkout --detach FETCH_HEAD
test "$(git -C touchgrass rev-parse HEAD)" = "$TOUCHGRASS_SHA"
test -f "touchgrass/arch/arm64/configs/$DEFCONFIG"
chmod 755 touchgrass/tools/dtc

sed -i 's/^SUBLEVEL = .*/SUBLEVEL = 200/' touchgrass/Makefile
python3 scripts/222_apply_sg_boot_progress_trace.py \
  --root touchgrass --backend printk \
  2>&1 | tee "$OUT/logs/stage.log"
git -C touchgrass diff --check
git -C touchgrass diff --binary --no-ext-diff \
  > "$OUT/stage/phase222-touchgrass-sg-reference.patch"
test -s "$OUT/stage/phase222-touchgrass-sg-reference.patch"
for marker in \
  A52_PHASE222_SG_BOOT_PROGRESS_TRACE \
  'TGSG 222 ioctl' \
  'TGSG 222 done'; do
  grep -Fq "$marker" touchgrass/drivers/scsi/sg.c
done
grep -Fq 'TGBOOT 222 exec' touchgrass/fs/exec.c
grep -Fq 'TGBOOT 222 exit' touchgrass/kernel/exit.c

common_make=(
  -C touchgrass O="$BUILD" ARCH=arm64
  CC=clang-14
  CROSS_COMPILE=aarch64-linux-gnu-
  CLANG_TRIPLE=aarch64-linux-gnu-
  DTC_EXT="$PWD/touchgrass/tools/dtc"
  CONFIG_BUILD_ARM64_DT_OVERLAY=y
  CONFIG_SECTION_MISMATCH_WARN_ONLY=y
  CONFIG_DRV_BUILD_IN=Y
)
make "${common_make[@]}" "$DEFCONFIG" 2>&1 | tee "$OUT/logs/defconfig.log"
touchgrass/scripts/config --file "$BUILD/.config" \
  --set-str LOCALVERSION '-touchGrassKernel+' \
  --disable LOCALVERSION_AUTO \
  --enable PRINTK_TIME \
  --enable PROC_FS \
  --enable CHR_DEV_SG \
  --set-val LOG_BUF_SHIFT 22 \
  --set-val LOG_CPU_MAX_BUF_SHIFT 16
make "${common_make[@]}" olddefconfig 2>&1 | tee "$OUT/logs/olddefconfig.log"
cp "$BUILD/.config" "$OUT/config/final.config"
make -s "${common_make[@]}" kernelrelease | tee "$OUT/config/kernelrelease.txt"
grep -Fxq 'CONFIG_CHR_DEV_SG=y' "$BUILD/.config"
grep -Fxq 'CONFIG_PRINTK_TIME=y' "$BUILD/.config"
grep -Fxq 'CONFIG_PSTORE=y' "$BUILD/.config"
grep -Fxq 'CONFIG_PSTORE_RAM=y' "$BUILD/.config"

clang-14 --version | tee "$OUT/logs/toolchain.log"
make "${common_make[@]}" -j4 KCFLAGS=-w Image 2>&1 | tee "$OUT/logs/build.log"
IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
cp "$IMAGE" "$OUT/package/Image"
for marker in \
  'TGSG 222 ioctl' \
  'TGSG 222 done' \
  'TGBOOT 222 exec' \
  'TGBOOT 222 exit'; do
  grep -aFq "$marker" "$IMAGE"
done
sha256sum "$IMAGE" | tee "$OUT/package/Image.sha256"

git init anykernel
git -C anykernel remote add origin https://github.com/osm0sis/AnyKernel3.git
git -C anykernel fetch --depth=1 origin "$ANYKERNEL_SHA"
git -C anykernel checkout --detach FETCH_HEAD
test "$(git -C anykernel rev-parse HEAD)" = "$ANYKERNEL_SHA"
rm -rf anykernel/.git anykernel/.github
rm -f anykernel/README.md anykernel/Image anykernel/Image.gz \
  anykernel/Image.gz-dtb anykernel/dtb anykernel/dtbo.img
cp "$OUT/package/Image" anykernel/Image
cat > anykernel/anykernel.sh <<'AK3'
# AnyKernel3 Ramdisk Mod Script
properties() { '
kernel.string=TouchGrass Phase 222 SG RPMB Reference
do.devicecheck=0
do.modules=0
do.systemless=1
do.cleanup=1
do.cleanuponabort=0
supported.versions=
supported.patchlevels=
supported.vendorpatchlevels=
'; }
BLOCK=/dev/block/by-name/boot;
IS_SLOT_DEVICE=0;
RAMDISK_COMPRESSION=auto;
PATCH_VBMETA_FLAG=auto;
. tools/ak3-core.sh;
ui_print "  TouchGrass Phase 222 SG/RPMB reference trace";
dump_boot;
write_boot;
AK3
chmod 755 anykernel/anykernel.sh
bash -n anykernel/anykernel.sh
cat > anykernel/README-FIRST.txt <<'TXT'
A52 TOUCHGRASS PHASE 222 SG/RPMB REFERENCE TRACE

Metadata-only reference build. It records TGSG 222 and TGBOOT 222 messages
in dmesg/pstore. It does not record RPMB frames, transfer buffers, keys,
userspace memory or CDB bytes beyond the opcode.

This candidate is not hardware validated. Keep a known-good boot image and
recovery/download-mode path available.
TXT
(
  cd anykernel
  zip -qr9 "$OUT/package/A52XQ-TouchGrass-Phase222-SG-Reference.zip" .
)
unzip -t "$OUT/package/A52XQ-TouchGrass-Phase222-SG-Reference.zip"
sha256sum "$OUT/package/A52XQ-TouchGrass-Phase222-SG-Reference.zip" \
  | tee "$OUT/package/flashable.sha256"

cat > "$OUT/package/COLLECT-PHASE222-TOUCHGRASS.ps1" <<'PS1'
$ErrorActionPreference = 'Stop'
adb wait-for-device
adb shell su 0 dmesg | Out-File -Encoding utf8 phase222-touchgrass-dmesg.txt
adb shell su 0 sh -c 'for f in /sys/fs/pstore/*; do echo ===$f===; cat $f; done' |
  Out-File -Encoding utf8 phase222-touchgrass-pstore.txt
adb shell getprop | Out-File -Encoding utf8 phase222-touchgrass-getprop.txt
adb shell cat /proc/version | Out-File -Encoding utf8 phase222-touchgrass-version.txt
Compress-Archive -Force -Path phase222-touchgrass-*.txt \
  -DestinationPath Phase222-TouchGrass-Capture.zip
PS1

cat > "$OUT/README-FIRST.txt" <<'TXT'
Phase 222 TouchGrass SG/RPMB reference candidate.
Build-audited only. Not hardware validated.
Flash once, collect dmesg and pstore, then restore the known-good image.
TXT
find "$OUT" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum \
  > "$OUT/SHA256SUMS"
