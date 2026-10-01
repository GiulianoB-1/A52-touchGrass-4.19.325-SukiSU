#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import struct
from pathlib import Path

DEBUG_OFFSET = 0x00800000
REGION_BYTES = 0x00100000
HEADER_BYTES = 0x1000
REC_BYTES = 128
MAGIC = 0x3535314453453241
HDR_COMMIT = 0x155B0071
REC_COMMIT = 0x155C0DE5

HDR = struct.Struct("<QIIQQIIIIiIIIII")
REC = struct.Struct("<QQQIHHI16s68sII")

def decode_region(blob: bytes, source: str) -> dict:
    if len(blob) < REGION_BYTES:
        blob = blob + bytes(REGION_BYTES - len(blob))
    blob = blob[:REGION_BYTES]

    vals = HDR.unpack_from(blob, 0)
    keys = (
        "magic", "version", "phase", "boot_id", "global_seq", "count",
        "capacity", "record_bytes", "disk_armed", "disk_last_rc",
        "disk_gen", "disk_written_gen", "disk_retry", "dropped", "commit",
    )
    header = dict(zip(keys, vals))
    valid_header = (
        header["magic"] == MAGIC and
        header["version"] == 1 and
        header["phase"] == 155 and
        header["record_bytes"] == REC_BYTES and
        header["commit"] == HDR_COMMIT
    )

    records = []
    max_count = min((REGION_BYTES - HEADER_BYTES) // REC_BYTES,
                    int(header["capacity"] or 0) if valid_header else
                    (REGION_BYTES - HEADER_BYTES) // REC_BYTES)
    count = min(int(header["count"]), max_count) if valid_header else max_count

    for i in range(count):
        off = HEADER_BYTES + i * REC_BYTES
        v = REC.unpack_from(blob, off)
        seq, ts_ns, boot_id, phase, cpu, text_len, pid, comm, text, commit, reserved = v
        if commit != REC_COMMIT or phase != 155 or not seq:
            continue
        comm_s = comm.split(b"\0", 1)[0].decode("utf-8", "replace")
        text_s = text[:min(text_len, len(text))].split(b"\0", 1)[0].decode("utf-8", "replace")
        records.append({
            "source": source,
            "index": i,
            "offset": off,
            "seq": seq,
            "ts_ns": ts_ns,
            "ts_s": ts_ns / 1e9,
            "boot_id": boot_id,
            "cpu": cpu,
            "pid": pid,
            "comm": comm_s,
            "text": text_s,
        })

    return {
        "source": source,
        "valid_header": valid_header,
        "header": header,
        "records": records,
    }

def load_debug(path: Path) -> bytes:
    b = path.read_bytes()
    if len(b) < DEBUG_OFFSET + REGION_BYTES:
        raise SystemExit(f"debug.bin too small: {len(b)} bytes")
    return b[DEBUG_OFFSET:DEBUG_OFFSET + REGION_BYTES]

def fuse(decoded: list[dict]) -> tuple[list[dict], list[dict]]:
    by_seq: dict[int, list[dict]] = {}
    for d in decoded:
        for r in d["records"]:
            by_seq.setdefault(r["seq"], []).append(r)

    fused = []
    mismatches = []
    for seq in sorted(by_seq):
        copies = by_seq[seq]
        base = copies[0]
        sig = (base["ts_ns"], base["boot_id"], base["cpu"], base["pid"],
               base["comm"], base["text"])
        for other in copies[1:]:
            osig = (other["ts_ns"], other["boot_id"], other["cpu"],
                    other["pid"], other["comm"], other["text"])
            if osig != sig:
                mismatches.append({
                    "seq": seq,
                    "sources": [x["source"] for x in copies],
                    "copies": copies,
                })
                break
        out = dict(base)
        out["sources"] = ",".join(x["source"] for x in copies)
        fused.append(out)
    return fused, mismatches

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug-bin", type=Path)
    ap.add_argument("--ram-bin", type=Path)
    ap.add_argument("--out", type=Path, default=Path("p155-decoded"))
    ns = ap.parse_args()
    if not ns.debug_bin and not ns.ram_bin:
        raise SystemExit("provide --debug-bin and/or --ram-bin")

    decoded = []
    if ns.debug_bin:
        decoded.append(decode_region(load_debug(ns.debug_bin), "samsung-debug"))
    if ns.ram_bin:
        decoded.append(decode_region(ns.ram_bin.read_bytes(), "reserved-b1a"))

    fused, mismatches = fuse(decoded)
    ns.out.mkdir(parents=True, exist_ok=True)

    summary = {
        "phase": 155,
        "sources": [{
            "source": d["source"],
            "valid_header": d["valid_header"],
            "header": d["header"],
            "valid_records": len(d["records"]),
        } for d in decoded],
        "fused_records": len(fused),
        "first_seq": fused[0]["seq"] if fused else None,
        "last_seq": fused[-1]["seq"] if fused else None,
        "first_ts_s": fused[0]["ts_s"] if fused else None,
        "last_ts_s": fused[-1]["ts_s"] if fused else None,
        "mirror_mismatches": len(mismatches),
        "last_records": fused[-20:],
    }
    (ns.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ns.out / "mismatches.json").write_text(json.dumps(mismatches, indent=2) + "\n")

    with (ns.out / "events.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "seq", "ts_s", "boot_id", "cpu", "pid", "comm", "text", "sources"
        ])
        w.writeheader()
        for r in fused:
            w.writerow({k: r.get(k) for k in w.fieldnames})

    print(json.dumps(summary, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
