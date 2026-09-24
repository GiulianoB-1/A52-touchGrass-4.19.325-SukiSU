#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

INTERNAL = Path("drivers/soc/qcom/rpmh-internal.h")
RSC = Path("drivers/soc/qcom/rpmh-rsc.c")
RPMH = Path("drivers/soc/qcom/rpmh.c")
COMPAT = Path("a52-port-compat.h")
TG_RSC = Path("drivers/soc/qcom/rpmh-rsc.c")
TG_RPMH = Path("drivers/soc/qcom/rpmh.c")
TG_SDE_RSC = Path("techpack/display/msm/sde_rsc.c")
MARK = "A52_PHASE375_RPMH_SOLVER_PARITY_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase375 {label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def function_span(text: str, name: str) -> tuple[int, int]:
    pos = 0
    while True:
        pos = text.find(name + "(", pos)
        if pos < 0:
            raise SystemExit("Phase375 function missing: " + name)
        line = text.rfind("\n", 0, pos) + 1
        semi = text.find(";", pos)
        brace = text.find("{", pos)
        if brace >= 0 and (semi < 0 or brace < semi):
            depth = 0
            for i in range(brace, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return line, i + 1
            raise SystemExit("Phase375 unterminated function: " + name)
        pos += len(name) + 1


def insert_after_function(text: str, name: str, block: str) -> str:
    start, end = function_span(text, name)
    del start
    return text[:end] + "\n\n" + block.rstrip() + "\n" + text[end:]


def patch_in_function(text: str, name: str, old: str, new: str, label: str) -> str:
    start, end = function_span(text, name)
    body = text[start:end]
    count = body.count(old)
    if count != 1:
        raise SystemExit(
            f"Phase375 {label}: {name} expected one anchor, found {count}"
        )
    body = body.replace(old, new, 1)
    return text[:start] + body + text[end:]


def validate_touchgrass(tg: Path) -> None:
    rsc = (tg / TG_RSC).read_text(errors="replace")
    rpmh = (tg / TG_RPMH).read_text(errors="replace")
    sde = (tg / TG_SDE_RSC).read_text(errors="replace")

    for token in (
        "bool in_solver_mode;",
        "void rpmh_rsc_mode_solver_set(struct rsc_drv *drv, bool enable)",
        "if (msg->state == RPMH_ACTIVE_ONLY_STATE && drv->in_solver_mode)",
        "drv->in_solver_mode = enable;",
    ):
        if token not in rsc + (tg / INTERNAL).read_text(errors="replace"):
            raise SystemExit("Phase375 TouchGrass RSC solver contract drifted: " + token)

    for token in (
        "static int check_ctrlr_state(struct rpmh_ctrlr *ctrlr, enum rpmh_state state)",
        "if (ctrlr->in_solver_mode && state == RPMH_ACTIVE_ONLY_STATE)",
        "int rpmh_mode_solver_set(const struct device *dev, bool enable)",
        "rpmh_rsc_mode_solver_set(ctrlr_to_drv(ctrlr), enable);",
    ):
        if token not in rpmh:
            raise SystemExit("Phase375 TouchGrass RPMh solver contract drifted: " + token)

    for token in (
        "rpmh_mode_solver_set(rsc->rpmh_dev, true);",
        "rpmh_mode_solver_set(rsc->rpmh_dev, false);",
        "rsc->version == SDE_RSC_REV_3 ? true : false",
        "rpmh_flush(rsc->rpmh_dev);",
    ):
        if token not in sde:
            raise SystemExit("Phase375 TouchGrass SDE-RSC contract drifted: " + token)


def patch_internal(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        "\tint num_tcs;\n\tstruct notifier_block rsc_pm;\n",
        "\tint num_tcs;\n"
        "\t/* " + MARK + ": display RSC software state mirrors HW solver mode. */\n"
        "\tbool in_solver_mode;\n"
        "\tstruct notifier_block rsc_pm;\n",
        "rsc_drv solver state",
    )

    text = one(
        text,
        "void rpmh_rsc_invalidate(struct rsc_drv *drv);\n",
        "void rpmh_rsc_invalidate(struct rsc_drv *drv);\n"
        "int a52_rpmh_rsc_mode_solver_set(struct rsc_drv *drv, bool enable);\n",
        "solver helper prototype",
    )
    return text


SOLVER_HELPER = r'''/* A52_PHASE375_RPMH_SOLVER_PARITY_V1
 *
 * TouchGrass SDE-RSC changes the hardware solver state first and then calls
 * rpmh_mode_solver_set() so the RPMh software transport agrees with hardware.
 *
 * Android common 5.10 has no dynamic solver-mode API. Keep the state only in
 * rsc_drv, which also owns the active-transfer lock. This avoids the 4.19
 * cache_lock -> drv->lock ordering on a 5.10 core whose documented ordering is
 * drv->lock before cache_lock.
 *
 * TouchGrass waits until any active TCS is idle before enabling solver mode.
 * Preserve that behavior while using irq-safe locking so the completion IRQ can
 * make progress between iterations.
 */
int a52_rpmh_rsc_mode_solver_set(struct rsc_drv *drv, bool enable)
{
	unsigned long flags;
	unsigned int spins = 0;

	if (!drv)
		return -EINVAL;

	for (;;) {
		spin_lock_irqsave(&drv->lock, flags);
		if (!enable || !rpmh_rsc_ctrlr_is_busy(drv)) {
			drv->in_solver_mode = enable;
			spin_unlock_irqrestore(&drv->lock, flags);
			if (a52_p301_disp_rsc(drv))
				a52_ackfr_record("P375 SG set e=%u spins=%u",
					enable, spins);
			return 0;
		}
		spin_unlock_irqrestore(&drv->lock, flags);
		spins++;
		cpu_relax();
	}
}
'''


def patch_rsc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1",
        "A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1",
        "static bool rpmh_rsc_ctrlr_is_busy(struct rsc_drv *drv)",
        "static bool a52_p301_disp_rsc(const struct rsc_drv *drv)",
    ):
        if token not in text:
            raise SystemExit("Phase375 inherited RSC contract missing: " + token)

    old_lock = "\tspin_lock_irq(&drv->lock);\n\n"
    new_lock = (
        "\tspin_lock_irq(&drv->lock);\n\n"
        "\tif (unlikely(drv->in_solver_mode)) {\n"
        "\t\tif (a52_p301_disp_rsc(drv))\n"
        "\t\t\ta52_ackfr_record(\"P375 SG reject active\");\n"
        "\t\tspin_unlock_irq(&drv->lock);\n"
        "\t\treturn -EBUSY;\n"
        "\t}\n\n"
    )
    text = patch_in_function(
        text, "rpmh_rsc_send_data", old_lock, new_lock, "active request gate"
    )

    text = insert_after_function(text, "rpmh_rsc_ctrlr_is_busy", SOLVER_HELPER)

    text = one(
        text,
        "\tspin_lock_init(&drv->lock);\n\tinit_waitqueue_head(&drv->tcs_wait);\n",
        "\tspin_lock_init(&drv->lock);\n"
        "\tdrv->in_solver_mode = false;\n"
        "\tinit_waitqueue_head(&drv->tcs_wait);\n",
        "solver state init",
    )
    return text


