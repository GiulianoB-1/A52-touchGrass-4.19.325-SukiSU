#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

GROUPS = {
    "D": [
        ("DSI_STATUS", "DSI + status"),
        ("DSI_LANE_STATUS", "DSI + lane status"),
        ("DSI_CLK_STATUS", "DSI + clock status"),
        ("DSI_INT_CTRL", "DSI + interrupt control"),
        ("DSI_COMMAND_MODE_DMA_CTRL", "DSI + command DMA control"),
        ("DSI_DMA_CMD_OFFSET", "DSI + command DMA offset"),
        ("DSI_DMA_CMD_LENGTH", "DSI + command DMA length"),
        ("DSI_AXI2AHB_CTRL", "DSI + AXI2AHB control"),
    ],
    "C0": [
        ("DISP_CC_MDSS_PCLK0_CBCR", "0xaf0100c"),
        ("DISP_CC_MDSS_MDP_CBCR", "0xaf01010"),
        ("DISP_CC_MDSS_BYTE0_CBCR", "0xaf0102c"),
        ("DISP_CC_MDSS_BYTE0_INTF_CBCR", "0xaf01030"),
        ("DISP_CC_MDSS_ESC0_CBCR", "0xaf01034"),
        ("DISP_CC_MDSS_AHB_CBCR", "0xaf0104c"),
        ("GCC_DISP_AHB_CBCR", "0x11700c"),
        ("GCC_DISP_AXI_CBCR", "0x11701c"),
    ],
    "C1": [
        ("DISP_CC_PCLK0_CMD_RCGR", "0xaf01064"),
        ("DISP_CC_PCLK0_CFG_RCGR", "0xaf01068"),
        ("DISP_CC_BYTE0_CMD_RCGR", "0xaf0107c"),
        ("DISP_CC_BYTE0_CFG_RCGR", "0xaf01080"),
        ("DISP_CC_ESC0_CMD_RCGR", "0xaf010c4"),
        ("DISP_CC_ESC0_CFG_RCGR", "0xaf010c8"),
        ("DISP_CC_MDP_CMD_RCGR", "0xaf010e0"),
        ("DISP_CC_MDP_CFG_RCGR", "0xaf010e4"),
    ],
    "P": [
        ("MDSS_CORE_GDSC", "0xaf01004"),
        ("DISP_CC_MDSS_RSCC_AHB_CBCR", "0xaf02004"),
        ("DISP_CC_MDSS_RSCC_VSYNC_CBCR", "0xaf02008"),
        ("DISP_CC_MDSS_RSCC_XO_CBCR", "0xaf0200c"),
    ],
    "V0": [
        ("VBIF_HW_VERSION", "0xaeb0000"),
        ("VBIF_CLKON", "0xaeb0008"),
        ("VBIF_CLKOFF", "0xaeb000c"),
        ("VBIF_XIN_HALT_CTRL0", "0xaeb0190"),
        ("VBIF_XIN_HALT_CTRL1", "0xaeb0194"),
        ("VBIF_XIN_HALT_ACK0", "0xaeb0200"),
        ("VBIF_XIN_HALT_ACK1", "0xaeb0204"),
        ("VBIF_QOS_REMAP_00", "0xaeb00b0"),
    ],
    "V1": [
        ("VBIF_MEMTYPE_0", "0xaeb0020"),
        ("VBIF_MEMTYPE_1", "0xaeb0024"),
        ("VBIF_QOS_REMAP_20", "0xaeb0550"),
        ("VBIF_QOS_REMAP_24", "0xaeb0590"),
    ],
    "R0": [
        ("RSC_SEQ_BUSY_DRV0", "0xaf20404"),
        ("RSC_SEQ_PC_DRV0", "0xaf20408"),
        ("RSC_ERROR_IRQ_STATUS", "0xaf200d0"),
        ("RSC_SOLVER_OVERRIDE_CTRL", "0xaf20c14"),
        ("RSC_MODES_ENABLED", "0xaf20c20"),
        ("RSC_SOLVER_STATUS0", "0xaf20c24"),
        ("RSC_SOLVER_STATUS1", "0xaf20c28"),
        ("RSC_SOLVER_STATUS2", "0xaf20c2c"),
    ],
    "R1": [
        ("RSC_AMC_MODE_IRQ", "0xaf21c00"),
        ("RSC_TCS_CONTROL", "0xaf21c14"),
        ("RSC_WRAPPER_CTRL", "0xaf30000"),
        ("RSC_WRAPPER_OVERRIDE_CTRL", "0xaf30004"),
        ("RSC_WRAPPER_POWER_CTRL", "0xaf30024"),
        ("RSC_WRAPPER_BW_INDICATION", "0xaf30048"),
    ],
}

LINE_RE = re.compile(
    r"\b(?:P424|TG424)\s+(?P<group>T|D|C0|C1|P|V0|V1|R0|R1)\s+(?P<body>.*)$"
)


def parse(path: Path) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for raw in path.read_text(errors="replace").splitlines():
        m = LINE_RE.search(raw)
        if not m:
            continue
        group = m.group("group")
        body = m.group("body").strip()
        if group == "T":
            n = re.search(r"ns=(\d+)", body)
            if n:
                out["T"] = [int(n.group(1))]
            continue
        vals = []
        for tok in body.split():
            tok = tok.strip().rstrip(",")
            try:
                vals.append(int(tok, 16))
            except ValueError:
                pass
        if vals:
            out[group] = vals
    return out


def fmt(v: int) -> str:
    return f"0x{v:08x}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diff Phase424 GKI P424 snapshot against TouchGrass TG424 snapshot"
    )
    ap.add_argument("golden", type=Path, help="TouchGrass /proc/a52_phase424g or dmesg text")
    ap.add_argument("gki", type=Path, help="GKI decoded sequential recorder text containing P424")
    ns = ap.parse_args()

    golden = parse(ns.golden)
    gki = parse(ns.gki)

    missing = [g for g in GROUPS if g not in golden or g not in gki]
    if missing:
        print("WARNING: missing paired groups:", ", ".join(missing))

    rows = []
    for group, names in GROUPS.items():
        gv = golden.get(group, [])
        kv = gki.get(group, [])
        for i, (name, addr) in enumerate(names):
            if i >= len(gv) or i >= len(kv):
                rows.append((group, name, addr, None, None))
            else:
                rows.append((group, name, addr, gv[i], kv[i]))

    changed = [r for r in rows if r[3] is not None and r[4] is not None and r[3] != r[4]]
    same = [r for r in rows if r[3] is not None and r[4] is not None and r[3] == r[4]]

    print(f"Compared {len(same) + len(changed)} registers: {len(same)} SAME, {len(changed)} DIFFERENT")
    if "T" in golden:
        print(f"Golden snapshot ns: {golden['T'][0]}")
    if "T" in gki:
        print(f"GKI snapshot ns:    {gki['T'][0]}")

    print("\nDIFFERENCES")
    if not changed:
        print("(none)")
    for group, name, addr, a, b in changed:
        xor = a ^ b
        print(
            f"{group:>2} {name:<32} {addr:<10} "
            f"TG={fmt(a)} GKI={fmt(b)} XOR={fmt(xor)}"
        )

    print("\nMATCHES")
    for group, name, addr, a, b in same:
        print(f"{group:>2} {name:<32} {addr:<10} {fmt(a)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
