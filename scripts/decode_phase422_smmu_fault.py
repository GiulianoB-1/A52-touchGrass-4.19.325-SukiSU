#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

MIB = 1024 * 1024
B190_IN_11M = 9 * MIB
B1A_IN_11M = 10 * MIB
OFF0 = 0x200
OFF1 = 0x300
OFF2 = MIB + 0xD80
SLOT_BYTES = 64
SLOTS = 4
MAGIC = 0xA52F4220A52F4220
COMMIT = 0x422C0DE5
FSR_SS = 1 << 30
SCTLR_CFCFG = 1 << 7

STAGE_NAMES = [
    "M00 IRQ registration + cmd_buffer_iova",
    "M01 SID 0x800 context fault",
    "M02 timeout SMMU snapshot",
    "M03 S08 SW_TRIGGER-return timestamp",
]


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def bit_majority(values: list[int], bits: int) -> int:
    if not values:
        return 0
    out = 0
    threshold = len(values) // 2
    for bit in range(bits):
        if sum((v >> bit) & 1 for v in values) > threshold:
            out |= 1 << bit
    return out


def bit_or(values: list[int], bits: int) -> int:
    out = 0
    for value in values:
        out |= value
    return out & ((1 << bits) - 1)


def signed(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def choose_region(raw: bytes) -> tuple[bytes, list[tuple[str, int]]]:
    if len(raw) >= B1A_IN_11M + MIB:
        region = raw[B190_IN_11M:B1A_IN_11M + MIB]
    elif len(raw) >= 2 * MIB:
        region = raw[:2 * MIB]
    elif len(raw) >= MIB:
        region = raw[:MIB]
    else:
        raise SystemExit(f"need >=1 MiB B190 dump or >=11 MiB reserved dump; got {len(raw)} bytes")

    copies = [("B190+0200", OFF0), ("B190+0300", OFF1)]
    if len(region) >= 2 * MIB:
        copies.append(("B1A+0D80", OFF2))
    return region, copies


def parse(rec: bytes) -> dict:
    magic, ts, a0, a1, a2, a3, stage, seq, stored_crc, commit = struct.unpack("<QQQQQQIIII", rec)
    return {
        "magic": magic,
        "ts_ns": ts,
        "arg0": a0,
        "arg1": a1,
        "arg2": a2,
        "arg3": a3,
        "stage": stage,
        "seq": seq,
        "stored_crc": stored_crc,
        "commit": commit,
        "crc_ok": magic == MAGIC and commit == COMMIT and crc32c(rec[:56]) == stored_crc,
    }


def fuse(samples: list[dict], key: str, bits: int) -> dict:
    vals = [s[key] for s in samples]
    maj = bit_majority(vals, bits)
    orm = bit_or(vals, bits)
    return {"majority": maj, "or_1to0": orm, "agree": maj == orm, "samples": vals}


def choose(d: dict) -> int:
    return d["majority"]


def h(v: int, width: int = 0) -> str:
    return f"0x{v:0{width}x}" if width else f"0x{v:x}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Phase422 SID 0x800 SMMU survival records")
    ap.add_argument("dump", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    region, layout = choose_region(args.dump.read_bytes())
    records = {}

    for stage in range(SLOTS):
        phys = []
        for name, base in layout:
            off = base + stage * SLOT_BYTES
            if off + SLOT_BYTES <= len(region):
                p = parse(region[off:off + SLOT_BYTES])
                p["copy"] = name
                phys.append(p)
        if not phys:
            continue

        fused = {k: fuse(phys, k, 64) for k in ("magic", "ts_ns", "arg0", "arg1", "arg2", "arg3")}
        fused.update({k: fuse(phys, k, 32) for k in ("stage", "seq", "commit")})
        crc_valid = [p["copy"] for p in phys if p["crc_ok"]]
        magic_ok = choose(fused["magic"]) == MAGIC or fused["magic"]["or_1to0"] == MAGIC
        stage_ok = choose(fused["stage"]) == stage or fused["stage"]["or_1to0"] == stage
        commit_ok = choose(fused["commit"]) == COMMIT or fused["commit"]["or_1to0"] == COMMIT

        if not crc_valid and not (magic_ok and stage_ok and commit_ok):
            continue

        records[stage] = {
            "stage": stage,
            "name": STAGE_NAMES[stage],
            "physical": phys,
            "crc_valid_copies": crc_valid,
            "fused": fused,
        }

    def fv(stage: int, key: str) -> int | None:
        if stage not in records:
            return None
        return choose(records[stage]["fused"][key])

    interpretation: dict[str, object] = {}
    cmd = fv(0, "arg0")
    if 0 in records:
        p = fv(0, "arg1") or 0
        request_ret_u = fv(0, "arg2") or 0
        interpretation["m00"] = {
            "cmd_buffer_iova": cmd,
            "sid": (p >> 48) & 0xFFFF,
            "cbndx": (p >> 40) & 0xFF,
            "irptndx": (p >> 32) & 0xFF,
            "linux_irq": p & 0xFFFFFFFF,
            "request_irq_ret": signed(request_ret_u, 64),
        }

    if 1 in records:
        far = fv(1, "arg0") or 0
        a1 = fv(1, "arg1") or 0
        a2 = fv(1, "arg2") or 0
        a3 = fv(1, "arg3") or 0
        fsr = a1 & 0xFFFFFFFF
        fsynr0 = (a1 >> 32) & 0xFFFFFFFF
        sctlr = a2 & 0xFFFFFFFF
        cbndx = (a2 >> 32) & 0xFFFFFFFF
        cbfrsynra = a3 & 0xFFFFFFFF
        irq = (a3 >> 32) & 0xFFFFFFFF
        command_exact = cmd is not None and far == cmd
        command_page = cmd is not None and (far >> 12) == (cmd >> 12)
        splash = 0xA0000000 <= far < 0xA2300000
        interpretation["m01"] = {
            "fault_count": fv(1, "seq"),
            "far": far,
            "fsr": fsr,
            "fsr_ss": bool(fsr & FSR_SS),
            "fsynr0": fsynr0,
            "sctlr": sctlr,
            "cfcfg": bool(sctlr & SCTLR_CFCFG),
            "cbndx": cbndx,
            "irq": irq,
            "cbfrsynra": cbfrsynra,
            "far_equals_cmd_buffer_iova": command_exact,
            "far_in_cmd_buffer_4k_page": command_page,
            "far_in_splash_range": splash,
        }

    if 2 in records:
        a2 = fv(2, "arg2") or 0
        a3 = fv(2, "arg3") or 0
        interpretation["m02"] = {
            "cmd_buffer_iova": fv(2, "arg0"),
            "far": fv(2, "arg1"),
            "fsr": a2 & 0xFFFFFFFF,
            "sctlr": (a2 >> 32) & 0xFFFFFFFF,
            "fault_count": a3 & 0xFFFFFFFF,
            "rpm_get_ret": signed((a3 >> 32) & 0xFFFFFFFF, 32),
        }

    if 3 in records:
        interpretation["m03"] = {"s08_ts_ns": fv(3, "ts_ns")}

    if 1 in records and 3 in records:
        m01_ts = fv(1, "ts_ns") or 0
        m03_ts = fv(3, "ts_ns") or 0
        interpretation["fault_minus_s08_ns"] = signed((m01_ts - m03_ts) & ((1 << 64) - 1), 64)

    verdict = "insufficient Phase422 records"
    m00 = interpretation.get("m00", {})
    m01 = interpretation.get("m01")
    m02 = interpretation.get("m02")
    if isinstance(m01, dict):
        if m01["far_in_cmd_buffer_4k_page"]:
            if m01["fsr_ss"] and m01["cfcfg"]:
                verdict = "SID 0x800 faulted the command buffer and the transaction was stalled"
            else:
                verdict = "SID 0x800 faulted the command buffer in non-stalled/terminate-mode evidence"
        elif m01["far_in_splash_range"]:
            verdict = "SID 0x800 faulted the splash/logo memory range, not the command buffer"
        else:
            verdict = "SID 0x800 context fault occurred, but FAR does not match command buffer or splash range"
    elif isinstance(m00, dict) and m00.get("request_irq_ret") != 0:
        verdict = "display context IRQ registration failed; zero handler count cannot clear SMMU"
    elif isinstance(m00, dict) and isinstance(m02, dict):
        if m02["rpm_get_ret"] < 0:
            verdict = "no handler fault recorded and timeout SMMU snapshot could not power/read the SMMU"
        elif m02["fsr"] != 0:
            verdict = "fault pending at timeout but handler did not record it: investigate context IRQ delivery"
        else:
            verdict = "no context fault recorded and timeout FSR is clean: SMMU fault theory is downgraded"

    summary = {
        "records_present": sorted(records),
        "copy_layout": [x[0] for x in layout],
        "interpretation": interpretation,
        "verdict": verdict,
        "records": records,
    }

    print("Phase422 records:", ", ".join(f"M{x:02d}" for x in sorted(records)) or "none")
    for stage in sorted(records):
        r = records[stage]
        f = r["fused"]
        print(f"M{stage:02d} {r['name']}: crc={len(r['crc_valid_copies'])}/{len(r['physical'])} "
              f"ts={h(choose(f['ts_ns']))} seq={choose(f['seq'])}")
    if "m00" in interpretation:
        x = interpretation["m00"]
        print(f"M00 cmd_iova={h(x['cmd_buffer_iova'])} sid={h(x['sid'])} cb={x['cbndx']} "
              f"irpt={x['irptndx']} irq={x['linux_irq']} request_ret={x['request_irq_ret']}")
    if "m01" in interpretation:
        x = interpretation["m01"]
        print(f"M01 far={h(x['far'])} fsr={h(x['fsr'],8)} ss={int(x['fsr_ss'])} "
              f"sctlr={h(x['sctlr'],8)} cfcfg={int(x['cfcfg'])} fsynr0={h(x['fsynr0'],8)} "
              f"cb={x['cbndx']} irq={x['irq']}")
        print(f"    cmd_exact={int(x['far_equals_cmd_buffer_iova'])} cmd_page={int(x['far_in_cmd_buffer_4k_page'])} "
              f"splash={int(x['far_in_splash_range'])}")
    if "fault_minus_s08_ns" in interpretation:
        print(f"fault - S08 = {interpretation['fault_minus_s08_ns']} ns")
    if "m02" in interpretation:
        x = interpretation["m02"]
        print(f"M02 fsr={h(x['fsr'],8)} far={h(x['far'])} sctlr={h(x['sctlr'],8)} "
              f"fault_count={x['fault_count']} rpm_ret={x['rpm_get_ret']}")
    print("VERDICT:", verdict)

    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
