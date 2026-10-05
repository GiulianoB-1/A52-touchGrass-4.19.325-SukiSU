#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE441_VRR_STATE_COMPAT_V1"
P440 = "A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1"
SYSFS = Path("drivers/a52_display/msm/samsung/ss_dsi_panel_sysfs.c")
DISP = Path("drivers/a52_display/msm/dsi/dsi_display.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def die(msg: str) -> None:
    raise SystemExit("Phase441: " + msg)


def extract_vrr_state(text: str) -> tuple[int, int, str]:
    needle = "static ssize_t vrr_state_show("
    if text.count(needle) != 1:
        die(f"expected exactly one vrr_state_show, found {text.count(needle)}")

    start = text.index(needle)
    next_fn = text.find("\nstatic ssize_t ", start + len(needle))
    if next_fn < 0:
        die("could not find function boundary after vrr_state_show")
    return start, next_fn + 1, text[start:next_fn + 1]


NEW_FN = r'''static ssize_t vrr_state_show(struct device *dev,
	struct device_attribute *attr, char *buf)
{
	char vrr_mode[16] = "";
	char default_mode[16] = "";
	char *x, *colon;
	struct samsung_display_driver_data *vdd =
		(struct samsung_display_driver_data *)dev_get_drvdata(dev);
	struct dsi_display *display = GET_DSI_DISPLAY(vdd);
	u32 mode_idx, timing_mode_count;
	bool is_hs_mode = false;

	timing_mode_count = display->panel->num_timing_nodes;
	for (mode_idx = 0; mode_idx < timing_mode_count; mode_idx++) {
		struct dsi_display_mode *hs_mode = &display->modes[mode_idx];

		if (hs_mode->timing.sot_hs_mode) {
			is_hs_mode = true;
			break;
		}
	}

	if (!is_hs_mode) {
		LCD_INFO(vdd, "default resolution\n");
		return snprintf(buf, sizeof(default_mode), "%s\n", default_mode);
	}

#if IS_ENABLED(CONFIG_SEC_PARAM)
	sec_get_param(param_index_VrrStatus, &vrr_mode);
#endif

	/*
	 * Samsung bootanimation expects an ordered W X H : mode string.
	 * On GKI, CONFIG_SEC_PARAM may be absent, so the legacy downstream
	 * buffer must never be exposed unless it is initialized and parseable.
	 */
	vrr_mode[sizeof(vrr_mode) - 1] = '\0';
	x = memchr(vrr_mode, 'X', sizeof(vrr_mode));
	colon = memchr(vrr_mode, ':', sizeof(vrr_mode));

	if (!vrr_mode[0] || !x || !colon || x == vrr_mode || colon <= x + 1) {
		snprintf(default_mode, sizeof(default_mode), "%dX%d:NOR",
			display->modes->timing.h_active,
			display->modes->timing.v_active);
		LCD_INFO(vdd, "DMS vrr_state fallback = %s\n", default_mode);
		return snprintf(buf, sizeof(default_mode), "%s\n", default_mode);
	}

	LCD_INFO(vdd, "DMS param_index_VrrStatus = %s\n", vrr_mode);
	return snprintf(buf, sizeof(vrr_mode), "%s\n", vrr_mode);
}

/* A52_PHASE441_VRR_STATE_COMPAT_V1
 * Keep /sys/class/lcd/panel/vrr_state safe when SEC_PARAM is unavailable.
 */
static const char a52_p441_vrr_state_build_tag[] __used =
	"A52_PHASE441_VRR_STATE_COMPAT_V1";

'''


def patch_sysfs(text: str) -> str:
    if MARK in text:
        return text

    start, end, old = extract_vrr_state(text)
    for token in (
        "char vrr_mode[16];",
        "sec_get_param(param_index_VrrStatus, &vrr_mode);",
        'return snprintf(buf, sizeof(vrr_mode), "%s\\n", vrr_mode);',
    ):
        if token not in old:
            die("legacy vrr_state_show anchor missing: " + token)

    return text[:start] + NEW_FN + text[end:]


def strip_phase440_active_f0(text: str) -> str:
    """Keep Phase440 passive event hooks, remove its scheduled F0 sender."""
    if "a52_p440_early_workfn" not in text and "a52_p440_schedule(display);" not in text:
        return text

    helper_start = text.find(
        "/* A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1 */\n"
        "extern void a52_p439_allow_next(void);"
    )
    if helper_start < 0:
        die("Phase440 display helper start missing")

    sched = text.find("static void a52_p440_schedule(", helper_start)
    if sched < 0:
        die("Phase440 schedule helper missing")
    brace = text.find("{", sched)
    if brace < 0:
        die("Phase440 schedule helper opening brace missing")

    depth = 0
    helper_end = -1
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                helper_end = i + 1
                while helper_end < len(text) and text[helper_end] in "\\r\\n":
                    helper_end += 1
                break
    if helper_end < 0:
        die("Phase440 schedule helper closing brace missing")

    text = text[:helper_start] + text[helper_end:]

    hook = (
        "\t/* Phase440: schedule one absolute-boottime early F0, leave natural late F0 untouched. */\n"
        "\ta52_p440_schedule(display);\n\n"
    )
    if text.count(hook) != 1:
        die(f"Phase440 early scheduling hook count = {text.count(hook)}")
    text = text.replace(hook, "", 1)

    trailer = (
        "\n/* A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1: "
        "absolute ~12 s early F0 + 75 s PASS/PASS flush. */\n"
    )
    text = text.replace(trailer, "\n", 1)

    return text


def validate(root: Path) -> None:
    sysfs = root / SYSFS
    disp = root / DISP
    rec = root / REC
    for p in (sysfs, disp, rec):
        if not p.is_file():
            die("missing " + str(p.relative_to(root)))

    text = sysfs.read_text(errors="replace")
    _, _, fn = extract_vrr_state(text)

    for token in (
        'char vrr_mode[16] = "";',
        "vrr_mode[sizeof(vrr_mode) - 1] = '\\0';",
        "memchr(vrr_mode, 'X', sizeof(vrr_mode))",
        "memchr(vrr_mode, ':', sizeof(vrr_mode))",
        "colon <= x + 1",
        '"%dX%d:NOR"',
        "display->modes->timing.h_active",
        "display->modes->timing.v_active",
        'return snprintf(buf, sizeof(default_mode), "%s\\n", default_mode);',
        'return snprintf(buf, sizeof(vrr_mode), "%s\\n", vrr_mode);',
    ):
        if token not in fn:
            die("missing patched token: " + token)

    if MARK not in text:
        die("Phase441 build marker missing")
    if "char vrr_mode[16];" in fn:
        die("uninitialized vrr_mode declaration remains")
    if "sec_set_param(param_index_VrrStatus" in fn:
        die("unexpected persistent param write remains")

    dtext = disp.read_text(errors="replace")
    for forbidden in (
        "a52_p440_early_workfn",
        "a52_p440_schedule(display);",
        "P440 EARLY start",
        "P440 EARLY unlock_rc",
        "P440 EARLY relock_rc",
    ):
        if forbidden in dtext:
            die("active Phase440 F0 injector remains: " + forbidden)

    rtext = rec.read_text(errors="replace")
    for token in (P440, "P440 EV n=%u", "a52_p440_event"):
        if token not in rtext:
            die("passive Phase440 event logging missing: " + token)

    print("Phase441 VRR state compatibility + passive-only forensics: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()

    if not ns.check_only:
        sysfs = root / SYSFS
        disp = root / DISP
        if not sysfs.is_file():
            die("missing " + str(SYSFS))
        if not disp.is_file():
            die("missing " + str(DISP))
        sysfs.write_text(patch_sysfs(sysfs.read_text(errors="replace")))
        disp.write_text(strip_phase440_active_f0(disp.read_text(errors="replace")))

    validate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
