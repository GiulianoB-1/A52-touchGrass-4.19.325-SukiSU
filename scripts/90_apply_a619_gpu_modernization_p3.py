#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


MARKER_LOCK = "A619 GPU P3: Qualcomm a404f6fde547"
MARKER_TZ = "A619 GPU P3: Qualcomm 081d5718cff2"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_perfcounter_lock_contention(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_a6xx.c"
    text = path.read_text()

    if MARKER_LOCK in text:
        print(f"[already] {path}: A6xx perfcounter lock contention")
        return

    old = """static int a6xx_perfcounter_update(struct adreno_device *adreno_dev,
\tstruct adreno_perfcount_register *reg, bool update_reg)
{
\tvoid *ptr = adreno_dev->pwrup_reglist.hostptr;
\tstruct cpu_gpu_lock *lock = ptr;
\tu32 *data = ptr + sizeof(*lock);
\tint i, offset = 0;

\tif (cpu_gpu_lock(lock)) {
\t\tcpu_gpu_unlock(lock);
\t\treturn -EBUSY;
\t}

\t/*
\t * If the perfcounter select register is already present in reglist
\t * update it, otherwise append the <select register, value> pair to
\t * the end of the list.
\t */
\tfor (i = 0; i < lock->list_length >> 1; i++) {
\t\tif (data[offset] == reg->select) {
\t\t\tdata[offset + 1] = reg->countable;
\t\t\tgoto update;
\t\t}

\t\toffset += 2;
\t}

\t/*
"""

    new = f"""static int a6xx_perfcounter_update(struct adreno_device *adreno_dev,
\tstruct adreno_perfcount_register *reg, bool update_reg)
{{
\tvoid *ptr = adreno_dev->pwrup_reglist.hostptr;
\tstruct cpu_gpu_lock *lock = ptr;
\tu32 *data = ptr + sizeof(*lock);
\tint i, offset = 0;
\tbool select_reg_present = false;

\t/*
\t * {MARKER_LOCK}
\t *
\t * The CP does not modify the power-up register list and KGSL updates
\t * this list while holding the device mutex. Scan the existing list
\t * before taking the CPU/GPU shared lock, then hold the shared lock only
\t * across the actual mutation. This shortens lock residency without
\t * changing the legacy A612 append layout below.
\t */
\tfor (i = 0; i < lock->list_length >> 1; i++) {{
\t\tif (data[offset] == reg->select) {{
\t\t\tselect_reg_present = true;
\t\t\tbreak;
\t\t}}

\t\toffset += 2;
\t}}

\tif (cpu_gpu_lock(lock)) {{
\t\tcpu_gpu_unlock(lock);
\t\treturn -EBUSY;
\t}}

\t/*
\t * If the perfcounter select register is already present in reglist
\t * update it, otherwise append the <select register, value> pair to
\t * the end of the list.
\t */
\tif (select_reg_present) {{
\t\tdata[offset + 1] = reg->countable;
\t\tgoto update;
\t}}

\t/*
"""

    text = replace_once(text, old, new, "A6xx perfcounter update")
    path.write_text(text)
    print(f"[patched] {path}: Qualcomm a404f6fde547 shared-lock contention reduction")


def patch_single_level_tz(root: Path) -> None:
    tz_path = root / "drivers/devfreq/governor_msm_adreno_tz.c"
    tz = tz_path.read_text()

    if MARKER_TZ not in tz:
        old = """\t/*
\t * Do not waste CPU cycles running this algorithm if
\t * the GPU just started, or if less than FLOOR time
\t * has passed since the last run or the gpu hasn't been
\t * busier than MIN_BUSY.
\t */
\tif ((stats->total_time == 0) ||
\t\t(priv->bin.total_time < FLOOR) ||
\t\t(unsigned int) priv->bin.busy_time < MIN_BUSY) {
\t\treturn 0;
\t}
"""
        new = f"""\t/*
\t * {MARKER_TZ}
\t *
\t * Keep collecting msm-adreno-tz statistics even when only one GPU
\t * power level is exposed, because gpubw_mon consumes those statistics.
\t * Skip only the TZ frequency-selection algorithm in that case.
\t */
\tif ((stats->total_time == 0) ||
\t\t(priv->bin.total_time < FLOOR) ||
\t\t(unsigned int) priv->bin.busy_time < MIN_BUSY ||
\t\tdevfreq->profile->max_state == 1) {{
\t\treturn 0;
\t}}
"""
        tz = replace_once(tz, old, new, "msm-adreno-tz single-level fast path")
        tz_path.write_text(tz)

    pwr_path = root / "drivers/gpu/msm/kgsl_pwrscale.c"
    pwr = pwr_path.read_text()

    old = """\t/* if there is only 1 freq, no point in running a governor */
\tif (profile->max_state == 1)
\t\tgovernor = "performance";

"""
    if old in pwr:
        pwr = replace_once(
            pwr,
            old,
            """\t/*
\t * A619 GPU P3: keep msm-adreno-tz attached even for a one-level
\t * frequency table so gpubw_mon continues receiving fresh GPU/VBIF
\t * statistics. The governor itself skips its frequency algorithm.
\t */

""",
            "single-level governor preservation",
        )
        pwr_path.write_text(pwr)
    elif "A619 GPU P3: keep msm-adreno-tz attached" not in pwr:
        raise SystemExit("single-level governor preservation: expected legacy anchor not found")

    print("[patched] Qualcomm 081d5718cff2 single-power-level TZ/gpubw behavior")


def audit(root: Path) -> None:
    a6xx = (root / "drivers/gpu/msm/adreno_a6xx.c").read_text()
    tz = (root / "drivers/devfreq/governor_msm_adreno_tz.c").read_text()
    pwr = (root / "drivers/gpu/msm/kgsl_pwrscale.c").read_text()

    required = (
        (a6xx, MARKER_LOCK, "perfcounter lock marker"),
        (a6xx, "bool select_reg_present = false;", "perfcounter pre-scan state"),
        (a6xx, "if (select_reg_present)", "perfcounter post-lock mutation"),
        (tz, MARKER_TZ, "TZ marker"),
        (tz, "devfreq->profile->max_state == 1", "TZ one-level fast path"),
        (pwr, "A619 GPU P3: keep msm-adreno-tz attached", "gpubw statistics preservation"),
    )
    for data, needle, label in required:
        if needle not in data:
            raise SystemExit(f"{label}: missing {needle}")

    if 'if (profile->max_state == 1)\n\t\tgovernor = "performance";' in pwr:
        raise SystemExit("legacy one-level performance-governor override still present")

    print("[audit] A6xx power-up reglist scan moved outside CPU/GPU shared lock")
    print("[audit] actual reglist mutation remains protected by CPU/GPU shared lock")
    print("[audit] legacy A612 append handling remains unchanged")
    print("[audit] msm-adreno-tz remains statistics provider for gpubw_mon at one power level")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_perfcounter_lock_contention(root)
    patch_single_level_tz(root)
    audit(root)

    print("[done] A619 GPU modernization Phase3 applied")
    print("[source] Qualcomm a404f6fde547: reduce CPU/GPU shared-lock contention")
    print("[source] Qualcomm 081d5718cff2: preserve gpubw stats with a one-level GPU table")
    print("[excluded] aggressive DDR-stall/SUPER_FAST bus boosts: known A6xx power-risk lineage")
    print("[excluded] Gen7 clockgating and hwsched-only changes: not applicable to A619 legacy KGSL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
