#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 91_apply_boot_diag.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
ram = root / "fs/pstore/ram.c"
main = root / "init/main.c"
super_c = root / "fs/f2fs/super.c"
segment_c = root / "fs/f2fs/segment.c"
for p in (ram, main, super_c, segment_c):
    if not p.is_file():
        raise SystemExit(f"missing {p}")

def replace_once(path, old, new, label):
    s = path.read_text()
    if new in s and old not in s:
        return
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    path.write_text(s.replace(old, new, 1))

# ---------------------------------------------------------------------------
# Direct persistent console in OrangeFox's proven 1MiB ramoops reservation.
# 4.19 persistent_ram_new() has the 6-argument API.
# ---------------------------------------------------------------------------
s = ram.read_text()
if "#include <linux/console.h>\n" not in s:
    anchors = (
        "#include <linux/compiler.h>\n#include <linux/pstore_ram.h>\n",
        "#include <linux/pstore.h>\n#include <linux/pstore_ram.h>\n",
    )
    for a in anchors:
        if a in s:
            s = s.replace(a, a.split("\n")[0] + "\n#include <linux/console.h>\n" + "\n".join(a.split("\n")[1:]), 1)
            break
    else:
        raise SystemExit("ram.c: console include anchor missing")

diag = r'''
#define A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL
#define A52_DIAG_CONSOLE_SIZE 0x00040000UL
#define A52_DIAG_LINE_SIZE 256

static struct persistent_ram_zone *a52_diag_prz;

void a52_persistent_diag_mark(const char *fmt, ...)
{
	char line[A52_DIAG_LINE_SIZE];
	va_list args;
	int len;

	if (IS_ERR_OR_NULL(a52_diag_prz))
		return;

	va_start(args, fmt);
	len = vscnprintf(line, sizeof(line), fmt, args);
	va_end(args);

	if (len > 0) {
		persistent_ram_write(a52_diag_prz, line, len);
		wmb();
	}
}

static void a52_diag_console_write(struct console *con, const char *s,
				   unsigned int count)
{
	if (IS_ERR_OR_NULL(a52_diag_prz) || !count)
		return;
	persistent_ram_write(a52_diag_prz, s, count);
	wmb();
}

static struct console a52_diag_console = {
	.name = "a52diag",
	.write = a52_diag_console_write,
	.flags = CON_PRINTBUFFER | CON_ENABLED | CON_ANYTIME,
	.index = -1,
};

int __init a52_persistent_diag_init(void)
{
	struct persistent_ram_ecc_info ecc = { };

	a52_diag_prz = persistent_ram_new(A52_DIAG_CONSOLE_PHYS,
					   A52_DIAG_CONSOLE_SIZE, 0, &ecc,
					   1, 0);
	if (IS_ERR(a52_diag_prz)) {
		int ret = PTR_ERR(a52_diag_prz);
		a52_diag_prz = NULL;
		return ret;
	}

	/* Start each test boot with a fresh producer buffer. */
	persistent_ram_zap(a52_diag_prz);
	a52_diag_prz->type = PSTORE_TYPE_CONSOLE;
	a52_persistent_diag_mark(
		"A52P91 READY phys=0x%llx size=0x%lx kernel=%s\n",
		(unsigned long long)A52_DIAG_CONSOLE_PHYS,
		A52_DIAG_CONSOLE_SIZE, UTS_RELEASE);
	register_console(&a52_diag_console);
	a52_persistent_diag_mark("A52P91 CONSOLE flags=0x%x\n",
				 a52_diag_console.flags);
	return 0;
}

'''
if "A52P91 READY" not in s:
    anchor="static int __init ramoops_init(void)\n"
    if s.count(anchor)!=1:
        raise SystemExit(f"ram.c: ramoops_init anchor count={s.count(anchor)}")
    s=s.replace(anchor,diag+anchor,1)

# Avoid the normal ramoops backend mapping the same reserved area in the test kernel.
if "postcore_initcall(ramoops_init);\n" in s:
    s=s.replace(
        "postcore_initcall(ramoops_init);\n",
        "/* Phase91 uses the dedicated A52 persistent console zone. */\n",
        1,
    )
ram.write_text(s)

