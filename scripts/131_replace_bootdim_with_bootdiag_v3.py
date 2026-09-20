#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/a52-bootdiag-v3").resolve()
out.mkdir(parents=True, exist_ok=True)

display = kernel / "techpack/display/msm/dsi/dsi_display.c"
panel = kernel / "techpack/display/msm/dsi/dsi_panel.c"
common = kernel / "techpack/display/msm/samsung/ss_dsi_panel_common.c"

def rep(path, old, new, count=1):
    s = path.read_text()
    n = s.count(old)
    if n < count:
        raise RuntimeError(f"{path}: anchor count {n}, expected >= {count}: {old[:120]!r}")
    path.write_text(s.replace(old, new, count))

# --------------------------------------------------------------------------
# BOOTDIAG v3
#
# v1/v2 used a raw DCS 0x51 during continuous-splash handoff. The device
# booted, but brightness did not visibly change and a display artifact was
# observed. Remove the write entirely. This phase is diagnostic-only.
#
# The Samsung S6E3FC3 brightness implementation builds a multi-command packet;
# a naked WRDISBV command is not a safe substitute during handoff.
# --------------------------------------------------------------------------

s = display.read_text()
old = r'''	dsi_config_host_engine_state_for_cont_splash(display);

#if defined(CONFIG_DISPLAY_SAMSUNG)
	/*
	 * A52 BOOTDIM: reduce OLED burn-in exposure from the static Samsung
	 * boot logo once Linux owns a working DSI host, but before continuous
	 * splash handoff finishes.
	 *
	 * S6E3FC3 uses big-endian WRDISBV payload ordering: [MSB, LSB].
	 * Keep software brightness state untouched so normal Android
	 * brightness is restored later by the regular Samsung path.
	 */
	if (display->panel) {
		u8 bootdim_dbv[2] = { 0x00, 0x40 };
		ssize_t bootdim_rc;

		/*
		 * BOOTDIM v2: this kernel image is A52xq-specific, and the
		 * actual Samsung panel name comes from the boot DT/cmdline
		 * rather than a matching string in this source tree.
		 * Log the real runtime name and do not gate the write on a
		 * guessed panel-name literal.
		 */
		pr_info("A52_BOOTDIM: cont_splash panel=%s\n",
			display->panel->name ? display->panel->name : "<null>");

		bootdim_rc = mipi_dsi_dcs_write(
				&display->panel->mipi_device,
				MIPI_DCS_SET_DISPLAY_BRIGHTNESS,
				bootdim_dbv, sizeof(bootdim_dbv));

		pr_info("A52_BOOTDIM: DCS51 DBV=0x0040 rc=%zd\n",
			bootdim_rc);

		if (bootdim_rc < 0)
			LCD_ERR(vdd, "BOOTDIM_V2: DCS 0x51 failed rc=%zd\n",
				bootdim_rc);
	}
#endif

	mutex_unlock(&display->display_lock);
'''
new = r'''	dsi_config_host_engine_state_for_cont_splash(display);

	/*
	 * A52 BOOTDIAG v3: no panel writes here.
	 * v1/v2 raw DCS 0x51 was intentionally removed after a visible
	 * display artifact. Diagnostics below identify the real ownership
	 * handoff before another brightness experiment.
	 */
	a52_bootdiag_cont_splash_seen = 1;
	pr_info("A52_BOOTDIAG: cont_splash host-ready display=%s panel=%s\n",
		display->name ? display->name : "<null>",
		(display->panel && display->panel->name) ?
			display->panel->name : "<null>");

	mutex_unlock(&display->display_lock);
'''
if old not in s:
    raise RuntimeError("dsi_display.c: BOOTDIM v2 block not found")
s = s.replace(old, new, 1)

# Persistent-in-code diagnostic state: later backlight logs report whether the
# early continuous-splash function was ever reached, even if its early printk
# has already left the ring buffer.
fn = "int dsi_display_set_backlight(struct drm_connector *connector,\n"
if "static int a52_bootdiag_cont_splash_seen;" not in s:
    pos = s.find(fn)
    if pos < 0:
        raise RuntimeError("dsi_display.c: set_backlight function not found")
    s = s[:pos] + (
        "static int a52_bootdiag_cont_splash_seen;\n"
        "static unsigned int a52_bootdiag_display_bl_calls;\n\n"
    ) + s[pos:]

