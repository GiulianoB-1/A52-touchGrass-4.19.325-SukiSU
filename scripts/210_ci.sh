#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-splash-takeover-trace"
OUT="$PWD/artifacts/a52xq-first-atomic-rs48"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

download_phase209() {
  local zip="$PWD/workspace/phase209-success.zip"
  rm -rf "$BASE_OUT" "$zip"
  mkdir -p "$BASE_OUT" "$PWD/workspace"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/8833147831/zip" \
    --output "$zip"
  printf '%s  %s\n' \
    31060a6f567843dc30e509cace7cfccc4c6d3f320f4e5dea1d4ea4e5bae4331b \
    "$zip" | sha256sum -c -
  unzip -q "$zip" -d "$BASE_OUT"
  (cd "$BASE_OUT" && sha256sum -c SHA256SUMS)
}

download_phase209
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

bash scripts/208_reconstruct_phase206_source.sh
cp "$BASE_OUT/config/final.config" "$BUILD/.config"
python3 scripts/208_apply_secure_vmid.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase208-patcher-self-test.log"
python3 scripts/208_apply_secure_vmid.py --root "$ROOT" \
  | tee "$OUT/logs/phase208-replay.log"
git -C "$ROOT" apply --check "$PWD/patches/209-splash-takeover-trace.patch"
git -C "$ROOT" apply "$PWD/patches/209-splash-takeover-trace.patch"

cmp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
    "$BASE_OUT/stage/sde-kms-after-phase209.c"
cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$BASE_OUT/stage/recorder-after-phase209.c"

cp "$BUILD/.config" "$OUT/config/before-phase210.config"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase210.c"
cp "$ROOT/drivers/a52_display/msm/msm_atomic.c" \
  "$OUT/stage/msm-atomic-before-phase210.c"

python3 scripts/210_apply_first_atomic_rs48.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase210-patcher-self-test.log"
python3 scripts/210_apply_first_atomic_rs48.py --root "$ROOT" \
  | tee "$OUT/logs/phase210-apply.log"

cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase210.c"
cp "$ROOT/drivers/a52_display/msm/msm_atomic.c" \
  "$OUT/stage/msm-atomic-after-phase210.c"
cp scripts/210_apply_first_atomic_rs48.py "$OUT/stage/"

RECORDER="$OUT/stage/recorder-after-phase210.c"
ATOMIC="$OUT/stage/msm-atomic-after-phase210.c"
for marker in \
  '#define A52_R179_BANK_CONSOLE BIT(0)' \
  '#define A52_R179_BANK_FTRACE BIT(1)' \
  '#define A52_R179_BANK_RECORD BIT(2)' \
  '#define A52_R179_BANK_ALL' \
  '#define A52_R179_RS_ROOTS 48U' \
  '#define A52_R179_DATA_BYTES 141U' \
  '#define A52_R179_CODE_BYTES (A52_R179_DATA_BYTES + A52_R179_RS_ROOTS)' \
  '#define A52_R179_TEXT_BYTES 255U' \
  '#define A52_R179_PREFIX "R48"' \
  '#define A52_R210_PACKED_MESSAGE_LEN 73U' \
  'A52R0210' \
  '__le32 crc32c;' \
  'a52_r199_crc32c' \
  'a52_r179_persist_event(&event, A52_R179_BANK_ALL)' \
  '!strncmp(message, "DRMPOST ", 8)' \
  'phase=210 roots=%u copies=3 crc=crc32c'; do
  grep -Fq "$marker" "$RECORDER"
done

for marker in \
  'DRMPOST 210 c=%u ' \
  'commit enter nb=%d' \
  'prepare_planes rc=%d' \
  'pending wait enter pc=0x%x pp=0x%x' \
  'swap_state enter' \
  'prepare_fence enter' \
  'dispatch queued crtc=%u' \
  'fences wait enter' \
  'modeset_enable enter' \
  'wait_done enter' \
  'complete exit'; do
  grep -Fq "$marker" "$ATOMIC"
done

git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase210.config" "$BUILD/.config"

python3 scripts/210_make_rs48_decoders.py \
  --source "$BASE_OUT/tools" --output "$OUT/tools" \
  | tee "$OUT/logs/phase210-decoder-generation.log"
python3 "$OUT/tools/decode-a52-r210-rs48-base.py" --self-test \
  | tee "$OUT/logs/phase210-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-triple.py" --self-test \
  | tee "$OUT/logs/phase210-triple-decoder-self-test.log"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase210-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase210.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase210-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase210-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase210-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase210-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
nm "$BUILD/vmlinux" | grep -Eq ' [tT] msm_atomic_commit$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_ackfr_record$'

missing=0
: > "$OUT/logs/phase210-binary-marker-audit.log"
for marker in \
  'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c' \
  'R48' \
  'A52R0210' \
  'DRMPOST 210 c=%u commit enter nb=%d' \
  'DRMPOST 210 c=%u fences wait enter' \
  'DRMPOST 210 c=%u dispatch queued crtc=%u'; do
  if grep -aFq "$marker" "$OUT/compile/Image"; then
    printf 'PASS %s\n' "$marker" | tee -a "$OUT/logs/phase210-binary-marker-audit.log"
  else
    printf 'FAIL %s\n' "$marker" | tee -a "$OUT/logs/phase210-binary-marker-audit.log"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  strings -a "$OUT/compile/Image" | \
    grep -E 'A52R|BOOT rs=ready|DRMPOST 210|R48' \
    > "$OUT/logs/phase210-related-image-strings.txt" || true
  exit 1
fi

gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$BASE_OUT/package/boot.img" \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 210 first-userspace-atomic recorder with RS48

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 210 preserves the Phase 209 display and secure-VMID behavior. It traces
only the first eight msm_atomic_commit transactions, including plane
preparation, pending-state waits, state swap, fence preparation, dispatch,
complete_commit fence waits, plane/modeset stages and CRTC completion waits.
All Phase 210 checkpoints begin with the retained DRMPOST prefix.

Recorder format:
  - three independent copies: record, console and ftrace
  - 141 protected data bytes per copy
  - 48 Reed-Solomon parity symbols per copy
  - correction of up to 24 unknown corrupted byte symbols per copy
  - CRC32C validation in every record
  - R48 Base64 transport, 255 bytes per copy

No display return value, userspace ABI, panel command, display timing, clock
rate, regulator policy, DTB, DTBO, ramdisk, SMMU behavior or secure-memory
behavior is changed. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-first-atomic-rs48')
base = json.loads(Path('artifacts/a52xq-splash-takeover-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-first-atomic-rs48-audited',
    'phase': 210,
    'base_phase': 209,
    'hardware_validated': False,
    'flashable_candidate': True,
    'recorder_transport_changed': True,
    'recorder_format': 'R48-base64-RS48-CRC32C',
    'recorder_copy_count': 3,
    'recorder_banks': ['record', 'console', 'ftrace'],
    'recorder_data_bytes': 141,
    'recorder_parity_symbols_per_copy': 48,
    'recorder_max_unknown_symbol_corrections_per_copy': 24,
    'recorder_crc': 'CRC32C',
    'recorder_transport_bytes': 255,
    'recorder_prefix': 'R48',
    'trace_scope': 'first eight msm_atomic_commit transactions',
    'trace_marker_prefix': 'DRMPOST 210',
    'display_control_flow_changed': False,
    'userspace_abi_changed': False,
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
