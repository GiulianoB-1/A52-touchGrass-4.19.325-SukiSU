#!/usr/bin/env python3
"""Inject the TouchGrass GPU recorder into the proven ReSukiSU safe build.

This script modifies only the project-side generated-build template. It does not
change GPU semantics. The injected block runs after ReSukiSU has been connected
to the kernel and its defconfig has been resolved, but immediately before the
kernel compile. This keeps the recorder on the same source/config profile as the
known-good 4.19.200 ReSukiSU safe reference.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: touchgrass_gpu_reference_resukisu_wrapper.py <safe-build-template>")

path = Path(sys.argv[1])
text = path.read_text()

anchor = '''info "Building Linux 4.19.153 + safe ReSukiSU"\nbuild_kernel "$LABEL"\n'''

block = r'''info "Applying observation-only TouchGrass GPU reference recorder"
ROOT="$KERNEL_DIR"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_gpu_reference_overlay.py"

# TouchGrass keeps kgsl_device_platform_probe() in kgsl.c. The overlay was
# originally written against the newer kgsl_device.c pathname. Use a temporary
# pathname alias only while applying the text instrumentation, then delete it
# before Kbuild sees the tree.
ln -s kgsl.c "$ROOT/drivers/gpu/msm/kgsl_device.c"
python3 "$PROJECT_DIR/scripts/touchgrass_gpu_reference_overlay.py" "$ROOT"
rm "$ROOT/drivers/gpu/msm/kgsl_device.c"

# The first hardware run proved that evaluating an optional dynamic recorder
# name inside arm_smmu_power_on can hand strlcpy() an unsafe early-boot pointer.
# Suppress the name argument at the macro boundary so dynamic expressions such
# as dev_name() and __clk_get_name() are not evaluated at all. Fixed event tags
# and all numeric payloads remain intact, so this changes recorder diagnostics
# only and does not touch SMMU/GPU resource semantics.
sed -i 's/tg_gpu_ref_record((_tag), (_name), (_rc)/tg_gpu_ref_record((_tag), NULL, (_rc)/' \
  "$ROOT/include/linux/tg_gpu_reference.h"
grep -Fq 'tg_gpu_ref_record((_tag), NULL, (_rc)' "$ROOT/include/linux/tg_gpu_reference.h" || \
  fail "GPU recorder safe-name suppression missing"

git -C "$ROOT" diff --check

test -s "$ROOT/include/linux/tg_gpu_reference.h" || fail "GPU recorder header missing"
test -s "$ROOT/kernel/tg_gpu_reference.c" || fail "GPU recorder implementation missing"
grep -Fq 'obj-y += tg_gpu_reference.o' "$ROOT/kernel/Makefile" || fail "GPU recorder Kbuild hook missing"
grep -Fq 'SMMU:CEN_POST' "$ROOT/drivers/iommu/arm-smmu.c" || fail "SMMU recorder hook missing"
grep -Fq 'GMU:ATT_POST' "$ROOT/drivers/gpu/msm/kgsl_gmu.c" || fail "GMU recorder hook missing"
grep -Fq 'KGSLI:ATT_POST' "$ROOT/drivers/gpu/msm/kgsl_iommu.c" || fail "KGSL IOMMU recorder hook missing"
grep -Fq 'HFI:CMD' "$ROOT/drivers/gpu/msm/kgsl_hfi.c" || fail "HFI recorder hook missing"
grep -Fq 'ADRENO:PROBE' "$ROOT/drivers/gpu/msm/adreno.c" || fail "Adreno recorder hook missing"
grep -Fq 'KGSL:PLAT_PROBE' "$ROOT/drivers/gpu/msm/kgsl.c" || fail "KGSL platform recorder hook missing"

info "Auditing exact TouchGrass/ReSukiSU reference configuration before compile"
configure_toolchain
PRE="$WORKSPACE/touchgrass-gpu-reference-config-audit"
rm -rf "$PRE"
mkdir -p "$PRE"
make -C "$KERNEL_DIR" O="$PRE" \
  DTC_EXT="$KERNEL_DIR/tools/dtc" \
  CONFIG_BUILD_ARM64_DT_OVERLAY=y \
  KCFLAGS=-w CONFIG_SECTION_MISMATCH_WARN_ONLY=y \
  a52xq_defconfig

CFG="$PRE/.config"
test -s "$CFG" || fail "Resolved reference config missing"
cp "$CFG" "$ARTIFACTS_DIR/touchgrass-gpu-reference-prebuild.config"

failed=0
for expected in \
  CONFIG_ARCH_LAGOON=y \
  CONFIG_SDM_GCC_LAGOON=y \
  CONFIG_SDM_GPUCC_LAGOON=y \
  CONFIG_ARM_SMMU=y \
  CONFIG_QCOM_KGSL=y \
  CONFIG_QCOM_KGSL_IOMMU=y \
  CONFIG_QCOM_GDSC=y \
  CONFIG_COMMON_CLK_QCOM=y \
  CONFIG_KSU=y \
  '# CONFIG_KSU_DEBUG is not set' \
  '# CONFIG_KSU_TOOLKIT_SUPPORT is not set' \
  CONFIG_KSU_MULTI_MANAGER_SUPPORT=y \
  '# CONFIG_KSU_TRACEPOINT_HOOK is not set' \
  CONFIG_KSU_MANUAL_HOOK=y \
  '# CONFIG_KSU_SUSFS is not set' \
  CONFIG_KSU_MANUAL_HOOK_AUTO_SETUID_HOOK=y \
  CONFIG_KSU_MANUAL_HOOK_AUTO_INITRC_HOOK=y \
  CONFIG_KSU_MANUAL_HOOK_AUTO_INPUT_HOOK=y \
  CONFIG_PROC_FS=y; do
  if [[ "$expected" == '# '* ]]; then
    symbol="${expected#\# }"
    symbol="${symbol% is not set}"
  else
    symbol="${expected%%=*}"
  fi
  actual="$(grep -E "^${symbol}=|^# ${symbol} is not set$" "$CFG" || true)"
  printf 'REFERENCE_CONFIG_AUDIT expected=%s actual=%s\n' "$expected" "${actual:-<absent>}"
  if test "$actual" != "$expected"; then
    failed=1
  fi
done
test "$failed" -eq 0 || fail "Resolved config differs from dumped TouchGrass reference profile"

info "Building Linux 4.19.153 + safe ReSukiSU + GPU reference recorder"
build_kernel "$LABEL"
'''

if text.count(anchor) != 1:
    raise SystemExit(f"safe build compile anchor mismatch: expected 1, found {text.count(anchor)}")

path.write_text(text.replace(anchor, block, 1))
print(f"Injected TouchGrass GPU reference recorder into {path}")
