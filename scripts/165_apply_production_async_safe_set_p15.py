#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"
defconfig = kernel / "arch/arm64/configs/a52xq_defconfig"
vidc = kernel / "techpack/video/msm/vidc/msm_v4l2_vidc.c"
vidc_res = kernel / "techpack/video/msm/vidc/msm_vidc_res_parse.c"
platform = kernel / "drivers/base/platform.c"

for path in (dd, defconfig, vidc, vidc_res, platform):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

s = dd.read_text()
cfg = defconfig.read_text()
vidc_s = vidc.read_text()
vidc_res_s = vidc_res.read_text()
platform_s = platform.read_text()

marker = "A52 P165: production-safe async exception set"
build_marker = "A52_P165_PRODUCTION_ASYNC"

if marker not in s:
    # P159 uses strcmp(). P155 used to add string.h as a side effect, but P165
    # intentionally starts from the clean P153 production chain and skips P155.
    include_anchor = "#include <linux/pinctrl/devinfo.h>\n"
    if "#include <linux/string.h>\n" not in s:
        if s.count(include_anchor) != 1:
            raise SystemExit(f"P165 string.h include anchor count={s.count(include_anchor)}")
        s = s.replace(include_anchor,
                      include_anchor + "#include <linux/string.h>\n", 1)

    sync_anchor = r'''		/* Main IOMMU provider. P158 keeps its TBU child race-safe. */
		"arm-smmu",

		/* Camera framework/provider chain. */
'''
    sync_repl = r'''		/* Main IOMMU provider. P158 keeps its TBU child race-safe. */
		"arm-smmu",

		/*
		 * A52 P165: production-safe async exception set.
		 *
		 * P162/P163 hardware exposed a large visible panel artifact with
		 * the full display bring-up running under P153 async-default.
		 * Keep only the two top-level display coordination drivers on the
		 * stock synchronous contract. Lower-level unrelated drivers still
		 * follow the global async policy.
		 */
		"msm-dsi-display",
		"msm_drm",

		/* Camera framework/provider chain. */
'''
    if s.count(sync_anchor) != 1:
        raise SystemExit(f"P165 display sync anchor count={s.count(sync_anchor)}")
    s = s.replace(sync_anchor, sync_repl, 1)

    helper_anchor = "static bool a52_p159_force_sync(const struct device_driver *drv)\n"
    marker_decl = (
        "/* Compiled identity marker for post-flash boot-partition read-back. */\n"
        "static const char a52_p165_build_marker[] __used =\n"
        "    \"A52_P165_PRODUCTION_ASYNC\";\n\n"
    )
    if s.count(helper_anchor) != 1:
        raise SystemExit(f"P165 build-marker anchor count={s.count(helper_anchor)}")
    s = s.replace(helper_anchor, marker_decl + helper_anchor, 1)

# P164 proved the only remaining census FAIL was the intentionally
# non-matching Lito VIDC SKU node.  Both qcom,vidc0 (sku-index 0) and
# qcom,vidc1 (sku-index 1) are described in DT; msm_decide_dt_node()
# rejects whichever one does not match the efuse-selected sku_version.
# That is device absence/not-applicability, not a broken probe, so use
# -ENODEV instead of -EINVAL.  Driver core already treats -ENODEV as a
# normal rejected match.
sku_marker = "A52 P165: VIDC non-matching SKU is not a probe failure"
if sku_marker not in vidc_res_s:
    old = r'''\tif (sku_index != res->sku_version) {
\t\td_vpr_h("Failed to parse dt: sku_index %d sku_version %d\\n",
\t\t\tsku_index, res->sku_version);
\t\treturn -EINVAL;
\t}
'''
    new = r'''\tif (sku_index != res->sku_version) {
\t\td_vpr_h("Failed to parse dt: sku_index %d sku_version %d\\n",
\t\t\tsku_index, res->sku_version);
\t\t/* A52 P165: VIDC non-matching SKU is not a probe failure */
\t\treturn -ENODEV;
\t}
'''
    if vidc_res_s.count(old) != 1:
        raise SystemExit(f"P165 VIDC SKU mismatch anchor count={vidc_res_s.count(old)}")
    vidc_res_s = vidc_res_s.replace(old, new, 1)

# Production configuration must retain the pre-diagnostic panic behavior.
for token in ("CONFIG_PANIC_ON_OOPS=y", "CONFIG_PANIC_ON_OOPS_VALUE=1"):
    if token not in cfg:
        raise SystemExit(f"P165 production config missing: {token}")

# Retained production async stack.
required_dd = (
    "A52 P153: default all ordinary initial driver probing asynchronous",
    "A52 P159: crash-proven synchronous provider exceptions",
    "if (a52_p159_force_sync(drv))",
    '"arm-smmu"',
    '"msm-dsi-display"',
    '"msm_drm"',
    marker,
    build_marker,
)
for token in required_dd:
    if token not in s:
        raise SystemExit(f"P165 dd.c audit missing: {token}")

# Diagnostic phases must never be present in this production tree.
for token in (
    "A52 P155 PROBE CENSUS",
    "A52 P156 EARLY PROBE CENSUS",
    "A52 P161: expanded async probe census capacity",
    "A52 P163: extend full async census to 12 seconds",
    "A52 P164: VIDC driver-core boundary forensic",
    "A52_PROBE_CENSUS CHECKPOINT_BEGIN",
    "A52_PROBE_CENSUS FINAL_REBOOT",
):
    if token in s:
        raise SystemExit(f"P165 diagnostic residue in dd.c: {token}")

if sku_marker not in vidc_res_s or "return -ENODEV;" not in vidc_res_s:
    raise SystemExit("P165 VIDC SKU semantic fix missing")

for token in (
    "A52 P163 VIDC forensic stage recorder",
    "A52_P163_VIDC_DIAG",
):
    if token in vidc_s:
        raise SystemExit(f"P165 VIDC forensic residue: {token}")

for token in (
    "A52 P164: VIDC platform wrapper forensic",
    "A52_P164_PLATFORM_VIDC",
):
    if token in platform_s:
        raise SystemExit(f"P165 platform forensic residue: {token}")

dd.write_text(s)
vidc_res.write_text(vidc_res_s)

print("A52 P165 production-safe async exception set applied")
print("  global P153 async-default retained")
print("  sync: arm-smmu + crash-proven camera chain")
print("  sync: msm-dsi-display + msm_drm")
print("  P158 QSMMUv500 and P160 DWC3 source-level fixes retained")
print("  VIDC: non-matching efuse SKU node returns -ENODEV (REJECT), not -EINVAL (FAIL)")
print("  no probe census, no forced recovery, no VIDC forensic")
print("  production panic_on_oops behavior retained")
print(f"  compiled flash identity marker: {build_marker}")
