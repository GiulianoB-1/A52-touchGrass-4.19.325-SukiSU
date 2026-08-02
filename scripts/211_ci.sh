#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-first-atomic-rs48"
OUT="$PWD/artifacts/a52xq-drm-client-trace"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

download_phase210() {
  local zip="$PWD/workspace/phase210-success.zip"
  rm -rf "$BASE_OUT" "$zip"
  mkdir -p "$BASE_OUT" "$PWD/workspace"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/8836059983/zip" \
    --output "$zip"
  printf '%s  %s\n' \
    3b33b63ce3d2382f45ec6bfe11406e205da34d88710a4be6aa80bb4914d79266 \
    "$zip" | sha256sum -c -
  unzip -q "$zip" -d "$BASE_OUT"
  (cd "$BASE_OUT" && sha256sum -c SHA256SUMS)
}

download_phase210
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
python3 scripts/210_apply_first_atomic_rs48.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase210-patcher-self-test.log"
python3 scripts/210_apply_first_atomic_rs48.py --root "$ROOT" \
  | tee "$OUT/logs/phase210-replay.log"

cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$BASE_OUT/stage/recorder-after-phase210.c"
cmp "$ROOT/drivers/a52_display/msm/msm_atomic.c" \
    "$BASE_OUT/stage/msm-atomic-after-phase210.c"
cmp "$ROOT/drivers/a52_display/msm/msm_drv.c" \
    "$BASE_OUT/stage/msm-drv-after-phase201.c"

cp "$BUILD/.config" "$OUT/config/before-phase211.config"
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$OUT/stage/msm-drv-before-phase211.c"

python3 scripts/211_apply_drm_client_trace.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase211-patcher-self-test.log"
python3 scripts/211_apply_drm_client_trace.py --root "$ROOT" \
  | tee "$OUT/logs/phase211-apply.log"

cp "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$OUT/stage/msm-drv-after-phase211.c"
cp scripts/211_apply_drm_client_trace.py "$OUT/stage/"
cp scripts/211_decode_r210_rs48_transport_fusion.py \
  "$OUT/tools/decode-a52-r210-rs48-transport-fusion.py"

RECORDER="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
MSM_DRV="$OUT/stage/msm-drv-after-phase211.c"
for marker in \
  '#define A52_R179_RS_ROOTS 48U' \
  '#define A52_R179_DATA_BYTES 141U' \
  '#define A52_R179_PREFIX "R48"' \
  'A52R0210' \
  '__le32 crc32c;' \
  'a52_r179_persist_event(&event, A52_R179_BANK_ALL)' \
  '!strncmp(message, "DRMPOST ", 8)' \
  'phase=210 roots=%u copies=3 crc=crc32c'; do
  grep -Fq "$marker" "$RECORDER"
done
for marker in \
  'DRMPOST 210 c=%u commit enter nb=%d' \
  'DRMPOST 210 c=%u prepare_planes rc=%d' \
  'DRMPOST 210 c=%u dispatch queued crtc=%u' \
  'DRMPOST 210 c=%u fences wait enter'; do
  grep -Fq "$marker" "$ATOMIC"
done
for marker in \
  '#define A52_R211_OPEN_LIMIT 8' \
  '#define A52_R211_IOCTL_LIMIT 24' \
  '#define A52_R211_CHECK_LIMIT 8' \
  'DRMPOST 211 open n=%u pid=%d comm=%.16s' \
  'DRMPOST 211 ioctl n=%u pid=%d nr=0x%x' \
  'DRMPOST 211 check n=%u pid=%d comm=%.16s' \
  'DRMPOST 211 close n=%u pid=%d comm=%.16s' \
  '.unlocked_ioctl     = a52_r211_drm_ioctl,' \
  '.compat_ioctl       = a52_r211_drm_compat_ioctl,' \
  '.atomic_commit = msm_atomic_commit,'; do
  grep -Fq "$marker" "$MSM_DRV"
done

git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase211.config" "$BUILD/.config"

python3 "$OUT/tools/decode-a52-r210-rs48-base.py" --self-test \
  | tee "$OUT/logs/phase211-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-triple.py" --self-test \
  | tee "$OUT/logs/phase211-triple-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-transport-fusion.py" --self-test \
  | tee "$OUT/logs/phase211-transport-fusion-decoder-self-test.log"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase211-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase211.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase211-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase211-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase211-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase211-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
nm "$BUILD/vmlinux" | grep -Eq ' [tT] msm_atomic_commit$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_ackfr_record$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_r211_drm_ioctl$'

missing=0
: > "$OUT/logs/phase211-binary-marker-audit.log"
for marker in \
  'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c' \
  'R48' \
  'DRMPOST 210 c=%u commit enter nb=%d' \
  'DRMPOST 211 open n=%u pid=%d comm=%.16s' \
  'DRMPOST 211 ioctl n=%u pid=%d nr=0x%x' \
  'DRMPOST 211 check n=%u pid=%d comm=%.16s'; do
  if grep -aFq "$marker" "$OUT/compile/Image"; then
    printf 'PASS %s\n' "$marker" | tee -a "$OUT/logs/phase211-binary-marker-audit.log"
  else
    printf 'FAIL %s\n' "$marker" | tee -a "$OUT/logs/phase211-binary-marker-audit.log"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  strings -a "$OUT/compile/Image" | \
    grep -E 'BOOT rs=ready|DRMPOST 210|DRMPOST 211|R48' \
    > "$OUT/logs/phase211-related-image-strings.txt" || true
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
A52 GKI 5.10 Phase 211 DRM client-path trace with retained RS48 recorder

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 210 hardware evidence recovered all 1,027 complete recorder transports,
showed successful splash configuration and heartbeats through 92.416 seconds,
but contained no DRMPOST 210 msm_atomic_commit records. Phase 211 moves one
layer earlier and records:
  - the first eight msm DRM client opens and closes
  - the first 24 native or compat DRM ioctls and their return values
  - the first eight msm_atomic_check calls and return values
  - all retained Phase 210 first-atomic checkpoints if a commit is reached

All new records use the retained DRMPOST prefix. No return value or control
flow is changed. The R48 triple-copy RS48 + CRC32C recorder is unchanged.
The artifact adds a transport-fusion decoder that aligns the record and ftrace
banks, ORs their 255-byte ASCII transports before Base64 decoding, then requires
RS48 correction and CRC32C validation.

No panel command, timing, clock rate, regulator policy, DTB, DTBO, ramdisk,
SMMU behavior, secure-memory behavior or userspace ABI is changed.
Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-drm-client-trace')
base = json.loads(Path('artifacts/a52xq-first-atomic-rs48/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-drm-client-trace-audited',
    'phase': 211,
    'base_phase': 210,
    'hardware_validated': False,
    'flashable_candidate': True,
    'recorder_transport_changed': False,
    'recorder_format': 'R48-base64-RS48-CRC32C',
    'trace_scope': {
        'msm_drm_open_calls': 8,
        'msm_drm_ioctl_calls': 24,
        'msm_atomic_check_calls': 8,
        'msm_drm_close_calls': 8,
        'phase210_atomic_commit_trace_retained': True,
    },
    'trace_marker_prefix': 'DRMPOST 211',
    'transport_fusion_decoder_added': True,
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
