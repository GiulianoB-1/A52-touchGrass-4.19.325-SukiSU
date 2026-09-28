#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path

MARK = "A52_PHASE402_DSI_NOHLT_CLEAN_V1"

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
IRQH = Path("kernel/irq/handle.c")
IDLE = Path("kernel/sched/idle.c")
EXIT = Path("kernel/exit.c")
RPMH = Path("drivers/soc/qcom/rpmh-rsc.c")
PM = Path("drivers/base/power/runtime.c")
CPUIDLE = Path("drivers/cpuidle/cpuidle.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
DSIHW = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
UFS = Path("drivers/scsi/ufs/ufs-qcom.c")
USB = Path("drivers/usb/dwc3/dwc3-qcom.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase402 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def load_phase397():
    path = Path(__file__).with_name("397_apply_downstream_bus_votes.py")
    spec = importlib.util.spec_from_file_location("a52_phase397", path)
    if spec is None or spec.loader is None:
        raise SystemExit("Phase402 cannot load Phase397 helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase402 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase402 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase402 closing brace missing: {sig}")


def patch_rec(text: str) -> str:
    if MARK in text:
        return text

    if "A52_PHASE392_PERSISTENT_GAP_DUAL_BACKEND_V1" not in text:
        raise SystemExit("Phase402 requires Phase392 persistent gap")
    if "A52_PHASE394_EARLY_FIXED_LANE_V1" not in text:
        raise SystemExit("Phase402 requires Phase394 lineage")

    # Keep Phase392's safe B1900000..B1AFFFFF persistent backend, but
    # filter recorder admission below so it stores only DSI/DMA_DONE traffic.
    # Do not resurrect Phase389's older B1400000 7 MiB recorder.

    sig = "void a52_ackfr_record(const char *fmt, ...)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]
    brace = fn.find("{")
    guard = (
        "\n\t/* " + MARK + ": suppress unrelated forensic traffic. */\n"
        "\tif (!fmt || (\n"
        "\t    strncmp(fmt, \"P402 \", 5) &&\n"
        "\t    strncmp(fmt, \"P276 280\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 303\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 307\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 312\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 314\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 316\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 319\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 323\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 328\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 329\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 330\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 345\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 346\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 347\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 348\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 349\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 387\", 9) &&\n"
        "\t    strncmp(fmt, \"P276 394\", 9)))\n"
        "\t\treturn;\n"
    )
    fn = fn[:brace + 1] + guard + fn[brace + 1:]
    text = text[:start] + fn + text[end:]

    init_re = re.compile(
        r'^(?P<kind>(?:pure|core|postcore|arch|subsys|fs|rootfs|device|late)_initcall(?:_sync)?)'
        r'\((?P<fn>a52_[pr]\d+_[A-Za-z0-9_]+)\);$',
        re.M,
    )
    retired = []
    def retire(m: re.Match[str]) -> str:
        fn = m.group("fn")
        if fn == "a52_p392_init":
            return m.group(0)
        retired.append(fn)
        return f"/* {MARK}: retired {m.group('kind')}({fn}); */"
    text = init_re.sub(retire, text)
    if not retired:
        raise SystemExit("Phase402 found no numbered recorder initcalls to retire")

    for fn in retired:
        text = text.replace(
            f"static int __init {fn}(",
            f"static int __init __maybe_unused {fn}(",
            1,
        )

    text += (
        "\n/* " + MARK + "\n"
        " * Runtime policy: retain DSI/DMA_DONE probes + Phase392 filtered persistent trace.\n"
        " * Phase397 UFS/USB bus contracts are functional fixes, not probes.\n"
        " */\n"
        "static const char a52_p402_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def patch_irq(text: str) -> str:
    for call in (
        "\ta52_ackfr_noc_irq_hit(irq);\n",
        "\ta52_ackfr_irq_witness(irq);\n",
        "\ta52_p401_irq_witness(irq);\n",
    ):
        text = text.replace(call, "")
    return text


def patch_idle(text: str) -> str:
    return text.replace("\t\ta52_p401_idle_enter();\n", "")


def patch_exit(text: str) -> str:
    return text.replace("\ta52_p401_task_exit(current);\n", "")


def patch_rpmh(text: str) -> str:
    for call in (
        "\ta52_r393_rpmh_send(msg, 0, false);\n",
        "\ta52_r393_rpmh_send(msg, ret, true);\n",
        "\ta52_r393_rpmh_send(msg, 0, true);\n",
        "\ta52_r393_rpmh_irq(irq_status);\n",
    ):
        text = text.replace(call, "")
    for sig in (
        "static bool a52_r393_frontier_ms(",
        "static void a52_r393_rpmh_send(",
        "static void a52_r393_rpmh_irq(",
    ):
        text = text.replace(sig, sig.replace("static ", "static __maybe_unused "), 1)
    return text


def patch_pm(text: str) -> str:
    sig = "static bool a52_r393_pm_window(u64 *elapsed_ms)"
    if sig in text:
        start, end = function_bounds(text, sig)
        text = text[:start] + (
            "static bool a52_r393_pm_window(u64 *elapsed_ms)\n"
            "{\n"
            "\t(void)elapsed_ms;\n"
            "\treturn false;\n"
            "}"
        ) + text[end:]
    return text


def patch_cpuidle(text: str) -> str:
    sig = "static bool a52_r393_idle_window(u64 *elapsed_ms)"
    if sig in text:
        start, end = function_bounds(text, sig)
        text = text[:start] + (
            "static bool a52_r393_idle_window(u64 *elapsed_ms)\n"
            "{\n"
            "\t(void)elapsed_ms;\n"
            "\treturn false;\n"
            "}"
        ) + text[end:]
    return text


def apply_bus_contracts(root: Path) -> None:
    phase397 = load_phase397()
    p = root / UFS
    p.write_text(phase397.patch_ufs(p.read_text(errors="replace")))
    p = root / USB
    p.write_text(phase397.patch_usb(p.read_text(errors="replace")))


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    irq = (root / IRQH).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")
    dsihw = (root / DSIHW).read_text(errors="replace")
    ufs = (root / UFS).read_text(errors="replace")
    usb = (root / USB).read_text(errors="replace")

    for token in (
        MARK,
        'strncmp(fmt, "P276 280", 9)',
        'strncmp(fmt, "P276 303", 9)',
        "A52_PHASE392_PERSISTENT_GAP_DUAL_BACKEND_V1",
        "core_initcall_sync(a52_p392_init);",
    ):
        if token not in rec:
            raise SystemExit("Phase402 recorder token missing: " + token)

    active_numbered = [
        m.group(2) for m in re.finditer(
            r'^((?:pure|core|postcore|arch|subsys|fs|rootfs|device|late)_initcall(?:_sync)?)'
            r'\((a52_[pr]\d+_[A-Za-z0-9_]+)\);$',
            rec,
            re.M,
        )
        if m.group(2) != "a52_p392_init"
    ]
    if active_numbered:
        raise SystemExit("Phase402 unrelated recorder initcalls remain: " +
                         ", ".join(active_numbered))

    for call in (
        "a52_ackfr_noc_irq_hit(irq);",
        "a52_ackfr_irq_witness(irq);",
        "a52_p401_irq_witness(irq);",
    ):
        if call in irq:
            raise SystemExit("Phase402 generic IRQ probe still active: " + call)

    for token in (
        "A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1",
        "P276 303 S04",
        "P276 303 S08",
        "P276 387D e",
        "P276 387D w",
        "P276 387I",
        "P276 394F result",
    ):
        if token not in dsi:
            raise SystemExit("Phase402 DSI controller token missing: " + token)

    for token in (
        "A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1",
        "P276 303 S05",
        "P276 303 S06",
    ):
        if token not in dsihw:
            raise SystemExit("Phase402 DSI HW token missing: " + token)

    for token in (
        "A52_PHASE397_UFS_BUS_VOTE_V1",
        "a52_p397_ufs_bus_register(pdev)",
        "pdata->num_usecases - 1",
    ):
        if token not in ufs:
            raise SystemExit("Phase402 UFS contract missing: " + token)
    for token in (
        "A52_PHASE397_USB_BUS_VOTE_V1",
        "a52_p397_usb_bus_register(to_platform_device(dev), qcom)",
        "pdata->num_usecases - 1",
    ):
        if token not in usb:
            raise SystemExit("Phase402 USB contract missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    for rel in (REC, IRQH, IDLE, EXIT, RPMH, PM, CPUIDLE, DSI, DSIHW, UFS, USB):
        if not (root / rel).is_file():
            raise SystemExit("Phase402 source missing: " + str(rel))

    if not ns.check_only:
        apply_bus_contracts(root)
        for rel, fn in (
            (REC, patch_rec),
            (IRQH, patch_irq),
            (IDLE, patch_idle),
            (EXIT, patch_exit),
            (RPMH, patch_rpmh),
            (PM, patch_pm),
            (CPUIDLE, patch_cpuidle),
        ):
            p = root / rel
            p.write_text(fn(p.read_text(errors="replace")))

    validate(root)
    print("Phase402 DSI-only cleanup + Phase397 contracts: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
