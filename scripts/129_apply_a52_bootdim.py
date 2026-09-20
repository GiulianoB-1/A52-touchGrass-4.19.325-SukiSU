#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/a52-bootdim").resolve()
out.mkdir(parents=True, exist_ok=True)

path = kernel / "techpack/display/msm/dsi/dsi_display.c"
s = path.read_text()

# A52 5G panel: Samsung S6E3FC3 AMS646YD01.
#
# During continuous-splash handoff the bootloader has already powered and
# initialized the panel. At this point dsi_display_cont_splash_config() has
# enabled DSI clocks/regulators and synchronized host-engine state, while the
# Samsung splash is still being displayed.
#
# Normal ss_brightness_dcs() deliberately rejects brightness changes during
# seamless/splash mode, so use a one-shot raw DCS 0x51 write here.
#
# IMPORTANT: This panel's Samsung brightness packets encode WRDISBV as
# [high byte, low byte]. Generic mipi_dsi_dcs_set_display_brightness() in this
# 4.19 tree sends [low, high], so do NOT use that helper.
#
# DBV 0x0040 = 64. Current boot default is 255, panel max is 486. This is a
# conservative first test: low enough to materially dim the static logo while
# still keeping it clearly visible.
#
# We intentionally do not modify panel->bl_config.bl_level or
# vdd->br_info.common_br.bl_level. Android's normal Samsung brightness path
# therefore restores the regular userspace-selected brightness after splash.

anchor = """	dsi_config_host_engine_state_for_cont_splash(display);
	mutex_unlock(&display->display_lock);

	/* Set the current brightness level */
"""
patch = r'''	dsi_config_host_engine_state_for_cont_splash(display);

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
	if (display->panel &&
	    !strcmp(display->panel->name,
		    "ss_dsi_panel_S6E3FC3_AMS646YD01_FHD")) {
		u8 bootdim_dbv[2] = { 0x00, 0x40 };
		ssize_t bootdim_rc;

		bootdim_rc = mipi_dsi_dcs_write(
				&display->panel->mipi_device,
				MIPI_DCS_SET_DISPLAY_BRIGHTNESS,
				bootdim_dbv, sizeof(bootdim_dbv));
		if (bootdim_rc < 0)
			LCD_ERR(vdd, "BOOTDIM: DCS 0x51 failed rc=%zd\n",
				bootdim_rc);
		else
			LCD_INFO(vdd,
				"BOOTDIM: continuous splash brightness DBV=0x0040\n");
	}
#endif

	mutex_unlock(&display->display_lock);

	/* Set the current brightness level */
'''
if anchor not in s:
    raise RuntimeError("dsi_display.c: continuous splash host-state anchor changed")
s = s.replace(anchor, patch, 1)
path.write_text(s)

# Guard against accidental state mutation or use of the wrong-endian helper.
data = path.read_text()
for token in [
    "A52 BOOTDIM",
    '"ss_dsi_panel_S6E3FC3_AMS646YD01_FHD"',
    "u8 bootdim_dbv[2] = { 0x00, 0x40 };",
    "MIPI_DCS_SET_DISPLAY_BRIGHTNESS",
    "BOOTDIM: continuous splash brightness DBV=0x0040",
]:
    if token not in data:
        raise RuntimeError(f"BOOTDIM invariant missing: {token}")

# Scope the audit to the inserted block.
a = data.index("A52 BOOTDIM")
b = data.index("mutex_unlock(&display->display_lock);", a)
blk = data[a:b]
if "mipi_dsi_dcs_set_display_brightness" in blk:
    raise RuntimeError("BOOTDIM invariant violated: wrong-endian generic helper used")
if "bl_config.bl_level =" in blk or "common_br.bl_level =" in blk:
    raise RuntimeError("BOOTDIM invariant violated: persistent brightness state modified")

subprocess.run(["git", "diff", "--check"], cwd=kernel, check=True)

report = """A52 BOOTDIM optional display variant
panel=S6E3FC3_AMS646YD01_FHD
trigger=continuous-splash kernel handoff
dcs_command=0x51
dbv=0x0040
payload_order=MSB,LSB
persistent_brightness_state=unchanged
scope=kernel handoff onward; pre-kernel bootloader-only logo cannot be guaranteed
"""
(out / "report.txt").write_text(report)
with (out / "bootdim.diff").open("wb") as f:
    subprocess.run(["git", "diff", "--",
                    "techpack/display/msm/dsi/dsi_display.c"],
                   cwd=kernel, stdout=f, check=True)

print(report)
print("A52 BOOTDIM adaptation complete")
