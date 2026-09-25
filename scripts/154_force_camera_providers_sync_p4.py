#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
smmu = kernel / "techpack/camera/drivers/cam_smmu/cam_smmu_api.c"
cpas = kernel / "techpack/camera/drivers/cam_cpas/cam_cpas_intf.c"

for p in (smmu, cpas):
    if not p.is_file():
        raise SystemExit(f"missing required source: {p}")

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"{label}: already applied")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))

replace_once(
    smmu,
    """		.of_match_table = msm_cam_smmu_dt_match,
		.suppress_bind_attrs = true,
""",
    """		.of_match_table = msm_cam_smmu_dt_match,
		.probe_type = PROBE_FORCE_SYNCHRONOUS, /* A52 P154 */
		.suppress_bind_attrs = true,
""",
    "camera SMMU forced synchronous",
)

replace_once(
    cpas,
    """		.of_match_table = cam_cpas_dt_match,
		.suppress_bind_attrs = true,
""",
    """		.of_match_table = cam_cpas_dt_match,
		.probe_type = PROBE_FORCE_SYNCHRONOUS, /* A52 P154 */
		.suppress_bind_attrs = true,
""",
    "camera CPAS forced synchronous",
)

smmu_text = smmu.read_text()
cpas_text = cpas.read_text()

assert smmu_text.count("PROBE_FORCE_SYNCHRONOUS, /* A52 P154 */") == 1
assert cpas_text.count("PROBE_FORCE_SYNCHRONOUS, /* A52 P154 */") == 1

print("A52 P154: camera provider ordering exception applied")
print("  synchronous: msm_cam_smmu")
print("  synchronous: cam_cpas")
print("  global P153 async-default remains active for all other ordinary drivers")
