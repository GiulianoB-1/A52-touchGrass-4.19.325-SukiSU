#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import tempfile
from pathlib import Path

MARKER = "A52_ACK_UNIFIED_SECURE_RECORDER_V2"
HEADER_REL = Path("include/linux/a52_ack_secure_flight_recorder.h")
SOURCE_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
RAMOOPS_REL = Path("fs/pstore/ram.c")
REPORT = "phase17-ack-unified-secure-recorder-report.json"
CAPACITY = 768
LINE_LEN = 256
BANK_CONSOLE = 1
BANK_FTRACE = 2
BANK_BOTH = BANK_CONSOLE | BANK_FTRACE

HEADER = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_A52_ACK_SECURE_FLIGHT_RECORDER_H
#define _LINUX_A52_ACK_SECURE_FLIGHT_RECORDER_H

#include <linux/compiler.h>

void a52_ackfr_record(const char *fmt, ...) __printf(1, 2);

#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52 ACK 5.10 unified secure-startup recorder v2.
 *
 * Every bounded metadata-only event is written immediately to two independent
 * RAMOOPS zones. An in-memory copy is retained only to retry events recorded
 * before the RAMOOPS backend is ready. No command buffers, response buffers,
 * key material, authentication tokens, or process memory are captured.
 */
#define pr_fmt(fmt) "A52USR2: " fmt

#include <linux/atomic.h>
#include <linux/export.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/utsname.h>

#include <linux/a52_ack_secure_flight_recorder.h>

#define A52_USR2_CAPACITY 768U
#define A52_USR2_MESSAGE_LEN 96U
#define A52_USR2_LINE_LEN 256U
#define A52_USR2_BANK_CONSOLE BIT(0)
#define A52_USR2_BANK_FTRACE BIT(1)
#define A52_USR2_BANK_BOTH (A52_USR2_BANK_CONSOLE | A52_USR2_BANK_FTRACE)
#define A52_USR2_COMMIT 0x5a52c0deU

struct a52_usr2_event {
	u64 seq;
	u64 monotonic_ns;
	pid_t pid;
	pid_t tgid;
	u32 cpu;
	u8 persisted_mask;
	char comm[TASK_COMM_LEN];
	char message[A52_USR2_MESSAGE_LEN];
};

static struct a52_usr2_event a52_usr2_events[A52_USR2_CAPACITY];
static DEFINE_SPINLOCK(a52_usr2_lock);
static atomic64_t a52_usr2_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_usr2_dropped = ATOMIC64_INIT(0);

extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,
					     unsigned int targets);

static size_t a52_usr2_format_line(const struct a52_usr2_event *event,
				   char *line, size_t line_len)
{
	return scnprintf(line, line_len,
		"A52USR2 seq=%llu ns=%llu pid=%d tgid=%d cpu=%u comm=%s msg=%s commit=%08x\\n",
		(unsigned long long)event->seq,
		(unsigned long long)event->monotonic_ns,
		event->pid, event->tgid, event->cpu, event->comm,
		event->message, A52_USR2_COMMIT);
}

static unsigned int a52_usr2_persist_event(const struct a52_usr2_event *event,
					    unsigned int targets)
{
	char line[A52_USR2_LINE_LEN];
	size_t len;

	if (!targets)
		return 0;
	len = a52_usr2_format_line(event, line, sizeof(line));
	return a52_ackfr_ramoops_write(line, len, targets);
}

