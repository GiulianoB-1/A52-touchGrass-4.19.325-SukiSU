#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

OLD_PROFILE = "display-bindcore-fec-prz-v2"
NEW_PROFILE = "heap19-display-init-fec-single-map-v1"
RAM_REL = Path("fs/pstore/ram.c")
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
REPORT = "phase36-a52-display-init-fec-report.json"

OLD_DECL = '''static struct persistent_ram_zone *a52_diag_prz[A52_DIAG_BANK_COUNT];
static u8 __iomem *a52_diag_banks[A52_DIAG_BANK_COUNT];
static struct rs_control *a52_diag_rs;
static DEFINE_RAW_SPINLOCK(a52_diag_lock);
'''

NEW_DECL = '''#define A52_DIAG_TOTAL_SIZE \\
\t(A52_DIAG_BANK_SIZE * A52_DIAG_BANK_COUNT)

static struct persistent_ram_zone *a52_diag_prz;
static u8 __iomem *a52_diag_banks[A52_DIAG_BANK_COUNT];
static struct rs_control *a52_diag_rs;
static DEFINE_RAW_SPINLOCK(a52_diag_lock);
'''

NEW_MAP = r'''static int a52_diag_map_all_banks(void)
{
\tstruct persistent_ram_ecc_info ecc = { };
\tu8 __iomem *base;
\tunsigned int bank;

\tif (a52_diag_prz && a52_diag_banks[0])
\t\treturn 0;

\t/*
\t * Map the three adjacent 256 KiB banks in one vmap operation. This makes
\t * recorder initialization all-or-nothing and avoids three independent
\t * early mappings of one contiguous reserved-RAM allocation.
\t */
\ta52_diag_prz = persistent_ram_new(a52_diag_phys[0],
\t\t\t\t\t A52_DIAG_TOTAL_SIZE, 0, &ecc,
\t\t\t\t\t 1, PRZ_FLAG_ZAP_OLD,
\t\t\t\t\t "a52-rec3-all-banks");
\tif (IS_ERR(a52_diag_prz)) {
\t\tint ret = PTR_ERR(a52_diag_prz);

\t\ta52_diag_prz = NULL;
\t\treturn ret;
\t}

\ta52_diag_prz->type = PSTORE_TYPE_DMESG;
\tbase = (u8 __iomem *)a52_diag_prz->vaddr;
\tif (!base)
\t\treturn -ENOMEM;

\tmemset_io(base, 0, A52_DIAG_TOTAL_SIZE);
\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\ta52_diag_banks[bank] = base + bank * A52_DIAG_BANK_SIZE;
\t\twritel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG,
\t\t\t       a52_diag_banks[bank]);
\t\twritel_relaxed(0, a52_diag_banks[bank] + 4);
\t\twritel_relaxed(0, a52_diag_banks[bank] + 8);
\t\ta52_diag_write_super(bank);
\t}
\twmb();
\treturn 0;
}

'''

OLD_INIT = r'''int __init a52_persistent_diag_init(void)
{
\tunsigned int bank;
\tunsigned int mapped = 0;
\tint ret;

\tif (a52_diag_rs)
\t\treturn 0;
\ta52_diag_rs = init_rs(8, 0x11d, 0, 1, A52_ACKFR_PARITY_BYTES);
\tif (!a52_diag_rs)
\t\treturn -ENOMEM;

\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\tret = a52_diag_map_bank(bank);
\t\tif (!ret)
\t\t\tmapped++;
\t\telse
\t\t\tpr_err("A52 recorder PRZ bank %u mapping failed: %d\n",
\t\t\t       bank, ret);
\t}
\tif (mapped < 2) {
\t\tfree_rs(a52_diag_rs);
\t\ta52_diag_rs = NULL;
\t\treturn -ENOMEM;
\t}
\tpr_info("A52 recorder v3 PRZ-mapped %u banks, slots=%u, RS parity=%u\n",
\t\tmapped, A52_DIAG_SLOT_COUNT, A52_ACKFR_PARITY_BYTES);
\treturn 0;
}
'''