old2 = r'''	panel = dsi_display->panel;

#if defined(CONFIG_DISPLAY_SAMSUNG)
'''
new2 = r'''	panel = dsi_display->panel;

	if (a52_bootdiag_display_bl_calls++ < 16)
		pr_info("A52_BOOTDIAG: display_set_backlight call=%u level=%u cont_seen=%d cont_active=%d panel=%s\n",
			a52_bootdiag_display_bl_calls, bl_lvl,
			a52_bootdiag_cont_splash_seen,
			dsi_display->is_cont_splash_enabled,
			panel->name ? panel->name : "<null>");

#if defined(CONFIG_DISPLAY_SAMSUNG)
'''
if old2 not in s:
    raise RuntimeError("dsi_display.c: display backlight anchor changed")
s = s.replace(old2, new2, 1)

# Also mark entry, before any host setup can fail.
old3 = r'''	if (!display) {
		DSI_ERR("invalid input display param\n");
		return -EINVAL;
	}

	rc = pm_runtime_get_sync(display->drm_dev->dev);
'''
new3 = r'''	if (!display) {
		DSI_ERR("invalid input display param\n");
		return -EINVAL;
	}

	pr_info("A52_BOOTDIAG: cont_splash enter display=%s panel=%s\n",
		display->name ? display->name : "<null>",
		(display->panel && display->panel->name) ?
			display->panel->name : "<null>");

	rc = pm_runtime_get_sync(display->drm_dev->dev);
'''
if old3 not in s:
    raise RuntimeError("dsi_display.c: cont splash entry anchor changed")
s = s.replace(old3, new3, 1)
display.write_text(s)

# Panel-layer diagnostics. No behavior changes.
s = panel.read_text()
if "a52_bootdiag_panel_bl_calls" not in s:
    marker = "int dsi_panel_set_backlight(struct dsi_panel *panel, u32 bl_lvl)\n"
    pos = s.find(marker)
    if pos < 0:
        raise RuntimeError("dsi_panel.c: set_backlight function missing")
    s = s[:pos] + "static unsigned int a52_bootdiag_panel_bl_calls;\n\n" + s[pos:]

old = r'''	if (panel->host_config.ext_bridge_mode)
		return 0;

	DSI_DEBUG("backlight type:%d lvl:%d\n", bl->type, bl_lvl);
'''
new = r'''	if (panel->host_config.ext_bridge_mode)
		return 0;

	if (a52_bootdiag_panel_bl_calls++ < 16)
		pr_info("A52_BOOTDIAG: panel_set_backlight call=%u level=%u type=%d panel=%s initialized=%d\n",
			a52_bootdiag_panel_bl_calls, bl_lvl, bl->type,
			panel->name ? panel->name : "<null>",
			dsi_panel_initialized(panel));

	DSI_DEBUG("backlight type:%d lvl:%d\n", bl->type, bl_lvl);
'''
if old not in s:
    raise RuntimeError("dsi_panel.c: panel backlight anchor changed")
s = s.replace(old, new, 1)

old = r'''	if (!panel) {
		DSI_ERR("invalid params\n");
		return -EINVAL;
	}

#if defined(CONFIG_DISPLAY_SAMSUNG)
'''
new = r'''	if (!panel) {
		DSI_ERR("invalid params\n");
		return -EINVAL;
	}

	pr_info("A52_BOOTDIAG: panel_prepare panel=%s initialized=%d\n",
		panel->name ? panel->name : "<null>",
		dsi_panel_initialized(panel));

#if defined(CONFIG_DISPLAY_SAMSUNG)
'''
if old not in s:
    raise RuntimeError("dsi_panel.c: panel_prepare anchor changed")
s = s.replace(old, new, 1)

old = r'''	if (!panel) {
		DSI_ERR("Invalid params\n");
		return -EINVAL;
	}

	mutex_lock(&panel->panel_lock);
'''
new = r'''	if (!panel) {
		DSI_ERR("Invalid params\n");
		return -EINVAL;
	}

	pr_info("A52_BOOTDIAG: panel_enable panel=%s initialized=%d\n",
		panel->name ? panel->name : "<null>",
		dsi_panel_initialized(panel));

	mutex_lock(&panel->panel_lock);
'''
if old not in s:
    raise RuntimeError("dsi_panel.c: panel_enable anchor changed")
