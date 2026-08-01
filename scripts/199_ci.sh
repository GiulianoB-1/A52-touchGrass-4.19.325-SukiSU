#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-post-kms-crc32c"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
PHASE198="$PWD/workspace/phase198-artifact"
ARTIFACT_ZIP="$PWD/workspace/phase198-artifact.zip"
mkdir -p "$OUT/logs" "$PWD/workspace"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

: "${PHASE198_ARTIFACT_ID:?PHASE198_ARTIFACT_ID is required}"
: "${PHASE198_ARTIFACT_SHA256:?PHASE198_ARTIFACT_SHA256 is required}"
: "${GKI_COMMON_SHA:?GKI_COMMON_SHA is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

rm -rf "$OUT" "$PHASE198" "$BUILD"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools} "$PHASE198" "$BUILD"

# Restore the exact audited Phase 198 artifact instead of rebuilding Phases 180-198.
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${PHASE198_ARTIFACT_ID}/zip" \
  --output "$ARTIFACT_ZIP"
printf '%s  %s\n' "$PHASE198_ARTIFACT_SHA256" "$ARTIFACT_ZIP" | sha256sum -c -
unzip -q "$ARTIFACT_ZIP" -d "$PHASE198"
(
  cd "$PHASE198"
  sha256sum -c SHA256SUMS
) > "$OUT/logs/phase198-manifest-verification.log"

for required in \
  "$PHASE198/stage/phase198-catalog-init-trace.patch" \
  "$PHASE198/config/final.config" \
  "$PHASE198/package/boot.img" \
  "$PHASE198/package/repack-report.json" \
  "$PHASE198/final-audit.json" \
  "$PHASE198/tools/decode-a52-r179-rs-recorder.py"; do
  test -s "$required"
done

python3 - <<'PY'
import json
from pathlib import Path
root = Path('workspace/phase198-artifact')
audit = json.loads((root / 'final-audit.json').read_text())
assert audit['status'] == 'a52-catalog-init-trace-audited'
assert audit['phase'] == 198
assert audit['flashable_candidate'] is True
assert audit['phase194_mdss_core_gdsc_fix_preserved'] is True
assert audit['phase196_kms_trace_preserved'] is True
assert audit['phase197_triple_rs_preserved'] is True
PY

# Recreate the exact Phase 198 source state from the pinned GKI commit.
test -d "$ROOT/.git"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" clean -fd
git -C "$ROOT" apply --check "$PHASE198/stage/phase198-catalog-init-trace.patch"
git -C "$ROOT" apply "$PHASE198/stage/phase198-catalog-init-trace.patch"
git -C "$ROOT" diff --check

cp "$PHASE198/config/final.config" "$BUILD/.config"
cp "$BUILD/.config" "$OUT/config/before-phase199.config"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase199.c"
sha256sum \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  > "$OUT/stage/phase198-invariants-before-phase199.sha256"

python3 scripts/199_apply_recorder_crc32c.py --self-test | \
  tee "$OUT/logs/phase199-patcher-self-test.log"
python3 scripts/199_apply_recorder_crc32c.py --root "$ROOT" | \
  tee "$OUT/logs/phase199-apply.log"

cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase199.c"
cp scripts/199_apply_recorder_crc32c.py "$OUT/stage/"
cp "$PHASE198/tools/decode-a52-r179-rs-recorder.py" "$OUT/tools/"
cp tools/decode-a52-r199-crc32c-base.py "$OUT/tools/"
cp tools/decode-a52-r199-crc32c-triple.py "$OUT/tools/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase198-invariants-before-phase199.sha256"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
ram = (root / 'fs/pstore/ram.c').read_text()
main = (root / 'init/main.c').read_text()
msm = (root / 'drivers/a52_display/msm/msm_drv.c').read_text()
kms = (root / 'drivers/a52_display/msm/sde/sde_kms.c').read_text()
cat = (root / 'drivers/a52_display/msm/sde/sde_hw_catalog.c').read_text()

for marker in (
    'A52 GKI 5.10 display takeover recorder, phase 199',
    '#define A52_R179_CAPACITY 896U',
    '#define A52_R179_MESSAGE_LEN 90U',
    '#define A52_R179_COMMIT 0x5a52c199U',
    '#define A52_R179_VERSION 2U',
    '#define A52_R179_PREFIX "R99"',
    '__le32 crc32c;',
    'a52_r199_crc32c',
    '0x82f63b78U',
    'memcpy(data->magic, "A52R0199"',
    'offsetof(struct a52_r179_data, crc32c)',
    '!strncmp(message, "DRMPOST ", 8)',
    '!strncmp(message, "KMSPOST ", 8)',
    '!strncmp(message, "KMSBLK ", 7)',
    '!strncmp(message, "CAT ", 4)',
    '!strncmp(message, "A52GDSC ", 8)',
    'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c',
    'phase199 triple-copy RS+CRC32C recorder enabled',
):
    assert marker in rec, marker