# ---------------------------------------------------------------------------
# Kernel initcall breadcrumbs.
# ---------------------------------------------------------------------------
s=main.read_text()
decl=(
    "#if IS_BUILTIN(CONFIG_PSTORE_RAM)\n"
    "extern int __init a52_persistent_diag_init(void);\n"
    "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    "#else\n"
    "static inline int __init a52_persistent_diag_init(void) { return -ENODEV; }\n"
    "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
    "#endif\n\n"
)
anchor="int __init_or_module do_one_initcall(initcall_t fn)\n"
if decl not in s:
    if s.count(anchor)!=1:
        raise SystemExit("init/main.c: do_one_initcall anchor missing")
    s=s.replace(anchor,decl+anchor,1)

old=(
    "\tdo_trace_initcall_start(fn);\n"
    "\tret = fn();\n"
    "\tdo_trace_initcall_finish(fn, ret);\n"
)
new=(
    "\ta52_persistent_diag_mark(\"A52P91 IC begin %pS\\n\", fn);\n"
    "\tdo_trace_initcall_start(fn);\n"
    "\tret = fn();\n"
    "\tdo_trace_initcall_finish(fn, ret);\n"
    "\ta52_persistent_diag_mark(\"A52P91 IC end %pS ret=%d\\n\", fn, ret);\n"
)
if "A52P91 IC begin" not in s:
    if s.count(old)!=1:
        raise SystemExit(f"init/main.c: initcall body anchor count={s.count(old)}")
    s=s.replace(old,new,1)

driver="\tdriver_init();\n\tinit_irq_proc();\n"
driver_new=(
    "\tdriver_init();\n"
    "\tif (a52_persistent_diag_init())\n"
    "\t\tpr_err(\"A52P91 persistent diagnostic init failed\\n\");\n"
    "\telse\n"
    "\t\ta52_persistent_diag_mark(\"A52P91 after driver_init\\n\");\n"
    "\ta52_persistent_diag_mark(\"A52P91 before init_irq_proc\\n\");\n"
    "\tinit_irq_proc();\n"
    "\ta52_persistent_diag_mark(\"A52P91 after init_irq_proc\\n\");\n"
)
if "A52P91 after driver_init" not in s:
    if s.count(driver)!=1:
        raise SystemExit(f"init/main.c: driver init anchor count={s.count(driver)}")
    s=s.replace(driver,driver_new,1)

main.write_text(s)

# ---------------------------------------------------------------------------
# F2FS mount and curseg breadcrumbs.
# ---------------------------------------------------------------------------
for p in (super_c, segment_c):
    text=p.read_text()
    declaration="extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if declaration not in text:
        # Put it after the local includes and before first code.
        marker="#include \"trace.h\"\n"
        if marker in text:
            text=text.replace(marker,marker+"\n"+declaration,1)
        else:
            # fallback: immediately before first static/global function
            pos=text.find("\nstatic ")
            if pos<0:
                raise SystemExit(f"{p.name}: declaration insertion anchor missing")
            text=text[:pos+1]+declaration+"\n"+text[pos+1:]
        p.write_text(text)

s=super_c.read_text()
sig="static int f2fs_fill_super(struct super_block *sb, void *data, int silent)\n{\n"
if "A52P91 F2FS fill enter" not in s:
    if s.count(sig)!=1:
        raise SystemExit("super.c: fill_super signature mismatch")
    s=s.replace(sig,sig+"\ta52_persistent_diag_mark(\"A52P91 F2FS fill enter dev=%pg\\n\", sb->s_bdev);\n",1)

old=(
    "\t/* setup f2fs internal modules */\n"
    "\terr = f2fs_build_segment_manager(sbi);\n"
    "\tif (err) {\n"
)
new=(
    "\t/* setup f2fs internal modules */\n"
    "\ta52_persistent_diag_mark(\"A52P91 F2FS sm begin dev=%pg\\n\", sb->s_bdev);\n"
    "\terr = f2fs_build_segment_manager(sbi);\n"
    "\ta52_persistent_diag_mark(\"A52P91 F2FS sm end err=%d\\n\", err);\n"
    "\tif (err) {\n"
)
if "A52P91 F2FS sm begin" not in s:
    if s.count(old)!=1:
        raise SystemExit("super.c: segment-manager call anchor mismatch")
    s=s.replace(old,new,1)

old="\terr = f2fs_build_node_manager(sbi);\n"
new="\ta52_persistent_diag_mark(\"A52P91 F2FS nm begin\\n\");\n\terr = f2fs_build_node_manager(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS nm end err=%d\\n\", err);\n"
if "A52P91 F2FS nm begin" not in s:
    if s.count(old)!=1:
        raise SystemExit("super.c: node-manager call anchor mismatch")
    s=s.replace(old,new,1)
