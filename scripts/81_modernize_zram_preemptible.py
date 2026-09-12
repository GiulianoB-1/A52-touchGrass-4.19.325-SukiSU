#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 81_modernize_zram_preemptible.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
zram_c = root / "drivers/block/zram/zram_drv.c"
zcomp_c = root / "drivers/block/zram/zcomp.c"
zcomp_h = root / "drivers/block/zram/zcomp.h"

for p in (zram_c, zcomp_c, zcomp_h):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

# Samsung's A52 tree already stores the slot lock in table[index].flags.
# Convert that bit from a spinning bitlock to a wait-on-bit lock.  This keeps
# the table ABI/size unchanged but allows the lock owner to schedule.
s = zram_c.read_text()
if "#include <linux/wait_bit.h>" not in s:
    anchor = "#include <linux/bitops.h>\n"
    if anchor not in s:
        raise SystemExit("zram bitops include anchor missing")
    s = s.replace(anchor, anchor + "#include <linux/wait_bit.h>\n", 1)

old = """static int zram_slot_trylock(struct zram *zram, u32 index)
{
\treturn bit_spin_trylock(ZRAM_LOCK, &zram->table[index].flags);
}

static void zram_slot_lock(struct zram *zram, u32 index)
{
\tbit_spin_lock(ZRAM_LOCK, &zram->table[index].flags);
}

static void zram_slot_unlock(struct zram *zram, u32 index)
{
\tbit_spin_unlock(ZRAM_LOCK, &zram->table[index].flags);
}
"""
new = """/*
 * Phase81: sleepable per-entry locking from the 2025 zram preemption
 * series.  Keep Samsung's compact bit-in-flags representation, but wait
 * instead of spinning.  trylock remains suitable for atomic callers.
 */
static bool zram_slot_trylock(struct zram *zram, u32 index)
{
\treturn !test_and_set_bit_lock(ZRAM_LOCK, &zram->table[index].flags);
}

static void zram_slot_lock(struct zram *zram, u32 index)
{
\twait_on_bit_lock(&zram->table[index].flags, ZRAM_LOCK,
\t\t\t TASK_UNINTERRUPTIBLE);
}

static void zram_slot_unlock(struct zram *zram, u32 index)
{
\tclear_and_wake_up_bit(ZRAM_LOCK, &zram->table[index].flags);
}
"""
if old not in s:
    raise SystemExit("Samsung zram slot-lock block changed")
s = s.replace(old, new, 1)

put_count = s.count("zcomp_stream_put(zram->comp);")
if put_count < 1:
    raise SystemExit("no legacy zcomp_stream_put(zram->comp) calls found")
s = s.replace("zcomp_stream_put(zram->comp);", "zcomp_stream_put(zstrm);")
zram_c.write_text(s)

# The current Samsung frontend allocates a pointer per CPU and pins the CPU
# for the whole compression operation.  Replace it with the 2025 model:
# a persistent per-CPU stream object protected by a mutex.  A task may be
# preempted/migrated while compression runs; CPU hot-unplug waits for users.
h = zcomp_h.read_text()
if "#include <linux/mutex.h>" not in h:
    h = h.replace("#define _ZCOMP_H_\n", "#define _ZCOMP_H_\n\n#include <linux/mutex.h>\n", 1)

h = h.replace(
"""struct zcomp_strm {
\t/* compression/decompression buffer */
\tvoid *buffer;
\tstruct crypto_comp *tfm;
\tvoid *tmpbuf;
};
""",
"""struct zcomp_strm {
\t/* Serialize use against CPU hot-unplug without disabling preemption. */
\tstruct mutex lock;
\t/* compression/decompression buffer */
\tvoid *buffer;
\tstruct crypto_comp *tfm;
\tvoid *tmpbuf;
};
""",
1)

old_stream = "\tstruct zcomp_strm * __percpu *stream;\n"
new_stream = "\tstruct zcomp_strm __percpu *stream;\n"
if old_stream not in h:
    raise SystemExit("zcomp per-cpu stream declaration changed")
h = h.replace(old_stream, new_stream, 1)

old_put = "void zcomp_stream_put(struct zcomp *comp);\n"
new_put = "void zcomp_stream_put(struct zcomp_strm *zstrm);\n"
if old_put not in h:
    raise SystemExit("zcomp_stream_put prototype changed")
h = h.replace(old_put, new_put, 1)
zcomp_h.write_text(h)

c = zcomp_c.read_text()
c = c.replace("#include <linux/cpu.h>\n", "#include <linux/cpuhotplug.h>\n", 1)

start = c.find("static void zcomp_strm_free(")
end = c.find("bool zcomp_available_algorithm(", start)
if start < 0 or end < 0:
    raise SystemExit("zcomp stream init/free region not found")

new_region = r'''static void zcomp_strm_free(struct zcomp_strm *zstrm)
{
	if (!IS_ERR_OR_NULL(zstrm->tfm))
		crypto_free_comp(zstrm->tfm);
	zstrm->tfm = NULL;

	if (zstrm->buffer) {
		free_pages((unsigned long)zstrm->buffer, 1);
		zstrm->buffer = NULL;
	}

	if (zstrm->tmpbuf) {
		free_pages((unsigned long)zstrm->tmpbuf, 1);
		zstrm->tmpbuf = NULL;
	}
}

/* Initialize storage owned by a persistent per-CPU stream object. */
static int zcomp_strm_init(struct zcomp *comp, struct zcomp_strm *zstrm)
{
	zstrm->tfm = crypto_alloc_comp(comp->name, 0, 0);
	/*
	 * Allocate two pages for compressed output plus Samsung's existing
	 * temporary buffer used by the vendor zram paths.
	 */
	zstrm->buffer = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, 1);
	zstrm->tmpbuf = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, 1);
	if (IS_ERR_OR_NULL(zstrm->tfm) || !zstrm->buffer || !zstrm->tmpbuf) {
		zcomp_strm_free(zstrm);
		return -ENOMEM;
	}

	return 0;
}

'''
c = c[:start] + new_region + c[end:]

