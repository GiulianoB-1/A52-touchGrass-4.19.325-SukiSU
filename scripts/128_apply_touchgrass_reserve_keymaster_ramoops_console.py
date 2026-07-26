#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

RAM_REL = Path("fs/pstore/ram.c")
REPORT_NAME = "touchgrass-keymaster-flight-recorder-report.json"
MARKER = "A52_KMFR_CONSOLE_RESERVED"


def replace_once_or_verify(text: str, old: str, new: str) -> tuple[str, bool]:
    if MARKER in text:
        return text, False
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            "reserve ramoops console zone: expected one console write branch, "
            f"found {count}"
        )
    return text.replace(old, new, 1), True


def patch_ram(path: Path) -> bool:
    if not path.is_file():
        raise SystemExit(f"missing TouchGrass ramoops source: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    old = (
        "\tif (record->type == PSTORE_TYPE_CONSOLE) {\n"
        "\t\tif (!cxt->cprz)\n"
        "\t\t\treturn -ENOMEM;\n"
        "\t\tpersistent_ram_write(cxt->cprz, record->buf, record->size);\n"
        "\t\treturn 0;\n"
        "\t} else if (record->type == PSTORE_TYPE_FTRACE) {\n"
    )
    new = (
        "\tif (record->type == PSTORE_TYPE_CONSOLE) {\n"
        "\t\t/* A52_KMFR_CONSOLE_RESERVED: retain first secure-startup events. */\n"
        "\t\treturn 0;\n"
        "\t} else if (record->type == PSTORE_TYPE_FTRACE) {\n"
    )
    updated, changed = replace_once_or_verify(text, old, new)
    path.write_text(updated, encoding="utf-8")
    return changed


def audit(path: Path) -> dict[str, bool]:
    text = path.read_text(encoding="utf-8", errors="replace")
    checks = {
        "console_zone_reserved": MARKER in text,
        "generic_console_write_removed": (
            "persistent_ram_write(cxt->cprz, record->buf, record->size);"
            not in text
        ),
        "keymaster_direct_writer_retained": (
            "a52_keymaster_ramoops_write" in text
            and "persistent_ram_write(prz, buf, len);" in text
        ),
        "pmsg_path_untouched": (
            "persistent_ram_write_user(cxt->mprz, buf, record->size)" in text
        ),
        "panic_dmesg_path_untouched": (
            "ramoops_write_kmsg_hdr(prz, record)" in text
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("ramoops console reservation audit failed: " + ", ".join(failed))
    return checks


def update_report(output: Path, checks: dict[str, bool], changed: bool) -> None:
    path = output / REPORT_NAME
    if not path.is_file():
        raise SystemExit(f"recorder report missing: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    persistence = report.get("failed_boot_persistence")
    if not isinstance(persistence, dict):
        raise SystemExit("failed-boot persistence report section is missing")
    persistence["console_frontend_reserved"] = True
    persistence["generic_printk_console_capture"] = False
    persistence["pmsg_capture_retained"] = True
    persistence["panic_dmesg_capture_retained"] = True
    persistence["console_reservation_changed"] = changed
    persistence["console_reservation_checks"] = checks
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def self_test() -> None:
    sample = r'''static int notrace ramoops_pstore_write(struct pstore_record *record)
{
\tstruct ramoops_context *cxt = record->psi->data;
\tstruct persistent_ram_zone *prz;
\tsize_t size, hlen;

\tif (record->type == PSTORE_TYPE_CONSOLE) {
\t\tif (!cxt->cprz)
\t\t\treturn -ENOMEM;
\t\tpersistent_ram_write(cxt->cprz, record->buf, record->size);
\t\treturn 0;
\t} else if (record->type == PSTORE_TYPE_FTRACE) {
\t\treturn 0;
\t}
\thlen = ramoops_write_kmsg_hdr(prz, record);
\treturn 0;
}

static int notrace ramoops_pstore_write_user(struct pstore_record *record,
\t\t\t\t\t     const char __user *buf)
{
\treturn persistent_ram_write_user(cxt->mprz, buf, record->size);
}

void a52_keymaster_ramoops_write(const char *buf, size_t len)
{
\tpersistent_ram_write(prz, buf, len);
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ram.c"
        path.write_text(sample, encoding="utf-8")
        first = patch_ram(path)
        second = patch_ram(path)
        if not first or second:
            raise SystemExit("ramoops console reservation is not idempotent")
        audit(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    self_test()
    ram = args.kernel.resolve() / RAM_REL
    changed = patch_ram(ram)
    checks = audit(ram)
    update_report(args.output.resolve(), checks, changed)
    print("reserved ramoops console zone for persistent Keymaster events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
