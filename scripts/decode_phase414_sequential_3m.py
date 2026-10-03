#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
import struct
from pathlib import Path

DEBUG_OFFSET = 0x800000
DISK_BYTES = 2 * 1024 * 1024
RAM_OFFSET_IN_11M = 0xA00000
RAM_BYTES = 1 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
DISK_CAP = (DISK_BYTES - HEADER_BYTES) // RECORD_BYTES
RAM_CAP = (RAM_BYTES - HEADER_BYTES) // RECORD_BYTES
MAGIC = 0x3431344D33434553
HDR_COMMIT = 0x414B0071
REC_COMMIT = 0x414C0DE5

# Phase416 persistent disk-transport status.  These three copies live in the
# otherwise-unused first 4 KiB header page at 0x100/0x180/0x200.
P416_STATUS_MAGIC = 0x3631345354415433
P416_STATUS_VERSION = 1
P416_STATUS_COMMIT = 0x416C0DE5
P416_STATUS_OFFSETS = (0x100, 0x180, 0x200)

HEADER = struct.Struct("<QIIQQIIIIIIII")
RECORD = struct.Struct("<QQQIHH88sII")
P416_STATUS = struct.Struct("<QQ" + "I" * 28)

# Only counters whose n= value is intended to advance once per event are
# checked.  Do not apply a generic n= rule: e.g. P269 PROP/PVAL legitimately
# repeats or skips ioctl sequence numbers.
COUNTER_RULES = {
    "P432 OWN": re.compile(r"^P432 OWN n=(\d+)\b"),
    "P432 IO": re.compile(r"^P432 IO n=(\d+)\b"),
    "P418 AT in": re.compile(r"^P418 AT in n=(\d+)\b"),
    "P432 C PR S": re.compile(r"^P432 C PR S .*?\bn=(\d+)\b"),
    "P431 RI e": re.compile(r"^P431 RI e n=(\d+)\b"),
    "P423 SOL": re.compile(r"^P423 SOL n=(\d+)\b"),
    "P269 IO": re.compile(r"^P269 IO n=(\d+)\b"),
}


def slice_disk(data: bytes) -> bytes:
    if len(data) == DISK_BYTES:
        return data
    if len(data) >= DEBUG_OFFSET + DISK_BYTES:
        return data[DEBUG_OFFSET:DEBUG_OFFSET + DISK_BYTES]
    raise SystemExit(f"debug input too small: {len(data)}")


def slice_ram(data: bytes) -> bytes:
    if len(data) == RAM_BYTES:
        return data
    if len(data) >= RAM_OFFSET_IN_11M + RAM_BYTES:
        return data[RAM_OFFSET_IN_11M:RAM_OFFSET_IN_11M + RAM_BYTES]
    raise SystemExit(f"reserved-RAM input too small: {len(data)}")


def parse_header(data: bytes) -> dict[str, int | bool]:
    if len(data) < HEADER_BYTES:
        raise ValueError("short header")
    (
        magic, version, phase, boot_id, global_seq,
        disk_count, ram_count, disk_capacity, ram_capacity,
        record_bytes, state, dropped, commit,
    ) = HEADER.unpack_from(data, 0)
    return {
        "magic": magic,
        "version": version,
        "phase": phase,
        "boot_id": boot_id,
        "global_seq": global_seq,
        "disk_count": disk_count,
        "ram_count": ram_count,
        "disk_capacity": disk_capacity,
        "ram_capacity": ram_capacity,
        "record_bytes": record_bytes,
        "state": state,
        "dropped": dropped,
        "commit": commit,
        "valid": (
            magic == MAGIC and version == 1 and phase == 414 and
            disk_capacity == DISK_CAP and ram_capacity == RAM_CAP and
            record_bytes == RECORD_BYTES and commit == HDR_COMMIT
        ),
    }


def _inv32(v: int) -> int:
    return (~v) & 0xFFFFFFFF


def _signed32(v: int) -> int:
    return v - 0x100000000 if v & 0x80000000 else v


