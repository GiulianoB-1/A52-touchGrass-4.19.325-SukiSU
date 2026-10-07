#!/usr/bin/env python3
import base64
import sys
import zlib
from pathlib import Path

def _arg_value(name):
    try:
        i = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[i + 1] if i + 1 < len(sys.argv) else None

def _compat_begin():
    kind = _arg_value("--kind")
    root = _arg_value("--root")
    if kind not in ("tg", "gki") or not root:
        return []

    root = Path(root)
    changed = []

    # The legacy compressed Phase446d patcher accepts only a plain "};"
    # struct terminator. Both current TG and GKI Phase446 recorders are packed.
    central = (
        root / "techpack/display/msm/a52_phase446g.c"
        if kind == "tg"
        else root / "drivers/a52_display/msm/a52_phase445.c"
    )
    if central.is_file():
        s = central.read_text()
        start = s.find("struct p446_rec {")
        if start >= 0:
            end = s.find("\n} __packed;", start)
            if end >= 0:
                s = s[:end] + "\n};" + s[end + len("\n} __packed;"):]
                central.write_text(s)
                changed.append(("packed", central))

    if kind == "tg":
        # The downstream TouchGrass file has both a forward declaration and the
        # real sde_kms_hw_init() definition. The legacy Phase446d fn_span()
        # helper matches the declaration first. Mask only that declaration
        # while applying the patch, then restore it byte-for-byte below.
        p = root / "techpack/display/msm/sde/sde_kms.c"
        if p.is_file():
            s = p.read_text()
            old = "static int sde_kms_hw_init(struct msm_kms *kms);"
            new = "static int sde_kms_hw_init__p446d_forward(struct msm_kms *kms);"
            if old in s:
                p.write_text(s.replace(old, new, 1))
                changed.append(("kms_forward", p))

    return changed

def _compat_end(changed):
    for kind, p in reversed(changed or []):
        if not p.is_file():
            continue
        s = p.read_text()
        if kind == "packed":
            start = s.find("struct p446_rec {")
            if start >= 0:
                end = s.find("\n};", start)
                if end >= 0:
                    s = s[:end] + "\n} __packed;" + s[end + len("\n};"):]
                    p.write_text(s)
        elif kind == "kms_forward":
            old = "static int sde_kms_hw_init__p446d_forward(struct msm_kms *kms);"
            new = "static int sde_kms_hw_init(struct msm_kms *kms);"
            if old in s:
                p.write_text(s.replace(old, new, 1))

payload = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
compat = _compat_begin()
try:
    src = zlib.decompress(base64.b64decode(payload.read_text().strip()))
    exec(compile(src, str(__file__), "exec"))
finally:
    _compat_end(compat)
