#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-kms-block-init-trace"
OUT="$PWD/artifacts/a52xq-kms-block-init-triple-rs"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/196_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase197.config"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase197.c"
cp "$ROOT/fs/pstore/ram.c" "$OUT/stage/ram-before-phase197.c"
cp "$ROOT/init/main.c" "$OUT/stage/main-before-phase197.c"
sha256sum \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  > "$OUT/stage/display-invariants-before-phase197.sha256"

python3 scripts/197_apply_triple_rs.py --self-test | tee "$OUT/logs/phase197-patcher-self-test.log"
python3 scripts/197_apply_triple_rs.py --root "$ROOT" | tee "$OUT/logs/phase197-apply.log"

cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase197.c"
cp "$ROOT/fs/pstore/ram.c" "$OUT/stage/ram-after-phase197.c"
cp "$ROOT/init/main.c" "$OUT/stage/main-after-phase197.c"
cp scripts/197_apply_triple_rs.py "$OUT/stage/"
cp tools/decode-a52-r197-triple-rs.py "$OUT/tools/"
chmod +x "$OUT/tools/decode-a52-r197-triple-rs.py"

git -C "$ROOT" diff --check
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase197.config" "$OUT/config/final.config"
sha256sum -c "$OUT/stage/display-invariants-before-phase197.sha256"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
ram = (root / 'fs/pstore/ram.c').read_text()
main = (root / 'init/main.c').read_text()
kms = (root / 'drivers/a52_display/msm/sde/sde_kms.c').read_text()
gdsc = (root / 'drivers/regulator/a52-legacy-gdsc-regulator.c').read_text()

for marker in (
    '#define A52_R179_BANK_RECORD BIT(2)',
    '#define A52_R179_BANK_ALL',
    'A52_R179_BANK_FTRACE | A52_R179_BANK_RECORD',
    'copies=3 crc=0',
    'BOOT rs=ready phase=197 roots=%u copies=3 crc=0',
    'phase197 triple-copy recorder enabled',
    'encode_rs8(a52_r179_rs, codeword, A52_R179_DATA_BYTES,',
    '#define A52_R179_RS_ROOTS 32U',
    '#define A52_R179_DATA_BYTES 157U',
    '#define A52_R179_CODE_BYTES (A52_R179_DATA_BYTES + A52_R179_RS_ROOTS)',
    '#define A52_R179_PREFIX "R79"',
):
    assert marker in rec, marker
assert rec.count('copies=3 crc=0') == 2
assert 'A52_R179_BANK_BOTH' not in rec
assert 'copies=2 crc=0' not in rec

for marker in (
    '#define A52_ACKFR_BANK_RECORD BIT(2)',
    '#define A52_DIAG_RECORD_PHYS 0xB1B00000ULL',
    '#define A52_DIAG_RECORD_SIZE 0x00040000UL',
    'a52_persistent_diag_mark_record("%.*s", (int)len, buf);',
    'static void a52_diag_record_raw_write',
    'void a52_persistent_diag_mark_record',
    'static int __init a52_persistent_diag_record_init',
    '"a52-early-record"',
    'a52_diag_record_prz->type = PSTORE_TYPE_DMESG;',
    'static int __init a52_persistent_diag_all_mirrors_init',
    'return a52_persistent_diag_record_init();',
):
    assert marker in ram, marker
assert ram.count('return a52_persistent_diag_all_mirrors_init();') == 3

assert 'extern void a52_persistent_diag_mark_record' in main
assert 'static inline void a52_persistent_diag_mark_record' in main
assert main.count('a52_persistent_diag_mark_record(') == 3
assert main.count('A52USR2 BOOT_EARLY stage=mm_init') == 3

for marker in (
    'KMSBLK core-rev exit rev=0x%x',
    'KMSMMU new exit domain=%d rc=%ld',
    'KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d',
    'KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d',
):
    assert marker in kms, marker