def parse_p416_status_at(data: bytes, off: int) -> dict[str, int | bool] | None:
    if off < 0 or off + P416_STATUS.size > len(data):
        return None
    v = P416_STATUS.unpack_from(data, off)
    (
        magic, magic_inv,
        version, version_inv,
        armed, armed_inv,
        worker_runs, worker_runs_inv,
        open_rc_u, open_rc_inv,
        submit_rc_u, submit_rc_inv,
        flush_rc_u, flush_rc_inv,
        retry_count, retry_count_inv,
        disk_gen, disk_gen_inv,
        written_gen, written_gen_inv,
        disk_count, disk_count_inv,
        last_page, last_page_inv,
        state, state_inv,
        commit, commit_inv,
        reserved0, reserved1,
    ) = v
    pairs = (
        (version, version_inv), (armed, armed_inv),
        (worker_runs, worker_runs_inv), (open_rc_u, open_rc_inv),
        (submit_rc_u, submit_rc_inv), (flush_rc_u, flush_rc_inv),
        (retry_count, retry_count_inv), (disk_gen, disk_gen_inv),
        (written_gen, written_gen_inv), (disk_count, disk_count_inv),
        (last_page, last_page_inv), (state, state_inv),
        (commit, commit_inv),
    )
    valid = (
        magic == P416_STATUS_MAGIC and
        magic_inv == ((~P416_STATUS_MAGIC) & 0xFFFFFFFFFFFFFFFF) and
        version == P416_STATUS_VERSION and
        commit == P416_STATUS_COMMIT and
        all(_inv32(a) == b for a, b in pairs)
    )
    return {
        "offset": off,
        "valid": valid,
        "magic": magic,
        "version": version,
        "armed": armed,
        "worker_runs": worker_runs,
        "open_rc": _signed32(open_rc_u),
        "submit_rc": _signed32(submit_rc_u),
        "flush_rc": _signed32(flush_rc_u),
        "retry_count": retry_count,
        "disk_gen": disk_gen,
        "written_gen": written_gen,
        "disk_count": disk_count,
        "last_page": last_page,
        "state": state,
        "commit": commit,
        "reserved0": reserved0,
        "reserved1": reserved1,
    }


def parse_p416_status_copies(data: bytes) -> list[dict]:
    return [
        s for off in P416_STATUS_OFFSETS
        if (s := parse_p416_status_at(data, off)) is not None
    ]


def choose_p416_status(disk: bytes, ram: bytes) -> tuple[dict | None, dict]:
    dc = parse_p416_status_copies(disk)
    rc = parse_p416_status_copies(ram)
    valid_disk = [s for s in dc if s["valid"]]
    valid_ram = [s for s in rc if s["valid"]]

    # Prefer the Samsung-disk sidecar: it describes exactly which generation
    # made it to the disk image being decoded.  Require at least two agreeing
    # copies before using it as a hard current-boot cutoff.
    chosen = None
    source = "none"
    if len(valid_disk) >= 2:
        counts = collections.Counter(
            (s["written_gen"], s["disk_gen"], s["disk_count"])
            for s in valid_disk
        )
        key, votes = counts.most_common(1)[0]
        if votes >= 2:
            chosen = next(
                s for s in valid_disk
                if (s["written_gen"], s["disk_gen"], s["disk_count"]) == key
            ).copy()
            chosen["votes"] = votes
            source = "disk"
    if chosen is None and len(valid_ram) >= 2:
        counts = collections.Counter(
            (s["written_gen"], s["disk_gen"], s["disk_count"])
            for s in valid_ram
        )
        key, votes = counts.most_common(1)[0]
        if votes >= 2:
            chosen = next(
                s for s in valid_ram
                if (s["written_gen"], s["disk_gen"], s["disk_count"]) == key
            ).copy()
            chosen["votes"] = votes
            source = "ram"

    meta = {
        "source": source,
        "disk_copies": dc,
        "ram_copies": rc,
        "disk_valid_copies": len(valid_disk),
        "ram_valid_copies": len(valid_ram),
    }
    return chosen, meta


def parse_records(
    data: bytes,
    count: int,
    capacity: int,
    boot_id: int,
    *,
    seq_limit: int | None = None,
) -> tuple[list[dict], list[dict]]:
    current = []
    beyond = []
    limit = min(count, capacity)
    for i in range(limit):
        off = HEADER_BYTES + i * RECORD_BYTES
        seq, ts_ns, rec_boot, phase, cpu, length, raw, commit, reserved = RECORD.unpack_from(data, off)
        if commit != REC_COMMIT or phase != 414 or rec_boot != boot_id:
            continue
        length = min(int(length), len(raw))
        text = raw[:length].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        rec = {
            "slot": i,
            "seq": seq,
            "ts_ns": ts_ns,
            "boot_id": rec_boot,
            "cpu": cpu,
            "text": text,
            "commit": f"0x{commit:08x}",
            "reserved": reserved,
        }
        if seq_limit is not None and seq > seq_limit:
            beyond.append(rec)
        else:
            current.append(rec)
    return current, beyond


