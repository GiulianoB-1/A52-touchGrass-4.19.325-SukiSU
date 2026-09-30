#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path

MARK = "A52_PHASE419_VREFRESH_MODEINFO_FIX_V1"
CORE = Path("drivers/gpu/drm/drm_modes.c")
DSI_DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
DSI_DRM = Path("drivers/a52_display/msm/dsi/dsi_drm.c")
CONNECTOR = Path("drivers/a52_display/msm/sde/sde_connector.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase419 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    # Good enough for kernel C definitions in the targeted files.
    hits = []
    for m in re.finditer(r"\\b" + re.escape(name) + r"\\s*\\(", text):
        p = m.end() - 1
        depth = 0
        close = -1
        state = "normal"
        i = p
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ""
            if state == "normal":
                if c == "/" and n == "/":
                    state = "line"; i += 2; continue
                if c == "/" and n == "*":
                    state = "block"; i += 2; continue
                if c == '"':
                    state = "string"
                elif c == "'":
                    state = "char"
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        close = i
                        break
            elif state == "line":
                if c == "\\n":
                    state = "normal"
            elif state == "block":
                if c == "*" and n == "/":
                    state = "normal"; i += 2; continue
            elif state == "string":
                if c == "\\":
                    i += 2; continue
                if c == '"':
                    state = "normal"
            elif state == "char":
                if c == "\\":
                    i += 2; continue
                if c == "'":
                    state = "normal"
            i += 1
        if close < 0:
            continue
        j = close + 1
        while j < len(text) and text[j].isspace():
            j += 1
        if j >= len(text) or text[j] != "{":
            continue
        opening = j
        depth = 0
        state = "normal"
        i = opening
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ""
            if state == "normal":
                if c == "/" and n == "/":
                    state = "line"; i += 2; continue
                if c == "/" and n == "*":
                    state = "block"; i += 2; continue
                if c == '"':
                    state = "string"
                elif c == "'":
                    state = "char"
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        hits.append((m.start(), i + 1))
                        break
            elif state == "line":
                if c == "\\n":
                    state = "normal"
            elif state == "block":
                if c == "*" and n == "/":
                    state = "normal"; i += 2; continue
            elif state == "string":
                if c == "\\":
                    i += 2; continue
                if c == '"':
                    state = "normal"
            elif state == "char":
                if c == "\\":
                    i += 2; continue
                if c == "'":
                    state = "normal"
            i += 1
    if len(hits) != 1:
        raise SystemExit(f"Phase419 definition count mismatch {name}: {len(hits)}")
    return hits[0]


def patch_core(text: str) -> str:
    if MARK in text:
        return text
    start, end = function_bounds(text, "drm_mode_convert_umode")
    fn = text[start:end]
    if "out->vrefresh = in->vrefresh;" in fn:
        raise SystemExit("Phase419 core already copies vrefresh unexpectedly")
    old = "\tout->vscan = in->vscan;\n\tout->flags = in->flags;\n"
    new = (
        "\tout->vscan = in->vscan;\n"
        f"\t/* {MARK}: restore downstream 4.19 userspace mode refresh ABI. */\n"
        "\tout->vrefresh = in->vrefresh;\n"
        "\tout->flags = in->flags;\n"
    )
    fn = one(fn, old, new, "drm_mode_convert_umode vrefresh")
    return text[:start] + fn + text[end:]


FIND_HELPER = r'''
/* A52_PHASE419_VREFRESH_MODEINFO_FIX_V1
 * One-shot Composer mode-cache comparison. This proves the userspace mode
 * tuple that reaches the downstream DSI matcher after the vrefresh repair.
 */
static atomic_t a52_p419_find_once = ATOMIC_INIT(0);
'''


def patch_dsi_display(text: str) -> str:
    if MARK in text:
        return text
    sig = "int dsi_display_find_mode(struct dsi_display *display,"
    pos = text.find(sig)
    if pos < 0:
        raise SystemExit("Phase419 dsi_display_find_mode signature missing")
    text = text[:pos] + FIND_HELPER + "\n" + text[pos:]

    start, end = function_bounds(text, "dsi_display_find_mode")
    fn = text[start:end]
    fn = one(fn,
        "\tu32 count, i;\n\tint rc;\n",
        "\tu32 count, i;\n\tint rc;\n"
        "\tint a52_p419_hit = -1;\n"
        "\tbool a52_p419_log = false;\n",
        "find locals")

    fn = one(fn,
        "\t*out_mode = NULL;\n",
        "\t*out_mode = NULL;\n"
        "\ta52_p419_log = a52_ackfr_phase269_is_composer_tgid(current->tgid) &&\n"
        "\t\t(atomic_cmpxchg(&a52_p419_find_once, 0, 1) == 0);\n",
        "find arm")

    old_get = '''	if (!display->modes) {
		struct dsi_display_mode *m;

		rc = dsi_display_get_modes(display, &m);
		if (rc)
			return rc;
	}
'''
    new_get = '''	if (!display->modes) {
		struct dsi_display_mode *m;

		rc = dsi_display_get_modes(display, &m);
		if (rc) {
			if (a52_p419_log)
				a52_ackfr_record("P419 FR rc=%d hit=-1 get=1", rc);
			return rc;
		}
	}

	if (a52_p419_log)
		a52_ackfr_record(
			"P419 FQ h=%u v=%u r=%u p=%u m=%u hs=%u ph=%u n=%u",
			cmp->timing.h_active, cmp->timing.v_active,
			cmp->timing.refresh_rate, cmp->pixel_clk_khz,
			cmp->panel_mode, cmp->timing.sot_hs_mode,
			cmp->timing.phs_mode, count);
'''
    fn = one(fn, old_get, new_get, "find query")

    old_loop = '''	for (i = 0; i < count; i++) {
		struct dsi_display_mode *m = &display->modes[i];

		if (cmp->timing.v_active == m->timing.v_active &&
'''
    new_loop = '''	for (i = 0; i < count; i++) {
		struct dsi_display_mode *m = &display->modes[i];

		if (a52_p419_log && i < 8U)
			a52_ackfr_record(
				"P419 FC i=%u h=%u v=%u r=%u p=%u m=%u hs=%u ph=%u",
				i, m->timing.h_active, m->timing.v_active,
				m->timing.refresh_rate, m->pixel_clk_khz,
				m->panel_mode, m->timing.sot_hs_mode,
				m->timing.phs_mode);

		if (cmp->timing.v_active == m->timing.v_active &&
'''
    fn = one(fn, old_loop, new_loop, "find candidates")

    fn = one(fn,
        "\t\t\t*out_mode = m;\n\t\t\trc = 0;\n\t\t\tbreak;\n",
        "\t\t\t*out_mode = m;\n\t\t\ta52_p419_hit = (int)i;\n"
        "\t\t\trc = 0;\n\t\t\tbreak;\n",
        "find hit")

    old_tail = '''	if (!*out_mode) {
		DSI_ERR("[%s] failed to find mode for v_active %u h_active %u fps %u pclk %u\\n",
				display->name, cmp->timing.v_active,
				cmp->timing.h_active, cmp->timing.refresh_rate,
				cmp->pixel_clk_khz);
		rc = -ENOENT;
	}

	return rc;
'''
    new_tail = '''	if (!*out_mode) {
		DSI_ERR("[%s] failed to find mode for v_active %u h_active %u fps %u pclk %u\\n",
				display->name, cmp->timing.v_active,
				cmp->timing.h_active, cmp->timing.refresh_rate,
				cmp->pixel_clk_khz);
		rc = -ENOENT;
	}

	if (a52_p419_log)
		a52_ackfr_record("P419 FR rc=%d hit=%d", rc, a52_p419_hit);

	return rc;
'''
    fn = one(fn, old_tail, new_tail, "find result")
    text = text[:start] + fn + text[end:]
    text += f"\n/* {MARK}: one-shot DSI mode-cache tuple recorder. */\n"
    return text


def patch_connector(text: str) -> str:
    if MARK in text:
        return text
    include = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if include not in text:
        incs = list(re.finditer(r'^#include[^\\n]*\\n', text, flags=re.M))
        if not incs:
            raise SystemExit("Phase419 sde_connector include anchor missing")
        p = incs[-1].end()
        text = text[:p] + include + text[p:]

    helper = (
        "\nextern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\n"
        "static atomic_t a52_p419_gmi_once = ATOMIC_INIT(0);\n\n"
    )
    start, _ = function_bounds(text, "sde_connector_get_mode_info")
    text = text[:start] + helper + text[start:]
    start, end = function_bounds(text, "sde_connector_get_mode_info")
    fn = text[start:end]

    fn = one(fn,
        "\tstruct sde_connector *sde_conn;\n\tstruct msm_resource_caps_info avail_res;\n",
        "\tstruct sde_connector *sde_conn;\n\tstruct msm_resource_caps_info avail_res;\n"
        "\tint rc;\n\tbool a52_p419_log;\n",
        "get-mode-info locals")

    old_return = '''	return sde_conn->ops.get_mode_info(conn, drm_mode,
			mode_info, sde_conn->display, &avail_res);
'''
    new_return = '''	rc = sde_conn->ops.get_mode_info(conn, drm_mode,
			mode_info, sde_conn->display, &avail_res);
	a52_p419_log = a52_ackfr_phase269_is_composer_tgid(current->tgid) &&
		(atomic_cmpxchg(&a52_p419_gmi_once, 0, 1) == 0);
	if (a52_p419_log)
		a52_ackfr_record(
			"P419 GMI rc=%d h=%d v=%d r=%d p=%d pv=%u",
			rc, drm_mode ? drm_mode->hdisplay : -1,
			drm_mode ? drm_mode->vdisplay : -1,
			drm_mode ? drm_mode->vrefresh : -1,
			drm_mode ? drm_mode->clock : -1,
			drm_mode && drm_mode->private ? 1U : 0U);
	return rc;
'''
    fn = one(fn, old_return, new_return, "get-mode-info result")
    text = text[:start] + fn + text[end:]
    text += f"\n/* {MARK}: one-shot connector mode-info result. */\n"
    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text

    # Unique per-boot ID even when the B1A header does not survive a flash/reboot.
    if "#include <linux/random.h>" not in text:
        text = one(text, "#include <linux/ktime.h>\n",
                   "#include <linux/ktime.h>\n#include <linux/random.h>\n",
                   "random include")

    old_boot = '''	memcpy_fromio(&old, a52_p414_ram, sizeof(old));
	if (old.magic == A52_P414_MAGIC &&
	    old.version == A52_P414_VERSION &&
	    old.phase == 414U &&
	    old.boot_id && old.boot_id != ~0ULL)
		a52_p414_boot_id = old.boot_id + 1ULL;
	else
		a52_p414_boot_id = 1ULL;
'''
    new_boot = '''	memcpy_fromio(&old, a52_p414_ram, sizeof(old));
	a52_p414_boot_id = get_random_u64();
	if (!a52_p414_boot_id || a52_p414_boot_id == ~0ULL)
		a52_p414_boot_id = ktime_get_real_ns() ^ ktime_get_boottime_ns() ^
		0x419a52c05eedULL;
	if (old.magic == A52_P414_MAGIC &&
	    old.version == A52_P414_VERSION &&
	    old.phase == 414U &&
	    old.boot_id == a52_p414_boot_id)
		a52_p414_boot_id ^= 0x9e3779b97f4a7c15ULL;
'''
    text = one(text, old_boot, new_boot, "unique boot id")

    # Phase402's second gate used 9 for an 8-byte 'P276 NNN' prefix.
    # Fix every such exact three-digit prefix, including the 387/394 controls.
    text, gate_fixes = re.subn(
        r'strncmp\\(fmt, "(P276 [0-9]{3})", 9\\)',
        r'strncmp(fmt, "\\1", 8)',
        text,
    )
    if gate_fixes < 2:
        raise SystemExit(f"Phase419 P276 gate repair unexpectedly small: {gate_fixes}")

    # Make the control itself carry the real boot id instead of a hard-coded 1.
    text = one(text,
        'a52_ackfr_record("P276 394T boot=1 ret=%u",\n\t\ta52_ackfr_phase418_retained());\n',
        'a52_ackfr_record("P276 394T boot=%llx ret=%u",\n'
        '\t\t(unsigned long long)a52_p414_boot_id,\n'
        '\t\ta52_ackfr_phase418_retained());\n',
        "P276 boot control id")

    # Tiny redundant RAM mirror for Phase419 critical evidence only. It occupies
    # unused bytes 0x400..0xfff inside the existing B1A header page and does not
    # reduce either the 2 MiB disk tier or the 1 MiB overflow tier.
    defs = '''#define A52_P416_STATUS_OFFSET2       0x00000200U
'''
    defs_new = defs + '''#define A52_P419_MIRROR_OFFSET        0x00000400U
#define A52_P419_MIRROR_SLOTS         ((A52_P414_HEADER_BYTES - A52_P419_MIRROR_OFFSET) / A52_P414_RECORD_BYTES)
'''
    text = one(text, defs, defs_new, "mirror definitions")

    var = "static u32 a52_p416_last_page;\n"
    text = one(text, var, var + "static u32 a52_p419_mirror_count;\n",
               "mirror count")

    helper_anchor = "static void a52_p414_append_text(const char *message)\n"
    helper = r'''static void a52_p419_mirror_locked(const struct a52_p414_record *rec)
{
	void __iomem *dst;

	if (!rec || strncmp(rec->text, "P419 ", 5) ||
	    a52_p419_mirror_count >= A52_P419_MIRROR_SLOTS)
		return;

	dst = (u8 __iomem *)a52_p414_ram + A52_P419_MIRROR_OFFSET +
		a52_p419_mirror_count * A52_P414_RECORD_BYTES;
	memcpy_toio(dst, rec, sizeof(*rec));
	a52_p406_persist(dst, sizeof(*rec));
	a52_p419_mirror_count++;
}

'''
    text = one(text, helper_anchor, helper + helper_anchor, "mirror helper")

    rec_commit = "\trec.commit = A52_P414_REC_COMMIT;\n\n"
    text = one(text, rec_commit,
               rec_commit + "\ta52_p419_mirror_locked(&rec);\n\n",
               "mirror call")

    text = one(text,
        "\ta52_p416_last_page = 0;\n",
        "\ta52_p416_last_page = 0;\n\ta52_p419_mirror_count = 0;\n",
        "mirror reset")

    build_bug = "\tBUILD_BUG_ON(PAGE_SIZE != 4096);\n"
    text = one(text, build_bug,
        "\tBUILD_BUG_ON(PAGE_SIZE != 4096);\n"
        "\tBUILD_BUG_ON(A52_P419_MIRROR_OFFSET +\n"
        "\t\tA52_P419_MIRROR_SLOTS * A52_P414_RECORD_BYTES >\n"
        "\t\tA52_P414_HEADER_BYTES);\n",
        "mirror bounds")

    text += f"\n/* {MARK}: unique boot id, P276 gate repair, critical RAM mirror. */\n"
    return text


def validate(root: Path) -> None:
    core=(root/CORE).read_text(errors="replace")
    dsi=(root/DSI_DISPLAY).read_text(errors="replace")
    drm=(root/DSI_DRM).read_text(errors="replace")
    con=(root/CONNECTOR).read_text(errors="replace")
    rec=(root/REC).read_text(errors="replace")

    s,e=function_bounds(core,"drm_mode_convert_umode")
    fn=core[s:e]
    if "out->vrefresh = in->vrefresh;" not in fn or MARK not in fn:
        raise SystemExit("Phase419 vrefresh restoration missing")

    for tok in (
        MARK, "P419 FQ h=%u v=%u r=%u p=%u", "P419 FC i=%u",
        "P419 FR rc=%d hit=%d", "a52_p419_find_once",
    ):
        if tok not in dsi:
            raise SystemExit("Phase419 DSI find marker missing: "+tok)

    # Confirm the exact compiled downstream dependency we're repairing.
    if "dsi_mode->timing.refresh_rate = drm_mode->vrefresh;" not in drm:
        raise SystemExit("Phase419 downstream dsi_drm no longer reads drm_mode->vrefresh")

    for tok in (
        MARK, "P419 GMI rc=%d h=%d v=%d r=%d p=%d pv=%u",
        "a52_p419_gmi_once",
    ):
        if tok not in con:
            raise SystemExit("Phase419 connector marker missing: "+tok)

    for tok in (
        MARK, "#include <linux/random.h>", "get_random_u64()",
        "A52_P419_MIRROR_OFFSET", "a52_p419_mirror_locked(&rec);",
        'strncmp(fmt, "P276 387", 8)', 'strncmp(fmt, "P276 394", 8)',
    ):
        if tok not in rec:
            raise SystemExit("Phase419 recorder marker missing: "+tok)
    if 'strncmp(fmt, "P276 387", 9)' in rec or 'strncmp(fmt, "P276 394", 9)' in rec:
        raise SystemExit("Phase419 stale P276 9-byte gate remains")


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    ns=ap.parse_args()
    for rel in (CORE,DSI_DISPLAY,DSI_DRM,CONNECTOR,REC):
        if not (ns.root/rel).is_file():
            raise SystemExit("Phase419 source missing: "+str(rel))
    if not ns.check_only:
        for rel,fn in ((CORE,patch_core),(DSI_DISPLAY,patch_dsi_display),
                       (CONNECTOR,patch_connector),(REC,patch_rec)):
            p=ns.root/rel
            p.write_text(fn(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase419 vrefresh mode-info fix: PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