assert 'copies=3 crc=0' not in rec
assert '#define A52_R179_BANK_RECORD BIT(2)' in rec
assert 'A52_R179_BANK_ALL' in rec
assert '#define A52_DIAG_RECORD_PHYS 0xB1B00000ULL' in ram
assert main.count('A52USR2 BOOT_EARLY stage=mm_init') == 3
for marker in (
    'DRMPOST thread-create enter',
    'DRMPOST vblank enter crtc=%d',
    'DRMPOST irq-install enter irq=%d',
    'DRMPOST dev-register enter',
    'DRMPOST mode-reset enter',
    'DRMPOST splash-config enter',
    'DRMPOST postinit enter',
    'DRMPOST poll-init enter',
    'DRMPOST init success',
):
    assert marker in msm, marker
for marker in (
    'KMSBLK catalog enter rev=0x%x',
    'KMSBLK catalog exit rc=%ld null=%d',
):
    assert marker in kms, marker
for marker in (
    'CAT enter rev=0x%x np-null=%d',
    'CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u',
):
    assert marker in cat, marker
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase199-post-kms-crc32c.patch"
test -s "$OUT/stage/phase199-post-kms-crc32c.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  2>&1 | tee "$OUT/logs/olddefconfig.log"
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase199.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase199-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase199-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase199-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase199-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/a52_secure/a52_ack_secure_flight_recorder.o' \
  "$OUT/logs/phase199-compile.log"
for marker in \
  'A52R0199' \
  'phase199 triple-copy RS+CRC32C recorder enabled' \
  'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c' \
  'DRMPOST thread-create enter' \
  'DRMPOST vblank enter crtc=%d' \
  'DRMPOST irq-install enter irq=%d' \
  'DRMPOST dev-register enter' \
  'DRMPOST splash-config enter' \
  'DRMPOST postinit enter' \
  'DRMPOST poll-init enter' \
  'KMSBLK catalog enter rev=0x%x' \
  'CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$PHASE198/package/boot.img" \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test | \
  tee "$OUT/logs/phase199-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | \
  tee "$OUT/logs/phase199-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'README'
A52 GKI 5.10 Phase 199 post-KMS trace with triple-copy RS + CRC32C

FLASH ONLY:
  package/boot.img -> BOOT partition

This candidate preserves the full Phase 198 catalog trace and the Phase 195
post-KMS checkpoints. The functional display path is unchanged.

Recorder format:
  - three independent physical RAMOOPS banks at +0x00000, +0x40000, +0x80000
  - 157 protected data bytes plus 32 Reed-Solomon parity symbols per copy
  - CRC32C over record metadata and message
  - fixed 255-byte R99 transport
  - 896-record initial retention
  - DRMPOST, KMSPOST, KMSBLK, CAT and A52GDSC remain retained after capacity

The previous Phase 198 capture proved that the hardware catalog and SDE KMS
hardware initialization complete. This image retains the already-present
checkpoints for thread creation, vblank setup, IRQ installation, DRM device
registration, mode reset, continuous splash, debugfs, KMS post-init and polling.

Collect the untouched full 1 MiB RAMOOPS snapshot. The raw collector can remain
unchanged. Decode on a computer with:
  python3 tools/decode-a52-r199-crc32c-triple.py RAW_OR_ZIP --output decoded-r199

Every accepted decoded record must pass CRC32C. The decoder also attempts
same-offset majority and clear-bit OR fusion across the three physical copies.

Compile-audited, not hardware validated.
README

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('artifacts/a52xq-post-kms-crc32c')
base_root = Path('workspace/phase198-artifact')
base = json.loads((base_root / 'final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
rec = (root / 'stage/recorder-after-phase199.c').read_text()

audit = dict(base)
audit.update({
    'status': 'a52-post-kms-crc32c-audited',
    'phase': 199,
    'base_phase': 198,
    'base_artifact_sha256': '99ff045d09811d43106612ef2682216a7d29dfaa7825e2360bef403e31a41eb6',
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase198': 'recorder-integrity-and-retention-only',
    'display_control_flow_changed': False,
    'return_codes_changed': False,
    'catalog_trace_preserved': True,
    'post_kms_trace_preserved': True,
    'phase194_mdss_core_gdsc_fix_preserved': True,
    'recorder_format': 'R99-base64-RS-CRC32C',
    'recorder_copy_count': 3,
    'recorder_banks': ['record', 'console', 'ftrace'],
    'recorder_data_bytes': 157,
    'recorder_message_bytes': 89,
    'recorder_parity_symbols_per_copy': 32,
    'recorder_crc': 'CRC32C',
    'recorder_crc_polynomial_reflected': '0x82f63b78',
    'recorder_initial_capacity': 896,
    'post_capacity_retention': ['BOOT', 'HB', 'REFGEN', 'DISP', 'WDT', 'DRMPOST', 'KMSPOST', 'KMSBLK', 'CAT', 'A52GDSC'],
    'decoder_cross_copy_fusion': ['bit-majority', 'clear-bit-OR'],
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
    'catalog_trace_preserved',
    'post_kms_trace_preserved',
    'phase194_mdss_core_gdsc_fix_preserved',
    'dtb_preserved',
    'ramdisk_preserved',
    'recovery_dtbo_preserved',
):
    assert audit[key] is True, key
assert audit['recorder_copy_count'] == 3
assert audit['recorder_parity_symbols_per_copy'] == 32
assert audit['recorder_crc'] == 'CRC32C'
assert 'copies=3 crc=0' not in rec
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
