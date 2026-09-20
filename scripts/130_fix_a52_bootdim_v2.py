#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/a52-bootdim-v2").resolve()
out.mkdir(parents=True, exist_ok=True)

path = kernel / "techpack/display/msm/dsi/dsi_display.c"
s = path.read_text()

old = r'''	if (display->panel &&
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
'''

new = r'''	if (display->panel) {
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
'''
if old not in s:
    raise RuntimeError("dsi_display.c: BOOTDIM v1 block anchor changed")
s = s.replace(old, new, 1)
path.write_text(s)

data = path.read_text()
for token in [
    "A52_BOOTDIM: cont_splash panel=%s",
    "A52_BOOTDIM: DCS51 DBV=0x0040 rc=%zd",
    "u8 bootdim_dbv[2] = { 0x00, 0x40 };",
    "MIPI_DCS_SET_DISPLAY_BRIGHTNESS",
]:
    if token not in data:
        raise RuntimeError(f"BOOTDIM v2 invariant missing: {token}")

# The old guessed exact-name gate must be gone from the inserted hook.
a = data.index("A52 BOOTDIM")
b = data.index("mutex_unlock(&display->display_lock);", a)
blk = data[a:b]
if "ss_dsi_panel_S6E3FC3_AMS646YD01_FHD" in blk:
    raise RuntimeError("BOOTDIM v2 invariant violated: stale guessed panel-name gate")
if "mipi_dsi_dcs_set_display_brightness" in blk:
    raise RuntimeError("BOOTDIM v2 invariant violated: wrong-endian helper used")

subprocess.run(["git","diff","--check"],cwd=kernel,check=True)

report = """A52 BOOTDIM v2
base=P7 BOOTDIM v1
fix=remove guessed runtime panel-name gate
runtime_log=A52_BOOTDIM
trigger=continuous-splash kernel handoff
dcs_command=0x51
dbv=0x0040
payload_order=MSB,LSB
persistent_brightness_state=unchanged
"""
(out/"report.txt").write_text(report)
with (out/"bootdim-v2.diff").open("wb") as f:
    subprocess.run(["git","diff","--",
                    "techpack/display/msm/dsi/dsi_display.c"],
                   cwd=kernel,stdout=f,check=True)

print(report)
print("A52 BOOTDIM v2 adaptation complete")
