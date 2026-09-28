#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

DEBUG_OFFSET = 0x00800000
DISK_BYTES = 2 * 1024 * 1024
HEADER_BYTES = 4096
RAM_BYTES = 1 * 1024 * 1024
HOT_OFFSET = HEADER_BYTES
COLD_OFFSET = HOT_OFFSET + RAM_BYTES
EVENT_BYTES = 96
EVENT_COMMIT = 0x409E0DE5
IMAGE_COMMIT = 0x409C0DE5
MAGIC = 0x3930344953443241

STAGE_NAMES = {
    1: "MATCH",
    2: "IRQ_PRE",
    3: "IRQ_POST",
    4: "DMA_PROGRAM",
    5: "TRIGGER_POST",
    6: "WAIT_ENTER",
    7: "ISR",
    8: "WAIT_EXIT",
    9: "FALLBACK",
    10: "FINAL",
    11: "P411_BEFORE_MODE",
    12: "P411_AFTER_MODE",
    13: "P411_ALIVE_1S",
    14: "P412_BOOTSTRAP",
}

REG_NAMES = [
    "status", "fifo", "clk_ctrl", "clk_status", "int_ctrl", "lane_status",
    "dma_ctrl", "dma_offset", "dma_length", "sw_trigger", "trig_ctrl",
    "ack_err", "timeout", "phy_err", "axi2ahb", "dbg171",
]


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def select_image(raw: bytes) -> bytes:
    if len(raw) == DISK_BYTES:
        return raw
    if len(raw) >= DEBUG_OFFSET + DISK_BYTES:
        return raw[DEBUG_OFFSET:DEBUG_OFFSET + DISK_BYTES]
    raise SystemExit(
        f"need a 2 MiB Phase409 image or a debug partition image covering "
        f"0x{DEBUG_OFFSET:x}..0x{DEBUG_OFFSET + DISK_BYTES - 1:x}; "
        f"got {len(raw)} bytes"
    )


def parse_event(buf: bytes, off: int) -> dict | None:
    if off + EVENT_BYTES > len(buf):
        return None
    ns, seq, stage, cpu = struct.unpack_from("<QIHH", buf, off)
    vals = struct.unpack_from("<20I", buf, off + 16)
    (
        status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
        dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl,
        ack_err, timeout, phy_err, axi2ahb, dbg171,
        aux0, aux1, reserved0, commit,
    ) = vals
    if commit != EVENT_COMMIT:
        return None
    return {
        "ns": ns,
        "seq": seq,
        "stage": stage,
        "stage_name": STAGE_NAMES.get(stage, f"STAGE_{stage}"),
        "cpu": cpu,
        "status": status,
        "fifo": fifo,
        "clk_ctrl": clk_ctrl,
        "clk_status": clk_status,
        "int_ctrl": int_ctrl,
        "lane_status": lane_status,
        "dma_ctrl": dma_ctrl,
        "dma_offset": dma_offset,
        "dma_length": dma_length,
        "sw_trigger": sw_trigger,
        "trig_ctrl": trig_ctrl,
        "ack_err": ack_err,
        "timeout": timeout,
        "phy_err": phy_err,
        "axi2ahb": axi2ahb,
        "dbg171": dbg171,
        "aux0": aux0,
        "aux1": aux1,
        "reserved0": reserved0,
    }


