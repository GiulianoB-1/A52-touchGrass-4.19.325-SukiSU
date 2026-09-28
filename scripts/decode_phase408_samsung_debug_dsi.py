#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

RECORD_BYTES = 4096
DEBUG_OFFSET = 0x009FF000
MAGIC = 0x3830344953443241
COMMIT = 0x408C0DE5
PAYLOAD_BYTES = 744
SAMPLE_OFFSET = 104
SAMPLE_BYTES = 80
MAX_SAMPLES = 8

FINAL_NAMES = [
    "status", "fifo", "clk_ctrl", "clk_status", "int_ctrl", "lane_status",
    "dma_ctrl", "dma_offset", "dma_length", "sw_trigger", "trig_ctrl",
    "ack_err", "timeout", "phy_err", "axi2ahb", "dbg171",
]

SAMPLE_NAMES = [
    "point", "status", "fifo", "clk_ctrl", "clk_status", "int_ctrl",
    "lane_status", "dma_ctrl", "dma_offset", "dma_length", "sw_trigger",
    "trig_ctrl", "ack_err", "timeout", "phy_err", "axi2ahb", "dbg171",
]


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def select_record(raw: bytes) -> bytes:
    if len(raw) == RECORD_BYTES:
        return raw
    if len(raw) >= DEBUG_OFFSET + RECORD_BYTES:
        return raw[DEBUG_OFFSET:DEBUG_OFFSET + RECORD_BYTES]
    raise SystemExit(
        f"need either a 4096-byte Phase408 page or a debug partition image "
        f"covering offset 0x{DEBUG_OFFSET:x}; got {len(raw)} bytes"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Phase408 single-copy Samsung debug DSI record")
    ap.add_argument("image", type=Path)
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    page = select_record(ns.image.read_bytes())
    stored_crc, commit = struct.unpack_from("<II", page, RECORD_BYTES - 8)
    calc_crc = crc32c(page[:RECORD_BYTES - 8])

    magic, version, phase, capture_ns = struct.unpack_from("<QIIQ", page, 0)
    sample_count, wait_ret, dma_irq = struct.unpack_from("<III", page, 24)

    genuine = (
        magic == MAGIC and
        version == 1 and
        phase == 408 and
        commit == COMMIT and
        stored_crc == calc_crc
    )

    result = {
        "genuine": genuine,
        "magic": f"0x{magic:016x}",
        "version": version,
        "phase": phase,
        "capture_ns": capture_ns,
        "sample_count": sample_count,
        "wait_ret": wait_ret,
        "dma_irq_trig": dma_irq,
        "stored_crc32c": f"0x{stored_crc:08x}",
        "calculated_crc32c": f"0x{calc_crc:08x}",
        "commit": f"0x{commit:08x}",
    }

    finals = struct.unpack_from("<16I", page, 36)
    result["final"] = dict(zip(FINAL_NAMES, finals))

    samples = []
    n = min(sample_count, MAX_SAMPLES)
    for i in range(n):
        off = SAMPLE_OFFSET + i * SAMPLE_BYTES
        ns_val = struct.unpack_from("<Q", page, off)[0]
        vals = struct.unpack_from("<17I", page, off + 8)
        row = {"index": i, "ns": ns_val}
        row.update(dict(zip(SAMPLE_NAMES, vals)))
        samples.append(row)
    result["samples"] = samples

    print(f"Phase408 genuine={genuine}")
    print(
        f"magic=0x{magic:016x} version={version} phase={phase} "
        f"crc=0x{stored_crc:08x}/0x{calc_crc:08x} commit=0x{commit:08x}"
    )
    print(
        f"capture={capture_ns}ns samples={sample_count} "
        f"wait_ret={wait_ret} dma_irq={dma_irq}"
    )
    print("final " + " ".join(f"{k}=0x{v:x}" for k, v in result["final"].items()))

    if samples:
        base = samples[0]["ns"]
        for s in samples:
            delta_us = (s["ns"] - base) / 1000.0
            print(
                f'p{s["point"]} +{delta_us:9.3f}us '
                f'st=0x{s["status"]:x} fifo=0x{s["fifo"]:x} '
                f'int=0x{s["int_ctrl"]:x} clk=0x{s["clk_status"]:x} '
                f'dma=0x{s["dma_ctrl"]:x} off=0x{s["dma_offset"]:x} '
                f'len=0x{s["dma_length"]:x} sw=0x{s["sw_trigger"]:x} '
                f'ack=0x{s["ack_err"]:x} to=0x{s["timeout"]:x} '
                f'phy=0x{s["phy_err"]:x} dbg171=0x{s["dbg171"]:x}'
            )

    if ns.json:
        ns.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    return 0 if genuine else 2


if __name__ == "__main__":
    raise SystemExit(main())
