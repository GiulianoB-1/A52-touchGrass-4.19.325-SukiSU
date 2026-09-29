#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, struct
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
HEADER = struct.Struct("<QIIQQIIIIIIII")
RECORD = struct.Struct("<QQQIHH88sII")

STATUS_MAGIC = 0x3631345354415433
STATUS_VERSION = 1
STATUS_COMMIT = 0x416C0DE5
STATUS_OFFSETS = (0x100, 0x180, 0x200)
STATUS_BYTES = 128
# Q,Q then 13 u32 pairs, then 2 reserved u32 = 128 bytes.
STATUS = struct.Struct("<QQ" + "II" * 13 + "II")


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


def parse_header(data: bytes) -> dict:
    vals = HEADER.unpack_from(data, 0)
    keys = ("magic","version","phase","boot_id","global_seq","disk_count","ram_count",
            "disk_capacity","ram_capacity","record_bytes","state","dropped","commit")
    out = dict(zip(keys, vals))
    out["valid"] = bool(
        out["magic"] == MAGIC and out["version"] == 1 and out["phase"] == 414 and
        out["disk_capacity"] == DISK_CAP and out["ram_capacity"] == RAM_CAP and
        out["record_bytes"] == RECORD_BYTES and out["commit"] == HDR_COMMIT)
    return out


def parse_records(data: bytes, count: int, capacity: int, boot_id: int) -> list[dict]:
    out=[]
    for i in range(min(max(count,0), capacity)):
        off=HEADER_BYTES+i*RECORD_BYTES
        seq,ts,rec_boot,phase,cpu,length,raw,commit,res=RECORD.unpack_from(data,off)
        if commit != REC_COMMIT or phase != 414 or rec_boot != boot_id:
            continue
        length=min(int(length),len(raw))
        out.append({"slot":i,"seq":seq,"ts_ns":ts,"cpu":cpu,
                    "text":raw[:length].split(b"\0",1)[0].decode("utf-8",errors="replace")})
    return out


def majority3(a: bytes,b: bytes,c: bytes) -> bytes:
    return bytes(((x & y) | (x & z) | (y & z)) for x,y,z in zip(a,b,c))


def inv_ok(v: int, inv: int, bits: int=32) -> bool:
    mask=(1<<bits)-1
    return ((v ^ inv) & mask) == mask


def parse_status(raw: bytes) -> dict:
    if len(raw) != STATUS_BYTES:
        raise ValueError("bad status length")
    v=STATUS.unpack(raw)
    magic,magic_inv=v[:2]
    pairs=list(zip(v[2:28:2],v[3:28:2]))
    names=("version","armed","worker_runs","open_rc_u32","submit_rc_u32","flush_rc_u32",
           "retry_count","disk_gen","written_gen","disk_count","last_page","state","commit")
    out={"magic":magic,"magic_inv":magic_inv}
    valid_pairs={}
    for name,(x,xi) in zip(names,pairs):
        out[name]=x; out[name+"_inv"]=xi; valid_pairs[name]=inv_ok(x,xi)
    def s32(x): return x-(1<<32) if x & 0x80000000 else x
    out["open_rc"]=s32(out["open_rc_u32"])
    out["submit_rc"]=s32(out["submit_rc_u32"])
    out["flush_rc"]=s32(out["flush_rc_u32"])
    out["pair_valid"]=valid_pairs
    out["valid_pair_count"]=sum(valid_pairs.values()) + int(inv_ok(magic,magic_inv,64))
    out["valid"] = bool(
        magic == STATUS_MAGIC and inv_ok(magic,magic_inv,64) and
        out["version"] == STATUS_VERSION and valid_pairs["version"] and
        out["commit"] == STATUS_COMMIT and valid_pairs["commit"])
    return out


def recover_status(ram: bytes) -> dict:
    copies=[ram[o:o+STATUS_BYTES] for o in STATUS_OFFSETS]
    parsed=[parse_status(x) for x in copies]
    fused=majority3(*copies)
    fp=parse_status(fused)
    return {"copies":parsed,"majority":fp}


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--debug",type=Path,required=True)
    ap.add_argument("--reserved",type=Path,required=True)
    ap.add_argument("--json",type=Path)
    ns=ap.parse_args()
    disk=slice_disk(ns.debug.read_bytes())
    ram=slice_ram(ns.reserved.read_bytes())
    dh,rh=parse_header(disk),parse_header(ram)
    st=recover_status(ram)
    drec=parse_records(disk,int(dh["disk_count"]),DISK_CAP,int(dh["boot_id"])) if dh["valid"] else []
    rrec=parse_records(ram,int(rh["ram_count"]),RAM_CAP,int(rh["boot_id"])) if rh["valid"] else []
    same=bool(dh["valid"] and rh["valid"] and dh["boot_id"]==rh["boot_id"])
    recs=sorted(drec+(rrec if same else []),key=lambda x:x["seq"])
    result={"disk_header":dh,"ram_header":rh,"same_boot":same,
            "disk_valid_records":len(drec),"ram_valid_records":len(rrec),
            "combined_records":len(recs),"transport_status":st,"records":recs}
    s=st["majority"]
    print(f'Phase416 disk_valid={dh["valid"]} ram_valid={rh["valid"]} same_boot={same}')
    print(f'counts disk={dh["disk_count"]} ram={rh["ram_count"]} records={len(recs)}')
    print('status majority valid=%s pairs=%u/14 armed=%u runs=%u open_rc=%d submit_rc=%d flush_rc=%d retries=%u gen=%u written=%u count=%u page=%u state=%u' % (
        s["valid"],s["valid_pair_count"],s["armed"],s["worker_runs"],s["open_rc"],s["submit_rc"],s["flush_rc"],
        s["retry_count"],s["disk_gen"],s["written_gen"],s["disk_count"],s["last_page"],s["state"]))
    for r in recs:
        print(f'{r["seq"]:06d} {r["ts_ns"]/1e9:12.6f}s cpu={r["cpu"]} {r["text"]}')
    if ns.json:
        ns.json.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    return 0 if (dh["valid"] and rh["valid"] and s["valid"]) else 2

if __name__=="__main__":
    raise SystemExit(main())
