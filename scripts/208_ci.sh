#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-smmu-display-contracts"
OUT="$PWD/artifacts/a52xq-secure-vmid"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

download_phase206() {
  local zip="$PWD/workspace/phase206-success.zip"
  rm -rf "$BASE_OUT" "$zip"
  mkdir -p "$BASE_OUT" "$PWD/workspace"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/8830356785/zip" \
    --output "$zip"
  printf '%s  %s\n' \
    f5e5c51cee21b1548aa19660f57e7b3f5cf05abee80035c7915d42d891d322e4 \
    "$zip" | sha256sum -c -
  unzip -q "$zip" -d "$BASE_OUT"
  (cd "$BASE_OUT" && sha256sum -c SHA256SUMS)
}

download_phase206
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools,comparison}

bash scripts/208_reconstruct_phase206_source.sh
cp "$BASE_OUT/config/final.config" "$BUILD/.config"
for pair in \
  'include/linux/iommu.h stage/include-linux-iommu.h-after-phase206' \
  'drivers/iommu/arm/arm-smmu/arm-smmu.h stage/drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase206' \
  'drivers/iommu/arm/arm-smmu/arm-smmu.c stage/drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase206' \
  'drivers/iommu/dma-iommu.c stage/drivers-iommu-dma-iommu.c-after-phase206' \
  'drivers/a52_display/msm/msm_smmu.c stage/drivers-a52_display-msm-msm_smmu.c-after-phase206' \
  'drivers/a52_secure/a52_ack_secure_flight_recorder.c stage/recorder-after-phase206.c'; do
  set -- $pair
  cmp "$ROOT/$1" "$BASE_OUT/$2"
done

cp "$BUILD/.config" "$OUT/config/before-phase208.config"
for rel in \
  include/linux/io-pgtable.h \
  drivers/iommu/io-pgtable-arm.c \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/a52_display/msm/msm_smmu.c; do
  safe="${rel//\//-}"
  cp "$ROOT/$rel" "$OUT/stage/${safe}-before-phase208"
done
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-before-phase208.c"

python3 scripts/208_apply_secure_vmid.py --root "$ROOT" --self-test \
  | tee "$OUT/logs/phase208-patcher-self-test.log"
python3 scripts/208_apply_secure_vmid.py --root "$ROOT" \
  | tee "$OUT/logs/phase208-apply.log"
cp scripts/208_apply_secure_vmid.py "$OUT/stage/"

for rel in \
  include/linux/io-pgtable.h \
  drivers/iommu/io-pgtable-arm.c \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/a52_display/msm/msm_smmu.c; do
  safe="${rel//\//-}"
  cp "$ROOT/$rel" "$OUT/stage/${safe}-after-phase208"
done
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
  "$OUT/stage/recorder-after-phase208.c"

git -C "$ROOT" diff --check
cmp "$OUT/config/before-phase208.config" "$BUILD/.config"
cmp "$OUT/stage/recorder-before-phase208.c" \
    "$OUT/stage/recorder-after-phase208.c"

python3 - <<'PY' | tee "$OUT/logs/phase208-touchgrass-comparison.log"
import json
from pathlib import Path
root = Path('gki/common')
tg = Path('workspace/touchgrass-a52xq')
out = Path('artifacts/a52xq-secure-vmid/comparison')

