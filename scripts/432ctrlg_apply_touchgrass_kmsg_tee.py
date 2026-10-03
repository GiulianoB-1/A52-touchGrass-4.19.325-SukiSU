#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE432_CTRL_TG_KMSG_ONLY_V1"
REL = Path("kernel/printk/printk.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"P432C-TG {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE432_CTRL_TG_KMSG_ONLY_V1
 *
 * TouchGrass Golden control companion for GKI Phase432-CTRL.
 * Scope is intentionally tiny: tee selected /dev/kmsg userspace writes into
 * recovery-persistent reserved RAM before devkmsg ratelimiting.
 *
 * No printk from this path, no allocation, no task walk, no stack/register
 * inspection, no display/DRM/QSEECOM hooks, and the real iov_iter is untouched.
 */
#define A52_P432CG_RAM_PHYS          0xB1400000ULL
#define A52_P432CG_RAM_BYTES         0x00040000U
#define A52_P432CG_HEADER_BYTES      0x00001000U
#define A52_P432CG_RECORD_BYTES      128U
#define A52_P432CG_CAPACITY          ((A52_P432CG_RAM_BYTES - A52_P432CG_HEADER_BYTES) / A52_P432CG_RECORD_BYTES)
#define A52_P432CG_MAGIC             0x47434D4B43323334ULL
#define A52_P432CG_VERSION           1U
#define A52_P432CG_HEADER_COMMIT     0x432c600dU
#define A52_P432CG_RECORD_COMMIT     0x432c0de5U
#define A52_P432CG_INIT_LIMIT        600U
#define A52_P432CG_COPY              192U

struct a52_p432cg_header {
	u64 magic;
	u32 version;
	u32 record_bytes;
	u32 capacity;
	u32 reserved0;
	u64 boot_token;
	u32 commit;
	u32 reserved1;
	u8 pad[24];
} __packed;

struct a52_p432cg_record {
	u64 seq;
	u64 ts_ns;
	u64 boot_token;
	u32 pid;
	u32 tgid;
	u16 cpu;
	u16 len;
	char comm[16];
	char text[68];
	u32 commit;
	u32 reserved;
} __packed;

static void __iomem *a52_p432cg_ram;
static u64 a52_p432cg_boot_token;
static atomic_t a52_p432cg_seq = ATOMIC_INIT(0);
static atomic_t a52_p432cg_init_seen = ATOMIC_INIT(0);

static bool a52_p432cg_source(void)
{
	return !strcmp(current->comm, "init") ||
	       !strcmp(current->comm, "ueventd") ||
	       !strcmp(current->comm, "bootanimation") ||
	       !strcmp(current->comm, "surfaceflinger");
}

static bool a52_p432cg_priority(const char *s)
{
	return strstr(s, "took") || strstr(s, "exited") ||
	       strstr(s, "killed") || strstr(s, "Wait for") ||
	       strstr(s, "wait_for_prop") || strstr(s, "starting service") ||
	       strstr(s, "processing action") || strstr(s, "bootanim") ||
	       strstr(s, "SurfaceFlinger") || strstr(s, "surfaceflinger") ||
	       strstr(s, "odsign") || strstr(s, "odrefresh") ||
	       strstr(s, "init_user0") || strstr(s, "restorecon") ||
	       strstr(s, "fsverity") || strstr(s, "apexd") ||
	       strstr(s, "keystore") || strstr(s, "vold") ||
	       strstr(s, "keymaster") || strstr(s, "KeyMint") ||
	       strstr(s, "keymint") || strstr(s, "qseecom");
}

static void a52_p432cg_append(const char *text)
{
	struct a52_p432cg_record rec;
	unsigned int seq;
	size_t len;
	void __iomem *dst;

	if (!a52_p432cg_ram || !text)
		return;

	seq = (unsigned int)atomic_inc_return(&a52_p432cg_seq);
	if (!seq || seq > A52_P432CG_CAPACITY)
		return;

	memset(&rec, 0, sizeof(rec));
	rec.seq = seq;
	rec.ts_ns = ktime_get_boot_ns();
	rec.boot_token = a52_p432cg_boot_token;
	rec.pid = current->pid;
	rec.tgid = current->tgid;
	rec.cpu = (u16)raw_smp_processor_id();
	get_task_comm(rec.comm, current);
	len = strnlen(text, sizeof(rec.text) - 1U);
	rec.len = (u16)len;
	memcpy(rec.text, text, len);
	rec.text[len] = '\0';
	rec.commit = A52_P432CG_RECORD_COMMIT;

	dst = (u8 __iomem *)a52_p432cg_ram + A52_P432CG_HEADER_BYTES +
	      (seq - 1U) * A52_P432CG_RECORD_BYTES;
	memcpy_toio(dst, &rec, sizeof(rec));
	wmb();
}

static void a52_p432cg_capture(struct iov_iter *from)
{
	struct iov_iter mirror;
	char text[A52_P432CG_COPY];
	size_t n;
	unsigned int seen = 0;
	bool priority;
	bool init_writer;

	if (!from || !a52_p432cg_ram || !a52_p432cg_source())
		return;

	mirror = *from;
	n = min_t(size_t, iov_iter_count(&mirror), sizeof(text) - 1U);
	if (!n || copy_from_iter(text, n, &mirror) != n)
		return;
	text[n] = '\0';

	priority = a52_p432cg_priority(text);
	init_writer = !strcmp(current->comm, "init");
	if (init_writer) {
		seen = (unsigned int)atomic_inc_return(&a52_p432cg_init_seen);
		if (seen > A52_P432CG_INIT_LIMIT && !priority)
			return;
	} else if (!priority) {
		return;
	}

	a52_p432cg_append(text);
}

static int __init a52_p432cg_init(void)
{
	struct a52_p432cg_header h;

	BUILD_BUG_ON(sizeof(struct a52_p432cg_header) != 64U);
	BUILD_BUG_ON(sizeof(struct a52_p432cg_record) != A52_P432CG_RECORD_BYTES);
	a52_p432cg_ram = ioremap_cache(A52_P432CG_RAM_PHYS, A52_P432CG_RAM_BYTES);
	if (!a52_p432cg_ram)
		return 0;

	a52_p432cg_boot_token = ktime_get_boot_ns();
	memset(&h, 0, sizeof(h));
	h.magic = A52_P432CG_MAGIC;
	h.version = A52_P432CG_VERSION;
	h.record_bytes = A52_P432CG_RECORD_BYTES;
	h.capacity = A52_P432CG_CAPACITY;
	h.boot_token = a52_p432cg_boot_token;
	h.commit = A52_P432CG_HEADER_COMMIT;
	memcpy_toio(a52_p432cg_ram, &h, sizeof(h));
	wmb();
	return 0;
}
subsys_initcall(a52_p432cg_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        "#include <linux/uio.h>\n",
        "#include <linux/uio.h>\n#include <linux/io.h>\n#include <linux/timekeeping.h>\n",
        "includes",
    )

    anchor = "static ssize_t devkmsg_write(struct kiocb *iocb, struct iov_iter *from)\n"
    if anchor not in text:
        raise SystemExit("P432C-TG devkmsg_write missing")
    text = text.replace(anchor, BLOCK + "\n" + anchor, 1)

    hook = """\tif (!user || len > LOG_LINE_MAX)\n\t\treturn -EINVAL;\n\n\t/* Ignore when user logging is disabled. */\n"""
    repl = """\tif (!user || len > LOG_LINE_MAX)\n\t\treturn -EINVAL;\n\n\ta52_p432cg_capture(from);\n\n\t/* Ignore when user logging is disabled. */\n"""
    text = one(text, hook, repl, "pre-ratelimit hook")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_P432CG_RAM_PHYS          0xB1400000ULL",
        "A52_P432CG_INIT_LIMIT        600U",
        "a52_p432cg_capture(from);",
        "mirror = *from;",
        "ktime_get_boot_ns()",
        "odsign",
        "odrefresh",
        "init_user0",
        "restorecon",
        "fsverity",
        "apexd",
        "keystore",
        "vold",
        "keymaster",
        "KeyMint",
        "qseecom",
    ):
        if token not in text:
            raise SystemExit("P432C-TG token missing: " + token)

    hook = text.index("a52_p432cg_capture(from);")
    off = text.index("if (devkmsg_log & DEVKMSG_LOG_MASK_OFF)", hook)
    rate = text.index("___ratelimit", off)
    if not hook < off < rate:
        raise SystemExit("P432C-TG hook is not before devkmsg off/ratelimit handling")

    for forbidden in (
        "stack_trace_save_tsk",
        "task_pt_regs",
        "get_wchan",
        "try_get_task_stack",
    ):
        block = text[text.index(MARK):text.index("static ssize_t devkmsg_write", text.index(MARK))]
        if forbidden in block:
            raise SystemExit("P432C-TG forbidden task inspection: " + forbidden)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    path = ns.root / REL
    if not path.is_file():
        raise SystemExit("P432C-TG source missing: " + str(path))
    text = path.read_text(errors="replace")
    if not ns.check_only:
        text = patch(text)
        path.write_text(text)
    validate(path.read_text(errors="replace"))
    print("Phase432-CTRL TouchGrass Golden KMSG-only tee: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
