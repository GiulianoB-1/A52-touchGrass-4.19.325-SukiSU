#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-qsecom-heap27"
OUT="$PWD/artifacts/a52xq-qsee-transaction-trace"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

test -s "$BASE_OUT/package/boot.img"
test -s "$BASE_OUT/compile/Image"
test -s "$BASE_OUT/final-audit.json"
test -s "$BUILD/.config"
test -s "$BUILD/arch/arm64/boot/Image"

rm -rf "$OUT"
mkdir -p "$OUT"
cp -a "$BASE_OUT"/. "$OUT"/
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools,comparison}

QSEE="$ROOT/drivers/a52_secure/qseecom.c"
cp "$BUILD/.config" "$OUT/config/before-phase215.config"
cp "$QSEE" "$OUT/stage/qseecom-before-phase215.c"

python3 scripts/215_apply_qsee_transaction_trace.py --self-test \
  | tee "$OUT/logs/phase215-patcher-self-test.log"
python3 scripts/215_apply_qsee_transaction_trace.py --root "$ROOT" \
  | tee "$OUT/logs/phase215-apply.log"

cp "$QSEE" "$OUT/stage/qseecom-after-phase215.c"
cp scripts/215_apply_qsee_transaction_trace.py "$OUT/stage/"
git -C "$ROOT" diff -- drivers/a52_secure/qseecom.c \
  > "$OUT/stage/phase215-qsee-transaction-trace.patch"

grep -Fq '#define A52_R215_QSEE_TRACE_LIMIT 256U' "$QSEE"
grep -Fq 'A52_R215_TRACE("ioctl cmd=%x arg=%lx"' "$QSEE"
grep -Fq 'A52_R215_TRACE("ioctl-ret cmd=%x rc=%ld"' "$QSEE"
grep -Fq 'A52_R215_TRACE("scm smc=%x ai=%x"' "$QSEE"
grep -Fq 'A52_R215_TRACE("scm-ret rc=%ld"' "$QSEE"
grep -Fq 'A52_R215_TRACE("send id=%u app=%.16s q=%u r=%u"' "$QSEE"
grep -Fq 'A52_R215_TRACE("cache op=%d"' "$QSEE"
grep -Fq 'A52_R215_TRACE("bridge fd=%d"' "$QSEE"
grep -Fq 'A52_R215_TRACE("open"' "$QSEE"
grep -Fq 'A52_R215_TRACE("release"' "$QSEE"
git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase215.config" "$BUILD/.config"

python3 "$OUT/tools/decode-a52-r210-rs48-base.py" --self-test \
  | tee "$OUT/logs/phase215-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-triple.py" --self-test \
  | tee "$OUT/logs/phase215-triple-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-transport-fusion.py" --self-test \
  | tee "$OUT/logs/phase215-transport-fusion-decoder-self-test.log"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase215-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase215.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase215-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase215-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase215-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase215-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
nm "$BUILD/vmlinux" | grep -Eq ' [tT] qseecom_ioctl$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] qseecom_open$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] __qseecom_scm_call2_locked$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_ackfr_record$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] msm_atomic_commit$'

missing=0
: > "$OUT/logs/phase215-binary-marker-audit.log"
for marker in \
  'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c' \
  'DRMPOST 212 path n=%u p=%d c=%.16s %.32s' \
  'IONPOST 213 A n=%u l=%llu m=%x f=%x' \
  'BOOT qsecom_heap registered id=%u base=0x%llx size=%llu' \
  'IONPOST 215 n=%u ioctl cmd=%x arg=%lx' \
  'IONPOST 215 n=%u ioctl-ret cmd=%x rc=%ld' \
  'IONPOST 215 n=%u scm smc=%x ai=%x' \
  'IONPOST 215 n=%u scm-ret rc=%ld' \
  'IONPOST 215 n=%u send id=%u app=%.16s q=%u r=%u' \
  'IONPOST 215 n=%u cache op=%d' \
  'IONPOST 215 n=%u bridge fd=%d' \
  'IONPOST 215 n=%u open' \
  'IONPOST 215 n=%u release'; do
  if grep -aFq "$marker" "$OUT/compile/Image"; then
    printf 'PASS %s\n' "$marker" | tee -a "$OUT/logs/phase215-binary-marker-audit.log"
  else
    printf 'FAIL %s\n' "$marker" | tee -a "$OUT/logs/phase215-binary-marker-audit.log"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  strings -a "$OUT/compile/Image" \
    | grep -E 'BOOT rs=ready|DRMPOST 212|IONPOST 213|IONPOST 215|qsecom_heap' \
    > "$OUT/logs/phase215-related-image-strings.txt" || true
  exit 1