def parse_phase345(buf: bytes, count: int) -> list[dict]:
    rows = []
    base = COLD_OFFSET + 8
    for i in range(min(count, 8)):
        off = base + i * 80
        ns = struct.unpack_from("<Q", buf, off)[0]
        vals = struct.unpack_from("<17I", buf, off + 8)
        names = [
            "point", "status", "fifo", "clk_ctrl", "clk_status", "int_ctrl",
            "lane_status", "dma_ctrl", "dma_offset", "dma_length",
            "sw_trigger", "trig_ctrl", "ack_err", "timeout", "phy_err",
            "axi2ahb", "dbg171",
        ]
        row = {"index": i, "ns": ns}
        row.update(dict(zip(names, vals)))
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Phase409 1MiB RAM + 2MiB Samsung DSI image")
    ap.add_argument("image", type=Path)
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    image = select_image(ns.image.read_bytes())
    stored_crc, commit = struct.unpack_from("<II", image, DISK_BYTES - 8)
    calculated_crc = crc32c(image[:DISK_BYTES - 8])

    magic, version, phase, capture_ns = struct.unpack_from("<QIIQ", image, 0)
    fields = struct.unpack_from("<25I", image, 24)
    (
        ram_bytes, event_bytes, event_capacity, event_count, event_dropped,
        wait_ret, dma_irq_trig, phase345_count,
        final_status, final_fifo, final_clk_ctrl, final_clk_status,
        final_int_ctrl, final_lane_status, final_dma_ctrl, final_dma_offset,
        final_dma_length, final_sw_trigger, final_trig_ctrl, final_ack_err,
        final_timeout, final_phy_err, final_axi2ahb, final_dbg171,
        writer_state,
    ) = fields

    genuine = (
        magic == MAGIC and
        version == 1 and
        phase in (409, 411, 412) and
        ram_bytes == RAM_BYTES and
        event_bytes == EVENT_BYTES and
        commit == IMAGE_COMMIT and
        stored_crc == calculated_crc
    )

    events = []
    limit = min(event_count, event_capacity, RAM_BYTES // EVENT_BYTES)
    for i in range(limit):
        event = parse_event(image, HOT_OFFSET + i * EVENT_BYTES)
        if event is not None:
            events.append(event)

    samples345 = parse_phase345(image, phase345_count)

    result = {
        "genuine": genuine,
        "magic": f"0x{magic:016x}",
        "version": version,
        "phase": phase,
        "capture_ns": capture_ns,
        "ram_bytes": ram_bytes,
        "event_bytes": event_bytes,
        "event_capacity": event_capacity,
        "event_count": event_count,
        "valid_events": len(events),
        "event_dropped": event_dropped,
        "wait_ret": wait_ret,
        "dma_irq_trig": dma_irq_trig,
        "phase345_count": phase345_count,
        "writer_state": writer_state,
        "final": dict(zip(REG_NAMES, [
            final_status, final_fifo, final_clk_ctrl, final_clk_status,
            final_int_ctrl, final_lane_status, final_dma_ctrl, final_dma_offset,
            final_dma_length, final_sw_trigger, final_trig_ctrl, final_ack_err,
            final_timeout, final_phy_err, final_axi2ahb, final_dbg171,
        ])),
        "stored_crc32c": f"0x{stored_crc:08x}",
        "calculated_crc32c": f"0x{calculated_crc:08x}",
        "commit": f"0x{commit:08x}",
        "events": events,
        "phase345": samples345,
    }

    print(
        f"Phase409 genuine={genuine} phase={phase} "
        f"crc=0x{stored_crc:08x}/0x{calculated_crc:08x} "
        f"commit=0x{commit:08x}"
    )
    print(
        f"events={event_count} valid={len(events)} dropped={event_dropped} "
        f"wait_ret={wait_ret} dma_irq={dma_irq_trig} "
        f"phase345={phase345_count} writer_state={writer_state}"
    )

    if events:
        base = events[0]["ns"]
        for e in events:
            print(
                f'{e["seq"]:04d} +{(e["ns"] - base)/1000.0:10.3f}us '
                f'{e["stage_name"]:<12} cpu={e["cpu"]} '
                f'st=0x{e["status"]:x} int=0x{e["int_ctrl"]:x} '
                f'dma=0x{e["dma_ctrl"]:x} off=0x{e["dma_offset"]:x} '
                f'len=0x{e["dma_length"]:x} ack=0x{e["ack_err"]:x} '
                f'to=0x{e["timeout"]:x} aux=0x{e["aux0"]:x}/0x{e["aux1"]:x}'
            )

    if samples345:
        base = samples345[0]["ns"]
        print("Phase345 microsecond burst:")
        for s in samples345:
            print(
                f'  p{s["point"]} +{(s["ns"] - base)/1000.0:9.3f}us '
                f'st=0x{s["status"]:x} int=0x{s["int_ctrl"]:x} '
                f'clk=0x{s["clk_status"]:x} dma=0x{s["dma_ctrl"]:x} '
                f'dbg171=0x{s["dbg171"]:x}'
            )

    if ns.json:
        ns.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    return 0 if genuine else 2


if __name__ == "__main__":
    raise SystemExit(main())
