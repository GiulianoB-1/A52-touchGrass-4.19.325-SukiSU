#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
REC = ROOT / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
CORE = ROOT / "drivers/gpu/drm/drm_connector.c"
SDE = ROOT / "drivers/a52_display/msm/sde/sde_connector.c"
DSI_DRM = ROOT / "drivers/a52_display/msm/dsi/dsi_drm.c"
DSI_DISPLAY = ROOT / "drivers/a52_display/msm/dsi/dsi_display.c"
MARKER = "A52_PHASE270_DSI_MODE_PATH_OBSERVER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def add_observer_decl(text: str, anchor: str, label: str) -> str:
    add = (
        anchor
        + '#include <linux/a52_ack_secure_flight_recorder.h>\n'
        + 'extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\n'
    )
    return one(text, anchor, add, label)


def patch_recorder(text: str) -> str:
    text = one(
        text,
        'return !strncmp(message, "P269 ", 5) ||',
        'return !strncmp(message, "P270 ", 5) ||\n'
        '       !strncmp(message, "P269 ", 5) ||',
        "critical P270 retention",
    )
    text = one(
        text,
        'if (strncmp(fmt, "P269", 4) &&',
        'if (strncmp(fmt, "P270", 4) &&\n'
        '    strncmp(fmt, "P269", 4) &&',
        "P270 admission",
    )
    return text


def patch_core(text: str) -> str:
    text = add_observer_decl(text, '#include <linux/uaccess.h>\n', "core observer include")
    old = '''\tmutex_lock(&dev->mode_config.mutex);\n\tif (out_resp->count_modes == 0) {\n\t\tconnector->funcs->fill_modes(connector,\n\t\t\t\t\t     dev->mode_config.max_width,\n\t\t\t\t\t     dev->mode_config.max_height);\n\t}\n'''
    new = '''\tmutex_lock(&dev->mode_config.mutex);\n\tif (out_resp->count_modes == 0) {\n\t\tint a52_p270_rc;\n\t\tunsigned int a52_p270_modes = 0, a52_p270_probed = 0;\n\t\tstruct drm_display_mode *a52_p270_mode;\n\t\tbool a52_p270_comp =\n\t\t\ta52_ackfr_phase269_is_composer_tgid(current->tgid);\n\n\t\tlist_for_each_entry(a52_p270_mode, &connector->modes, head)\n\t\t\ta52_p270_modes++;\n\t\tlist_for_each_entry(a52_p270_mode, &connector->probed_modes, head)\n\t\t\ta52_p270_probed++;\n\t\tif (a52_p270_comp)\n\t\t\ta52_ackfr_record("P270 CORE pre id=%u st=%u m=%u pm=%u",\n\t\t\t\tconnector->base.id, connector->status,\n\t\t\t\ta52_p270_modes, a52_p270_probed);\n\n\t\ta52_p270_rc = connector->funcs->fill_modes(connector,\n\t\t\t\t\t     dev->mode_config.max_width,\n\t\t\t\t\t     dev->mode_config.max_height);\n\n\t\ta52_p270_modes = 0;\n\t\ta52_p270_probed = 0;\n\t\tlist_for_each_entry(a52_p270_mode, &connector->modes, head)\n\t\t\ta52_p270_modes++;\n\t\tlist_for_each_entry(a52_p270_mode, &connector->probed_modes, head)\n\t\t\ta52_p270_probed++;\n\t\tif (a52_p270_comp)\n\t\t\ta52_ackfr_record("P270 CORE post id=%u rc=%d st=%u m=%u pm=%u",\n\t\t\t\tconnector->base.id, a52_p270_rc, connector->status,\n\t\t\t\ta52_p270_modes, a52_p270_probed);\n\t}\n'''
    return one(text, old, new, "core fill_modes boundary")


def patch_sde(text: str) -> str:
    text = add_observer_decl(text, '#include "msm_drv.h"\n', "SDE observer include")
    old = '''\tmode_count = c_conn->ops.get_modes(connector, c_conn->display,\n\t\t\t&avail_res);\n\tif (!mode_count) {\n'''
    new = '''\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))\n\t\ta52_ackfr_record("P270 SDE pre id=%u disp=%u cb=%u",\n\t\t\tconnector->base.id, c_conn->display ? 1 : 0,\n\t\t\tc_conn->ops.get_modes ? 1 : 0);\n\tmode_count = c_conn->ops.get_modes(connector, c_conn->display,\n\t\t\t&avail_res);\n\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))\n\t\ta52_ackfr_record("P270 SDE post id=%u cnt=%d",\n\t\t\tconnector->base.id, mode_count);\n\tif (!mode_count) {\n'''
    return one(text, old, new, "SDE get_modes boundary")


def patch_dsi_drm(text: str) -> str:
    text = add_observer_decl(text, '#include "msm_kms.h"\n', "DSI DRM observer include")
    old = '''\trc = dsi_display_get_mode_count(display, &count);\n\tif (rc) {\n'''
    new = '''\trc = dsi_display_get_mode_count(display, &count);\n\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))\n\t\ta52_ackfr_record("P270 DSI count id=%u rc=%d cnt=%u disp=%u panel=%u",\n\t\t\tconnector->base.id, rc, count, display ? 1 : 0,\n\t\t\t(display && display->panel) ? 1 : 0);\n\tif (rc) {\n'''
    text = one(text, old, new, "DSI mode-count boundary")
    old = '''\trc = dsi_display_get_modes(display, &modes);\n\tif (rc) {\n'''
    new = '''\trc = dsi_display_get_modes(display, &modes);\n\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))\n\t\ta52_ackfr_record("P270 DSI modes id=%u rc=%d ptr=%u expect=%u",\n\t\t\tconnector->base.id, rc, modes ? 1 : 0, count);\n\tif (rc) {\n'''
    return one(text, old, new, "DSI get_modes boundary")


def patch_dsi_display(text: str) -> str:
    text = add_observer_decl(text, '#include "msm_drv.h"\n', "DSI display observer include")
    old = '''\tmutex_lock(&display->display_lock);\n\t*count = display->panel->num_display_modes;\n\tmutex_unlock(&display->display_lock);\n'''
    new = '''\tmutex_lock(&display->display_lock);\n\t*count = display->panel->num_display_modes;\n\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))\n\t\ta52_ackfr_record("P270 PANEL cnt=%u cached=%u", *count,\n\t\t\tdisplay->modes ? 1 : 0);\n\tmutex_unlock(&display->display_lock);\n'''
    return one(text, old, new, "panel mode-count source")


def apply() -> None:
    patches = [
        (REC, patch_recorder),
        (CORE, patch_core),
        (SDE, patch_sde),
        (DSI_DRM, patch_dsi_drm),
        (DSI_DISPLAY, patch_dsi_display),
    ]
    for path, fn in patches:
        if not path.is_file():
            raise RuntimeError(f"missing {path}")
        src = path.read_text(encoding="utf-8")
        dst = fn(src)
        if dst == src:
            raise RuntimeError(f"no change for {path}")
        path.write_text(dst, encoding="utf-8")
    print(MARKER)
    print("Phase270 diagnostic DSI mode-path observer applied")


if __name__ == "__main__":
    apply()