def counter_continuity(records: list[dict]) -> dict:
    values: dict[str, list[int]] = {name: [] for name in COUNTER_RULES}
    for rec in records:
        text = rec["text"]
        for name, rx in COUNTER_RULES.items():
            m = rx.search(text)
            if m:
                values[name].append(int(m.group(1)))
                break

    out = {}
    for name, seqs in values.items():
        if not seqs:
            continue
        jumps = []
        prev = seqs[0]
        for cur in seqs[1:]:
            # Repeated values are allowed only where entry/exit variants happen
            # to share a counter; backwards motion is always suspicious.
            if cur != prev and cur != prev + 1:
                jumps.append([prev, cur])
            prev = cur
        out[name] = {
            "count": len(seqs),
            "first": seqs[0],
            "last": seqs[-1],
            "jumps": jumps,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", type=Path, required=True,
                    help="10 MiB Samsung debug.bin or extracted 2 MiB Phase414 region")
    ap.add_argument("--reserved", type=Path, required=True,
                    help="11 MiB Samsung reserved RAM dump or extracted final 1 MiB")
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    disk = slice_disk(ns.debug.read_bytes())
    ram = slice_ram(ns.reserved.read_bytes())

    dh = parse_header(disk)
    rh = parse_header(ram)
    p416, p416_meta = choose_p416_status(disk, ram)

    disk_seq_limit = int(dh["global_seq"]) if dh["valid"] else None
    cutoff_reason = "disk header global_seq"
    if p416 and p416["armed"] and p416["written_gen"]:
        # written_gen is a flush generation, not intrinsically a record number.
        # It can run one ahead because late-arm queues a flush without appending
        # a record.  min(global_seq, written_gen) is therefore conservative in
        # both directions: it retains all records when the transport is caught
        # up, and rejects trailing ring slots when the final flush lagged.
        disk_seq_limit = min(int(dh["global_seq"]), int(p416["written_gen"]))
        cutoff_reason = "min(disk_header.global_seq, P416.written_gen)"

    if dh["valid"]:
        disk_records, stale_disk = parse_records(
            disk, int(dh["disk_count"]), DISK_CAP, int(dh["boot_id"]),
            seq_limit=disk_seq_limit,
        )
    else:
        disk_records, stale_disk = [], []

    if rh["valid"]:
        ram_records, stale_ram = parse_records(
            ram, int(rh["ram_count"]), RAM_CAP, int(rh["boot_id"]),
        )
    else:
        ram_records, stale_ram = [], []

    same_boot = bool(dh["valid"] and rh["valid"] and dh["boot_id"] == rh["boot_id"])
    records = sorted(disk_records + (ram_records if same_boot else []), key=lambda r: r["seq"])

    gaps = []
    for a, b in zip(records, records[1:]):
        if b["seq"] != a["seq"] + 1:
            gaps.append([a["seq"], b["seq"]])

    continuity = counter_continuity(records)

    result = {
        "disk_header": dh,
        "ram_header": rh,
        "p416_status": p416,
        "p416_status_meta": p416_meta,
        "same_boot": same_boot,
        "disk_seq_limit": disk_seq_limit,
        "disk_cutoff_reason": cutoff_reason,
        "disk_valid_records": len(disk_records),
        "disk_records_rejected_beyond_persisted_frontier": len(stale_disk),
        "stale_disk_records": stale_disk,
        "ram_valid_records": len(ram_records),
        "stale_ram_records": stale_ram,
        "combined_records": len(records),
        "sequence_gaps": gaps,
        "family_counter_continuity": continuity,
        "records": records,
    }

    print(
        f'Phase414 disk_valid={dh["valid"]} ram_valid={rh["valid"]} '
        f'same_boot={same_boot} boot={dh["boot_id"] if dh["valid"] else 0}'
    )
    if p416:
        print(
            f'P416 status source={p416_meta["source"]} votes={p416.get("votes", 1)} '
            f'armed={p416["armed"]} gen={p416["disk_gen"]} '
            f'written_gen={p416["written_gen"]} status_count={p416["disk_count"]} '
            f'worker_runs={p416["worker_runs"]} submit_rc={p416["submit_rc"]} '
            f'flush_rc={p416["flush_rc"]}'
        )
    else:
        print(
            f'P416 status unavailable: disk_valid_copies={p416_meta["disk_valid_copies"]} '
            f'ram_valid_copies={p416_meta["ram_valid_copies"]}'
        )
    print(
        f'disk_count={dh["disk_count"]} header_seq={dh["global_seq"]} '
        f'persisted_seq_limit={disk_seq_limit} '
        f'valid_current={len(disk_records)}/{DISK_CAP} '
        f'rejected_trailing={len(stale_disk)} reason={cutoff_reason}'
    )
    print(
        f'ram_count={rh["ram_count"]} valid={len(ram_records)}/{RAM_CAP} '
        f'combined={len(records)} state={dh["state"]}/{rh["state"]} '
        f'dropped={max(int(dh["dropped"]), int(rh["dropped"]))}'
    )

    for r in records:
        print(
            f'{r["seq"]:06d} {r["ts_ns"]/1e9:12.6f}s '
            f'cpu={r["cpu"]} {r["text"]}'
        )

    if stale_disk:
        print(
            "stale_or_unpersisted_disk_tail="
            + json.dumps([
                {
                    "seq": r["seq"],
                    "ts_s": round(r["ts_ns"] / 1e9, 6),
                    "text": r["text"],
                }
                for r in stale_disk
            ])
        )
    if gaps:
        print("sequence_gaps=" + json.dumps(gaps))

    counter_issues = {
        k: v["jumps"] for k, v in continuity.items() if v["jumps"]
    }
    if counter_issues:
        print("family_counter_discontinuities=" + json.dumps(counter_issues, sort_keys=True))

    if ns.json:
        ns.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    return 0 if dh["valid"] and rh["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
