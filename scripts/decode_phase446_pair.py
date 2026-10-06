#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0x363434504D4F4346
REC_COMMIT = 0x446C0DE5
HDR_COMMIT = 0x446B0071
HDR_BYTES = 4096
REC_BYTES = 508

EVENTS = {
    0x000: "INIT",
    0x001: "BOOT_CB",
    0x002: "SMMU_CHILD",
    0x003: "DRM_BIND",
    0x004: "POWER_INIT_PRE",
    0x005: "POWER_INIT_POST",
    0x006: "KMS_MMU_PRE",
    0x007: "KMS_MMU_POST",
    0x008: "S2CR_PRE",
    0x009: "S2CR_POST",
    0x00A: "SPLASH_MAP_PRE",
    0x00B: "SPLASH_MAP_POST",
    0x00C: "EARLY_MAP_PRE",
    0x00D: "EARLY_MAP_POST",
    0x00E: "VBIF_INIT",
    0x00F: "IRQ_PREINSTALL",
    0x010: "CONT_SPLASH",
    0x040: "SPLASH_BUS_PRE",
    0x041: "SPLASH_BUS_POST",
    0x042: "TG_CLK_GET_PRE",
    0x043: "TG_CLK_GET_POST",
    0x044: "TG_CLK_ENABLE_PRE",
    0x045: "TG_CLK_ENABLE_POST",
    0x050: "BUS_HANDOFF_NODE",
    0x052: "BUS_LATE_BEGIN",
    0x053: "BUS_LATE_REMOVE",
    0x054: "BUS_LATE_DONE",
    0x060: "BUS_QUOTA_PRE",
    0x061: "BUS_QUOTA_POST",
    0x062: "BUS_QUOTA_STUB",
    0x070: "TG_APPS_SMMU_HANDOFF",
    0x071: "TG_ALLOC_CB",
    0x072: "TG_MSM_SMMU_DOMAIN_PRE",
    0x073: "TG_MSM_SMMU_DOMAIN_POST",
    0x074: "TG_CB_WRITE_PRE",
    0x075: "TG_CB_WRITE_POST",
    0x078: "TG_VBIF_POST",
    0x079: "TG_RSC_INIT",
    0x080: "TG_DSI_SPLASH_ENTRY",
    0x081: "TG_DSI_PM_PRE",
    0x082: "TG_DSI_PM_POST",
    0x083: "TG_DSI_ISR_PRE",
    0x084: "TG_DSI_ISR_POST",
    0x085: "TG_DSI_CLK_PRE",
    0x086: "TG_DSI_CLK_POST",
    0x087: "TG_DSI_VREG_PRE",
    0x088: "TG_DSI_VREG_POST",
    0x089: "TG_DSI_HOST_PRE",
    0x08A: "TG_DSI_HOST_POST",
    0x090: "TG_SPLASH_PANEL_PRE",
    0x091: "TG_F0_TERMINAL",
    0x100: "PERIODIC",
    0x120: "H00_FIRST_SAFE", 0x121: "H01_CONT_SPLASH_RES_PRE",
    0x122: "H02_CONT_SPLASH_RES_POST", 0x123: "H03_IOMMU_ATTACH_PRE",
    0x124: "H04_IOMMU_ATTACH_POST", 0x125: "H05_SPLASH_MAP_POST",
    0x126: "H06_EARLY_MAP_M_POST", 0x127: "H07_DRM_BIND_EXIT",
    0x128: "H08_PLANE_ATOMIC_UPDATE", 0x129: "H09_SET_SCANOUT",
    0x12A: "H10_CLEAR_BLENDSTAGES", 0x12B: "H11_SETUP_BLENDSTAGE",
    0x12C: "H12_BRIDGE_PRE_ENABLE", 0x12D: "H13_PANEL_ENABLE_PRE",
    0x130: "C_KMS_BLOCKS_ENTER",
    0x131: "C_MMU_INIT_PRE", 0x132: "C_MMU_INIT_POST",
    0x133: "C_REGDMA_PRE", 0x134: "C_REGDMA_POST",
    0x135: "C_RM_INIT_PRE", 0x136: "C_RM_INIT_POST",
    0x137: "C_HW_INTR_PRE", 0x138: "C_HW_INTR_POST",
    0x139: "C_VBIF_INIT_PRE", 0x13A: "C_VBIF_INIT_POST",
    0x13B: "C_SID_INIT_PRE", 0x13C: "C_SID_INIT_POST",
    0x13D: "C_DRM_OBJ_PRE", 0x13E: "C_DRM_OBJ_POST",
    0x140: "C_HW_BLOCKS_DONE",
    0x141: "C_POST_ENABLE_ENTER",
    0x142: "C_IRQ_UPDATE_PRE", 0x143: "C_IRQ_UPDATE_POST",
    0x144: "C_MEMTYPE_PRE", 0x145: "C_MEMTYPE_POST",
    0x146: "C_UBWC_RESET_PRE", 0x147: "C_UBWC_RESET_POST",
    0x148: "C_SID_ROT_PRE", 0x149: "C_SID_ROT_POST",
    0x14A: "C_LUTDMA_REMAP_PRE", 0x14B: "C_LUTDMA_REMAP_POST",
    0x14C: "C_FIRST_KICKOFF_SET",
    0x14D: "C_PM_QOS_PRE", 0x14E: "C_PM_QOS_POST",
    0x14F: "C_POST_ENABLE_EXIT",
    0x150: "C_KEEP_EARLYMAP",
    0x151: "C_SKIP_INITIAL_POST_ENABLE",
    0x152: "C_DBG_BUSES_PRE", 0x153: "C_DBG_BUSES_POST",
    0x154: "C_GET_MDP_PRE", 0x155: "C_GET_MDP_POST",
}


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def u64(b: bytes, off: int) -> int:
    return struct.unpack_from("<Q", b, off)[0]


