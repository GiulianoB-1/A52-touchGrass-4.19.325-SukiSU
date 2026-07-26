#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

REPORT_NAME = "touchgrass-keymaster-flight-recorder-report.json"
RAM_REL = Path("fs/pstore/ram.c")
RECORDER_REL = Path("drivers/misc/a52_keymaster_flight_recorder.c")
MARKER = "A52_TOUCHGRASS_FAILED_BOOT_KEYMASTER_RAMOOPS"
PERSIST_CAPACITY = 768
PERSIST_LINE_LEN = 256


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once_or_verify(
    text: str,
    old: str,
    new: str,
    marker: str,
    label: str,
) -> tuple[str, bool]:
    if marker in text:
        return text, False
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1), True


def patch_ramoops(root: Path) -> bool:
    path = root / RAM_REL
    if not path.is_file():
        raise SystemExit(f"missing TouchGrass ramoops source: {path}")

    text = read(path)
    anchor = '''static struct ramoops_context oops_cxt = {
\t.pstore = {
\t\t.owner\t= THIS_MODULE,
\t\t.name\t= "ramoops",
\t\t.open\t= ramoops_pstore_open,
\t\t.read\t= ramoops_pstore_read,
\t\t.write\t= ramoops_pstore_write,
\t\t.write_user\t= ramoops_pstore_write_user,
\t\t.erase\t= ramoops_pstore_erase,
\t},
};
'''
    addition = anchor + r'''
/* A52_TOUCHGRASS_FAILED_BOOT_KEYMASTER_RAMOOPS */
#define A52_KMFR_RAMOOPS_MAX_WRITE 512U

void a52_keymaster_ramoops_write(const char *buf, size_t len)
{
\tstruct persistent_ram_zone *prz = READ_ONCE(oops_cxt.cprz);

\tif (!prz || !buf || !len)
\t\treturn;
\tif (len > A52_KMFR_RAMOOPS_MAX_WRITE)
\t\tlen = A52_KMFR_RAMOOPS_MAX_WRITE;

\tpersistent_ram_write(prz, buf, len);
\twmb();
}
EXPORT_SYMBOL_GPL(a52_keymaster_ramoops_write);
'''
    updated, changed = replace_once_or_verify(
        text,
        anchor,
        addition,
        MARKER,
        "ramoops persistent writer",
    )
    write(path, updated)
    return changed


