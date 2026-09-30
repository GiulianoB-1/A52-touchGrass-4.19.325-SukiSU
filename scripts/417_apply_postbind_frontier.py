#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path

MARK = "A52_PHASE417_POSTBIND_FRONTIER_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DRM = Path("drivers/a52_display/msm/dsi/dsi_drm.c")
DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase417 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def inject_scope(text: str, fn: str, scope_name: str, ret: str) -> str:
    statement = f'A52_ACKFR_SCOPE("DISP", "{scope_name}");'
    if statement in text:
        return text
    pat = re.compile(
        r'((?:static\\s+)?' + re.escape(ret) + r'\\s+' + re.escape(fn) +
        r'\\s*\\([^;]*?\\)\\s*\\n\\{)', re.S)
    m = pat.search(text)
    if not m:
        raise SystemExit(f"Phase417 function not found for scope: {fn}")
    return text[:m.end()] + "\n\t" + statement + text[m.end():]


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    for tok in (
        "A52_PHASE416_SEQUENTIAL_3M_DISK_PERSIST_V2",
        "static void a52_r273_frontier_fn(struct work_struct *work)",
        "static unsigned long a52_r274_frontier_start_jiffies;",
        "static DECLARE_DELAYED_WORK(a52_r273_frontier_work, a52_r273_frontier_fn);",
    ):
        if tok not in text:
            raise SystemExit("Phase417 recorder prerequisite missing: " + tok)

    text = one(
        text,
        'if (strncmp(fmt, "P416", 4) &&\n',
        'if (strncmp(fmt, "P417", 4) &&\n'
        '    strncmp(fmt, "P416", 4) &&\n',
        "first P417 gate",
    )
    text = one(
        text,
        '\tif (!fmt || (\n\t    strncmp(fmt, "P416", 4) &&\n',
        '\tif (!fmt || (\n\t    strncmp(fmt, "P417", 4) &&\n'
        '\t    strncmp(fmt, "P416", 4) &&\n',
        "second P417 gate",
    )

    anchor = '\t    strncmp(fmt, "P414", 4) &&\n'
    addition = (
        anchor +
        '\t    strncmp(fmt, "P273", 4) &&\n'
        '\t    strncmp(fmt, "P269", 4) &&\n'
        '\t    strncmp(fmt, "P268", 4) &&\n'
        '\t    strncmp(fmt, "P267", 4) &&\n'
        '\t    strncmp(fmt, "DRMPOST 212", 11) &&\n'
        '\t    strncmp(fmt, "KMSPOST", 7) &&\n'
    )
    text = one(text, anchor, addition, "post-bind gate admission")

    frontier_anchor = '''static void a52_r273_frontier_fn(struct work_struct *work)
{
	unsigned long elapsed_j = jiffies - a52_r274_frontier_start_jiffies;
'''
    if frontier_anchor not in text:
        raise SystemExit("Phase417 frontier function anchor missing")

    insertion_anchor = '''	schedule_delayed_work(&a52_r273_frontier_work,
		msecs_to_jiffies(delay_ms));
}
'''
    late = r'''

/* A52_PHASE417_POSTBIND_FRONTIER_V1
 * Diagnostic-only: re-arm the existing low-rate task frontier after late init.
 * It tracks init/servicemanager/surfaceflinger/zygote/system_server/bootanimation
 * without restoring any Phase380 UFS sampler or Phase395/396 NoC observer.
 */
static int __init a52_p417_frontier_init(void)
{
	memset(a52_r273_last_pid, 0, sizeof(a52_r273_last_pid));
	a52_r273_ever_mask = 0U;
	a52_r273_gone_mask = 0U;
	a52_r273_last_summary_bucket = 0U;
	a52_r274_frontier_start_jiffies = jiffies;
	schedule_delayed_work(&a52_r273_frontier_work, msecs_to_jiffies(250));
	a52_ackfr_record("P417 FRONTIER arm delay=250");
	return 0;
}
late_initcall_sync(a52_p417_frontier_init);
'''
    text = one(text, insertion_anchor, insertion_anchor + late,
               "re-arm P273 task frontier")

    text += f'\n/* {MARK}: post-bind task/DRM bridge frontier only. */\n'
    return text