void a52_ackfr_record(const char *fmt, ...)
{
	struct a52_usr2_event event;
	unsigned long irq_flags;
	unsigned int written;
	va_list args;
	u64 seq;

	seq = (u64)atomic64_inc_return(&a52_usr2_sequence);
	if (seq > A52_USR2_CAPACITY) {
		atomic64_inc(&a52_usr2_dropped);
		return;
	}

	memset(&event, 0, sizeof(event));
	event.seq = seq;
	event.monotonic_ns = ktime_get_ns();
	event.pid = current->pid;
	event.tgid = current->tgid;
	event.cpu = task_cpu(current);
	get_task_comm(event.comm, current);

	va_start(args, fmt);
	vscnprintf(event.message, sizeof(event.message), fmt, args);
	va_end(args);

	written = a52_usr2_persist_event(&event, A52_USR2_BANK_BOTH);
	event.persisted_mask = written & A52_USR2_BANK_BOTH;

	spin_lock_irqsave(&a52_usr2_lock, irq_flags);
	a52_usr2_events[seq - 1] = event;
	spin_unlock_irqrestore(&a52_usr2_lock, irq_flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_record);

static void a52_usr2_write_control(const char *kind)
{
	char line[A52_USR2_LINE_LEN];
	size_t len;

	len = scnprintf(line, sizeof(line),
		"A52USR2 %s release=%s capacity=%u line_len=%u banks=console,ftrace metadata_only=1 commit=%08x\\n",
		kind, init_utsname()->release, A52_USR2_CAPACITY,
		A52_USR2_LINE_LEN, A52_USR2_COMMIT);
	a52_ackfr_ramoops_write(line, len, A52_USR2_BANK_BOTH);
}

static int __init a52_usr2_retry_early_events(void)
{
	struct a52_usr2_event event;
	unsigned long irq_flags;
	unsigned int missing;
	unsigned int written;
	u64 stored;
	u64 cursor;

	a52_usr2_write_control("BOOT_BEGIN");
	stored = (u64)atomic64_read(&a52_usr2_sequence);
	if (stored > A52_USR2_CAPACITY)
		stored = A52_USR2_CAPACITY;

	for (cursor = 1; cursor <= stored; cursor++) {
		spin_lock_irqsave(&a52_usr2_lock, irq_flags);
		event = a52_usr2_events[cursor - 1];
		spin_unlock_irqrestore(&a52_usr2_lock, irq_flags);
		if (event.seq != cursor)
			continue;
		missing = A52_USR2_BANK_BOTH & ~event.persisted_mask;
		if (!missing)
			continue;
		written = a52_usr2_persist_event(&event, missing);
		if (!written)
			continue;
		spin_lock_irqsave(&a52_usr2_lock, irq_flags);
		a52_usr2_events[cursor - 1].persisted_mask |= written;
		spin_unlock_irqrestore(&a52_usr2_lock, irq_flags);
	}

	a52_usr2_write_control("BOOT_READY");
	pr_info("unified secure recorder enabled stored=%llu dropped=%llu\\n",
		(unsigned long long)stored,
		(unsigned long long)atomic64_read(&a52_usr2_dropped));
	return 0;
}
late_initcall(a52_usr2_retry_early_events);
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_trace_module():
    path = Path(__file__).with_name("124_apply_a52xq_ion_qsee_runtime_trace.py")
    spec = importlib.util.spec_from_file_location("a52_trace124", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load trace helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch_ramoops(path: Path) -> dict[str, object]:
    text = read(path)
    if MARKER not in text:
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
        if anchor not in text:
            raise SystemExit("ACK 5.10 ramoops context anchor missing")
        addition = anchor + r'''
/* A52_ACK_UNIFIED_SECURE_RECORDER_V2 */
#define A52_ACKFR_RAMOOPS_MAX_WRITE 256U
#define A52_ACKFR_BANK_CONSOLE BIT(0)
#define A52_ACKFR_BANK_FTRACE BIT(1)

unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,
                                     unsigned int targets)
{
\tstruct persistent_ram_zone **fprzs;
\tstruct persistent_ram_zone *console;
\tstruct persistent_ram_zone *ftrace = NULL;
\tunsigned int written = 0;

\tif (!buf || !len)
\t\treturn 0;
\tif (len > A52_ACKFR_RAMOOPS_MAX_WRITE)
\t\tlen = A52_ACKFR_RAMOOPS_MAX_WRITE;
\tif (!targets)
\t\ttargets = A52_ACKFR_BANK_CONSOLE | A52_ACKFR_BANK_FTRACE;

\tconsole = READ_ONCE(oops_cxt.cprz);
\tfprzs = READ_ONCE(oops_cxt.fprzs);
\tif (fprzs)
\t\tftrace = READ_ONCE(fprzs[0]);

\tif ((targets & A52_ACKFR_BANK_CONSOLE) && console) {
\t\tpersistent_ram_write(console, buf, len);
\t\twritten |= A52_ACKFR_BANK_CONSOLE;
\t}
\tif ((targets & A52_ACKFR_BANK_FTRACE) && ftrace) {
\t\tpersistent_ram_write(ftrace, buf, len);
\t\twritten |= A52_ACKFR_BANK_FTRACE;
\t}
\twmb();
\treturn written;
}
EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);
'''.replace(r"\t", "\t")
        text = text.replace(anchor, addition, 1)

    old_console = '''\tif (record->type == PSTORE_TYPE_CONSOLE) {
\t\tif (!cxt->cprz)
\t\t\treturn -ENOMEM;
\t\tpersistent_ram_write(cxt->cprz, record->buf, record->size);
\t\treturn 0;
\t} else if (record->type == PSTORE_TYPE_FTRACE) {
\t\tint zonenum;

\t\tif (!cxt->fprzs)
\t\t\treturn -ENOMEM;
\t\t/*
\t\t * Choose zone by if we're using per-cpu buffers.
\t\t */
\t\tif (cxt->flags & RAMOOPS_FLAG_FTRACE_PER_CPU)
\t\t\tzonenum = smp_processor_id();
\t\telse
\t\t\tzonenum = 0;

\t\tpersistent_ram_write(cxt->fprzs[zonenum], record->buf,
\t\t\t\t     record->size);
\t\treturn 0;
'''
    reserved = '''\tif (record->type == PSTORE_TYPE_CONSOLE) {
\t\t/* A52_ACK_UNIFIED_SECURE_RECORDER_V2: reserve mirrored bank A. */
\t\treturn 0;
\t} else if (record->type == PSTORE_TYPE_FTRACE) {
\t\t/* A52_ACK_UNIFIED_SECURE_RECORDER_V2: reserve mirrored bank B. */
\t\treturn 0;
'''
    if old_console in text:
        text = text.replace(old_console, reserved, 1)
    elif "reserve mirrored bank A" not in text:
        raise SystemExit("ACK ramoops console/ftrace write anchor missing")

    write(path, text)
    checks = {
        "direct_writer_exported": "EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);" in text,
        "console_bank_reserved": "reserve mirrored bank A" in text,
        "ftrace_bank_reserved": "reserve mirrored bank B" in text,
        "panic_dmesg_path_retained": "ramoops_write_kmsg_hdr(prz, record)" in text,
        "pmsg_path_retained": "persistent_ram_write_user(cxt->mprz, buf, record->size)" in text,
    }
    failed = [key for key, value in checks.items() if not value]
    if failed:
        raise SystemExit("unified ramoops audit failed: " + ", ".join(failed))
    return {"source": str(RAMOOPS_REL), "checks": checks}


def ensure_include(text: str) -> str:
    include = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if include in text:
        return text
    anchors = ('#include <linux/module.h>\n', '#include <linux/kernel.h>\n')
    for anchor in anchors:
        if anchor in text:
            return text.replace(anchor, anchor + include, 1)
    return include + text


def inject_custom_entry(trace, text: str, name: str, fmt_suffix: str,
                        args: str) -> tuple[str, str]:
    found = trace.function_definition(text, name)
    if found is None:
        return text, "missing"
    _, opening, _, _ = found
    marker = f"A52USR2 enter fn={name}"
    if marker in text[opening : opening + 1600]:
        return text, "already-present"
    fmt = marker + (f" {fmt_suffix}" if fmt_suffix else "")
    call_args = f", {args}" if args else ""
    declaration = (
        f'\n\tint __a52_usr2_entry_{trace.identifier(name)} __maybe_unused = '
        f'(a52_ackfr_record("{fmt}"{call_args}), 0);'
    )
    return text[: opening + 1] + declaration + text[opening + 1 :], "inserted"


def patch_boundary_file(trace, root: Path, relative: Path,
                        specs: tuple[tuple[str, str, str], ...],
                        domain: str) -> dict[str, object]:
    path = root / relative
    if not path.is_file():
        return {"source": str(relative), "missing_file": True, "functions": []}
    text = ensure_include(read(path))
    staged = []
    for name, fmt_suffix, args in specs:
        text, state = inject_custom_entry(trace, text, name, fmt_suffix, args)
        exits = 0
        if state != "missing":
            text, exits = trace.inject_simple_returns(text, domain, name)
        staged.append({"name": name, "entry": state, "exit_count": exits})
    write(path, text)
    return {"source": str(relative), "missing_file": False, "functions": staged}


def stage_recorder(root: Path) -> dict[str, object]:
    write(root / HEADER_REL, HEADER)
    write(root / SOURCE_REL, SOURCE)
    source = read(root / SOURCE_REL)
    checks = {
        "immediate_write": "a52_usr2_persist_event(&event, A52_USR2_BANK_BOTH)" in source,
        "early_retry": "a52_usr2_retry_early_events" in source,
        "no_delayed_work": "delayed_work" not in source and "schedule_delayed_work" not in source,
        "metadata_only": all(token not in source for token in (
            "cmd_req_buf", "resp_buf", "/proc/", "copy_from_user", "process_vm_readv"
        )),
        "bounded": f"#define A52_USR2_CAPACITY {CAPACITY}U" in source,
        "commit_marker": "A52_USR2_COMMIT" in source,
    }
    failed = [key for key, value in checks.items() if not value]
    if failed:
        raise SystemExit("recorder source audit failed: " + ", ".join(failed))
    return {
        "header": str(HEADER_REL),
        "source": str(SOURCE_REL),
        "capacity": CAPACITY,
        "line_len": LINE_LEN,
        "banks": ["ramoops-console", "ramoops-ftrace"],
        "checks": checks,
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ram = root / RAMOOPS_REL
        ram.parent.mkdir(parents=True)
        ram.write_text('''#include <linux/kernel.h>\nstatic int notrace ramoops_pstore_write(struct pstore_record *record)\n{\n\tstruct ramoops_context *cxt = record->psi->data;\n\tstruct persistent_ram_zone *prz;\n\tsize_t size, hlen;\n\tif (record->type == PSTORE_TYPE_CONSOLE) {\n\t\tif (!cxt->cprz)\n\t\t\treturn -ENOMEM;\n\t\tpersistent_ram_write(cxt->cprz, record->buf, record->size);\n\t\treturn 0;\n\t} else if (record->type == PSTORE_TYPE_FTRACE) {\n\t\tint zonenum;\n\n\t\tif (!cxt->fprzs)\n\t\t\treturn -ENOMEM;\n\t\t/*\n\t\t * Choose zone by if we're using per-cpu buffers.\n\t\t */\n\t\tif (cxt->flags & RAMOOPS_FLAG_FTRACE_PER_CPU)\n\t\t\tzonenum = smp_processor_id();\n\t\telse\n\t\t\tzonenum = 0;\n\n\t\tpersistent_ram_write(cxt->fprzs[zonenum], record->buf,\n\t\t\t\t     record->size);\n\t\treturn 0;\n\t}\n\thlen = ramoops_write_kmsg_hdr(prz, record);\n\treturn persistent_ram_write_user(cxt->mprz, buf, record->size);\n}\nstatic struct ramoops_context oops_cxt = {\n\t.pstore = {\n\t\t.owner\t= THIS_MODULE,\n\t\t.name\t= "ramoops",\n\t\t.open\t= ramoops_pstore_open,\n\t\t.read\t= ramoops_pstore_read,\n\t\t.write\t= ramoops_pstore_write,\n\t\t.write_user\t= ramoops_pstore_write_user,\n\t\t.erase\t= ramoops_pstore_erase,\n\t},\n};\n''')
        first = patch_ramoops(ram)
        second = patch_ramoops(ram)
        if not first["checks"]["direct_writer_exported"] or not second["checks"]["direct_writer_exported"]:
            raise SystemExit("ramoops patch self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    required = (HEADER_REL, SOURCE_REL, RAMOOPS_REL)
    missing = [str(item) for item in required if not (root / item).is_file()]
    if missing:
        raise SystemExit("workflow 123 staging must run first; missing: " + ", ".join(missing))

    trace = load_trace_module()
    report = {
        "status": "ack-unified-secure-startup-recorder-v2-staged",
        "hardware_validated": False,
        "marker": MARKER,
        "recorder": stage_recorder(root),
        "ramoops": patch_ramoops(root / RAMOOPS_REL),
        "boundaries": {},
        "privacy": {
            "metadata_only": True,
            "captures_command_buffers": False,
            "captures_response_buffers": False,
            "captures_key_material": False,
            "captures_auth_tokens": False,
            "captures_process_memory": False,
        },
    }

    # Explicit boundary specifications. Each expression is a complete printf
    # argument list after the fixed format fragment.
    report["boundaries"] = {
        "shmbridge": patch_boundary_file(
            trace, root, Path("drivers/a52_secure/qtee_shmbridge.c"),
            (
                ("qtee_shmbridge_enable", "enable=%d", "enable"),
                ("qtee_shmbridge_register", "size=%zu ns_vmid_num=%u tz_perm=0x%x", "size, ns_vmid_num, tz_perm"),
                ("qtee_shmbridge_deregister", "handle_nonzero=%d", "handle != 0"),
                ("qtee_shmbridge_allocate_shm", "size=%zu", "size"),
                ("qtee_shmbridge_init", "stage=init", ""),
            ), "SHMBRIDGE"
        ),
        "secure_buffer": patch_boundary_file(
            trace, root, Path("drivers/a52_secure/secure_buffer.c"),
            (
                ("secure_buffer_change_chunk", "nchunks=%u chunk_size=%u lock=%d", "nchunks, chunk_size, lock"),
                ("secure_buffer_change_table", "nents=%u lock=%d", "table ? table->nents : 0, lock"),
                ("msm_secure_table", "nents=%u", "table ? table->nents : 0"),
                ("msm_unsecure_table", "nents=%u", "table ? table->nents : 0"),
                ("batched_hyp_assign", "nents=%u", "table ? table->nents : 0"),
                ("__hyp_assign_table", "nents=%u src=%d dst=%d try=%d", "table ? table->nents : 0, source_nelems, dest_nelems, try_lock"),
                ("hyp_assign_phys", "size=%llu src=%d dst=%d", "(unsigned long long)size, source_nelems, dest_nelems"),
            ), "SECUREBUF"
        ),
        "qsee_scm": patch_boundary_file(
            trace, root, Path("drivers/a52_secure/qseecom.c"),
            (
                ("__qseecom_scm_call2_locked", "smc=0x%x arginfo=0x%x", "smc_id, desc ? desc->arginfo : 0"),
                ("qseecom_scm_call2", "svc=0x%x tz_cmd=0x%x", "svc_id, tz_cmd_id"),
                ("qseecom_scm_call", "svc=0x%x tz_cmd=0x%x", "svc_id, tz_cmd_id"),
            ), "SCM"
        ),
    }

    # At least the critical runtime boundary files must exist and expose one
    # instrumented function each. Missing optional symbols remain visible in
    # the report instead of failing the entire port.
    for group in ("shmbridge", "secure_buffer", "qsee_scm"):
        item = report["boundaries"][group]
        if item["missing_file"]:
            raise SystemExit(f"critical boundary file missing: {item['source']}")
        if not any(fn["entry"] != "missing" for fn in item["functions"]):
            raise SystemExit(f"no recorder entries staged for {group}")

    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