RPMH_BLOCK = r'''/* A52_PHASE375_RPMH_SOLVER_PARITY_V1 */
static int a52_rpmh_check_solver_state(const struct device *dev,
				       enum rpmh_state state)
{
	struct rpmh_ctrlr *ctrlr;
	struct rsc_drv *drv;
	unsigned long flags;
	int ret = 0;

	if (state != RPMH_ACTIVE_ONLY_STATE)
		return 0;
	if (!dev || !dev->parent)
		return -EINVAL;

	ctrlr = get_rpmh_ctrlr(dev);
	drv = ctrlr_to_drv(ctrlr);
	spin_lock_irqsave(&drv->lock, flags);
	if (drv->in_solver_mode)
		ret = -EBUSY;
	spin_unlock_irqrestore(&drv->lock, flags);

	return ret;
}

int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)
{
	struct rsc_drv *drv;

	if (!dev || !dev->parent)
		return -EINVAL;
	drv = dev_get_drvdata(dev->parent);
	if (!drv)
		return -ENODEV;

	return a52_rpmh_rsc_mode_solver_set(drv, enable);
}
EXPORT_SYMBOL_GPL(a52_rpmh_mode_solver_set_compat);
'''


def patch_rpmh(text: str) -> str:
    if MARK in text:
        return text

    text = insert_after_function(text, "get_rpmh_ctrlr", RPMH_BLOCK)

    old = "\tint ret = -EINVAL;\n\tstruct cache_req *req;\n\tint i;\n\n"
    new = (
        old
        + "\tret = a52_rpmh_check_solver_state(dev, state);\n"
        + "\tif (ret)\n"
        + "\t\treturn ret;\n\n"
    )
    text = patch_in_function(text, "__rpmh_write", old, new, "normal active pre-cache gate")

    old = "\tint ret, i;\n\tvoid *ptr;\n\n"
    new = (
        old
        + "\tret = a52_rpmh_check_solver_state(dev, state);\n"
        + "\tif (ret)\n"
        + "\t\treturn ret;\n\n"
    )
    text = patch_in_function(text, "rpmh_write_batch", old, new, "batch active gate")
    return text