def patch_recorder(root: Path) -> bool:
    path = root / RECORDER_REL
    if not path.is_file():
        raise SystemExit(f"generated Keymaster recorder is missing: {path}")

    text = read(path)
    changed = False

    include_anchor = "#include <linux/string.h>\n"
    include_addition = (
        include_anchor
        + "#include <linux/utsname.h>\n\n"
        + "extern void a52_keymaster_ramoops_write(const char *buf, size_t len);\n"
        + f"/* {MARKER} */\n"
    )
    text, one = replace_once_or_verify(
        text,
        include_anchor,
        include_addition,
        MARKER,
        "recorder persistent writer declaration",
    )
    changed |= one

    constants_anchor = (
        "#define A52_KMFR_CAPACITY 8192U\n"
        "#define A52_KMFR_MESSAGE_LEN 192U\n"
    )
    constants_addition = constants_anchor + (
        f"#define A52_KMFR_PERSIST_CAPACITY {PERSIST_CAPACITY}U\n"
        f"#define A52_KMFR_PERSIST_LINE_LEN {PERSIST_LINE_LEN}U\n"
    )
    text, one = replace_once_or_verify(
        text,
        constants_anchor,
        constants_addition,
        "A52_KMFR_PERSIST_CAPACITY",
        "recorder persistence limits",
    )
    changed |= one

    declarations_anchor = (
        "\tstruct a52_kmfr_event event;\n"
        "\tunsigned long irq_flags;\n"
        "\tva_list args;\n"
        "\tu64 seq;\n"
    )
    declarations_addition = (
        "\tstruct a52_kmfr_event event;\n"
        "\tunsigned long irq_flags;\n"
        "\tchar persistent_line[A52_KMFR_PERSIST_LINE_LEN];\n"
        "\tva_list args;\n"
        "\tint persistent_len;\n"
        "\tu64 seq;\n"
    )
    text, one = replace_once_or_verify(
        text,
        declarations_anchor,
        declarations_addition,
        "persistent_line[A52_KMFR_PERSIST_LINE_LEN]",
        "recorder persistent line locals",
    )
    changed |= one

    store_anchor = (
        "\tspin_lock_irqsave(&a52_kmfr_lock, irq_flags);\n"
        "\ta52_kmfr_ring[seq - 1] = event;\n"
        "\tspin_unlock_irqrestore(&a52_kmfr_lock, irq_flags);\n\n"
        "\tpr_info_ratelimited(\"seq=%llu pid=%d tgid=%d comm=%s %s\\n\",\n"
    )
    store_addition = (
        "\tspin_lock_irqsave(&a52_kmfr_lock, irq_flags);\n"
        "\ta52_kmfr_ring[seq - 1] = event;\n"
        "\tspin_unlock_irqrestore(&a52_kmfr_lock, irq_flags);\n\n"
        "\tif (seq <= A52_KMFR_PERSIST_CAPACITY) {\n"
        "\t\tpersistent_len = scnprintf(persistent_line,\n"
        "\t\t\tsizeof(persistent_line),\n"
        "\t\t\t\"A52KMFR-PERSIST seq=%llu ns=%llu pid=%d tgid=%d comm=%s %s\\n\",\n"
        "\t\t\t(unsigned long long)event.seq,\n"
        "\t\t\t(unsigned long long)event.monotonic_ns,\n"
        "\t\t\tevent.pid, event.tgid, event.comm, event.message);\n"
        "\t\ta52_keymaster_ramoops_write(persistent_line, persistent_len);\n"
        "\t} else if (seq == A52_KMFR_PERSIST_CAPACITY + 1) {\n"
        "\t\tpersistent_len = scnprintf(persistent_line,\n"
        "\t\t\tsizeof(persistent_line),\n"
        "\t\t\t\"A52KMFR-PERSIST FULL capacity=%u later-events-omitted\\n\",\n"
        "\t\t\tA52_KMFR_PERSIST_CAPACITY);\n"
        "\t\ta52_keymaster_ramoops_write(persistent_line, persistent_len);\n"
        "\t}\n\n"
        "\tpr_info_ratelimited(\"seq=%llu pid=%d tgid=%d comm=%s %s\\n\",\n"
    )
    text, one = replace_once_or_verify(
        text,
        store_anchor,
        store_addition,
        "A52KMFR-PERSIST seq=%llu",
        "persist first recorder events",
    )
    changed |= one

    init_anchor = "static int __init a52_kmfr_init(void)\n"
    boot_init = (
        "static int __init a52_kmfr_persistent_boot_init(void)\n"
        "{\n"
        "\ta52_kmfr_record(\n"
        "\t\t\"BOOT BEGIN release=%s backend=ramoops-console capacity=%u metadata-only\",\n"
        "\t\tinit_utsname()->release, A52_KMFR_PERSIST_CAPACITY);\n"
        "\treturn 0;\n"
        "}\n"
        "subsys_initcall(a52_kmfr_persistent_boot_init);\n\n"
        + init_anchor
    )
    text, one = replace_once_or_verify(
        text,
        init_anchor,
        boot_init,
        "a52_kmfr_persistent_boot_init",
        "failed-boot generation header",
    )
    changed |= one

    write(path, text)
    return changed


