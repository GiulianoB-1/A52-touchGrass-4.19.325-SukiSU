#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import struct
from pathlib import Path

DEBUG_OFFSET = 0x800000
DISK_BYTES = 2 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
CAPACITY = (DISK_BYTES - HEADER_BYTES) // RECORD_BYTES

MAGIC = 0x3833334D32434553
VERSION = 1
PHASE = 383
REC_COMMIT = 0x38330DE5
HDR_COMMIT = 0x3833B007

STATUS_MAGIC = 0x3833535441545533
STATUS_VERSION = 1
STATUS_COMMIT = 0x3833C0DE
STATUS_OFFSETS = (0x100, 0x180, 0x200)
STATUS_BYTES = 128

HEADER = struct.Struct("<QIIQQIIIIIIII")
RECORD = struct.Struct("<QQQIHH88sII")
STATUS = struct.Struct("<QQ" + "II" * 13 + "II")

def slice_debug(data: bytes) -> bytes:
    if len(data) == DISK_BYTES:
        return data
    if len(data) >= DEBUG_OFFSET + DISK_BYTES:
        return data[DEBUG_OFFSET:DEBUG_OFFSET + DISK_BYTES]
    raise SystemExit(f"debug input too small: {len(data)}")

def inv_ok(v: int, inv: int, bits: int = 32) -> bool:
    mask = (1 << bits) - 1
    return ((v ^ inv) & mask) == mask

def majority3(a: bytes, b: bytes, c: bytes) -> bytes:
    return bytes(((x & y) | (x & z) | (y & z)) for x, y, z in zip(a, b, c))

def parse_header(data: bytes) -> dict:
    vals = HEADER.unpack_from(data, 0)
    keys = ("magic","version","phase","boot_id","global_seq","disk_count","ram_count",
            "disk_capacity","ram_capacity","record_bytes","state","dropped","commit")
    out = dict(zip(keys, vals))
    out["valid"] = bool(
        out["magic"] == MAGIC and out["version"] == VERSION and
        out["phase"] == PHASE and out["disk_capacity"] == CAPACITY and
        out["record_bytes"] == RECORD_BYTES and out["commit"] == HDR_COMMIT
    )
    return out

def parse_status(raw: bytes) -> dict:
    v = STATUS.unpack(raw)
    magic, magic_inv = v[:2]
    pairs = list(zip(v[2:28:2], v[3:28:2]))
    names = ("version","armed","worker_runs","open_rc_u32","submit_rc_u32","flush_rc_u32",
             "retry_count","disk_gen","written_gen","disk_count","last_page","state","commit")
    out = {"magic": magic, "magic_inv": magic_inv}
    valid = {}
    for name, (x, xi) in zip(names, pairs):
        out[name] = x
        out[name+"_inv"] = xi
        valid[name] = inv_ok(x, xi)
    def s32(x): return x - (1 << 32) if x & 0x80000000 else x
    out["open_rc"] = s32(out["open_rc_u32"])
    out["submit_rc"] = s32(out["submit_rc_u32"])
    out["flush_rc"] = s32(out["flush_rc_u32"])
    out["pair_valid"] = valid
    out["valid_pair_count"] = sum(valid.values()) + int(inv_ok(magic, magic_inv, 64))
    out["valid"] = bool(
        magic == STATUS_MAGIC and inv_ok(magic, magic_inv, 64) and
        out["version"] == STATUS_VERSION and valid["version"] and
        out["commit"] == STATUS_COMMIT and valid["commit"]
    )
    return out

def recover_status(data: bytes) -> dict:
    copies = [data[o:o+STATUS_BYTES] for o in STATUS_OFFSETS]
    parsed = [parse_status(x) for x in copies]
    fused = majority3(*copies)
    return {"copies": parsed, "majority": parse_status(fused)}

def parse_records(data: bytes, count: int, boot_id: int) -> list[dict]:
    out = []
    for i in range(min(max(count, 0), CAPACITY)):
        off = HEADER_BYTES + i * RECORD_BYTES
        seq, ts, rec_boot, phase, cpu, length, raw, commit, reserved = RECORD.unpack_from(data, off)
        if commit != REC_COMMIT or phase != PHASE or rec_boot != boot_id:
            continue
        length = min(int(length), len(raw))
        text = raw[:length].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        out.append({"slot": i, "seq": seq, "ts_ns": ts, "cpu": cpu, "text": text})
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", type=Path, required=True)
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    data = slice_debug(ns.debug.read_bytes())
    hdr = parse_header(data)
    status = recover_status(data)
    records = parse_records(data, int(hdr["disk_count"]), int(hdr["boot_id"])) if hdr["valid"] else []
    result = {"header": hdr, "status": status, "records": records}

    st = status["majority"]
    print(f'Phase383 header_valid={hdr["valid"]} boot_id={hdr["boot_id"]} count={hdr["disk_count"]} records={len(records)}')
    print('status majority valid=%s pairs=%u/14 armed=%u runs=%u open_rc=%d submit_rc=%d flush_rc=%d retries=%u gen=%u written=%u count=%u page=%u state=%u' % (
        st["valid"], st["valid_pair_count"], st["armed"], st["worker_runs"],
        st["open_rc"], st["submit_rc"], st["flush_rc"], st["retry_count"],
        st["disk_gen"], st["written_gen"], st["disk_count"], st["last_page"], st["state"]))
    for r in records:
        print(f'{r["seq"]:06d} {r["ts_ns"]/1e9:12.6f}s cpu={r["cpu"]} {r["text"]}')

    if ns.json:
        ns.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if hdr["valid"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
