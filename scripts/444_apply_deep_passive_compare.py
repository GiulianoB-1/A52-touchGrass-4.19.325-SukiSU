#!/usr/bin/env python3
from __future__ import annotations

import base64
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")

# Recover the single semantic scar left by the damaged compressed byte.
_bad_decl = "static atomic_t p444_disk_on, dis = ATOMIC_INIT(0);"
_good_decl = "static atomic_t p444_disk_written = ATOMIC_INIT(0);"
if source.count(_bad_decl) != 1:
    raise SystemExit("Phase444 payload repair declaration count=" + str(source.count(_bad_decl)))
source = source.replace(_bad_decl, _good_decl, 1)

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
if source.count(_old_checkpoint) != 1:
    raise SystemExit("Phase444 disk checkpoint anchor count=" + str(source.count(_old_checkpoint)))
source = source.replace(_old_checkpoint, _new_checkpoint, 1)

_flag_anchor = "#define P444_F_DISK_OK BIT(3)"
if source.count(_flag_anchor) != 1:
    raise SystemExit("Phase444 disk flag anchor count=" + str(source.count(_flag_anchor)))
source = source.replace(
    _flag_anchor,
    _flag_anchor + "\n#define P444_F_DISK_DISABLED_OVERLAP BIT(4)",
    1,
)
_init_anchor = "h=p444_hdr(); h->magic=P444_MAGIC; h->version=P444_VERSION; h->kind=P444_KIND;"
if source.count(_init_anchor) != 1:
    raise SystemExit("Phase444 init header anchor count=" + str(source.count(_init_anchor)))
source = source.replace(
    _init_anchor,
    _init_anchor + "\n#if P444_KIND == 1\n    h->flags |= P444_F_DISK_DISABLED_OVERLAP;\n#endif",
    1,
)

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
