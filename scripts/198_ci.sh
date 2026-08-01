#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-kms-block-init-triple-rs"
OUT="$PWD/artifacts/a52xq-catalog-init-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/197_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase198.config"
cp "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$OUT/stage/sde-hw-catalog-before-phase198.c"
sha256sum \
  "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  > "$OUT/stage/phase197-invariants-before-phase198.sha256"

python3 scripts/198_apply_catalog_trace.py --self-test | \
  tee "$OUT/logs/phase198-patcher-self-test.log"
python3 scripts/198_apply_catalog_trace.py --root "$ROOT" | \
  tee "$OUT/logs/phase198-apply.log"

cp "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$OUT/stage/sde-hw-catalog-after-phase198.c"
cp scripts/198_apply_catalog_trace.py "$OUT/stage/"

git -C "$ROOT" diff --check
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase198.config" "$OUT/config/final.config"
sha256sum -c "$OUT/stage/phase197-invariants-before-phase198.sha256"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
cat = (root / 'drivers/a52_display/msm/sde/sde_hw_catalog.c').read_text()
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
ram = (root / 'fs/pstore/ram.c').read_text()
main = (root / 'init/main.c').read_text()
kms = (root / 'drivers/a52_display/msm/sde/sde_kms.c').read_text()

for marker in (
    '#include <linux/a52_ack_secure_flight_recorder.h>',
    'CAT enter rev=0x%x np-null=%d',
    'CAT alloc exit null=%d',
    'CAT pre-caps exit rc=%d',
    'CAT top exit rc=%d mdp=%u',
    'CAT samsung primary exit null=%d err=%ld',
    'CAT samsung pba exit i=%d',
    'CAT perf exit rc=%d',
    'CAT qos exit rc=%d',
    'CAT rot exit rc=%d',
    'CAT uidle exit rc=%d',
    'CAT ctl exit rc=%d count=%u',
    'CAT sspp exit rc=%d count=%u',
    'CAT dspp-top exit rc=%d',
    'CAT dspp exit rc=%d count=%u',
    'CAT ds exit rc=%d count=%u',
    'CAT dsc exit rc=%d count=%u',
    'CAT pp exit rc=%d count=%u',
    'CAT mixer exit rc=%d count=%u',
    'CAT intf exit rc=%d count=%u',
    'CAT wb exit rc=%d count=%u',
    'CAT cdm exit rc=%d count=%u',
    'CAT vbif exit rc=%d count=%u',
    'CAT reg-dma exit rc=%d count=%u',
    'CAT merge3d exit rc=%d count=%u',
    'CAT qdss exit rc=%d',
    'CAT post-caps exit rc=%d',
    'CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u',
    'CAT fail rc=%d',
):
    assert marker in cat, marker

assert cat.count('a52_ackfr_record("CAT ') >= 50
for marker in (
    '#define A52_R179_BANK_RECORD BIT(2)',
    'copies=3 crc=0',
    'BOOT rs=ready phase=197 roots=%u copies=3 crc=0',
):
    assert marker in rec, marker
for marker in (
    '#define A52_DIAG_RECORD_PHYS 0xB1B00000ULL',
    'a52_persistent_diag_mark_record("%.*s", (int)len, buf);',
):
    assert marker in ram, marker
assert main.count('A52USR2 BOOT_EARLY stage=mm_init') == 3
for marker in (
    'KMSBLK catalog enter rev=0x%x',
    'KMSBLK catalog exit rc=%ld null=%d',
    'KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d',
):
    assert marker in kms, marker
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase198-catalog-init-trace.patch"
test -s "$OUT/stage/phase198-catalog-init-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase198-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase198-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase198-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase198-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/a52_display/msm/sde/sde_hw_catalog.o' \
  "$OUT/logs/phase198-compile.log"
for marker in \
  'CAT enter rev=0x%x np-null=%d' \
  'CAT samsung pba exit i=%d' \
  'CAT sspp exit rc=%d count=%u' \
  'CAT post-caps exit rc=%d' \
  'CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u' \
  'CAT fail rc=%d' \
  'BOOT rs=ready phase=197 roots=%u copies=3 crc=0' \
  'KMSBLK catalog enter rev=0x%x' \
  'A52GDSC disable profile=mdss name=%s rc=%d before=0x%x after=0x%x'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source source/extracted/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r179-rs-recorder.py" --self-test
python3 "$OUT/tools/decode-a52-r197-triple-rs.py" --self-test

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 198 SDE hardware-catalog initialization trace

FLASH ONLY:
  package/boot.img -> BOOT partition

This candidate is Phase 197 plus observation-only checkpoints inside:
  sde_hw_catalog_init()

The checkpoints bracket allocation, hardware pre-caps, top parsing, Samsung PBA
callbacks, and every major catalog parser through post-caps. The final Phase 197
capture ended at "KMSBLK catalog enter", so this build identifies the exact
catalog substage that fails, hangs, or returns an error.

Recorder behavior is unchanged from Phase 197:
  - 157 data bytes plus 32 Reed-Solomon parity symbols per copy
  - 255-byte R79 transport
  - three independent banks at +0x00000, +0x40000 and +0x80000
  - no CRC and no recorder-v3 format

The Phase 194 mdss_core_gdsc fix and all Phase 196 KMS block tracing remain
preserved. Return values, DTB, DTBO, ramdisk, panel commands, timing, IOMMU
behavior, splash policy and GDSC policy are unchanged.

Decode an untouched 1 MiB RAMOOPS capture with:
  python3 tools/decode-a52-r197-triple-rs.py RAW_OR_ZIP --output decoded-r198

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('artifacts/a52xq-catalog-init-trace')
base = json.loads(Path('artifacts/a52xq-kms-block-init-triple-rs/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
cat = (root / 'stage/sde-hw-catalog-after-phase198.c').read_text()

audit = dict(base)
audit.update({
    'status': 'a52-catalog-init-trace-audited',
    'phase': 198,
    'base_phase': 197,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase197': 'observation-only-catalog-trace',
    'phase197_triple_rs_preserved': True,
    'phase196_kms_trace_preserved': True,
    'phase194_mdss_core_gdsc_fix_preserved': True,
    'catalog_function_instrumented': True,
    'catalog_checkpoint_count': cat.count('a52_ackfr_record("CAT '),
    'recorder_copy_count': 3,
    'recorder_parity_symbols_per_copy': 32,
    'recorder_crc': False,
    'return_codes_changed': False,
    'iommu_bypass_added': False,
    'continuous_splash_forced': False,
    'gdsc_keep_on_forced': False,
    'dtb_changed': False,
    'dtbo_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in (
    'phase197_triple_rs_preserved',
    'phase196_kms_trace_preserved',
    'phase194_mdss_core_gdsc_fix_preserved',
    'catalog_function_instrumented',
    'dtb_preserved',
    'ramdisk_preserved',
    'recovery_dtbo_preserved',
):
    assert audit[key] is True, key
assert audit['catalog_checkpoint_count'] >= 50
assert audit['recorder_copy_count'] == 3
assert audit['recorder_parity_symbols_per_copy'] == 32
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
