#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

OLD = "ramoops.pmsg_size=0x00040000"
NEW = "ramoops.pmsg_size=0"

REQUIRED = (
    "ramoops.mem_address=0xB1B00000",
    "ramoops.mem_size=0x00100000",
    "ramoops.record_size=0x00040000",
    "ramoops.console_size=0x00040000",
    "ramoops.ftrace_size=0x00040000",
    "ramoops.dump_oops=1",
    "pstore.backend=ramoops",
)

def load_boot_module():
    path = Path(__file__).with_name("38_repack_a52_p1_boot.py")
    spec = importlib.util.spec_from_file_location("a52_boot_repack", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()

    boot = load_boot_module()
    source = args.source.read_bytes()
    original = boot.parse_boot(source)
    cmdline = original["cmdline"]

    if cmdline.count(OLD) != 1:
        raise SystemExit(f"expected exactly one {OLD!r} in source cmdline")
    if NEW in cmdline:
        raise SystemExit("source cmdline already contains disabled pmsg token")

    new_cmdline = cmdline.replace(OLD, NEW, 1)
    encoded = new_cmdline.encode("ascii")
    if len(encoded) > 1536:
        raise SystemExit(f"command line too long: {len(encoded)} > 1536")

    output = bytearray(source)
    output[64:576] = b"\0" * 512
    output[608:1632] = b"\0" * 1024
    output[64:64 + min(len(encoded), 512)] = encoded[:512]
    if len(encoded) > 512:
        output[608:608 + len(encoded) - 512] = encoded[512:]

    rebuilt = boot.parse_boot(bytes(output))
    invariants = {
        "partition_size_preserved": len(output) == len(source),
        "kernel_preserved": rebuilt["kernel"] == original["kernel"],
        "ramdisk_preserved": rebuilt["ramdisk"] == original["ramdisk"],
        "second_preserved": rebuilt["second"] == original["second"],
        "recovery_dtbo_preserved": rebuilt["recovery_dtbo"] == original["recovery_dtbo"],
        "dtb_preserved": rebuilt["dtb"] == original["dtb"],
        "boot_id_preserved": rebuilt["id"] == original["id"],
        "pmsg_disabled_exactly_once": rebuilt["cmdline"].split().count(NEW) == 1,
        "old_pmsg_token_removed": OLD not in rebuilt["cmdline"],
        "other_ramoops_contract_preserved": all(x in rebuilt["cmdline"] for x in REQUIRED),
    }
    failed = [k for k, v in invariants.items() if not v]
    if failed:
        raise SystemExit("pmsg release audit failed: " + ", ".join(failed))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    report = {
        "status": "touchgrass-v2-pmsg-quarter-released",
        "hardware_validated": False,
        "source_sha256": sha(source),
        "output_sha256": sha(output),
        "source_cmdline": cmdline,
        "output_cmdline": rebuilt["cmdline"],
        "functional_scope": (
            "diagnostic-only: disable ramoops pmsg allocation so the existing "
            "fourth 256-KiB reserved quarter is owned exclusively by TGCR"
        ),
        "invariants": invariants,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
