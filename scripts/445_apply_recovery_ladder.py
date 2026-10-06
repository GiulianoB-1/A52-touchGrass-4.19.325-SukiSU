#!/usr/bin/env python3
from __future__ import annotations
import base64, zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
source = zlib.decompress(base64.b64decode(payload_path.read_text().strip())).decode("utf-8")

# Phase445 fixup: the reconstructed GKI refgen.c is semantically the same as
# TouchGrass but its struct whitespace is not byte-identical. Replace only the
# refgen patch helper with a structural implementation; all recovery-ladder
# logic in the retained z64 payload remains untouched.
_start = source.find("def patch_refgen(root: Path) -> None:")
_end = source.find("\ndef patch_deferred(root: Path) -> None:", _start)
if _start < 0 or _end < 0:
    raise SystemExit("Phase445 wrapper: patch_refgen bounds missing")

_refgen = r'''def patch_refgen(root: Path) -> None:
    p=root/"drivers/regulator/refgen.c"
    if not p.exists(): die("refgen source missing")
    s=p.read_text(errors="replace")
    if "A52_PHASE445_REFGEN_SNAPSHOT" in s:
        return

    # Locate the structure by syntax, not exact tabs/spacing.
    struct_start=s.find("struct refgen {")
    if struct_start < 0:
        die("refgen struct start missing")
    struct_end=s.find("\n};",struct_start)
    if struct_end < 0:
        die("refgen struct end missing")
    struct_end += len("\n};")
    s=s[:struct_end] + "\n\n/* A52_PHASE445_REFGEN_SNAPSHOT */\nstatic struct refgen *a52_p445_refgen;" + s[struct_end:]

    # Install the pointer at the successful end of refgen_probe().
    # Do not depend on the exact regulator-registration API used by this tree.
    probe_start=s.find("static int refgen_probe(")
    if probe_start < 0:
        probe_start=s.find("static int qcom_refgen_probe(")
    if probe_start < 0:
        die("refgen probe start missing")

    brace=s.find("{",probe_start)
    if brace < 0:
        die("refgen probe opening brace missing")
    depth=0
    probe_end=-1
    for i in range(brace,len(s)):
        if s[i]=="{":
            depth+=1
        elif s[i]=="}":
            depth-=1
            if depth==0:
                probe_end=i
                break
    if probe_end < 0:
        die("refgen probe end missing")

    body=s[brace:probe_end]
    rel=body.rfind("return 0;")
    if rel < 0:
        die("refgen probe success return missing")
    retpos=brace+rel
    line_start=s.rfind("\n",brace,retpos)+1
    indent=s[line_start:retpos]
    if indent.strip():
        indent="\t"
    s=s[:retpos] + "a52_p445_refgen=vreg;\n" + indent + s[retpos:]

    s += r"""

int a52_p445_refgen_snapshot(u32 *registered, u32 *enabled,
        u32 *use_count, u32 *pwrdwn)
{
    struct refgen *v=a52_p445_refgen;
    if (registered) *registered=!!v;
    if (!v || !v->rdev || !v->addr) return -ENODEV;
    if (enabled) *enabled=refgen_kona_is_enabled(v->rdev);
    if (use_count) *use_count=READ_ONCE(v->rdev->use_count);
    if (pwrdwn) *pwrdwn=readl_relaxed(v->addr+REFGEN_REG_PWRDWN_CTRL5);
    return 0;
}
EXPORT_SYMBOL_GPL(a52_p445_refgen_snapshot);
"""
    p.write_text(s)
'''

source = source[:_start] + _refgen + source[_end:]
# Regulator use_count is diagnostic only. On 5.10 rdev->mutex is a ww_mutex,
# while downstream 4.19 used a plain mutex. Avoid taking either lock and use
# a one-shot READ_ONCE snapshot, which is sufficient for PRE/R2 comparison.
source = source.replace(
    """    mutex_lock(&rdev->mutex);
    if (use_count) *use_count=(int)rdev->use_count;
    mutex_unlock(&rdev->mutex);""",
    """    if (use_count) *use_count=(int)READ_ONCE(rdev->use_count);"""
)


# Phase445 recorder hardening. 120 section slots already come from the retained
# payload; add explicit dropped-section accounting without moving the section
# table (two u32s consume eight bytes from the existing reserved area).
source = source.replace(
    """    u32 r2_flags;
    u8 reserved[64];
    struct p445_section sec[P445_MAX_SECTIONS];""",
    """    u32 r2_flags;
    u32 dropped_sections, section_capacity;
    u8 reserved[56];
    struct p445_section sec[P445_MAX_SECTIONS];"""
)
source = source.replace(
    "static atomic_t p445_rung = ATOMIC_INIT(0);",
    "static atomic_t p445_rung = ATOMIC_INIT(0);\\nstatic atomic_t p445_dropped = ATOMIC_INIT(0);"
)

_pc0 = source.find("def patch_central(root: Path, kind: str) -> None:")
_pc1 = source.find("\\ndef patch_ctrl(", _pc0)
if _pc0 < 0 or _pc1 < 0:
    raise SystemExit("Phase445 wrapper: patch_central bounds missing")
_pc = source[_pc0:_pc1]
_write = _pc.rfind("    p.write_text(s)")
if _write < 0:
    raise SystemExit("Phase445 wrapper: patch_central write anchor missing")

_hardening = r'''
    # Explicitly expose recorder capacity and count every dropped section.
    sync_old = """    h->section_count = (u32)atomic_read(&p445_sections);
    h->used_bytes = (u32)atomic_read(&p445_used);"""
    sync_new = """    h->section_count = (u32)atomic_read(&p445_sections);
    h->used_bytes = (u32)atomic_read(&p445_used);
    h->dropped_sections = (u32)atomic_read(&p445_dropped);
    h->section_capacity = P445_MAX_SECTIONS;"""
    s = one(s, sync_old, sync_new, "recorder drop sync")

    overflow_old = """        p445_hdr()->flags |= P445_F_OVERFLOW;
        p445_sync_header();
        return -ENOSPC;"""
    overflow_new = """        atomic_inc(&p445_dropped);
        p445_hdr()->flags |= P445_F_OVERFLOW;
        p445_hdr()->dropped_sections = (u32)atomic_read(&p445_dropped);
        p445_hdr()->section_capacity = P445_MAX_SECTIONS;
        pr_err("P445 DROP section idx=%u cap=%u off=%x len=%x dropped=%u\\n",
            idx, P445_MAX_SECTIONS, off, aligned,
            (u32)atomic_read(&p445_dropped));
        p445_sync_header();
        return -ENOSPC;"""
    s = one(s, overflow_old, overflow_new, "recorder drop path")
'''
_pc = _pc[:_write] + _hardening + _pc[_write:]
source = source[:_pc0] + _pc + source[_pc1:]



exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
