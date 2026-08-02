#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-drm-client-trace"
OUT="$PWD/artifacts/a52xq-graphics-startup-trace"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

download_phase211() {
  local zip="$PWD/workspace/phase211-success.zip"
  rm -rf "$BASE_OUT" "$zip"
  mkdir -p "$BASE_OUT" "$PWD/workspace"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/8836666538/zip" \
    --output "$zip"
  printf '%s  %s\n' \
    dd9a57c56b25d53f3d765373aef0032d1af6f9e6c53a4eaf7919bdaa4bf55d17 \
    "$zip" | sha256sum -c -
  unzip -q "$zip" -d "$BASE_OUT"
  (cd "$BASE_OUT" && sha256sum -c SHA256SUMS)
}

download_phase211
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools,comparison}

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
python3 scripts/211_apply_drm_client_trace.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase211-patcher-self-test.log"
python3 scripts/211_apply_drm_client_trace.py --root "$ROOT" \
  | tee "$OUT/logs/phase211-replay.log"

cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$BASE_OUT/stage/recorder-after-phase210.c"
cmp "$ROOT/drivers/a52_display/msm/msm_atomic.c" \
    "$BASE_OUT/stage/msm-atomic-after-phase210.c"
cmp "$ROOT/drivers/a52_display/msm/msm_drv.c" \
    "$BASE_OUT/stage/msm-drv-after-phase211.c"

cp "$BUILD/.config" "$OUT/config/before-phase212.config"
for file in \
  fs/open.c \
  fs/exec.c \
  kernel/exit.c \
  drivers/gpu/drm/drm_drv.c \
  drivers/gpu/drm/drm_file.c; do
  mkdir -p "$OUT/stage/before/$(dirname "$file")"
  cp "$ROOT/$file" "$OUT/stage/before/$file"
done

python3 scripts/212_apply_graphics_startup_trace.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase212-patcher-self-test.log"
python3 scripts/212_apply_graphics_startup_trace.py --root "$ROOT" \
  | tee "$OUT/logs/phase212-apply.log"

for file in \
  fs/open.c \
  fs/exec.c \
  kernel/exit.c \
  drivers/gpu/drm/drm_drv.c \
  drivers/gpu/drm/drm_file.c; do
  mkdir -p "$OUT/stage/after/$(dirname "$file")"
  cp "$ROOT/$file" "$OUT/stage/after/$file"
done
cp scripts/212_apply_graphics_startup_trace.py "$OUT/stage/"

cat > "$OUT/comparison/touchgrass-conclusions.txt" <<'EOF'
Phase 212 was selected only after comparison with exact TouchGrass commit
6bf351bdf18bdb228db79e66f14a7a9c0178e5d7 and pinned GKI commit
f960ed27302b1ff8e61e152fc202554d778deccd.

The comparison found no direct TouchGrass compatibility fix to copy:
- MSM open, file operations, driver features and private ioctl table match.
- Both DRM cores call the driver open callback after minor acquisition and
  file allocation, before DRM master setup.
- Both create card%d and renderD%d with the same major/minor ranges.
- Both propagate DRM minor device_add failures through drm_dev_register.
- TouchGrass disables DRM fbdev emulation and FB_MSM, so enabling those would
  not reproduce the working kernel.
- Open, exec and exit differences are normal 4.19-to-5.10 API evolution.

Phase 212 is therefore observation-only and traces graphics-service execution,
relevant device-path opens, DRM node publication, generic DRM open stages and
graphics-related exits.
EOF

RECORDER="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
MSM_DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
OPEN="$ROOT/fs/open.c"
EXEC="$ROOT/fs/exec.c"
EXIT="$ROOT/kernel/exit.c"
DRM_DRV="$ROOT/drivers/gpu/drm/drm_drv.c"
DRM_FILE="$ROOT/drivers/gpu/drm/drm_file.c"

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
  'DRMPOST 210 c=%u ' \
  'commit enter nb=%d' \
  'prepare_planes rc=%d' \
  'dispatch queued crtc=%u'; do
  grep -Fq "$marker" "$ATOMIC"
done
for marker in \
  'DRMPOST 211 open n=%u pid=%d comm=%.16s' \
  'DRMPOST 211 ioctl n=%u pid=%d nr=0x%x' \
  'DRMPOST 211 check n=%u pid=%d comm=%.16s'; do
  grep -Fq "$marker" "$MSM_DRV"
done
for marker in \
  '#define A52_R212_PATH_LIMIT 128' \
  '/dev/dri/' \
  '/dev/graphics/' \
  '/dev/kgsl' \
  '/dev/dma_heap/' \
  'DRMPOST 212 path n=%u p=%d c=%.16s %.32s' \
  'DRMPOST 212 path-ret n=%u fd=%d'; do
  grep -Fq "$marker" "$OPEN"
done
for marker in \
  '#define A52_R212_EXEC_LIMIT 96' \
  'surfaceflinger' \
  'composer' \
  'DRMPOST 212 exec n=%u p=%d %.40s' \
  'DRMPOST 212 exec-ret n=%u rc=%d c=%.16s'; do
  grep -Fq "$marker" "$EXEC"
