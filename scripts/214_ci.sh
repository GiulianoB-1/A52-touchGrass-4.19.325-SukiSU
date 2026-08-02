#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-ion-transaction-trace"
OUT="$PWD/artifacts/a52xq-qsecom-heap27"
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

ION="$ROOT/drivers/staging/android/ion/ion.c"
HEAP="$ROOT/drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c"
cp "$BUILD/.config" "$OUT/config/before-phase214.config"
cp "$ION" "$OUT/stage/ion-before-phase214.c"
cp "$HEAP" "$OUT/stage/qseecom-heaps-before-phase214.c"

python3 scripts/214_apply_qsecom_heap27.py --self-test | tee "$OUT/logs/phase214-patcher-self-test.log"
python3 scripts/214_apply_qsecom_heap27.py --root "$ROOT" | tee "$OUT/logs/phase214-apply.log"

cp "$ION" "$OUT/stage/ion-after-phase214.c"
cp "$HEAP" "$OUT/stage/qseecom-heaps-after-phase214.c"
cp scripts/214_apply_qsecom_heap27.py "$OUT/stage/"
git -C "$ROOT" diff -- drivers/staging/android/ion/ion.c drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c > "$OUT/stage/phase214-qsecom-heap27.patch"

grep -Fq 'A52_QSEECOM_DMA_ID19_ID27_COMPAT' "$ION"
grep -Fq 'heap->id == BIT(19) || heap->id == BIT(27)' "$ION"
grep -Fq '#define A52_QSEECOM_HEAP_ID 27U' "$HEAP"
grep -Fq '#define A52_QSEECOM_HEAP_NAME "qsecom"' "$HEAP"
grep -Fq 'BOOT qsecom_heap registered id=%u base=0x%llx size=%llu' "$HEAP"
grep -Fq 'subsys_initcall_sync(a52_qseecom_heaps_init);' "$HEAP"
grep -Fq 'IONPOST 213 F n=%u rc=%d fl=%lx' "$HEAP"
git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase214.config" "$BUILD/.config"

python3 "$OUT/tools/decode-a52-r210-rs48-base.py" --self-test | tee "$OUT/logs/phase214-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-triple.py" --self-test | tee "$OUT/logs/phase214-triple-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r210-rs48-transport-fusion.py" --self-test | tee "$OUT/logs/phase214-transport-fusion-decoder-self-test.log"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig > "$OUT/logs/phase214-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase214.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 KCFLAGS=-Wno-error=frame-larger-than Image > "$OUT/logs/phase214-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase214-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase214-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase214-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
nm "$BUILD/vmlinux" | grep -Eq ' [tT] ion_ioctl$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] a52_ackfr_record$'
nm "$BUILD/vmlinux" | grep -Eq ' [tT] msm_atomic_commit$'

missing=0
: > "$OUT/logs/phase214-binary-marker-audit.log"
for marker in \
  'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c' \
  'DRMPOST 212 path n=%u p=%d c=%.16s %.32s' \
  'IONPOST 213 A n=%u l=%llu m=%x f=%x' \
  'IONPOST 213 R n=%u e=%x fd=%d rc=%d' \
  'BOOT qsecom_heap registered id=%u base=0x%llx size=%llu' \
  'BOOT qseecom_ta_heap registered id=%u base=0x%llx size=%llu' \
  'A52 ION heap %s registered id=%u at %pa size=%zu'; do
  if grep -aFq "$marker" "$OUT/compile/Image"; then
    printf 'PASS %s\n' "$marker" | tee -a "$OUT/logs/phase214-binary-marker-audit.log"
  else
    printf 'FAIL %s\n' "$marker" | tee -a "$OUT/logs/phase214-binary-marker-audit.log"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  strings -a "$OUT/compile/Image" | grep -E 'BOOT rs=ready|DRMPOST 212|IONPOST 213|qsecom_heap|qseecom_ta_heap|A52 ION heap' > "$OUT/logs/phase214-related-image-strings.txt" || true
  exit 1
fi

gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source "$BASE_OUT/package/boot.img" --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c %s "$OUT/package/boot.img")" = 100663296

cat > "$OUT/comparison/phase214-hardware-evidence.txt" <<'EOF'
Phase 213 hardware capture A52_RAW_RAMOOPS_20260803_013705.zip:
- 1,028 CRC32C-valid RS48 records recovered from the frozen 1 MiB snapshot.
- ION heap mask BIT(19) succeeded repeatedly and returned dma-buf file descriptors.
- ION heap mask BIT(27) failed 63/63 observed allocations with -ENODEV.
- PID 424 alternated BIT(19), 1,265,664-byte successes with BIT(27), 208,896-byte failures.
- PID 426 produced one BIT(19), 880,640-byte success and one BIT(27), 1,052,672-byte failure.
- No DRM client open or atomic commit followed.

The preserved DTB defines qcom,ion-heap@27 as a DMA heap backed by the reusable
20 MiB /reserved-memory/qseecom_region shared-dma-pool. Phase 213 registered
heap 19 only, while generic ACK ION rejected fixed DMA ID 27.
EOF

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 214 qsecom ION heap-27 compatibility

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 213 hardware evidence identified a deterministic pre-DRM failure:
BIT(19) allocations succeed, while every observed BIT(27) allocation returns
-ENODEV. The preserved Samsung DTB defines ID 27 as the qsecom DMA heap backed
by a reusable 20 MiB qseecom_region shared-dma-pool, but ACK never registered it.

Phase 214 keeps heap-19 qsecom_ta, registers heap-27 qsecom, allows only fixed
DMA IDs 19 and 27 beyond ACK's normal ID range, preserves dma_buf get_flags for
both heaps and retains all Phase 213 ION and Phase 212 DRM traces.

No DTB, DTBO, ramdisk, display timing, panel command, SMMU contract, secure VMID
policy, recorder format or unrelated allocator behavior is changed.

Three-copy RS48 plus CRC32C remains active. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-qsecom-heap27')
base = json.loads(Path('artifacts/a52xq-ion-transaction-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-qsecom-heap27-compatibility-audited',
    'phase': 214,
    'base_phase': 213,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase213_hardware_evidence': {
        'valid_rs48_crc32c_records': 1028,
        'bit19_successes_observed': 63,
        'bit27_enodev_failures_observed': 63,
        'bit27_errno': -19,
        'drm_client_open_observed': False,
        'atomic_commit_observed': False,
    },
    'phase214_change': {
        'register_qsecom_dma_heap_id': 27,
        'qsecom_region_bytes': 0x01400000,
        'preserve_qsecom_ta_heap_id': 19,
        'fixed_dma_ids_allowed': [19, 27],
        'ion_trace_retained': True,
        'dtb_changed': False,
        'ramdisk_changed': False,
    },
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'image_bytes': image.stat().st_size,
    'boot_bytes': boot.stat().st_size,
    'repack': repack,
})
(root / 'final-audit.json').write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
if not repack.get('flashable_candidate') or repack.get('hardware_validated'):
    raise SystemExit('invalid repack status')
for key, value in repack.get('invariants', {}).items():
    if value is not True:
        raise SystemExit(f'repack invariant failed: {key}={value!r}')
print(json.dumps({'phase': 214, 'image_sha256': base['image_sha256'], 'boot_sha256': base['boot_sha256'], 'boot_bytes': base['boot_bytes']}, indent=2))
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

printf 'Phase 214 qsecom heap-27 build and audit: PASS\n'
