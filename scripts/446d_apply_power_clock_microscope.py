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

    # Both reconstructed display trees carry a forward declaration before
    # the real sde_kms_hw_init() definition. The legacy Phase446d fn_span()
    # helper otherwise matches the declaration and searches the wrong block.
    p = (
        root / "techpack/display/msm/sde/sde_kms.c"
        if kind == "tg"
        else root / "drivers/a52_display/msm/sde/sde_kms.c"
    )
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

def _compat_post():
    if _arg_value("--kind") != "gki" or "--check-only" in sys.argv:
        return
    root = _arg_value("--root")
    if not root:
        return
    p = Path(root) / "drivers/a52_display/msm/sde/sde_kms.c"
    if not p.is_file():
        return
    s = p.read_text()

    old = """static int sde_kms_hw_init(struct msm_kms *kms)
{
\ta52_p446_mark(0x164U, 0U, 0U);
\t{ const unsigned char *p=(const unsigned char *)saved_command_line; u32 h=2166136261U; if(p) while(*p){h^=*p++;h*=16777619U;} a52_p446_mark(0x15eU,h,0U); }
\ta52_p446_mark(0x15fU, (a52_p446c_keep_earlymap ? 1U : 0U) | (a52_p446c_skip_post_enable_init ? 2U : 0U), 0U);

\tA52_ACKFR_SCOPE("DISP", "a52.life.sde_kms_hw_init");
\tstruct sde_kms *sde_kms;
\tstruct drm_device *dev;
\tstruct msm_drm_private *priv;
\tstruct platform_device *platformdev;
\tint i, irq_num, rc = -EINVAL;

"""
    new = """static int sde_kms_hw_init(struct msm_kms *kms)
{
\tA52_ACKFR_SCOPE("DISP", "a52.life.sde_kms_hw_init");
\tstruct sde_kms *sde_kms;
\tstruct drm_device *dev;
\tstruct msm_drm_private *priv;
\tstruct platform_device *platformdev;
\tint i, irq_num, rc = -EINVAL;

\ta52_p446_mark(0x164U, 0U, 0U);
\t{ const unsigned char *p=(const unsigned char *)saved_command_line; u32 h=2166136261U; if(p) while(*p){h^=*p++;h*=16777619U;} a52_p446_mark(0x15eU,h,0U); }
\ta52_p446_mark(0x15fU, (a52_p446c_keep_earlymap ? 1U : 0U) | (a52_p446c_skip_post_enable_init ? 2U : 0U), 0U);

"""
    if old in s:
        s = s.replace(old, new, 1)

    old = """struct msm_kms *sde_kms_init(struct drm_device *dev)
{
\ta52_p446_mark(0x162U, 0U, 0U);
\tA52_ACKFR_SCOPE("DISP", "a52.life.sde_kms_init");
\tstruct msm_drm_private *priv;
\tstruct sde_kms *sde_kms;

"""
    new = """struct msm_kms *sde_kms_init(struct drm_device *dev)
{
\tA52_ACKFR_SCOPE("DISP", "a52.life.sde_kms_init");
\tstruct msm_drm_private *priv;
\tstruct sde_kms *sde_kms;

\ta52_p446_mark(0x162U, 0U, 0U);
"""
    if old in s:
        s = s.replace(old, new, 1)

    p.write_text(s)

payload = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
compat = _compat_begin()
try:
    src = zlib.decompress(base64.b64decode(payload.read_text().strip()))
    exec(compile(src, str(__file__), "exec"))
    _compat_post()
finally:
    _compat_end(compat)
