#!/usr/bin/env python3
from __future__ import annotations

import base64
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")

# Phase444 storage v2. The first scaffold incorrectly took all of B1900000-
# B1AFFFFF, disabling the proven P392/P414 witnesses. Keep those recorders
# alive and carve only a documented 608 KiB slice from the upper P414 RAM
# window. Samsung debug-partition persistence is disabled: its 10 MiB map has
# no spare 2 MiB extent (1-3 MiB klog, 3-5 MiB summary, 6-8 MiB LPM klog,
# 8-10 MiB P414).
_storage_old = """#define P444_IMAGE_BYTES (2U * SZ_1M)
#define P444_HEADER_BYTES SZ_4K
#define P444_MAX_SECTIONS 56U
#define P444_MAX_HOT 27U
#define P444_RAM_PHYS 0xB1900000ULL
#define P444_RAM_BYTES (2U * SZ_1M)
#define P444_DEBUG_OFFSET 0x00800000ULL"""
_storage_new = """#define P444_IMAGE_BYTES 0x00098000U
#define P444_HEADER_BYTES SZ_4K
#define P444_MAX_SECTIONS 56U
#define P444_MAX_HOT 27U
#define P444_RAM_PHYS 0xB1A62000ULL
#define P444_RAM_BYTES 0x00098000U
#define P444_DEBUG_OFFSET 0x00000000ULL
#define P444_DISK_ENABLED 0
#define P444_STORAGE_V2 1"""
if source.count(_storage_old) != 1:
    raise SystemExit("Phase444 storage macro block count=" + str(source.count(_storage_old)))
source = source.replace(_storage_old, _storage_new, 1)

_disk_block_old = "#if P444_KIND == 1\nstatic int p444_submit_page"
_disk_block_new = "#if P444_KIND == 1 && P444_DISK_ENABLED\nstatic int p444_submit_page"
if source.count(_disk_block_old) != 1:
    raise SystemExit("Phase444 disk compile gate count=" + str(source.count(_disk_block_old)))
source = source.replace(_disk_block_old, _disk_block_new, 1)

# Map DSI PHY only in PRE_DEEP, while the display is known powered. Do not add
# an MMIO mapping operation to early init and never map it for the first time
# from a wedged terminal path.
_phy_init_old = "    p444_phy=ioremap(0x0ae94000ULL,0x1000U);\n    p444_sync_header();"
_phy_init_new = "    p444_sync_header();"
if source.count(_phy_init_old) != 1:
    raise SystemExit("Phase444 early PHY map anchor count=" + str(source.count(_phy_init_old)))
source = source.replace(_phy_init_old, _phy_init_new, 1)
_predeep_anchor = "    if (!p444_ctrl || atomic_read(&p444_state) != 0) return;\n    p444_hdr()->predeep_ns = ktime_get_ns();"
_predeep_new = "    if (!p444_ctrl || atomic_read(&p444_state) != 0) return;\n    if (!p444_phy) p444_phy=ioremap(0x0ae94000ULL,0x1000U);\n    p444_hdr()->predeep_ns = ktime_get_ns();"
if source.count(_predeep_anchor) != 1:
    raise SystemExit("Phase444 PRE_DEEP PHY map anchor count=" + str(source.count(_predeep_anchor)))
source = source.replace(_predeep_anchor, _predeep_new, 1)

# Match the proven persistent-recorder ordering: complete the cache clean to
# DRAM before the experiment advances or a warm reset can occur.
_flush_old = "    __flush_dcache_area((void __force *)p, n);\n    wmb();"
_flush_new = "    __flush_dcache_area((void __force *)p, n);\n    dsb(sy);"
if source.count(_flush_old) != 1:
    raise SystemExit("Phase444 RAM flush anchor count=" + str(source.count(_flush_old)))
source = source.replace(_flush_old, _flush_new, 1)

# Replace the original destructive GKI ownership helper with a partitioning
# helper. P392, P414, P436 and P437 all remain live; only P414's RAM capacity
# is reduced to end exactly where the Phase444 slice starts.
_fn_a = source.find("def retire_gki_reserved(root: Path) -> None:\n")
_fn_b = source.find("\ndef validate(root: Path, kind: str) -> None:\n", _fn_a)
if _fn_a < 0 or _fn_b < 0:
    raise SystemExit("Phase444 reserved helper bounds missing")
_partition_helper = r'''def partition_gki_reserved(root: Path) -> None:
    rec=root/"drivers/a52_secure/a52_ack_secure_flight_recorder.c"
    s=rec.read_text(errors="replace")
    old="#define A52_P414_RAM_BYTES           (SZ_1M - SZ_16K - SZ_8K)"
    new="#define A52_P414_RAM_BYTES           0x00062000U /* A52_PHASE444_RESERVED_PARTITION_V2 */"
    if new not in s:
        if s.count(old)!=1: die("P414 RAM partition anchor count="+str(s.count(old)))
        s=s.replace(old,new,1)
        s += "\n/* A52_PHASE444_RESERVED_PARTITION_V2: P414 B1A00000-B1A61FFF; P444 B1A62000-B1AF9FFF; P437/P436 upper tail preserved. */\n"
        rec.write_text(s)
    if "A52_PHASE444_RESERVED_EXCLUSIVE_V1" in s:
        die("old destructive Phase444 reserved ownership marker present")
'''
source = source[:_fn_a] + _partition_helper + source[_fn_b:]
source = source.replace('if kind=="gki": retire_gki_reserved(root)', 'if kind=="gki": partition_gki_reserved(root)', 1)