@dataclass
class Header:
    count: int
    dropped: int
    record_bytes: int
    init_ns: int


@dataclass
class Rec:
    seq: int
    event: int
    ns: int
    flags: int
    aux0: int
    aux1: int
    frame: int
    pp_auto: int
    pp_line: int
    pp_out_line: int
    s2cr: int
    cb: int
    sctlr: int
    vig0_size: int
    vig0_src2: int
    vig0_addr: int
    vig0_fmt: int
    vig0_stride: int
    vig0_op: int
    sspp10_size: int
    sspp10_src2: int
    sspp10_addr: int
    sspp10_fmt: int
    sspp10_stride: int
    sspp10_op: int
    ctl_ext2: int
    ctl_start: int
    lm_op: int
    commit: int

    @property
    def safe_mdp(self) -> bool:
        return bool(self.flags & (1 << 1))

    @property
    def s2cr_cbndx(self) -> int | None:
        if not (self.flags & (1 << 5)):
            return None
        return self.s2cr & 0xFF

    @property
    def m_bit(self) -> int | None:
        if not (self.flags & (1 << 5)):
            return None
        return self.sctlr & 1


def parse(path: Path) -> tuple[Header, list[Rec]]:
    b = path.read_bytes()
    if len(b) < HDR_BYTES:
        raise SystemExit(f"{path}: only {len(b)} bytes, expected at least {HDR_BYTES}")

    magic = u64(b, 0)
    if magic != MAGIC:
        raise SystemExit(f"{path}: bad P446 magic 0x{magic:016x}")

    record_bytes = u32(b, 12)
    capacity = u32(b, 16)
    count = u32(b, 20)
    dropped = u32(b, 24)
    init_ns = u64(b, 36)
    hdr_commit = u32(b, 76)

    if record_bytes != REC_BYTES:
        raise SystemExit(f"{path}: record size {record_bytes}, expected {REC_BYTES}")
    if hdr_commit != HDR_COMMIT:
        print(f"warning: {path}: header commit 0x{hdr_commit:08x}, expected 0x{HDR_COMMIT:08x}")
    count = min(count, capacity, (len(b) - HDR_BYTES) // record_bytes)

    out: list[Rec] = []
    for i in range(count):
        off = HDR_BYTES + i * record_bytes
        r = b[off:off + record_bytes]
        if len(r) != record_bytes or u64(r, 0) != MAGIC:
            continue
        commit = u32(r, 504)
        if commit != REC_COMMIT:
            continue

        out.append(Rec(
            seq=u32(r, 16),
            event=u32(r, 20),
            flags=u32(r, 24),
            aux0=u32(r, 28),
            aux1=u32(r, 32),
            ns=u64(r, 8),
            frame=u32(r, 36),
            pp_auto=u32(r, 160),
            pp_line=u32(r, 164),
            pp_out_line=u32(r, 172),
            s2cr=u32(r, 444),
            cb=u32(r, 448),
            sctlr=u32(r, 452),
            # sspp[] is catalog order for lagoon:
            # [VIG0@0x5000, DMA0@0x25000, DMA1@0x27000, DMA2/SSPP10@0x29000].
            vig0_size=u32(r, 304),
            vig0_src2=u32(r, 308),
            vig0_addr=u32(r, 312),
            vig0_fmt=u32(r, 316),
            vig0_stride=u32(r, 320),
            vig0_op=u32(r, 324),
            sspp10_size=u32(r, 376),
            sspp10_src2=u32(r, 380),
            sspp10_addr=u32(r, 384),
            sspp10_fmt=u32(r, 388),
            sspp10_stride=u32(r, 392),
            sspp10_op=u32(r, 396),
            ctl_ext2=u32(r, 408),
            ctl_start=u32(r, 420),
            lm_op=u32(r, 428),
            commit=commit,
        ))

    return Header(count=count, dropped=dropped, record_bytes=record_bytes, init_ns=init_ns), out


def fmt_time(r: Rec | None, h: Header) -> str:
    if r is None:
        return "-"
    return f"{(r.ns - h.init_ns) / 1_000_000.0:9.3f}"


def fmt_hex(v: int | None, width: int = 8) -> str:
    return "-" if v is None else f"{v:0{width}x}"


def fmt_frame(r: Rec | None) -> str:
    if r is None:
        return "-"
    return fmt_hex(r.frame) if r.safe_mdp else "UNSAFE"


def fmt_vig0_addr(r: Rec | None) -> str:
    if r is None:
        return "-"
    return fmt_hex(r.vig0_addr) if r.safe_mdp else "UNSAFE"


def fmt_s10_addr(r: Rec | None) -> str:
    if r is None:
        return "-"
    return fmt_hex(r.sspp10_addr) if r.safe_mdp else "UNSAFE"


def fmt_ext2(r: Rec | None) -> str:
    if r is None:
        return "-"
    return fmt_hex(r.ctl_ext2) if r.safe_mdp else "UNSAFE"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Print GKI446 and TouchGrass446 heartbeat records side by side."
    )
    ap.add_argument("gki_raw", type=Path)
    ap.add_argument("tg_raw", type=Path)
    ap.add_argument("--events-only", action="store_true",
                    help="omit 0x100 periodic heartbeat rows")
    a = ap.parse_args()

    gh, gr = parse(a.gki_raw)
    th, tr = parse(a.tg_raw)

    if a.events_only:
        gr = [r for r in gr if r.event != 0x100]
        tr = [r for r in tr if r.event != 0x100]

    def index(rs: list[Rec]):
        d: dict[int, list[Rec]] = defaultdict(list)
        for r in rs:
            d[r.event].append(r)
        return d

    gd, td = index(gr), index(tr)
    rows = []
    for ev in sorted(set(gd) | set(td)):
        n = max(len(gd.get(ev, [])), len(td.get(ev, [])))
        for occ in range(n):
            g = gd.get(ev, [])[occ] if occ < len(gd.get(ev, [])) else None
            t = td.get(ev, [])[occ] if occ < len(td.get(ev, [])) else None
            gt = (g.ns - gh.init_ns) if g else 1 << 63
            tt = (t.ns - th.init_ns) if t else 1 << 63
            rows.append((min(gt, tt), ev, occ, g, t))

    rows.sort(key=lambda x: (x[0], x[1], x[2]))

    print(f"GKI: records={len(gr)}/{gh.count} dropped={gh.dropped}  TG: records={len(tr)}/{th.count} dropped={th.dropped}")
    print(
        "EVENT                    # | GKI_ms    TG_ms | "
        "GKI_AUX0/AUX1 TG_AUX0/AUX1 | GKI_FRAME TG_FRAME | GKI_CB TG_CB | GKI_M TG_M | "
        "GKI_VIG0_SRC0 TG_VIG0_SRC0 | GKI_S10_SRC0 TG_S10_SRC0 | GKI_EXT2 TG_EXT2 | GKI_START TG_START"
    )
    print("-" * 176)

    for _, ev, occ, g, t in rows:
        name = EVENTS.get(ev, f"EV_{ev:03x}")
        gcb = g.s2cr_cbndx if g else None
        tcb = t.s2cr_cbndx if t else None
        gm = g.m_bit if g else None
        tm = t.m_bit if t else None
        print(
            f"{name:<22} {occ:3d} | {fmt_time(g, gh)} {fmt_time(t, th)} | "
            f"{(f'{g.aux0:08x}/{g.aux1:08x}' if g else '-'):>17} {(f'{t.aux0:08x}/{t.aux1:08x}' if t else '-'):>17} | "
            f"{fmt_frame(g):>9} {fmt_frame(t):>9} | "
            f"{str(gcb) if gcb is not None else '-':>6} {str(tcb) if tcb is not None else '-':>5} | "
            f"{str(gm) if gm is not None else '-':>5} {str(tm) if tm is not None else '-':>4} | "
            f"{fmt_vig0_addr(g):>13} {fmt_vig0_addr(t):>13} | "
            f"{fmt_s10_addr(g):>12} {fmt_s10_addr(t):>12} | "
            f"{fmt_ext2(g):>8} {fmt_ext2(t):>8} | "
            f"{(f'{g.ctl_start:08x}' if g and g.safe_mdp else 'UNSAFE'):>8} {(f'{t.ctl_start:08x}' if t and t.safe_mdp else 'UNSAFE'):>8}"
        )


if __name__ == "__main__":
    main()