def patch_drm(text: str) -> str:
    if MARK in text:
        return text
    for fn, ret in (
        ("dsi_bridge_mode_fixup", "bool"),
        ("dsi_bridge_mode_set", "void"),
        ("dsi_bridge_pre_enable", "void"),
        ("dsi_bridge_enable", "void"),
    ):
        text = inject_scope(text, fn, f"a52.{fn}", ret)

    pat = re.compile(
        r'(\trc\s*=\s*dsi_display_set_mode\(c_bridge->display,\s*\n'
        r'\s*&\(c_bridge->dsi_mode\),\s*0x0\);\n)')
    m = pat.search(text)
    if not m:
        raise SystemExit("Phase417 dsi_display_set_mode call anchor missing")
    repl = m.group(1) + (
        '\ta52_ackfr_record("P417 BR set rc=%d fl=%x", rc,\n'
        '\t\tc_bridge->dsi_mode.dsi_mode_flags);\n')
    text = text[:m.start()] + repl + text[m.end():]

    old = '\t\tDSI_DEBUG("[%d] seamless pre-enable\\n", c_bridge->id);\n\t\treturn;\n'
    new = (
        '\t\tDSI_DEBUG("[%d] seamless pre-enable\\n", c_bridge->id);\n'
        '\t\ta52_ackfr_record("P417 BR early fl=%x",\n'
        '\t\t\tc_bridge->dsi_mode.dsi_mode_flags);\n'
        '\t\treturn;\n')
    text = one(text, old, new, "seamless early-return record")

    text += f'\n/* {MARK}: DSI bridge pre-enable discriminator. */\n'
    return text


def patch_display(text: str) -> str:
    if MARK in text:
        return text
    text = inject_scope(text, "dsi_display_set_mode",
                        "a52.dsi_display_set_mode", "int")
    text += f'\n/* {MARK}: set-mode lifecycle scope. */\n'
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    drm = (root / DRM).read_text(errors="replace")
    disp = (root / DISPLAY).read_text(errors="replace")
    for tok in (
        MARK,
        'strncmp(fmt, "P417", 4)',
        'strncmp(fmt, "P273", 4)',
        'strncmp(fmt, "DRMPOST 212", 11)',
        'strncmp(fmt, "KMSPOST", 7)',
        'P417 FRONTIER arm delay=250',
        'late_initcall_sync(a52_p417_frontier_init);',
    ):
        if tok not in rec:
            raise SystemExit("Phase417 recorder token missing: " + tok)
    if rec.count('strncmp(fmt, "P417", 4)') < 2:
        raise SystemExit("Phase417 must pass both ACK admission gates")
    for tok in (
        'A52_ACKFR_SCOPE("DISP", "a52.dsi_bridge_mode_fixup");',
        'A52_ACKFR_SCOPE("DISP", "a52.dsi_bridge_mode_set");',
        'A52_ACKFR_SCOPE("DISP", "a52.dsi_bridge_pre_enable");',
        'A52_ACKFR_SCOPE("DISP", "a52.dsi_bridge_enable");',
        'P417 BR set rc=%d fl=%x',
        'P417 BR early fl=%x',
    ):
        if tok not in drm:
            raise SystemExit("Phase417 DRM token missing: " + tok)
    if 'A52_ACKFR_SCOPE("DISP", "a52.dsi_display_set_mode");' not in disp:
        raise SystemExit("Phase417 display set-mode scope missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    for rel in (REC, DRM, DISPLAY):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase417 source missing: " + str(rel))
    if not ns.check_only:
        p = ns.root / REC; p.write_text(patch_rec(p.read_text(errors="replace")))
        p = ns.root / DRM; p.write_text(patch_drm(p.read_text(errors="replace")))
        p = ns.root / DISPLAY; p.write_text(patch_display(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase417 post-bind frontier: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
