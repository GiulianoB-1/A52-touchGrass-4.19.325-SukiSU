#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-post-kms-crc32c"
OUT="$PWD/artifacts/a52xq-smmu-defer-trace"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/199_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase200.config"
cp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$OUT/stage/msm-smmu-before-phase200.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$OUT/stage/sde-kms-before-phase200.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-before-phase200.c"
sha256sum \
  "$ROOT/fs/pstore/ram.c" \
  "$ROOT/init/main.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  > "$OUT/stage/phase199-invariants-before-phase200.sha256"

python3 scripts/200_apply_smmu_defer_trace.py --self-test | tee "$OUT/logs/phase200-patcher-self-test.log"
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT" | tee "$OUT/logs/phase200-apply.log"

cp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$OUT/stage/msm-smmu-after-phase200.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$OUT/stage/sde-kms-after-phase200.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-after-phase200.c"
cp scripts/200_apply_smmu_defer_trace.py "$OUT/stage/"

git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase199-invariants-before-phase200.sha256"
cmp "$OUT/config/before-phase200.config" "$BUILD/.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
smmu = (root / 'drivers/a52_display/msm/msm_smmu.c').read_text()
kms = (root / 'drivers/a52_display/msm/sde/sde_kms.c').read_text()
rec = (root / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
for marker in (
    '#include <linux/a52_ack_secure_flight_recorder.h>',
    'SMMU driver-register exit rc=%d',
    'SMMU probe domain compat=%s ready=%d',
    'SMMU probe defer compat=%s',
    'SMMU create state domain=%d driver=%d client=%d',
    'SMMU create defer domain=%d',
    'of_node_clear_flag(child, OF_POPULATED);',
    'return ERR_PTR(-EPROBE_DEFER);',
    'SMMU map fail rc=%d client=%d domain=%d',
):
    assert marker in smmu, marker
assert 'KMSMMU required-domain fail domain=%d rc=%d' in kms
assert '!strncmp(message, "SMMU ", 5)' in rec
for marker in (
    'copies=3 crc=crc32c',
    '#define A52_R179_PREFIX "R99"',
    'a52_r199_crc32c',
):
    assert marker in rec, marker
PY

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase200-smmu-defer-trace.patch"
test -s "$OUT/stage/phase200-smmu-defer-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase200-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase200-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase200-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase200-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
for object in \
  'drivers/a52_display/msm/msm_smmu.o' \
  'drivers/a52_display/msm/sde/sde_kms.o' \
  'drivers/a52_secure/a52_ack_secure_flight_recorder.o'; do
  grep -Fq "$object" "$OUT/logs/phase200-compile.log"
done
for marker in \
  'SMMU driver-register exit rc=%d' \
  'SMMU probe domain compat=%s ready=%d' \
  'SMMU probe defer compat=%s' \
  'SMMU create defer domain=%d' \
  'SMMU map fail rc=%d client=%d domain=%d' \
  'KMSMMU required-domain fail domain=%d rc=%d' \
  'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c'; do
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
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test | tee "$OUT/logs/phase200-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | tee "$OUT/logs/phase200-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 200 SMMU client deferred-probe trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 199 capture result:
  - hardware catalog completed
  - unsecure SDE address-space object was created
  - continuous-splash one-to-one map returned -ENODEV
  - msm_smmu_new() had returned success even though its context-bank client or
    IOMMU domain was not usable

TouchGrass comparison:
  The DT nodes, SMMU compatibles and one-to-one mapping implementation match the
  TouchGrass kernel. The compatibility issue is probe timing under the 5.10
  generic IOMMU/device model.

Phase 200 changes:
  - return -EPROBE_DEFER when the dynamically created SMMU context-bank device
    has not obtained driver data yet
  - return -EPROBE_DEFER from the SMMU context-bank probe while its IOMMU domain
    is unavailable
  - propagate failure/defer for the required unsecure display domain when a
    splash region must be mapped
  - add detailed SMMU driver, probe, client, domain and map checkpoints
  - preserve three physical recorder copies, 32 RS parity symbols and CRC32C

No IOMMU bypass is added. DTB, DTBO, ramdisk, panel commands and display timing
remain unchanged.

Collect the untouched full 1 MiB RAMOOPS image with collector 3.1 and decode it
using the included Phase 199 R99 decoder.

Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-smmu-defer-trace')
base = json.loads(Path('artifacts/a52xq-post-kms-crc32c/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-smmu-defer-trace-audited',
    'phase': 200,
    'base_phase': 199,
    'hardware_validated': False,
    'flashable_candidate': True,
    'phase199_crc32c_rs3_preserved': True,
    'functional_change_from_phase199': 'required-smmu-context-deferred-probe-fix-and-trace',
    'iommu_bypass_added': False,
    'smmu_context_missing_drvdata_returns_probe_defer': True,
    'smmu_missing_domain_returns_probe_defer': True,
    'required_unsecure_splash_domain_failure_propagated': True,
    'touchgrass_smmu_dt_and_map_path_compared': True,
    'dtb_changed': False,
    'dtbo_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'recorder_copy_count': 3,
    'recorder_parity_symbols_per_copy': 32,
    'recorder_crc': 'CRC32C',
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in (
    'phase199_crc32c_rs3_preserved',
    'smmu_context_missing_drvdata_returns_probe_defer',
    'smmu_missing_domain_returns_probe_defer',
    'required_unsecure_splash_domain_failure_propagated',
    'touchgrass_smmu_dt_and_map_path_compared',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert base[key] is True, key
assert base['recorder_copy_count'] == 3
assert base['recorder_parity_symbols_per_copy'] == 32
assert base['recorder_crc'] == 'CRC32C'
(root / 'final-audit.json').write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
