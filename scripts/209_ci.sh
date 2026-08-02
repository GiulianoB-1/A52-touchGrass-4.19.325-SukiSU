#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-secure-vmid"
OUT="$PWD/artifacts/a52xq-splash-takeover-trace"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

download_phase208() {
  local zip="$PWD/workspace/phase208-success.zip"
  rm -rf "$BASE_OUT" "$zip"
  mkdir -p "$BASE_OUT" "$PWD/workspace"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/8831889401/zip" \
    --output "$zip"
  printf '%s  %s\n' \
    e95866e67df1cdbffb945eb26bd510615c95f2f410c74c1f5a3aecb97219935f \
    "$zip" | sha256sum -c -
  unzip -q "$zip" -d "$BASE_OUT"
  (cd "$BASE_OUT" && sha256sum -c SHA256SUMS)
}

download_phase208
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

# Reconstruct the exact Phase 206 source, then replay the audited Phase 208
# secure-VMID patch so the new trace is based on the hardware-tested image.
bash scripts/208_reconstruct_phase206_source.sh
cp "$BASE_OUT/config/final.config" "$BUILD/.config"
python3 scripts/208_apply_secure_vmid.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase208-patcher-self-test.log"
python3 scripts/208_apply_secure_vmid.py --root "$ROOT" \
  | tee "$OUT/logs/phase208-replay.log"

for pair in \
  'include/linux/io-pgtable.h stage/include-linux-io-pgtable.h-after-phase208' \
  'drivers/iommu/io-pgtable-arm.c stage/drivers-iommu-io-pgtable-arm.c-after-phase208' \
  'drivers/iommu/arm/arm-smmu/arm-smmu.h stage/drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase208' \
  'drivers/iommu/arm/arm-smmu/arm-smmu.c stage/drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase208' \
  'drivers/a52_display/msm/msm_smmu.c stage/drivers-a52_display-msm-msm_smmu.c-after-phase208' \
  'drivers/a52_display/msm/sde/sde_kms.c stage/sde-kms-after-phase200.c' \
  'drivers/a52_secure/a52_ack_secure_flight_recorder.c stage/recorder-after-phase208.c'; do
  set -- $pair
  cmp "$ROOT/$1" "$BASE_OUT/$2"
done

cp "$BUILD/.config" "$OUT/config/before-phase209.config"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$OUT/stage/sde-kms-before-phase209.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase209.c"

git -C "$ROOT" apply --check "$PWD/patches/209-splash-takeover-trace.patch"
git -C "$ROOT" apply "$PWD/patches/209-splash-takeover-trace.patch"
cp patches/209-splash-takeover-trace.patch "$OUT/stage/"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$OUT/stage/sde-kms-after-phase209.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase209.c"

# The recorder transport must remain exactly the Phase 208 implementation.
cmp "$OUT/stage/recorder-before-phase209.c" \
    "$OUT/stage/recorder-after-phase209.c"
RECORDER="$OUT/stage/recorder-after-phase209.c"
for marker in \
  '#define A52_R179_BANK_CONSOLE BIT(0)' \
  '#define A52_R179_BANK_FTRACE BIT(1)' \
  '#define A52_R179_BANK_RECORD BIT(2)' \
  '#define A52_R179_BANK_ALL' \
  '#define A52_R179_RS_ROOTS 32U' \
  '#define A52_R179_DATA_BYTES 157U' \
  '#define A52_R179_TEXT_BYTES 255U' \
  '#define A52_R179_PREFIX "R99"' \
  '__le32 crc32c;' \
  'a52_r199_crc32c' \
  'a52_r179_persist_event(&event, A52_R179_BANK_ALL)' \
  'copies=3 crc=crc32c'; do
  grep -Fq "$marker" "$RECORDER"
done

git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase209.config" "$BUILD/.config"
for marker in \
  'SPLCFG209 enter kms=%d' \
  'SPLCFG209 fill enter i=%d conn=%u' \
  'SPLCFG209 fill exit i=%d empty=%d' \
  'SPLCFG209 conn-cb enter i=%d has=%d' \
  'SPLCFG209 planes exit i=%d rc=%d' \
  'SPLCFG209 exit rc=%d'; do
  grep -Fq "$marker" "$OUT/stage/sde-kms-after-phase209.c"
done

cp "$PWD/patches/209-splash-takeover-trace.patch" \
  "$OUT/stage/phase209-splash-takeover-trace.patch"
test -s "$OUT/stage/phase209-splash-takeover-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase209-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase209.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase209-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase209-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase209-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase209-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
nm "$BUILD/vmlinux" | grep -Eq ' [tT] sde_kms_cont_splash_config$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_ackfr_record$'
for marker in \
  'SPLCFG209 fill enter i=%d conn=%u' \
  'SPLCFG209 conn-cb exit i=%d' \
  'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c' \
  'phase199 triple-copy RS+CRC32C recorder enabled'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$BASE_OUT/package/boot.img" \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test \
  | tee "$OUT/logs/phase209-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test \
  | tee "$OUT/logs/phase209-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 209 continuous-splash takeover recorder

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 209 preserves the complete Phase 208 secure-display VMID implementation
and adds read-only checkpoints inside sde_kms_cont_splash_config(). The trace
separates display selection, DSI information lookup, encoder/CRTC assignment,
connector lookup, fill_modes, preferred-mode selection, atomic mode setup,
continuous-splash encoder and CRTC setup, connector callback, and plane update.

Recorder protection is unchanged and explicitly audited:
  - three independent copies: record, console and ftrace banks
  - 157 protected data bytes per record
  - 32 Reed-Solomon parity symbols per copy
  - CRC32C validation inside every protected record
  - R99 Base64 transport, 255 bytes per copy

No return value, panel command, display timing, clock rate, regulator policy,
DTB, DTBO, ramdisk, SMMU behavior or secure-memory behavior is changed.
Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-splash-takeover-trace')
base = json.loads(Path('artifacts/a52xq-secure-vmid/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-splash-takeover-trace-audited',
    'phase': 209,
    'base_phase': 208,
    'hardware_validated': False,
    'flashable_candidate': True,
    'new_recorder_added': True,
    'recorder_transport_changed': False,
    'recorder_format': 'R99-base64-RS-CRC32C',
    'recorder_copy_count': 3,
    'recorder_banks': ['record', 'console', 'ftrace'],
    'recorder_data_bytes': 157,
    'recorder_parity_symbols_per_copy': 32,
    'recorder_crc': 'CRC32C',
    'recorder_transport_bytes': 255,
    'trace_scope': 'sde_kms_cont_splash_config',
    'trace_marker_prefix': 'SPLCFG209',
    'display_control_flow_changed': False,
    'smmu_behavior_changed': False,
    'secure_memory_behavior_changed': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
})
(root / 'final-audit.json').write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
