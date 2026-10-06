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


exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