super_c.write_text(s)

s=segment_c.read_text()
sig="static int build_curseg(struct f2fs_sb_info *sbi)\n{\n"
if "A52P91 F2FS curseg begin" not in s:
    if s.count(sig)!=1:
        raise SystemExit("segment.c: build_curseg signature mismatch")
    s=s.replace(
        sig,
        sig+"\ta52_persistent_diag_mark(\"A52P91 F2FS curseg begin total=%d persist=%d\\n\", NR_CURSEG_TYPE, NR_CURSEG_PERSIST_TYPE);\n",
        1,
    )

old="\treturn restore_curseg_summaries(sbi);\n}\n\nstatic int build_sit_entries"
new=(
    "\ti = restore_curseg_summaries(sbi);\n"
    "\ta52_persistent_diag_mark(\"A52P91 F2FS curseg restore err=%d pinned_inited=%d pinned_seg=%u\\n\",\n"
    "\t\ti, CURSEG_I(sbi, CURSEG_COLD_DATA_PINNED)->inited,\n"
    "\t\tCURSEG_I(sbi, CURSEG_COLD_DATA_PINNED)->segno);\n"
    "\treturn i;\n"
    "}\n\nstatic int build_sit_entries"
)
if "A52P91 F2FS curseg restore" not in s:
    if s.count(old)!=1:
        raise SystemExit("segment.c: restore_curseg return anchor mismatch")
    s=s.replace(old,new,1)

# Trace each major segment-manager step without changing control flow.
repls=[
("\terr = build_sit_info(sbi);\n", "\ta52_persistent_diag_mark(\"A52P91 F2FS sit-info begin\\n\");\n\terr = build_sit_info(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS sit-info end err=%d\\n\", err);\n"),
("\terr = build_free_segmap(sbi);\n", "\ta52_persistent_diag_mark(\"A52P91 F2FS free-map begin\\n\");\n\terr = build_free_segmap(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS free-map end err=%d\\n\", err);\n"),
("\terr = build_curseg(sbi);\n", "\ta52_persistent_diag_mark(\"A52P91 F2FS build-curseg call\\n\");\n\terr = build_curseg(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS build-curseg end err=%d\\n\", err);\n"),
("\terr = build_sit_entries(sbi);\n", "\ta52_persistent_diag_mark(\"A52P91 F2FS sit-entries begin\\n\");\n\terr = build_sit_entries(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS sit-entries end err=%d\\n\", err);\n"),
("\terr = build_dirty_segmap(sbi);\n", "\ta52_persistent_diag_mark(\"A52P91 F2FS dirty-map begin\\n\");\n\terr = build_dirty_segmap(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS dirty-map end err=%d\\n\", err);\n"),
("\terr = sanity_check_curseg(sbi);\n", "\ta52_persistent_diag_mark(\"A52P91 F2FS sanity begin\\n\");\n\terr = sanity_check_curseg(sbi);\n\ta52_persistent_diag_mark(\"A52P91 F2FS sanity end err=%d\\n\", err);\n"),
]
for old,new in repls:
    tag=new.split('A52P91 ')[1].split('\\n')[0]
    if old in s and f"A52P91 {tag}" not in s:
        s=s.replace(old,new,1)
segment_c.write_text(s)

# Audits.
checks={
    ram:["A52P91 READY","persistent_ram_zap(a52_diag_prz);","CON_PRINTBUFFER"],
    main:["A52P91 IC begin","A52P91 after driver_init"],
    super_c:["A52P91 F2FS fill enter","A52P91 F2FS sm begin","A52P91 F2FS nm begin"],
    segment_c:["A52P91 F2FS curseg begin","A52P91 F2FS curseg restore","A52P91 F2FS sanity end"],
}
for p,needles in checks.items():
    text=p.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{p}: missing diagnostic marker {needle}")

report=root.parent.parent/"artifacts"/"phase91-bootdiag.txt"
report.parent.mkdir(parents=True,exist_ok=True)
report.write_text(
    "diag=A52P91\n"
    "persistent_phys=0xB1B40000\n"
    "persistent_size=0x40000\n"
    "producer=4.19-persistent_ram\n"
    "initcalls=begin+end\n"
    "f2fs=fill+segment-manager+curseg+sanity\n"
)
print(report.read_text(),end="")
