#!/usr/bin/env python3
from __future__ import annotations

import base64
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")

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
