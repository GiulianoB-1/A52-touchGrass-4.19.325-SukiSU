#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE441_VRR_STATE_COMPAT_V1"
SYSFS = Path("drivers/a52_display/msm/samsung/ss_dsi_panel_sysfs.c")


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
	 * Samsung bootanimation expects exactly an ordered W X H : mode string.
	 * On GKI, CONFIG_SEC_PARAM may be absent, leaving the legacy downstream
	 * buffer untouched. Always terminate it, then reject empty or malformed
	 * contents before exposing them to userspace.
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
 * Keep /sys/class/lcd/panel/vrr_state safe when SEC_PARAM is not available.
 */
static const char a52_p441_vrr_state_build_tag[] __used =
	"A52_PHASE441_VRR_STATE_COMPAT_V1";

'''


def patch_sysfs(text: str) -> str:
    if MARK in text:
        return text

    start, end, old = extract_vrr_state(text)

    required = (
        "char vrr_mode[16];",
        "sec_get_param(param_index_VrrStatus, &vrr_mode);",
        'return snprintf(buf, sizeof(vrr_mode), "%s\\n", vrr_mode);',
    )
    for token in required:
        if token not in old:
            die("legacy vrr_state_show anchor missing: " + token)

    return text[:start] + NEW_FN + text[end:]


def validate(root: Path) -> None:
    p = root / SYSFS
    if not p.is_file():
        die("missing " + str(SYSFS))

    text = p.read_text(errors="replace")
    _, _, fn = extract_vrr_state(text)

    for token in (
        MARK,
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
        if token not in fn and token != MARK:
            die("missing patched token: " + token)

    if MARK not in text:
        die("build marker missing")
    if "char vrr_mode[16];" in fn:
        die("uninitialized vrr_mode declaration remains")
    if "sec_set_param(param_index_VrrStatus" in fn:
        die("unexpected persistent param write remains")

    print("Phase441 VRR state compatibility: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()

    if not ns.check_only:
        p = root / SYSFS
        if not p.is_file():
            die("missing " + str(SYSFS))
        p.write_text(patch_sysfs(p.read_text(errors="replace")))

    validate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
