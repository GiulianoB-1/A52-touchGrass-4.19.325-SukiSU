#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

BASE_PHYS = 0xB1B00000
FIXED_PHYS = 0xB1BFFE00
COPY_BYTES = 0x100
OFFSETS = (FIXED_PHYS - BASE_PHYS, FIXED_PHYS - BASE_PHYS + COPY_BYTES)
MAGIC = 0x353833594B435453
COMMIT = 0x385B0DE5
VERSION = 1


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def cstr(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


def s32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def u64(buf: bytes, off: int) -> int:
    return struct.unpack_from("<Q", buf, off)[0]


def decode(buf: bytes, offset: int) -> dict:
    r = buf[offset:offset + COPY_BYTES]
    if len(r) != COPY_BYTES:
        return {"offset": hex(offset), "valid": False, "error": "short copy"}

    stored_crc = u32(r, 240)
    calc_crc = crc32c(r[:240])
    usb_diag = [s32(r, 100 + i * 4) for i in range(9)]
    out = {
        "offset": hex(offset),
        "physical": hex(BASE_PHYS + offset),
        "magic": hex(u64(r, 0)),
        "seq": u64(r, 8),
        "ns": u64(r, 16),
        "queue": hex(u64(r, 24)),
        "freeze_age_ms": u64(r, 32),
        "ufs_irq_count": u64(r, 40),
        "ufs_outstanding": hex(u64(r, 48)),
        "flags": hex(u32(r, 56)),
        "frontier_sample": u32(r, 60),
        "frontier_ms": u32(r, 64),
        "freeze_slot": s32(r, 68),
        "freeze_depth": s32(r, 72),
        "freeze_nr": u32(r, 76),
        "freeze_zero": u32(r, 80),
        "freeze_event": u32(r, 84),
        "ufs_sample": u32(r, 88),
        "ufs_doorbell": hex(u32(r, 92)),
        "usb_pullup_capable": u32(r, 96),
        "usb_diag": {
            "mode_in": usb_diag[0],
            "hw_mode": usb_diag[1],
            "mode_out": usb_diag[2],
            "core_mode": usb_diag[3],
            "gadget_init": usb_diag[4],
            "gadget_add_rc": usb_diag[5],
            "g_serial_probe_rc": usb_diag[6],
            "udc_bind_rc": usb_diag[7],
            "pullup_rc": usb_diag[8],
        },
        "udc": cstr(r[136:160]),
        "gadget": cstr(r[160:184]),
        "parent": cstr(r[184:208]),
        "driver": cstr(r[208:232]),
        "ofsimple_ret": s32(r, 232),
        "ofsimple_stage": hex(u32(r, 236)),
        "stored_crc32c": hex(stored_crc),
        "calculated_crc32c": hex(calc_crc),
        "commit": hex(u32(r, 244)),
        "version": u32(r, 248),
    }
    out["valid"] = (
        u64(r, 0) == MAGIC
        and stored_crc == calc_crc
        and u32(r, 244) == COMMIT
        and u32(r, 248) == VERSION
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Decode Phase385b fixed sticky copies from the frozen 1 MiB A52 ramoops image"
    )
    ap.add_argument("image", type=Path)
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args()

    data = ns.image.read_bytes()
    if len(data) < 0x100000:
        raise SystemExit(f"expected a 1 MiB frozen image, got {len(data)} bytes")

    copies = [decode(data, off) for off in OFFSETS]
    if ns.json:
        print(json.dumps(copies, indent=2, sort_keys=True))
    else:
        for i, rec in enumerate(copies):
            print(f"=== Phase385b fixed copy {i} @ {rec['physical']} ===")
            print(json.dumps(rec, indent=2, sort_keys=True))
        valid = [r for r in copies if r.get("valid")]
        if valid:
            best = max(valid, key=lambda r: r["seq"])
            print("=== newest valid copy ===")
            print(json.dumps(best, indent=2, sort_keys=True))
        else:
            print("No CRC-valid Phase385b fixed copy found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