# Retarget the expanded patcher's own validation to the v2 layout and make
# preservation of the old witnesses a hard contract.
source = source.replace('"P444_DEBUG_OFFSET 0x00800000ULL","P444_RAM_PHYS 0xB1900000ULL"', '"P444_DISK_ENABLED 0","P444_RAM_PHYS 0xB1A62000ULL","P444_IMAGE_BYTES 0x00098000U"', 1)
_old_validate = '''        if "A52_PHASE444_RESERVED_EXCLUSIVE_V1" not in rec: die("GKI reserved ownership retirement missing")
        if "bool a52_p439_f0 = false;" not in alltxt or "a52_p421_f0 = false;" not in alltxt: die("legacy GKI F0 probes not suppressed")'''
_new_validate = '''        if "A52_PHASE444_RESERVED_PARTITION_V2" not in rec: die("GKI reserved partition marker missing")
        if "A52_PHASE444_RESERVED_EXCLUSIVE_V1" in rec: die("destructive reserved ownership marker leaked")
        if "a52_p392_base = NULL" in rec or "a52_p414_ram = NULL" in rec: die("P392/P414 witness disabled")
        if "a52_p392_base = ioremap_cache(A52_P392_PHYS, A52_P392_BYTES);" not in rec: die("P392 mapping not preserved")
        if "a52_p414_ram = ioremap_cache(A52_P414_RAM_PHYS, A52_P414_RAM_BYTES);" not in rec: die("P414 mapping not preserved")
        if "bool a52_p439_f0 = false;" not in alltxt or "a52_p421_f0 = false;" not in alltxt: die("legacy GKI F0 probes not suppressed")'''
if source.count(_old_validate) != 1:
    raise SystemExit("Phase444 expanded validation anchor count=" + str(source.count(_old_validate)))
source = source.replace(_old_validate, _new_validate, 1)

# Cross-kernel C portability fixes.  The Phase444 C body is shared by GKI
# 5.10 and TouchGrass 4.19, so keep it valid under both toolchains before the
# expanded patcher is executed.
_c90_old = """    struct task_struct *g;
    (void)work;
    bool found=false;"""
_c90_new = """    struct task_struct *g;
    bool found=false;
    (void)work;"""
if source.count(_c90_old) != 1:
    raise SystemExit("Phase444 C90 declaration anchor count=" + str(source.count(_c90_old)))
source = source.replace(_c90_old, _c90_new, 1)

_tg_time_old = "h->init_ns=ktime_get_boottime_ns();"
_tg_time_new = "h->init_ns=ktime_get_ns();"
if source.count(_tg_time_old) != 1:
    raise SystemExit("Phase444 boot-time API anchor count=" + str(source.count(_tg_time_old)))
source = source.replace(_tg_time_old, _tg_time_new, 1)

# Keep the passive SD inventory off the kernel stack. 64 packed records are
# ~2.5 KiB, above GKI's 2048-byte frame limit. Workqueue context may sleep, so
# a temporary GFP_KERNEL allocation is appropriate and preserves the on-disk/
# /proc section schema exactly.
_slab_anchor = "#include <linux/string.h>\n"
if source.count(_slab_anchor) != 1:
    raise SystemExit("Phase444 slab include anchor count=" + str(source.count(_slab_anchor)))
if "#include <linux/slab.h>\n" not in source:
    source = source.replace(_slab_anchor, '#include <linux/slab.h>\n' + _slab_anchor, 1)

_sd_old = """static void p444_sd_workfn(struct work_struct *work)
{
    struct p444_sdrec r[64]; unsigned int n=0, minor; int partno; struct gendisk *gd;
    (void)work; memset(r,0,sizeof(r));
    for (minor=0; minor<256 && n<ARRAY_SIZE(r); minor++) {
        gd=get_gendisk(MKDEV(MMC_BLOCK_MAJOR,minor),&partno);
        if (!gd) continue;
        r[n].minor=minor; r[n].partno=partno; r[n].sectors=get_capacity(gd);
        strlcpy(r[n].name,gd->disk_name,sizeof(r[n].name)); n++;
        put_disk(gd);
    }
    if (n) a52_p444_store_section(P444_STAGE_SD,P444_TYPE_SD,"MMC_MAJOR179",n,0,r,n*sizeof(r[0]));
#if P444_KIND == 1
    p444_checkpoint();
#endif
}"""
_sd_new = """static void p444_sd_workfn(struct work_struct *work)
{
    struct p444_sdrec *r;
    unsigned int n=0, minor;
    int partno;
    struct gendisk *gd;
    (void)work;
    r=kcalloc(64U,sizeof(*r),GFP_KERNEL);
    if (!r) return;
    for (minor=0; minor<256 && n<64U; minor++) {
        gd=get_gendisk(MKDEV(MMC_BLOCK_MAJOR,minor),&partno);
        if (!gd) continue;
        r[n].minor=minor; r[n].partno=partno; r[n].sectors=get_capacity(gd);
        strlcpy(r[n].name,gd->disk_name,sizeof(r[n].name)); n++;
        put_disk(gd);
    }
    if (n) a52_p444_store_section(P444_STAGE_SD,P444_TYPE_SD,"MMC_MAJOR179",n,0,r,n*sizeof(r[0]));
    kfree(r);
#if P444_KIND == 1
    p444_checkpoint();
#endif
}"""
if source.count(_sd_old) != 1:
    raise SystemExit("Phase444 SD stack buffer anchor count=" + str(source.count(_sd_old)))