for marker in ('"mdss_core_gdsc"', 'A52GDSC disable profile=mdss', 'REGULATOR_CHANGE_MODE'):
    assert marker in gdsc, marker
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase197-kms-block-triple-rs.patch"
test -s "$OUT/stage/phase197-kms-block-triple-rs.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase197-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase197-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase197-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase197-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in \
  'drivers/a52_secure/a52_ack_secure_flight_recorder.o' \
  'fs/pstore/ram.o' \
  'init/main.o'; do
  grep -Fq "$object" "$OUT/logs/phase197-compile.log"
done
for marker in \
  'BOOT rs=ready phase=197 roots=%u copies=3 crc=0' \
  'phase197 triple-copy recorder enabled' \
  'a52-early-record' \
  'KMSBLK core-rev exit rev=0x%x' \
  'KMSMMU new exit domain=%d rc=%ld' \
  'KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d' \
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
A52 GKI 5.10 phase 197 KMS block trace with triple-copy Reed-Solomon recorder

FLASH ONLY:
  package/boot.img -> BOOT partition

This candidate is Phase 196 plus one recorder-only change:
  - the existing RS(189,157) format is preserved
  - every 157-byte event still receives 32 Reed-Solomon parity symbols
  - the exact 255-byte R79 transport is written independently to three banks
  - bank 1: record  at RAMOOPS +0x00000
  - bank 2: console at RAMOOPS +0x40000
  - bank 3: ftrace  at RAMOOPS +0x80000
  - no CRC and no recorder-v3 binary format are introduced

The Phase 194 mdss_core_gdsc fix and all Phase 196 KMS/SMMU block tracing are
preserved. Display control flow, return values, IOMMU behavior, continuous
splash policy, GDSC policy, DTB, DTBO, panel commands, timing, clocks,
regulator voltages, ramdisk and recovery DTBO are unchanged.

Decode an untouched 1 MiB RAMOOPS capture with:
  python3 tools/decode-a52-r197-triple-rs.py RAW_OR_ZIP --output decoded-r197

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('artifacts/a52xq-kms-block-init-triple-rs')
base = json.loads(Path('artifacts/a52xq-kms-block-init-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
rec = (root / 'stage/recorder-after-phase197.c').read_text()
ram = (root / 'stage/ram-after-phase197.c').read_text()
main = (root / 'stage/main-after-phase197.c').read_text()

audit = dict(base)
audit.update({
    'status': 'a52-kms-block-init-triple-rs-audited',
    'phase': 197,
    'base_phase': 196,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase196': 'recorder-third-copy-only',
    'phase196_kms_trace_preserved': True,
    'phase194_mdss_core_gdsc_fix_preserved': True,
    'recorder_format': 'phase179-r79-rs-shortened',
    'recorder_data_bytes': 157,
    'recorder_parity_symbols_per_copy': 32,
    'recorder_codeword_bytes': 189,
    'recorder_transport_bytes': 255,
    'recorder_crc': False,
    'recorder_copy_count': 3,
    'recorder_banks': [
        {'name': 'record', 'offset': 0x00000, 'physical': '0xB1B00000', 'bytes': 0x40000},
        {'name': 'console', 'offset': 0x40000, 'physical': '0xB1B40000', 'bytes': 0x40000},
        {'name': 'ftrace', 'offset': 0x80000, 'physical': '0xB1B80000', 'bytes': 0x40000},
    ],
    'third_copy_source_present': '#define A52_R179_BANK_RECORD BIT(2)' in rec,
    'third_copy_writer_present': 'a52_persistent_diag_mark_record("%.*s", (int)len, buf);' in ram,
    'third_bank_mapping_present': '#define A52_DIAG_RECORD_PHYS 0xB1B00000ULL' in ram,
    'early_marker_three_copies': main.count('A52USR2 BOOT_EARLY stage=mm_init') == 3,
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
    'phase196_kms_trace_preserved',
    'phase194_mdss_core_gdsc_fix_preserved',
    'third_copy_source_present',
    'third_copy_writer_present',
    'third_bank_mapping_present',
    'early_marker_three_copies',
    'dtb_preserved',
    'ramdisk_preserved',
    'recovery_dtbo_preserved',
):
    assert audit[key] is True, key
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
