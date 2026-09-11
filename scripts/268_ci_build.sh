#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SEED=/tmp/phase227-seed

fail_report() {
  set +e
  rm -rf phase268-failure
  mkdir -p phase268-failure/source phase268-failure/config phase268-failure/logs
  cp phase268-compile.log phase268-failure/logs/ 2>/dev/null || true
  cp "$OUT/.config" phase268-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/268_phase267_composer_drm_sticky.py phase268-failure/ 2>/dev/null || true
  for p in \
    "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$ROOT/drivers/a52_display/msm/msm_drv.c" \
    "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
    "$ROOT/drivers/gpu/drm/drm_drv.c" \
    "$ROOT/drivers/gpu/drm/drm_file.c" \
    "$ROOT/fs/open.c"; do
    [ -f "$p" ] && cp "$p" "phase268-failure/source/$(echo "$p" | tr '/' '_')"
  done
  {
    echo '=== Phase268 markers ==='
    grep -RsnE 'A52_PHASE268|P268|DRMPOST 21[12]' \
      "$ROOT/drivers/a52_secure" "$ROOT/drivers/a52_display/msm" \
      "$ROOT/drivers/gpu/drm" "$ROOT/fs/open.c" 2>/dev/null | head -n 3000
    echo '=== Phase267 markers ==='
    grep -RsnE 'A52_PHASE267|P267' \
      "$ROOT/drivers/a52_secure" "$ROOT/drivers/a52_display/msm/sde" \
      "$ROOT/drivers/gpu/drm" 2>/dev/null | head -n 2000
  } > phase268-failure/phase268-diagnostics.txt 2>&1
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Materialize the exact cumulative tooling used by the hardware-proven Phase267R build.
python3 scripts/213_payload.py
python3 scripts/216_payload.py
python3 scripts/217_payload.py
mv scripts/217_apply_graphics_service_trace.py scripts/217_apply_graphics_service_trace_base.py
python3 scripts/218_payload.py
mv scripts/218_phase217_wrapper.py scripts/218_phase217_wrapper_phase226.py
python3 scripts/234_repair_phase230_layout.py
python3 scripts/233_payload.py
python3 scripts/239_phase233_fb_msm_audit_repair.py --self-test
python3 scripts/239_phase233_fb_msm_audit_repair.py
python3 scripts/257_namei_510_patcher_repair.py

python3 -m py_compile \
  scripts/208_apply_secure_vmid.py \
  scripts/210_apply_first_atomic_rs48.py \
  scripts/211_apply_drm_client_trace.py \
  scripts/212_apply_graphics_startup_trace.py \
  scripts/213_apply_ion_transaction_trace.py \
  scripts/214_apply_qsecom_heap27.py \
  scripts/215_apply_qsee_transaction_trace.py \
  scripts/216_apply_qsee_deep_trace.py \
  scripts/217_apply_graphics_service_trace_base.py \
  scripts/227_phase226_retention_wrapper.py \
  scripts/227_phase226_retention_wrapper_base.py \
  scripts/263r_hwspinlock_smem_pil_fix.py \
  scripts/265_gfx_iommu_parity.py \
  scripts/266_kgsl_dynamic_iommu_group_compat.py \
  scripts/267_phase266_display_sticky_boundary.py \
  scripts/268_phase267_composer_drm_sticky.py \
  scripts/38_repack_a52_p1_boot.py

python3 scripts/263r_hwspinlock_smem_pil_fix.py --self-test
python3 scripts/265_gfx_iommu_parity.py --self-test
python3 scripts/266_kgsl_dynamic_iommu_group_compat.py --self-test
python3 scripts/267_phase266_display_sticky_boundary.py --self-test
python3 scripts/268_phase267_composer_drm_sticky.py --self-test

test -d gki/common/.git
test "$(git -C gki/common rev-parse HEAD)" = "$GKI_COMMON_SHA"
test -d workspace/touchgrass-a52xq/.git
test "$(git -C workspace/touchgrass-a52xq rev-parse HEAD)" = "$TOUCHGRASS_COMMIT"

# Phase337 retention repair: the historical Phase227/206 artifacts expired.
# Recreate the four seed inputs consumed by this one-compile path from the
# still-retained successful Phase319 evidence plus the exact successful Phase209
# Git patch. Kernel source is NOT taken from Phase319: it is regenerated below.
PHASE319_RETAINED_ARTIFACT_ID=9966129085
PHASE319_RETAINED_ZIP_SHA256=0a7f06e332d580b2b2548783685ecc28a10ee261109db189d446fca18b5ad423
PHASE209_PATCH_REF=b0b2c73eea4ce59abed7cf3b70d236613a9b5e85
rm -rf "$SEED" /tmp/phase319-retained /tmp/phase319-retained.zip
mkdir -p "$SEED"/{package,compile,config,stage} /tmp/phase319-retained
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${PHASE319_RETAINED_ARTIFACT_ID}/zip" \
  --output /tmp/phase319-retained.zip
printf '%s  %s\n' "$PHASE319_RETAINED_ZIP_SHA256" /tmp/phase319-retained.zip | sha256sum -c -
unzip -q /tmp/phase319-retained.zip -d /tmp/phase319-retained
(cd /tmp/phase319-retained && sha256sum -c SHA256SUMS)
cp /tmp/phase319-retained/package/boot.img "$SEED/package/boot.img"
cp /tmp/phase319-retained/compile/Image "$SEED/compile/Image"
cp /tmp/phase319-retained/config/final.config "$SEED/config/before-phase216.config"

# Phase337 retention repair: Phase319 final.config is a valid retained config
# carrier, but it contains ten options enabled much later than the historical
# pre-217 boundary. The original successful Phase233 run proves that only
# CAM_CC/GPU_CC/NPU_CC/QCOM_MDT_LOADER/VIDEO_CC changed across its final
# olddefconfig comparison. Roll these twelve late Phase319 options back before
# using the config as the synthetic before-phase216 boundary. Do not widen the
# Phase233 drift allowlist: if Kconfig reintroduces any of these later, the
# inherited Phase233 guard must still fail closed.
python3 - "$SEED/config/before-phase216.config" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

bools = (
    "TMPFS_POSIX_ACL",
    "TMPFS_XATTR",
    "DEVFREQ_GOV_QCOM_ADRENO_TZ",
    "DEVFREQ_GOV_QCOM_GPUBW_MON",
    "MSM_PIL",
    "MSM_PIL_SSR_GENERIC",
    "MSM_SUBSYSTEM_RESTART",
    "QCOM_BUS_CONFIG_RPMH",
    "QCOM_BUS_SCALING",
    "QCOM_KGSL",
    "QCOM_KGSL_IOMMU",
)
string_sym = "QCOM_ADRENO_DEFAULT_GOVERNOR"

lines = text.splitlines()
out = []
seen = set()
for line in lines:
    handled = False
    for sym in bools:
        if line.startswith(f"CONFIG_{sym}=") or line == f"# CONFIG_{sym} is not set":
            if sym not in seen:
                out.append(f"# CONFIG_{sym} is not set")
                seen.add(sym)
            handled = True
            break
    if handled:
        continue
    if (line.startswith(f"CONFIG_{string_sym}=") or
            line == f"# CONFIG_{string_sym} is not set"):
        continue
    out.append(line)

for sym in bools:
    if sym not in seen:
        out.append(f"# CONFIG_{sym} is not set")

path.write_text("\n".join(out) + "\n", encoding="utf-8")

check = path.read_text(encoding="utf-8")
for sym in bools:
    token = f"# CONFIG_{sym} is not set"
    if check.count(token) != 1:
        raise SystemExit(f"Phase337 synthetic pre217 rollback failed: {sym}")
    if re.search(rf"^CONFIG_{re.escape(sym)}=", check, re.M):
        raise SystemExit(f"Phase337 late enable survived rollback: {sym}")
if re.search(rf"^(?:CONFIG_|# CONFIG_){re.escape(string_sym)}", check, re.M):
    raise SystemExit("Phase337 late Adreno default-governor string survived rollback")

print("Phase337 synthetic pre217 late-config rollback: PASS")
PY
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github.raw+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/patches/209-splash-takeover-trace.patch?ref=${PHASE209_PATCH_REF}" \
  --output "$SEED/stage/phase209-splash-takeover-trace.patch"
for path in \
  "$SEED/package/boot.img" \
  "$SEED/compile/Image" \
  "$SEED/config/before-phase216.config" \
  "$SEED/stage/phase209-splash-takeover-trace.patch"; do
  test -s "$path"
done
printf '%s  %s\n' d698dea506375ed96c0fc447151681268228b0494747f4cb5ad1a340b9362c2f \
  "$SEED/config/before-phase216.config" | sha256sum -c -
printf '%s  %s\n' 3e9728e45bfcaaced602f93c15d25dc438131619ca7259a9352315d412979a69 \
  "$SEED/package/boot.img" | sha256sum -c -
grep -Fq 'SPLCFG209 enter kms=%d' "$SEED/stage/phase209-splash-takeover-trace.patch"
echo 'Phase337 retained seed bridge: PASS'

python3 scripts/199_runtime_fix_crc_anchor_v2.py
python3 scripts/199_runtime_fix_binary_audit.py
# Phase337's replacement 208 helper regenerates Phase175-206 directly and no
# longer needs the expired historical Phase206 artifact prepared by script 209.
bash scripts/208_reconstruct_phase206_source.sh
test -f "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
test -f "$OUT/.config"

# Replay the exact early graphics instrumentation that already exists in Phase267R.
python3 scripts/208_apply_secure_vmid.py --root "$ROOT"
git -C "$ROOT" apply --check "$SEED/stage/phase209-splash-takeover-trace.patch"
git -C "$ROOT" apply "$SEED/stage/phase209-splash-takeover-trace.patch"
python3 scripts/210_apply_first_atomic_rs48.py --root "$ROOT"
python3 scripts/211_apply_drm_client_trace.py --root "$ROOT"
python3 scripts/212_apply_graphics_startup_trace.py --root "$ROOT"
python3 scripts/213_apply_ion_transaction_trace.py --root "$ROOT"
python3 scripts/214_apply_qsecom_heap27.py --root "$ROOT"
python3 scripts/215_apply_qsee_transaction_trace.py --root "$ROOT"
python3 scripts/216_apply_qsee_deep_trace.py --root "$ROOT"
mkdir -p artifacts/a52xq-graphics-startup-trace/config
cp "$SEED/config/before-phase216.config" artifacts/a52xq-graphics-startup-trace/config/before-phase217.config

grep -Fq 'DRMPOST 211 open n=%u' "$ROOT/drivers/a52_display/msm/msm_drv.c"
grep -Fq 'DRMPOST 212 path n=%u' "$ROOT/fs/open.c"
grep -Fq 'DRMPOST 212 drm-open n=%u' "$ROOT/drivers/gpu/drm/drm_file.c"

# Reconstruct the exact hardware-proven Phase266 base.
python3 scripts/227_phase226_retention_wrapper.py "$ROOT"
python3 scripts/263r_hwspinlock_smem_pil_fix.py "$ROOT"
python3 scripts/265_gfx_iommu_parity.py "$ROOT"
python3 scripts/266_kgsl_dynamic_iommu_group_compat.py "$ROOT"

grep -Fq 'A52_PHASE266_KGSL_DYNAMIC_IOMMU_GROUP_COMPAT_V1' "$ROOT/drivers/iommu/iommu.c"
grep -Fq 'A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1' "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
grep -Fq 'F261 it' "$ROOT/drivers/gpu/msm/kgsl_iommu.c"
cp "$OUT/.config" /tmp/phase266-before-diagnostics.config

# Reapply the hardware-proven Phase267R sticky boundary, then add Phase268 only.
python3 scripts/267_phase266_display_sticky_boundary.py "$ROOT"
cmp -s /tmp/phase266-before-diagnostics.config "$OUT/.config"
grep -Fq 'A52_PHASE267_PREDRM_STICKY_RETENTION_V1' "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
grep -Fq 'P267 drm-obj-exit rc=%d c=%d e=%d n=%d p=%d' "$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
grep -Fq 'P267 node-add type=%u idx=%d rc=%d' "$ROOT/drivers/gpu/drm/drm_drv.c"

python3 scripts/268_phase267_composer_drm_sticky.py "$ROOT"
cmp -s /tmp/phase266-before-diagnostics.config "$OUT/.config"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
MSM="$ROOT/drivers/a52_display/msm/msm_drv.c"
OPEN="$ROOT/fs/open.c"
DFILE="$ROOT/drivers/gpu/drm/drm_file.c"
grep -Fq 'A52_PHASE268_COMPOSER_DRM_STICKY_V1' "$REC"
grep -Fq 'return !strncmp(message, "P268 ", 5) ||' "$REC"
grep -Fq '!strncmp(message, "DRMPOST 211", 11)' "$REC"
grep -Fq '!strncmp(message, "DRMPOST 212", 11)' "$REC"
grep -Fq 'P268 A t=%u cp=%d ex=%d pa=%d/%d/%d dr=%d/%d/%d/%d' "$REC"
grep -Fq 'P268 B t=%u mo=%d/%d io=%d/%d/%d ac=%d/%d cl=%d' "$REC"
grep -Fq 'P268 C t=%u pn=%d,%d,%d,%d,%d er=%d,%d,%d,%d,%d' "$REC"
grep -Fq 'DRMPOST 211 ioctl n=%u pid=%d nr=0x%x g=%d' "$MSM"
grep -Fq 'DRMPOST 212 path n=%u p=%d c=%.16s %.32s g=%d' "$OPEN"
grep -Fq 'DRMPOST 212 drm-open n=%u id=%u p=%d g=%d' "$DFILE"

# One final compile, same config semantics as Phase267R.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
for symbol in \
  CONFIG_HWSPINLOCK=y CONFIG_HWSPINLOCK_QCOM=y CONFIG_IOMMU_SUPPORT=y \
  CONFIG_IOMMU_DMA=y CONFIG_ARM_SMMU=y CONFIG_QCOM_BUS_SCALING=y \
  CONFIG_QCOM_BUS_CONFIG_RPMH=y; do
  grep -Fxq "$symbol" "$OUT/.config"
done
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase268-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'F265 iommu-dma disabled -> identity' \
  'F266 %s n=%u g=%d rc=%d' \
  'F261 it' \
  'P267 drm-obj-exit rc=%d c=%d e=%d n=%d p=%d' \
  'P267 node-add type=%u idx=%d rc=%d' \
  'P268 A t=%u cp=%d ex=%d pa=%d/%d/%d dr=%d/%d/%d/%d' \
  'P268 B t=%u mo=%d/%d io=%d/%d/%d ac=%d/%d cl=%d' \
  'P268 C t=%u pn=%d,%d,%d,%d,%d er=%d,%d,%d,%d,%d' \
  'DRMPOST 211 ioctl n=%u pid=%d nr=0x%x g=%d' \
  'DRMPOST 212 drm-open n=%u id=%u p=%d g=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

# Repack against the same proven Phase227 boot container.
rm -rf phase268-out
mkdir -p phase268-out/compile phase268-out/config phase268-out/package phase268-out/audit phase268-out/source
cp "$IMAGE" phase268-out/compile/Image
cp "$OUT/.config" phase268-out/config/final.config
gzip -n -c phase268-out/compile/Image > phase268-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$SEED/package/boot.img" \
  --kernel phase268-out/package/Image.gz \
  --output phase268-out/package/boot.img \
  --report phase268-out/package/repack-report.json
cp scripts/268_phase267_composer_drm_sticky.py phase268-out/audit/
cp phase268-compile.log phase268-out/audit/
cp "$REC" phase268-out/source/
cp "$MSM" phase268-out/source/
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" phase268-out/source/
cp "$ROOT/drivers/gpu/drm/drm_drv.c" phase268-out/source/
cp "$DFILE" phase268-out/source/
cp "$OPEN" phase268-out/source/

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase268-out')
identity = {
    'phase': 268,
    'name': 'COMPOSER-DRM-STICKY-BOUNDARY',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'phase266_base_sha': os.environ.get('PHASE266_BASE_SHA'),
    'phase267_hardware_sha': os.environ.get('PHASE267_HW_SHA'),
    'phase267_hardware_result': 'SDE bus, blocks, DRM objects, DRM node and device_add all succeeded',
    'hardware_validated': False,
    'diagnostic_only': True,
    'device_functional_semantics_changed': False,
    'question': 'Where is the first vendor composer to DRM client boundary that fails or stalls?',
}
(out / 'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
files = [out/'compile/Image', out/'config/final.config', out/'package/Image.gz', out/'package/boot.img', out/'package/repack-report.json']
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY
(cd phase268-out && sha256sum -c SHA256SUMS)
test -s phase268-out/package/boot.img

trap - EXIT
echo 'Phase268 build, closure verification, and repack: PASS'