source = source.replace(_sd_old, _sd_new, 1)

# The verified payload already contains the intended disk-written
# declaration. Keep compatibility with the earlier damaged stream, but never
# require corruption to be present.
_bad_decl = "static atomic_t p444_disk_on, dis = ATOMIC_INIT(0);"
_good_decl = "static atomic_t p444_disk_written = ATOMIC_INIT(0);"
_bad_n = source.count(_bad_decl)
_good_n = source.count(_good_decl)
if _bad_n == 1 and _good_n == 0:
    source = source.replace(_bad_decl, _good_decl, 1)
elif not (_bad_n == 0 and _good_n == 1):
    raise SystemExit(
        "Phase444 disk declaration state bad=%d good=%d" % (_bad_n, _good_n)
    )

# Storage safety: Phase441/P414 owns debug partition [0x00800000,0x00a00000),
# exactly the 2 MiB extent the first Phase444 scaffold selected. Do not issue
# any Phase444 block writes until that ownership is deliberately reassigned.
_old_checkpoint = """static void p444_checkpoint(void)
{
    atomic_inc(&p444_disk_gen);
    p444_sync_header();
    mod_delayed_work(system_unbound_wq, &p444_disk_work, 0);
}"""
_new_checkpoint = """static void p444_checkpoint(void)
{
    /* P444 v1: disk tier disabled; P414 already owns 8-10 MiB of debug. */
    p444_sync_header();
}"""
_old_n = source.count(_old_checkpoint)
_new_n = source.count(_new_checkpoint)
if _old_n == 1 and _new_n == 0:
    source = source.replace(_old_checkpoint, _new_checkpoint, 1)
elif not (_old_n == 0 and _new_n == 1):
    raise SystemExit(
        "Phase444 disk checkpoint state old=%d new=%d" % (_old_n, _new_n)
    )

_flag_anchor = "#define P444_F_DISK_OK BIT(3)"
_disabled_flag = "#define P444_F_DISK_DISABLED_OVERLAP BIT(4)"
if source.count(_flag_anchor) != 1:
    raise SystemExit("Phase444 disk flag anchor count=" + str(source.count(_flag_anchor)))
if _disabled_flag not in source:
    source = source.replace(_flag_anchor, _flag_anchor + "\n" + _disabled_flag, 1)

_init_anchor = "h=p444_hdr(); h->magic=P444_MAGIC; h->version=P444_VERSION; h->kind=P444_KIND;"
_init_patch = _init_anchor + "\n#if P444_KIND == 1\n    h->flags |= P444_F_DISK_DISABLED_OVERLAP;\n#endif"
if source.count(_init_anchor) != 1:
    raise SystemExit("Phase444 init header anchor count=" + str(source.count(_init_anchor)))
if "h->flags |= P444_F_DISK_DISABLED_OVERLAP;" not in source:
    source = source.replace(_init_anchor, _init_patch, 1)

# Fail before source reconstruction/compilation if the compressed experiment
# payload lost one of the locked Phase444 contracts.
_required = (
    "A52_PHASE444_DEEP_PASSIVE_COMPARE_V1",
    "PRE_DEEP",
    "PRE_TRIGGER",
    "HOT_0_75US",
    "DSI_KONA_BUS",
    "MMC_MAJOR179",
    "a52_p444_sde_capture",
    "a52_p444_store_section",
    "P444 TERM ret=%d/%d irq=%u/%u",
)
_missing = [token for token in _required if token not in source]
if _missing:
    raise SystemExit("Phase444 payload contract missing: " + ", ".join(_missing))

_low = source.lower()
_has_crc = ("crc=%" in _low) or ("crc32" in _low)
_dup_sets = (
    ("st=%x/%x", "ck=%x/%x", "in=%x/%x", "ln=%x/%x", "db=%x/%x"),
    ("st=%08x/%08x", "ck=%08x/%08x", "in=%08x/%08x",
     "ln=%08x/%08x", "db=%08x/%08x"),
)
_has_dups = any(all(token in source for token in group) for group in _dup_sets)
if not (_has_crc or _has_dups):
    raise SystemExit(
        "Phase444 payload lacks TG clear-bit-corruption protection "
        "(need CRC32 or duplicated key fields)"
    )

exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