def patch_compat(text: str) -> str:
    if MARK in text:
        return text
    old = "#define rpmh_mode_solver_set(d,e) do{}while(0)\n"
    new = (
        "/* " + MARK + ": restore live SDE-RSC solver-mode contract. */\n"
        "int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable);\n"
        "#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))\n"
    )
    return one(text, old, new, "Phase13 solver stub replacement")


def validate(internal: str, rsc: str, rpmh: str, compat: str) -> None:
    joined = internal + rsc + rpmh + compat
    required = (
        MARK,
        "bool in_solver_mode;",
        "int a52_rpmh_rsc_mode_solver_set(struct rsc_drv *drv, bool enable);",
        "if (unlikely(drv->in_solver_mode))",
        'a52_ackfr_record("P375 SG reject active");',
        "if (!enable || !rpmh_rsc_ctrlr_is_busy(drv))",
        "drv->in_solver_mode = enable;",
        'a52_ackfr_record("P375 SG set e=%u spins=%u",',
        "drv->in_solver_mode = false;",
        "static int a52_rpmh_check_solver_state(const struct device *dev,",
        "ret = a52_rpmh_check_solver_state(dev, state);",
        "int a52_rpmh_mode_solver_set_compat(const struct device *dev, bool enable)",
        "return a52_rpmh_rsc_mode_solver_set(drv, enable);",
        "#define rpmh_mode_solver_set(d,e) a52_rpmh_mode_solver_set_compat((d), (e))",
    )
    for token in required:
        if token not in joined:
            raise SystemExit("Phase375 patched source missing: " + token)

    if "#define rpmh_mode_solver_set(d,e) do{}while(0)" in compat:
        raise SystemExit("Phase375 solver-mode no-op still present")

    if internal.count("bool in_solver_mode;") != 1:
        raise SystemExit("Phase375 requires one rsc_drv solver state")
    if rsc.count("P375 SG reject active") != 1:
        raise SystemExit("Phase375 active gate count mismatch")
    if rsc.count("a52_rpmh_rsc_mode_solver_set(struct rsc_drv *drv, bool enable)") != 1:
        raise SystemExit("Phase375 solver setter count mismatch")
    if rpmh.count("a52_rpmh_mode_solver_set_compat(") != 1:
        raise SystemExit("Phase375 API wrapper definition count mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    root = args.root.resolve()
    tg = args.touchgrass.resolve()
    validate_touchgrass(tg)

    ip, rp, apip, cp = (
        root / INTERNAL,
        root / RSC,
        root / RPMH,
        root / COMPAT,
    )
    for path in (ip, rp, apip, cp):
        if not path.is_file():
            raise SystemExit("Phase375 source missing: " + str(path))

    internal = ip.read_text(errors="replace")
    rsc = rp.read_text(errors="replace")
    rpmh = apip.read_text(errors="replace")
    compat = cp.read_text(errors="replace")

    if args.check_only:
        validate(internal, rsc, rpmh, compat)
        print("Phase375 RPMh solver parity audit: PASS")
        return 0

    internal2 = patch_internal(internal)
    rsc2 = patch_rsc(rsc)
    rpmh2 = patch_rpmh(rpmh)
    compat2 = patch_compat(compat)
    validate(internal2, rsc2, rpmh2, compat2)

    ip.write_text(internal2)
    rp.write_text(rsc2)
    apip.write_text(rpmh2)
    cp.write_text(compat2)
    print("Phase375 RPMh solver parity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
