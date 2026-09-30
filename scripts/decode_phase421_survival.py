#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

MIB = 1024 * 1024
B190_IN_11M = 9 * MIB
B1A_IN_11M = 10 * MIB
OFF0 = 0x500
OFF1 = 0xA80
OFF2 = MIB + 0x800
SLOT_BYTES = 128
SLOTS = 11
STAGE_BASE = 0xD0
FORMAT = 0xA1
COMMIT = 0x421C0DE5

V_FLAGS = 1 << 0
V_RC = 1 << 1
V_IRQ = 1 << 2
V_RET = 1 << 3

STAGE_NAMES = [
    "S00 exact F0 entered",
    "S01 after setup_tx_mode",
    "S02 validate_tx_mode returned",
    "S03 mipi_dsi_create_packet returned",
    "S04 copy_and_pad returned",
    "S05 before video-done / IRQ setup",
    "S06 DMA_DONE armed / completion reset",
    "S07 DMA programmed, before SW_TRIGGER",
    "S08 SW_TRIGGER write returned",
    "S09 DMA_DONE ISR reached",
    "S10 completion wait returned",
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
        ones = sum((v >> bit) & 1 for v in values)
        if ones > threshold:
            out |= 1 << bit
    return out


def bit_or(values: list[int]) -> int:
    out = 0
    for value in values:
        out |= value
    return out


def signed(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    mask = (1 << bits) - 1
    value &= mask
    return value - (1 << bits) if value & sign else value


def choose_region(raw: bytes) -> tuple[bytes, list[tuple[str, int]]]:
    # Normalize offsets so B1900000 is offset 0 and B1A00000 is +1 MiB.
    if len(raw) >= B1A_IN_11M + MIB:
        region = raw[B190_IN_11M:B1A_IN_11M + MIB]
        copies = [
            ("B190+0500", OFF0),
            ("B190+0A80", OFF1),
            ("B1A+0800", OFF2),
        ]
        return region, copies
    if len(raw) >= 2 * MIB:
        region = raw[:2 * MIB]
        copies = [
            ("B190+0500", OFF0),
            ("B190+0A80", OFF1),
            ("B1A+0800", OFF2),
        ]
        return region, copies
    if len(raw) >= MIB:
        region = raw[:MIB]
        copies = [
            ("B190+0500", OFF0),
            ("B190+0A80", OFF1),
        ]
        return region, copies
    raise SystemExit(
        f"need >=1 MiB B190 dump, 2 MiB B190+B1A dump, or >=11 MiB reserved dump; got {len(raw)} bytes"
    )


def u8s(rec: bytes, off: int, count: int) -> list[int]:
    return list(rec[off:off + count])


def s16s(rec: bytes, off: int, count: int) -> list[int]:
    return [struct.unpack_from("<H", rec, off + i * 2)[0] for i in range(count)]


def u32s(rec: bytes, off: int, count: int) -> list[int]:
    return [struct.unpack_from("<I", rec, off + i * 4)[0] for i in range(count)]


def slot_samples(rec: bytes) -> dict:
    stored_crc, commit = struct.unpack_from("<II", rec, 120)
    return {
        "stage": u8s(rec, 0, 32) + u8s(rec, 67, 32),
        "flags": u8s(rec, 32, 5),
        "rc": s16s(rec, 37, 5),
        "nonce": u32s(rec, 47, 5),
        "irq": u8s(rec, 99, 5),
        "ret": s16s(rec, 104, 5),
        "valid": u8s(rec, 114, 5),
        "format": rec[119],
        "stored_crc": stored_crc,
        "commit": commit,
        "crc_ok": (
            rec[119] == FORMAT
            and commit == COMMIT
            and crc32c(rec[:120]) == stored_crc
        ),
    }


def decode_unsigned(samples: list[int], bits: int) -> dict:
    maj = bit_majority(samples, bits)
    orm = bit_or(samples) & ((1 << bits) - 1)
    return {
        "majority": maj,
        "or_1to0": orm,
        "agree": maj == orm,
        "samples": len(samples),
    }


def decode_signed(samples: list[int], bits: int) -> dict:
    d = decode_unsigned(samples, bits)
    d["majority_signed"] = signed(d["majority"], bits)
    d["or_1to0_signed"] = signed(d["or_1to0"], bits)
    return d


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Decode Phase421 corruption-tolerant DSI survival slots"
    )
    ap.add_argument("dump", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    region, copy_layout = choose_region(args.dump.read_bytes())
    rows = []

    for stage in range(SLOTS):
        physical = []
        for name, base in copy_layout:
            off = base + stage * SLOT_BYTES
            if off + SLOT_BYTES > len(region):
                continue
            rec = region[off:off + SLOT_BYTES]
            physical.append((name, slot_samples(rec)))

        stage_samples: list[int] = []
        flags_samples: list[int] = []
        rc_samples: list[int] = []
        nonce_samples: list[int] = []
        irq_samples: list[int] = []
        ret_samples: list[int] = []
        valid_samples: list[int] = []
        crc_valid = []

        for name, p in physical:
            stage_samples.extend(p["stage"])
            flags_samples.extend(p["flags"])
            rc_samples.extend(p["rc"])
            nonce_samples.extend(p["nonce"])
            irq_samples.extend(p["irq"])
            ret_samples.extend(p["ret"])
            valid_samples.extend(p["valid"])
            if p["crc_ok"]:
                crc_valid.append(name)

        ds = decode_unsigned(stage_samples, 8)
        expected = STAGE_BASE + stage
        stage_majority_match = ds["majority"] == expected
        stage_or_match = ds["or_1to0"] == expected

        # Unwritten slots are zero-cleared. Require at least one independent
        # decode model to identify the legal per-slot stage code.
        if not stage_majority_match and not stage_or_match and not crc_valid:
            continue

        flags_d = decode_unsigned(flags_samples, 8)
        rc_d = decode_signed(rc_samples, 16)
        nonce_d = decode_unsigned(nonce_samples, 32)
        irq_d = decode_unsigned(irq_samples, 8)
        ret_d = decode_signed(ret_samples, 16)
        valid_d = decode_unsigned(valid_samples, 8)

        if stage_majority_match and stage_or_match:
            confidence = "high"
        elif stage_majority_match or stage_or_match:
            confidence = "medium"
        else:
            confidence = "crc-only"

        row = {
            "stage": stage,
            "stage_name": STAGE_NAMES[stage],
            "expected_stage_code": expected,
            "stage_decode": ds,
            "stage_majority_match": stage_majority_match,
            "stage_or_match": stage_or_match,
            "confidence": confidence,
            "physical_copies": [name for name, _ in physical],
            "crc_valid_copies": crc_valid,
            "nonce": nonce_d,
            "valid_mask": valid_d,
            "flags": flags_d,
            "rc": rc_d,
            "dma_irq_trig": irq_d,
            "ret": ret_d,
        }
        rows.append(row)

    highest = max((r["stage"] for r in rows), default=None)

    # Nonce is repeated in every reached slot. Aggregate all stage-level raw
    # nonce samples again to get a boot identity with much higher redundancy.
    global_nonce_samples: list[int] = []
    for stage in range(SLOTS):
        if not any(r["stage"] == stage for r in rows):
            continue
        for _name, base in copy_layout:
            off = base + stage * SLOT_BYTES
            if off + SLOT_BYTES <= len(region):
                global_nonce_samples.extend(
                    u32s(region[off:off + SLOT_BYTES], 47, 5)
                )
    global_nonce = decode_unsigned(global_nonce_samples, 32) if global_nonce_samples else None

    summary = {
        "records": len(rows),
        "highest_stage": highest,
        "highest_stage_name": STAGE_NAMES[highest] if highest is not None else None,
        "copy_layout": [name for name, _ in copy_layout],
        "global_nonce": global_nonce,
        "records_detail": rows,
    }

    print(json.dumps({
        "records": summary["records"],
        "highest_stage": summary["highest_stage"],
        "highest_stage_name": summary["highest_stage_name"],
        "copy_layout": summary["copy_layout"],
        "global_nonce": summary["global_nonce"],
    }, indent=2))

    for r in rows:
        flags = r["flags"]
        rc = r["rc"]
        irq = r["dma_irq_trig"]
        ret = r["ret"]
        nonce = r["nonce"]
        print(
            f'{r["stage"]:02d} {r["stage_name"]:<38} '
            f'conf={r["confidence"]:<6} crc={len(r["crc_valid_copies"])}/{len(r["physical_copies"])} '
            f'stage=0x{r["stage_decode"]["majority"]:02x}/0x{r["stage_decode"]["or_1to0"]:02x} '
            f'nonce=0x{nonce["majority"]:08x}/0x{nonce["or_1to0"]:08x} '
            f'flags=0x{flags["majority"]:02x}/0x{flags["or_1to0"]:02x} '
            f'rc={rc["majority_signed"]}/{rc["or_1to0_signed"]} '
            f'irq={irq["majority"]}/{irq["or_1to0"]} '
            f'ret={ret["majority_signed"]}/{ret["or_1to0_signed"]}'
        )

    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