old_get_put = """struct zcomp_strm *zcomp_stream_get(struct zcomp *comp)
{
\treturn *get_cpu_ptr(comp->stream);
}

void zcomp_stream_put(struct zcomp *comp)
{
\tput_cpu_ptr(comp->stream);
}
"""
new_get_put = r'''struct zcomp_strm *zcomp_stream_get(struct zcomp *comp)
{
	for (;;) {
		struct zcomp_strm *zstrm = raw_cpu_ptr(comp->stream);

		/*
		 * We may migrate between raw_cpu_ptr() and mutex_lock().  That is
		 * fine: the stream object is persistent, and CPU teardown must take
		 * the same mutex before releasing its backend resources.
		 */
		mutex_lock(&zstrm->lock);
		if (likely(zstrm->buffer))
			return zstrm;
		mutex_unlock(&zstrm->lock);
		cond_resched();
	}
}

void zcomp_stream_put(struct zcomp_strm *zstrm)
{
	mutex_unlock(&zstrm->lock);
}
'''
if old_get_put not in c:
    raise SystemExit("legacy zcomp get/put block changed")
c = c.replace(old_get_put, new_get_put, 1)

start = c.find("int zcomp_cpu_up_prepare(")
end = c.find("void zcomp_destroy(", start)
if start < 0 or end < 0:
    raise SystemExit("zcomp CPU lifecycle region not found")

new_lifecycle = r'''int zcomp_cpu_up_prepare(unsigned int cpu, struct hlist_node *node)
{
	struct zcomp *comp = hlist_entry(node, struct zcomp, node);
	struct zcomp_strm *zstrm = per_cpu_ptr(comp->stream, cpu);
	int ret;

	mutex_lock(&zstrm->lock);
	if (WARN_ON(zstrm->buffer)) {
		mutex_unlock(&zstrm->lock);
		return 0;
	}

	ret = zcomp_strm_init(comp, zstrm);
	mutex_unlock(&zstrm->lock);
	if (ret)
		pr_err("Can't allocate a compression stream\n");
	return ret;
}

int zcomp_cpu_dead(unsigned int cpu, struct hlist_node *node)
{
	struct zcomp *comp = hlist_entry(node, struct zcomp, node);
	struct zcomp_strm *zstrm = per_cpu_ptr(comp->stream, cpu);

	mutex_lock(&zstrm->lock);
	zcomp_strm_free(zstrm);
	mutex_unlock(&zstrm->lock);
	return 0;
}

static int zcomp_init(struct zcomp *comp)
{
	int ret, cpu;

	comp->stream = alloc_percpu(struct zcomp_strm);
	if (!comp->stream)
		return -ENOMEM;

	for_each_possible_cpu(cpu) {
		struct zcomp_strm *zstrm = per_cpu_ptr(comp->stream, cpu);

		memset(zstrm, 0, sizeof(*zstrm));
		mutex_init(&zstrm->lock);
	}

	ret = cpuhp_state_add_instance(CPUHP_ZCOMP_PREPARE, &comp->node);
	if (ret < 0)
		goto cleanup;
	return 0;

cleanup:
	free_percpu(comp->stream);
	comp->stream = NULL;
	return ret;
}

'''
c = c[:start] + new_lifecycle + c[end:]

zcomp_c.write_text(c)

# Structural checks: these are deliberately specific so later Samsung source
# changes fail loudly instead of silently producing a half-port.
zc = zram_c.read_text()
cc = zcomp_c.read_text()
hh = zcomp_h.read_text()
checks = [
    ("sleepable entry lock missing", "wait_on_bit_lock(&zram->table[index].flags" in zc),
    ("wake-up unlock missing", "clear_and_wake_up_bit(ZRAM_LOCK" in zc),
    ("legacy spinning slot lock remains", "bit_spin_lock(ZRAM_LOCK" not in zc),
    ("legacy CPU pinning remains", "get_cpu_ptr(comp->stream)" not in cc),
    ("legacy CPU unpinning remains", "put_cpu_ptr(comp->stream)" not in cc),
    ("stream mutex missing", "struct mutex lock;" in hh),
    ("percpu embedded stream missing", "struct zcomp_strm __percpu *stream;" in hh),
    ("mutex stream get missing", "mutex_lock(&zstrm->lock);" in cc),
    ("zram call sites not converted", "zcomp_stream_put(zram->comp);" not in zc),
]
for label, ok in checks:
    if not ok:
        raise SystemExit(label)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "phase81-zram-preemptible.txt").write_text(
    "zram_entry_lock=sleepable-wait-on-bit\n"
    "zram_table_layout=preserved-samsung\n"
    "zcomp_streams=preemptible-mutex-protected\n"
    "zcomp_cpu_hotplug=mutex-serialized\n"
    f"zcomp_put_calls_converted={put_count}\n"
    "samsung_dedup=preserved\n"
    "samsung_lru_writeback=preserved\n"
)

print("Phase81 Samsung ZRAM preemptibility foundation applied")
