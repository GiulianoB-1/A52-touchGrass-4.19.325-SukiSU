#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def patch_idle_timer_race(root: Path) -> None:
    devh = root / "drivers/gpu/msm/kgsl_device.h"
    pwr = root / "drivers/gpu/msm/kgsl_pwrctrl.c"
    adreno = root / "drivers/gpu/msm/adreno.c"
    dispatch = root / "drivers/gpu/msm/adreno_dispatch.c"

    dh = devh.read_text()
    if "A619 GPU P2: track latest idle expiry" not in dh:
        dh = replace_once(
            dh,
            """\tstruct work_struct idle_check_ws;
\tstruct timer_list idle_timer;
\tstruct kgsl_pwrctrl pwrctrl;
""",
            """\tstruct work_struct idle_check_ws;
\tstruct timer_list idle_timer;
\t/* A619 GPU P2: track latest idle expiry, Qualcomm f139f08fef49 */
\tunsigned long idle_jiffies;
\tstruct kgsl_pwrctrl pwrctrl;
""",
            "kgsl idle_jiffies field",
        )

        anchor = """#define KGSL_MMU_DEVICE(_mmu) \\
\tcontainer_of((_mmu), struct kgsl_device, mmu)
"""
        helper = """/*
 * Track the absolute expiry whenever the GPU idle timer is re-armed.
 * This lets stale idle work detect that newer GPU activity extended the
 * timeout after the old timer callback had already queued work.
 */
static inline void kgsl_mod_idle_timer(struct kgsl_device *device,
\t\tunsigned long expires)
{
\tdevice->idle_jiffies = expires;
\tmod_timer(&device->idle_timer, expires);
}

"""
        dh = replace_once(dh, anchor, helper + anchor, "kgsl idle timer helper")
        devh.write_text(dh)

    pc = pwr.read_text()
    if "A619 GPU P2: honor the latest idle deadline" not in pc:
        pc = replace_once(
            pc,
            """\trequested_state = device->requested_state;

\tif ((requested_state != KGSL_STATE_NONE) &&
""",
            """\trequested_state = device->requested_state;

\t/*
\t * A619 GPU P2: honor the latest idle deadline.
\t * Qualcomm f139f08fef49 fixes a race where an expired timer can queue
\t * SLUMBER work, then fresh GPU activity extends the idle timeout before
\t * that work acquires the mutex. Do not collapse on the stale request.
\t */
\tif (requested_state == KGSL_STATE_SLUMBER &&
\t\t\ttime_is_after_jiffies(device->idle_jiffies)) {
\t\tkgsl_pwrctrl_request_state(device, KGSL_STATE_NONE);
\t\tkgsl_pwrscale_update(device);
\t\tmutex_unlock(&device->mutex);
\t\treturn;
\t}

\tif ((requested_state != KGSL_STATE_NONE) &&
""",
            "stale idle work guard",
        )

    replacements = [
        (
            """\t\t\tmod_timer(&device->idle_timer,
\t\t\t\t\tjiffies +
\t\t\t\t\tdevice->pwrctrl.interval_timeout);""",
            """\t\t\tkgsl_mod_idle_timer(device,
\t\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "idle_check rearm",
        ),
        (
            """\t\tmod_timer(&device->idle_timer, jiffies +
\t\t\t\tdevice->pwrctrl.interval_timeout);""",
            """\t\tkgsl_mod_idle_timer(device,
\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "wake nap rearm",
        ),
        (
            """\t\tmod_timer(&device->idle_timer,
\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            """\t\tkgsl_mod_idle_timer(device,
\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "active count rearm",
        ),
    ]

    # Two wake-state sites share exactly the same source form.
    old = """\t\tmod_timer(&device->idle_timer, jiffies +
\t\t\t\tdevice->pwrctrl.interval_timeout);"""
    if pc.count(old) == 2:
        pc = pc.replace(
            old,
            """\t\tkgsl_mod_idle_timer(device,
\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
        )
    elif pc.count(old) != 0:
        raise SystemExit(f"wake-state idle timer sites: expected 0 or 2, found {pc.count(old)}")

    old = """\t\t\tmod_timer(&device->idle_timer,
\t\t\t\t\tjiffies +
\t\t\t\t\tdevice->pwrctrl.interval_timeout);"""
    if old in pc:
        pc = replace_once(
            pc, old,
            """\t\t\tkgsl_mod_idle_timer(device,
\t\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "idle_check timer",
        )

    old = """\t\tmod_timer(&device->idle_timer,
\t\t\tjiffies + device->pwrctrl.interval_timeout);"""
    if old in pc:
        pc = replace_once(
            pc, old,
            """\t\tkgsl_mod_idle_timer(device,
\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "active-count timer",
        )
    pwr.write_text(pc)

    ac = adreno.read_text()
    old = """\tmod_timer(&device->idle_timer,
\t\tjiffies + msecs_to_jiffies(adreno_wake_timeout));"""
    if old in ac:
        ac = replace_once(
            ac, old,
            """\tkgsl_mod_idle_timer(device,
\t\tjiffies + msecs_to_jiffies(adreno_wake_timeout));""",
            "touch wake timer",
        )

    old = """\t\tmod_timer(&device->idle_timer,
\t\t\tjiffies + device->pwrctrl.interval_timeout);"""
    if old in ac:
        ac = replace_once(
            ac, old,
            """\t\tkgsl_mod_idle_timer(device,
\t\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "touch nap timer",
        )
    adreno.write_text(ac)

    dc = dispatch.read_text()
    old = """\tmod_timer(&device->idle_timer,
\t\tjiffies + device->pwrctrl.interval_timeout);"""
    if old in dc:
        dc = replace_once(
            dc, old,
            """\tkgsl_mod_idle_timer(device,
\t\tjiffies + device->pwrctrl.interval_timeout);""",
            "dispatcher idle timer",
        )
    dispatch.write_text(dc)

    leftovers = []
    for path in (pwr, adreno, dispatch):
        if "mod_timer(&device->idle_timer" in path.read_text():
            leftovers.append(str(path))
    if leftovers:
        raise SystemExit("untracked GPU idle timer arm remains in: " + ", ".join(leftovers))

    print("[patched] Qualcomm f139f08fef49 adapted to legacy KGSL idle work")


def patch_rpmh_slumber_votes(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_a6xx_gmu.c"
    text = path.read_text()

    if "A619 GPU P2: Qualcomm 83b408e0f209" in text:
        print(f"[already] {path}: RPMh pre-slumber vote completion")
        return

    text = replace_once(
        text,
        """static int a6xx_complete_rpmh_votes(struct kgsl_device *device)
{
\tint ret = 0;

\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS0_DRV0_STATUS,
\t\t\tBIT(0), GPU_RESET_TIMEOUT, BIT(0));
\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS1_DRV0_STATUS,
\t\t\tBIT(0), GPU_RESET_TIMEOUT, BIT(0));
\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS2_DRV0_STATUS,
\t\t\tBIT(0), GPU_RESET_TIMEOUT, BIT(0));
\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS3_DRV0_STATUS,
\t\t\tBIT(0), GPU_RESET_TIMEOUT, BIT(0));

\treturn ret;
}
""",
        """static int a6xx_complete_rpmh_votes(struct kgsl_device *device,
\t\tunsigned int timeout)
{
\tint ret = 0;

\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS0_DRV0_STATUS,
\t\t\tBIT(0), timeout, BIT(0));
\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS1_DRV0_STATUS,
\t\t\tBIT(0), timeout, BIT(0));
\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS2_DRV0_STATUS,
\t\t\tBIT(0), timeout, BIT(0));
\tret |= timed_poll_check_rscc(device, A6XX_RSCC_TCS3_DRV0_STATUS,
\t\t\tBIT(0), timeout, BIT(0));

\tif (ret)
\t\tdev_err(device->dev, "RPMh votes timed out: %d\\n", ret);

\treturn ret;
}
""",
        "RPMh vote helper",
    )

    text = replace_once(
        text,
        "a6xx_complete_rpmh_votes(device);",
        "a6xx_complete_rpmh_votes(device, GPU_RESET_TIMEOUT);",
        "suspend RPMh wait",
    )

    text = replace_once(
        text,
        """/*
 * a6xx_gmu_notify_slumber() - initiate request to GMU to prepare to slumber
""",
        """#define RPMH_VOTE_TIMEOUT 2 /* ms */

/*
 * a6xx_gmu_notify_slumber() - initiate request to GMU to prepare to slumber
""",
        "RPMh timeout define",
    )

    text = replace_once(
        text,
        """\tint ret, state;

\t/* Disable the power counter so that the GMU is not busy */
""",
        """\tint ret, state;

\t/*
\t * A619 GPU P2: Qualcomm 83b408e0f209.
\t * A pending RSCC/RPMh vote can make prepare-slumber fail. Wait for the
\t * four TCS used by the A615/A619 family before touching GMU slumber
\t * state. Keep this ahead of the power-counter disable so failure leaves
\t * the running state unchanged.
\t */
\tret = a6xx_complete_rpmh_votes(device, RPMH_VOTE_TIMEOUT);
\tif (ret)
\t\treturn ret;

\t/* Disable the power counter so that the GMU is not busy */
""",
        "pre-slumber RPMh wait",
    )

    path.write_text(text)
    print("[patched] Qualcomm 83b408e0f209 RPMh vote completion before slumber")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_idle_timer_race(root)
    patch_rpmh_slumber_votes(root)

    checks = {
        root / "drivers/gpu/msm/kgsl_device.h": [
            "unsigned long idle_jiffies;",
            "kgsl_mod_idle_timer",
        ],
        root / "drivers/gpu/msm/kgsl_pwrctrl.c": [
            "A619 GPU P2: honor the latest idle deadline",
            "time_is_after_jiffies(device->idle_jiffies)",
        ],
        root / "drivers/gpu/msm/adreno_a6xx_gmu.c": [
            "RPMH_VOTE_TIMEOUT",
            "A619 GPU P2: Qualcomm 83b408e0f209",
            "a6xx_complete_rpmh_votes(device, RPMH_VOTE_TIMEOUT)",
        ],
    }

    for path, needles in checks.items():
        data = path.read_text()
        for needle in needles:
            if needle not in data:
                raise SystemExit(f"{path}: missing expected GPU P2 element: {needle}")

    print("[done] A619 GPU modernization P2 applied")
    print("[source] Qualcomm f139f08fef49: stale idle timer / SLUMBER race")
    print("[source] Qualcomm 83b408e0f209: complete RPMh votes before prepare-slumber")
    print("[audit] A619/A615 uses four RSCC TCS, so later A650 10-TCS expansion is not applicable")
    print("[audit] legacy adreno_isidle already includes pending IRQ/refcount protection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