done
for marker in \
  '#define A52_R212_EXIT_LIMIT 96' \
  'DRMPOST 212 exit n=%u p=%d c=%.16s code=%ld'; do
  grep -Fq "$marker" "$EXIT"
done
for marker in \
  'DRMPOST 212 node type=%u idx=%d name=%.16s' \
  'DRMPOST 212 node-add type=%u idx=%d rc=%d'; do
  grep -Fq "$marker" "$DRM_DRV"
done
for marker in \
  '#define A52_R212_DRM_OPEN_LIMIT 32' \
  'DRMPOST 212 drm-open n=%u id=%u p=%d' \
  'DRMPOST 212 drm-minor n=%u type=%u idx=%d power=%d' \
  'DRMPOST 212 drm-helper n=%u rc=%d' \
  'DRMPOST 212 drm-open-ret n=%u rc=%d'; do
  grep -Fq "$marker" "$DRM_FILE"
done

git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase212.config" "$BUILD/.config"

python3 "$OUT/tools/decode-a52-r210-rs48-base.py" --self-test \
  | tee "$OUT/logs/phase212-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-triple.py" --self-test \
  | tee "$OUT/logs/phase212-triple-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-transport-fusion.py" --self-test \
  | tee "$OUT/logs/phase212-transport-fusion-decoder-self-test.log"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase212-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase212.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase212-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase212-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase212-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase212-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
nm "$BUILD/vmlinux" | grep -Eq ' [tT] msm_atomic_commit$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_ackfr_record$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] do_sys_open$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] drm_open$'

missing=0
: > "$OUT/logs/phase212-binary-marker-audit.log"
for marker in \
  'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c' \
  'R48' \
  'DRMPOST 210 c=%u commit enter nb=%d' \
  'DRMPOST 211 open n=%u pid=%d comm=%.16s' \
  'DRMPOST 212 path n=%u p=%d c=%.16s %.32s' \
  'DRMPOST 212 exec n=%u p=%d %.40s' \
  'DRMPOST 212 exit n=%u p=%d c=%.16s code=%ld' \
  'DRMPOST 212 node type=%u idx=%d name=%.16s' \
  'DRMPOST 212 drm-open n=%u id=%u p=%d'; do
  if grep -aFq "$marker" "$OUT/compile/Image"; then
    printf 'PASS %s\n' "$marker" | tee -a "$OUT/logs/phase212-binary-marker-audit.log"
  else
    printf 'FAIL %s\n' "$marker" | tee -a "$OUT/logs/phase212-binary-marker-audit.log"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  strings -a "$OUT/compile/Image" | \
    grep -E 'BOOT rs=ready|DRMPOST 210|DRMPOST 211|DRMPOST 212|R48' \
    > "$OUT/logs/phase212-related-image-strings.txt" || true
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
A52 GKI 5.10 Phase 212 graphics-startup observation trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 211 hardware evidence showed no MSM DRM open, ioctl, atomic-check or
atomic-commit callback during more than 124 seconds. Before Phase 212, the exact
TouchGrass and pinned GKI sources were compared. No direct TouchGrass fix was
found in MSM DRM, generic DRM registration/open, node naming, fbdev settings,
open, exec or exit behavior.

Phase 212 therefore records the missing userspace-to-kernel boundary:
  - actual DRM primary/render node indices, names and device_add results
  - graphics-related execve attempts and return values
  - opens and return values for /dev/dri, /dev/graphics, KGSL, ION, DMA heaps
    and /sys/class/drm
  - generic DRM-core open stages and return values
  - graphics-related process exits
  - all retained Phase 211 and Phase 210 trace points

All new records use the retained DRMPOST prefix. No return value, userspace ABI,
security decision, pathname result, display control flow, panel command, timing,
clock rate, regulator policy, DTB, DTBO, ramdisk, SMMU behavior or secure-memory
behavior is changed.

Recorder remains unchanged:
  - three independent copies: record, console and ftrace
  - 141 protected data bytes
  - 48 Reed-Solomon parity symbols per copy
  - correction of up to 24 unknown corrupted byte symbols per copy
  - CRC32C validation
  - fixed 255-byte R48 transport

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-graphics-startup-trace')
base = json.loads(Path('artifacts/a52xq-drm-client-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-graphics-startup-trace-audited',
    'phase': 212,
    'base_phase': 211,
    'hardware_validated': False,
    'flashable_candidate': True,
    'recorder_transport_changed': False,
    'recorder_format': 'R48-base64-RS48-CRC32C',
    'touchgrass_comparison_completed': True,
    'touchgrass_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'gki_commit': 'f960ed27302b1ff8e61e152fc202554d778deccd',
    'direct_touchgrass_fix_found': False,
    'trace_scope': {
        'graphics_exec_attempts': 96,
        'relevant_device_path_opens': 128,
        'graphics_process_exits': 96,
        'generic_drm_opens': 32,
        'drm_node_publication': True,
        'phase211_drm_client_trace_retained': True,
        'phase210_atomic_commit_trace_retained': True,
    },
    'trace_marker_prefix': 'DRMPOST 212',
    'display_control_flow_changed': False,
    'userspace_abi_changed': False,
    'security_decisions_changed': False,
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