s = s.replace(old, new, 1)
panel.write_text(s)

# Samsung brightness packet path. Keep it rate-limited by a tiny static counter.
s = common.read_text()
if "a52_bootdiag_ss_br_calls" not in s:
    marker = "int ss_brightness_dcs(struct samsung_display_driver_data *vdd, int level, int backlight_origin)\n"
    pos = s.find(marker)
    if pos < 0:
        raise RuntimeError("ss_dsi_panel_common.c: ss_brightness_dcs missing")
    s = s[:pos] + "static unsigned int a52_bootdiag_ss_br_calls;\n\n" + s[pos:]

old = r'''	struct dsi_panel *panel = GET_DSI_PANEL(vdd);
	static int backup_bl_level, backup_acl;

	/* Bloom project TEMP CODE
'''
new = r'''	struct dsi_panel *panel = GET_DSI_PANEL(vdd);
	static int backup_bl_level, backup_acl;

	if (a52_bootdiag_ss_br_calls++ < 16)
		pr_info("A52_BOOTDIAG: ss_brightness_dcs call=%u level=%d origin=%d saved_bl=%d splash=%d panel=%s\n",
			a52_bootdiag_ss_br_calls, level, backlight_origin,
			vdd->br_info.common_br.bl_level,
			vdd->samsung_splash_enabled,
			(panel && panel->name) ? panel->name : "<null>");

	/* Bloom project TEMP CODE
'''
if old not in s:
    raise RuntimeError("ss_dsi_panel_common.c: brightness body anchor changed")
s = s.replace(old, new, 1)
common.write_text(s)

# Safety audits: v3 must contain no BOOTDIM raw brightness write.
d = display.read_text()
a = d.find("A52 BOOTDIAG v3")
b = d.find("mutex_unlock(&display->display_lock);", a)
if a < 0 or b < 0:
    raise RuntimeError("BOOTDIAG v3 block bounds missing")
blk = d[a:b]
for forbidden in [
    "mipi_dsi_dcs_write",
    "MIPI_DCS_SET_DISPLAY_BRIGHTNESS",
    "bootdim_dbv",
    "DCS51 DBV",
]:
    if forbidden in blk:
        raise RuntimeError(f"BOOTDIAG v3 safety violation: {forbidden}")

for path, token in [
    (display, "A52_BOOTDIAG: cont_splash enter"),
    (display, "A52_BOOTDIAG: cont_splash host-ready"),
    (display, "A52_BOOTDIAG: display_set_backlight"),
    (panel, "A52_BOOTDIAG: panel_prepare"),
    (panel, "A52_BOOTDIAG: panel_enable"),
    (panel, "A52_BOOTDIAG: panel_set_backlight"),
    (common, "A52_BOOTDIAG: ss_brightness_dcs"),
]:
    if token not in path.read_text():
        raise RuntimeError(f"BOOTDIAG token missing: {path}: {token}")

subprocess.run(["git", "diff", "--check"], cwd=kernel, check=True)

report = """A52 BOOTDIAG v3
base=P7 + BOOTDIM v1/v2 reconstruction
behavior=diagnostic-only
raw_dcs_0x51=REMOVED
panel_writes_added=0
reason=v2 boot artifact + missing runtime marker
instrumentation=cont_splash,panel_prepare,panel_enable,display_set_backlight,panel_set_backlight,ss_brightness_dcs
persistent_hint=display_set_backlight reports cont_splash_seen
"""
(out / "report.txt").write_text(report)
with (out / "bootdiag-v3.diff").open("wb") as f:
    subprocess.run([
        "git", "diff", "--",
        "techpack/display/msm/dsi/dsi_display.c",
        "techpack/display/msm/dsi/dsi_panel.c",
        "techpack/display/msm/samsung/ss_dsi_panel_common.c",
    ], cwd=kernel, stdout=f, check=True)

print(report)
print("A52 BOOTDIAG v3 diagnostic-only adaptation complete")