NEW_INIT = r'''int __init a52_persistent_diag_init(void)
{
\tint ret;

\tif (a52_diag_rs && a52_diag_banks[0])
\t\treturn 0;
\ta52_diag_rs = init_rs(8, 0x11d, 0, 1, A52_ACKFR_PARITY_BYTES);
\tif (!a52_diag_rs)
\t\treturn -ENOMEM;

\tret = a52_diag_map_all_banks();
\tif (ret) {
\t\tpr_err("A52 recorder contiguous mapping failed: %d\n", ret);
\t\tfree_rs(a52_diag_rs);
\t\ta52_diag_rs = NULL;
\t\treturn ret;
\t}
\tpr_info("A52 recorder v3 single-mapped %u banks, slots=%u, RS parity=%u\n",
\t\tA52_DIAG_BANK_COUNT, A52_DIAG_SLOT_COUNT,
\t\tA52_ACKFR_PARITY_BYTES);
\treturn 0;
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, signature: str, replacement: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"function signature missing: {signature}")
    brace = text.find("{", start + len(signature))
    if brace < 0:
        raise SystemExit(f"function opening brace missing: {signature}")
    depth = 0
    end = None
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise SystemExit(f"function closing brace missing: {signature}")
    while end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def patch_ram(text: str) -> str:
    if NEW_PROFILE in text and "a52_diag_map_all_banks" in text:
        return text
    text = replace_once(text, OLD_DECL, NEW_DECL, "mapping declarations")
    start = text.index("static int a52_diag_map_bank(unsigned int bank)")
    end = text.index("unsigned int a52_ackfr_ramoops_write_record", start)
    text = text[:start] + NEW_MAP + text[end:]
    text = replace_function(
        text, "int __init a52_persistent_diag_init(void)", NEW_INIT
    )
    if OLD_PROFILE not in text:
        raise SystemExit("old recorder profile marker missing from ram.c")
    return text.replace(OLD_PROFILE, NEW_PROFILE)


def patch_recorder(text: str) -> str:
    if NEW_PROFILE in text:
        return text
    if OLD_PROFILE not in text:
        raise SystemExit("old recorder profile marker missing from recorder source")
    return text.replace(OLD_PROFILE, NEW_PROFILE)


def run(gki: Path, output: Path) -> dict[str, object]:
    ram = gki / RAM_REL
    recorder = gki / RECORDER_REL
    if not ram.is_file() or not recorder.is_file():
        raise SystemExit("run the recorder-v3 patcher before this single-map patch")
    ram.write_text(patch_ram(ram.read_text()), encoding="utf-8")
    recorder.write_text(patch_recorder(recorder.read_text()), encoding="utf-8")
    final_ram = ram.read_text()
    required = [
        "A52_DIAG_TOTAL_SIZE",
        "a52_diag_map_all_banks",
        "a52-rec3-all-banks",
        "A52 recorder v3 single-mapped %u banks",
        NEW_PROFILE,
        "A52_ACKFR_PARITY_BYTES 32U",
        "A52_DIAG_BANK_COUNT 3U",
    ]
    for marker in required:
        if marker not in final_ram:
            raise SystemExit(f"post-patch marker missing: {marker}")
    if "a52_diag_map_bank(unsigned int bank)" in final_ram:
        raise SystemExit("legacy per-bank mapping function remains")
    report = {
        "status": "a52-display-init-recorder-fec-staged",
        "hardware_validated": False,
        "persistent_profile": NEW_PROFILE,
        "mapping_backend": "one-persistent_ram_new-vmap-three-fixed-banks",
        "record_bytes": 256,
        "protected_data_bytes": 208,
        "reed_solomon_parity_bytes": 32,
        "copies": 3,
        "physical_bank_spacing_bytes": 262144,
        "capture_finding": (
            "the latest plain display recorder produced no intact events; "
            "this build combines the same probes with protected binary records"
        ),
        "files": [str(RAM_REL), str(RECORDER_REL)],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-single-map-") as td:
        root = Path(td)
        ram = root / RAM_REL
        rec = root / RECORDER_REL
        ram.parent.mkdir(parents=True)
        rec.parent.mkdir(parents=True)
        old_map = '''static int a52_diag_map_bank(unsigned int bank)\n{\n\treturn bank;\n}\n\n'''
        ram.write_text(
            OLD_DECL + old_map +
            'unsigned int a52_ackfr_ramoops_write_record(const void *d, size_t l, u64 s) { return 0; }\n' +
            OLD_INIT + f'const char *profile = "{OLD_PROFILE}";\n' +
            '#define A52_ACKFR_PARITY_BYTES 32U\n#define A52_DIAG_BANK_COUNT 3U\n'
        )
        rec.write_text(f'#define A52_REC3_PROFILE "{OLD_PROFILE}"\n')
        first = run(root, root / "out")
        assert first["copies"] == 3
        second = run(root, root / "out2")
        assert second["mapping_backend"].startswith("one-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gki", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "self-test-passed"}))
        return 0
    if args.gki is None or args.output is None:
        ap.error("--gki and --output are required")
    print(json.dumps(run(args.gki.resolve(), args.output.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