fi

gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$BASE_OUT/package/boot.img" \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c %s "$OUT/package/boot.img")" = 100663296

cat > "$OUT/comparison/phase215-hardware-evidence.txt" <<'EOF'
Phase 214 hardware capture A52_RAW_RAMOOPS_20260803_091145.zip:
- 1,028 CRC32C-valid RS48 records recovered from the frozen 1 MiB snapshot.
- Heap-mask BIT(19) allocations returned valid dma-buf file descriptors.
- Heap-mask BIT(27) allocations also returned valid dma-buf file descriptors.
- The Phase 213 BIT(27) -ENODEV retry storm is gone.
- QSEECOM initialization and heap-19/heap-27 shared-memory bridges completed.
- The trace advanced into keymaster@4.0-service dma-buf flag/cache activity.
- No DRM client open, DRM ioctl, atomic check or atomic commit followed.
- Current pmsg contains repeated keystore2 DEAD_OBJECT / KM(-1000) text, but
  pmsg transport corruption makes that evidence supportive rather than primary.

The remaining boundary is the QSEECOM transaction path used by keymaster after
successful ION allocation. Phase 215 changes no result; it retains a bounded
256-event view of QSEECOM open/release, ioctl entry/return, app load/start/send,
dma-buf map/cache/bridge and low-level SCM entry/return.
EOF

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 215 bounded QSEECOM transaction trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 214 hardware validation proves the missing heap-27 registration was fixed:
both qsecom_ta BIT(19) and qsecom BIT(27) allocations now succeed. The previous
allocation retry storm disappeared and startup advanced into keymaster secure
buffer processing, but no DRM client open or atomic commit followed.

Phase 215 is observation-only. It retains at most 256 QSEECOM transaction events:
  - qseecom open and release
  - ioctl command entry and exact return code
  - trusted-app load, start and send-command stages
  - dma-buf map, cache and secure-bridge stages
  - low-level SCM command entry and exact return code

All retained records use the existing IONPOST critical prefix. No ioctl result,
secure-world command, ION allocation, display flow, DTB, DTBO, ramdisk, SMMU
contract, VMID policy or panel behavior is changed.

Three-copy RS48 plus CRC32C remains active. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('artifacts/a52xq-qsee-transaction-trace')
base = json.loads(Path('artifacts/a52xq-qsecom-heap27/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-qsee-transaction-trace-audited',
    'phase': 215,
    'base_phase': 214,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase214_hardware_evidence': {
        'valid_rs48_crc32c_records': 1028,
        'bit19_allocation_success_observed': True,
        'bit27_allocation_success_observed': True,
        'bit27_enodev_retry_storm_resolved': True,
        'qseecom_keymaster_activity_observed': True,
        'drm_client_open_observed': False,
        'atomic_commit_observed': False,
    },
    'phase215_trace': {
        'bounded_events': 256,
        'qseecom_open_release': True,
        'qseecom_ioctl_entry_return': True,
        'qseecom_load_start_send': True,
        'dmabuf_map_cache_bridge': True,
        'low_level_scm_entry_return': True,
        'behavior_changed': False,
        'dtb_changed': False,
        'ramdisk_changed': False,
    },
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'image_bytes': image.stat().st_size,
    'boot_bytes': boot.stat().st_size,
    'repack': repack,
})
(root / 'final-audit.json').write_text(
    json.dumps(base, indent=2, sort_keys=True) + '\n'
)
if not repack.get('flashable_candidate') or repack.get('hardware_validated'):
    raise SystemExit('invalid repack status')
for key, value in repack.get('invariants', {}).items():
    if value is not True:
        raise SystemExit(f'repack invariant failed: {key}={value!r}')
print(json.dumps({
    'phase': 215,
    'image_sha256': base['image_sha256'],
    'boot_sha256': base['boot_sha256'],
    'boot_bytes': base['boot_bytes'],
}, indent=2))
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

printf 'Phase 215 bounded QSEECOM transaction build and audit: PASS\n'