hdr = (root / 'include/linux/io-pgtable.h').read_text()
pg = (root / 'drivers/iommu/io-pgtable-arm.c').read_text()
smmu_h = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.h').read_text()
smmu = (root / 'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
msm = (root / 'drivers/a52_display/msm/msm_smmu.c').read_text()
secure = (root / 'drivers/a52_secure/secure_buffer.c').read_text()
tg_hdr = (tg / 'include/linux/io-pgtable.h').read_text()
tg_pg = (tg / 'drivers/iommu/io-pgtable-arm.c').read_text()
tg_smmu = (tg / 'drivers/iommu/arm-smmu.c').read_text()
phase206 = json.loads((out.parent.parent / 'a52xq-smmu-display-contracts' /
                       'comparison/phase206-active-display-dt.json').read_text())

assert phase206['secure']['properties']['qcom,iommu-vmid'] == [10]
assert 'hyp_assign_phys' in secure
assert 'EXPORT_SYMBOL(hyp_assign_phys)' in secure
for marker in ('alloc_pages_exact)(void *cookie', 'free_pages_exact',
               'cfg->alloc_pages_exact', 'cfg->free_pages_exact'):
    assert marker in hdr + pg, marker
for marker in ('secure_vmid', 'pte_info_list', 'unassign_list',
               'secure_pool_list', 'assign_lock'):
    assert marker in smmu_h, marker
for marker in ('arm_smmu_assign_table', 'arm_smmu_unassign_table',
               'arm_smmu_secure_pool_destroy', 'arm_smmu_secure_domain_lock',
               'hyp_assign_phys',
               'qcom,iommu-vmid', 'DOMAIN_ATTR_SECURE_VMID'):
    assert marker in smmu, marker
assert 'SMMU secure-domain unavailable' not in msm
assert 'SMMU secure-streams faulted' not in smmu
assert 'a52_arm_smmu_attach_fault' not in smmu
assert 'a52_unported_secure_display' not in smmu
assert 'mutex_lock(&to_smmu_domain(domain)->assign_lock)' not in smmu
assert 'alloc_pages_exact' in tg_hdr and 'free_pages_exact' in tg_hdr
assert 'io_pgtable_alloc_pages_exact' in tg_pg
assert 'arm_smmu_assign_table' in tg_smmu
assert 'arm_smmu_unassign_table' in tg_smmu
assert 'hyp_assign_phys' in tg_smmu

report = {
    'status': 'phase208-secure-vmid-source-pass',
    'touchgrass_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'active_secure_vmid': 10,
    'secure_buffer_backend_reused': True,
    'io_pgtable_allocator_hooks_ported': True,
    'secure_domain_state_ported': True,
    'initial_page_tables_assigned': True,
    'runtime_new_page_tables_assigned': True,
    'freed_page_tables_unassigned': True,
    'secure_streams_translating': True,
    'phase206_fault_containment_removed': True,
    'tbu_backend_ported': False,
    'runtime_pm_blocked_for_unmanaged_tbus': True,
    'system_suspend_blocked_for_unmanaged_tbus': True,
    'new_recorder_added': False,
}
(out / 'phase208-touchgrass-comparison.json').write_text(
    json.dumps(report, indent=2, sort_keys=True) + '\n')
print(json.dumps(report, indent=2, sort_keys=True))
PY

git -C "$ROOT" diff --binary --no-ext-diff -- \
  include/linux/io-pgtable.h \
  drivers/iommu/io-pgtable-arm.c \
  drivers/iommu/arm/arm-smmu/arm-smmu.h \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/a52_display/msm/msm_smmu.c \
  > "$OUT/stage/phase208-secure-vmid.patch"
test -s "$OUT/stage/phase208-secure-vmid.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/phase208-olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase208.config" "$OUT/config/final.config"

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase208-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase208-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase208-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase208-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
test -s "$BUILD/vmlinux"
for symbol in \
  arm_smmu_assign_table \
  arm_smmu_unassign_table \
  arm_smmu_alloc_pages_exact \
  arm_smmu_free_pages_exact \
  hyp_assign_phys; do
  nm "$BUILD/vmlinux" | grep -Eq " [tT] ${symbol}$"
done
for marker in \
  'qcom,iommu-vmid' \
  'SMMU parent-qcom scm=%d handoff=%d' \
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
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test \
  | tee "$OUT/logs/phase208-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test \
  | tee "$OUT/logs/phase208-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 208 secure display VMID page-table ownership

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 208 ports the exact secure page-table ownership contract identified by
the Phase 207 TouchGrass comparison:
  - reads qcom,iommu-vmid=10 before secure context creation
  - tracks every secure io-pgtable allocation
  - assigns page-table memory to HLOS RW and VMID 10 read-only
  - assigns new lower-level tables created during map/unmap
  - retains freed secure tables in an assigned reuse pool
  - returns all table memory to HLOS before final free
  - attaches secure display streams to their translating context bank

The existing secure_buffer/hyp_assign_phys backend is reused. No new recorder,
DTB change, DTBO change, ramdisk change, forced bind, IOMMU bypass, supplier
relaxation, panel command, display timing, clock-rate, or regulator-policy
change is included.

The QSMMUv500 TBU backend remains unported. Apps SMMU runtime PM and system
suspend remain blocked. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-secure-vmid')
base = json.loads(Path('artifacts/a52xq-smmu-display-contracts/final-audit.json').read_text())
comparison = json.loads((root / 'comparison/phase208-touchgrass-comparison.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
base.update({
    'status': 'a52-secure-vmid-audited',
    'phase': 208,
    'base_phase': 206,
    'hardware_validated': False,
    'flashable_candidate': True,
    'secure_vmid_backend_ported': True,
    'secure_display_fail_closed': False,
    'secure_display_default_domain_faulted': False,
    'secure_display_streams_translating': True,
    'secure_page_table_assignment_ported': True,
    'secure_page_table_unassignment_ported': True,
    'secure_page_table_reuse_pool_ported': True,
    'tbu_backend_ported': False,
    'runtime_pm_blocked_for_unmanaged_tbus': True,
    'system_suspend_blocked_for_unmanaged_tbus': True,
    'new_recorder_added': False,
    'touchgrass_commit': comparison['touchgrass_commit'],
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