def audit(root: Path) -> dict[str, bool]:
    ram = read(root / RAM_REL)
    recorder = read(root / RECORDER_REL)
    checks = {
        "ramoops_writer_exported": (
            MARKER in ram
            and "EXPORT_SYMBOL_GPL(a52_keymaster_ramoops_write);" in ram
            and "persistent_ram_write(prz, buf, len);" in ram
        ),
        "existing_console_zone_reused": "READ_ONCE(oops_cxt.cprz)" in ram,
        "first_events_bounded": (
            f"#define A52_KMFR_PERSIST_CAPACITY {PERSIST_CAPACITY}U" in recorder
            and "seq <= A52_KMFR_PERSIST_CAPACITY" in recorder
            and "later-events-omitted" in recorder
        ),
        "boot_header_present": (
            "a52_kmfr_persistent_boot_init" in recorder
            and "BOOT BEGIN release=%s backend=ramoops-console" in recorder
        ),
        "metadata_only": (
            "A52KMFR-PERSIST seq=%llu" in recorder
            and "cmd_req_buf" not in recorder
            and "resp_buf" not in recorder
        ),
        "volatile_proc_reader_retained": (
            'proc_create("a52_keymaster_flight_recorder"' in recorder
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("failed-boot persistence audit failed: " + ", ".join(failed))
    return checks


def update_report(output: Path, checks: dict[str, bool], changed: bool) -> None:
    report_path = output / REPORT_NAME
    if not report_path.is_file():
        raise SystemExit(f"recorder report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["failed_boot_persistence"] = {
        "status": "touchgrass-keymaster-ramoops-persistence-staged",
        "backend": "existing-ramoops-console-zone",
        "survives_incomplete_android_boot": True,
        "requires_adb_before_reboot": False,
        "persistent_capacity": PERSIST_CAPACITY,
        "persistent_line_length": PERSIST_LINE_LEN,
        "capture_mode": "first-events-preserved",
        "payload_policy": "metadata-only-no-command-or-response-buffers",
        "source_changed": changed,
        "checks": checks,
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def self_test() -> None:
    ram_sample = '''#include <linux/kernel.h>
static struct ramoops_context oops_cxt = {
\t.pstore = {
\t\t.owner\t= THIS_MODULE,
\t\t.name\t= "ramoops",
\t\t.open\t= ramoops_pstore_open,
\t\t.read\t= ramoops_pstore_read,
\t\t.write\t= ramoops_pstore_write,
\t\t.write_user\t= ramoops_pstore_write_user,
\t\t.erase\t= ramoops_pstore_erase,
\t},
};
'''
    recorder_sample = r'''#include <linux/string.h>

#include <linux/a52_keymaster_flight_recorder.h>

#define A52_KMFR_CAPACITY 8192U
#define A52_KMFR_MESSAGE_LEN 192U

struct a52_kmfr_event {
\tu64 seq;
\tu64 monotonic_ns;
\tpid_t pid;
\tpid_t tgid;
\tchar comm[TASK_COMM_LEN];
\tchar message[A52_KMFR_MESSAGE_LEN];
};

static struct a52_kmfr_event a52_kmfr_ring[A52_KMFR_CAPACITY];
static DEFINE_SPINLOCK(a52_kmfr_lock);
static atomic64_t a52_kmfr_sequence = ATOMIC64_INIT(0);

void a52_kmfr_record(const char *fmt, ...)
{
\tstruct a52_kmfr_event event;
\tunsigned long irq_flags;
\tva_list args;
\tu64 seq;

\tseq = (u64)atomic64_inc_return(&a52_kmfr_sequence);
\tmemset(&event, 0, sizeof(event));
\tevent.seq = seq;
\tva_start(args, fmt);
\tvscnprintf(event.message, sizeof(event.message), fmt, args);
\tva_end(args);

\tspin_lock_irqsave(&a52_kmfr_lock, irq_flags);
\ta52_kmfr_ring[seq - 1] = event;
\tspin_unlock_irqrestore(&a52_kmfr_lock, irq_flags);

\tpr_info_ratelimited("seq=%llu pid=%d tgid=%d comm=%s %s\n",
\t\t(unsigned long long)event.seq, event.pid, event.tgid,
\t\tevent.comm, event.message);
}

static int a52_kmfr_show(struct seq_file *m, void *unused)
{
\treturn 0;
}

static int __init a52_kmfr_init(void)
{
\tif (!proc_create("a52_keymaster_flight_recorder", 0444, NULL,
\t\t\t &a52_kmfr_fops))
\t\treturn -ENOMEM;
\treturn 0;
}
late_initcall(a52_kmfr_init);
'''
    # The fixture is raw so C string \n escapes remain literal. Normalize only indentation escapes for the production patch anchors.
    recorder_sample = recorder_sample.replace(r"\t", "	")
    if r"\t" in recorder_sample:
        raise SystemExit("recorder fixture still contains literal tab escapes")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / RAM_REL).parent.mkdir(parents=True, exist_ok=True)
        (root / RECORDER_REL).parent.mkdir(parents=True, exist_ok=True)
        write(root / RAM_REL, ram_sample)
        write(root / RECORDER_REL, recorder_sample)
        first = patch_ramoops(root) | patch_recorder(root)
        second = patch_ramoops(root) | patch_recorder(root)
        if not first or second:
            raise SystemExit("failed-boot persistence self-test is not idempotent")
        audit(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    self_test()
    root = args.kernel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    changed = patch_ramoops(root) | patch_recorder(root)
    checks = audit(root)
    update_report(output, checks, changed)
    print(
        "staged persistent failed-boot Keymaster recorder: "
        f"capacity={PERSIST_CAPACITY} backend=ramoops-console"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
