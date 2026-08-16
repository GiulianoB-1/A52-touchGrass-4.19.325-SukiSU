#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
REC = ROOT / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c'
PROBE = ROOT / 'drivers/gpu/drm/drm_probe_helper.c'
MARKER = 'A52_PHASE271_DRM_MODE_VALIDATION_OBSERVER_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected exactly one anchor, found {n}')
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    text = one(
        text,
        'return !strncmp(message, "P270 ", 5) ||',
        'return !strncmp(message, "P271 ", 5) ||\n'
        '       !strncmp(message, "P270 ", 5) ||',
        'critical P271 retention',
    )
    text = one(
        text,
        'if (strncmp(fmt, "P270", 4) &&',
        'if (strncmp(fmt, "P271", 4) &&\n'
        '    strncmp(fmt, "P270", 4) &&',
        'P271 admission',
    )
    return text


def patch_probe(text: str) -> str:
    # Composer identity and a strict DSI-only filter. No validation return value
    # or mode status is changed by this observer.
    text = one(
        text,
        '#include <linux/moduleparam.h>\n',
        '#include <linux/moduleparam.h>\n'
        '#include <linux/sched.h>\n'
        '#include <linux/a52_ack_secure_flight_recorder.h>\n\n'
        'extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\n',
        'probe observer includes',
    )
    anchor = 'static bool drm_kms_helper_poll = true;\nmodule_param_named(poll, drm_kms_helper_poll, bool, 0600);\n'
    helper = anchor + '''\n/* A52_PHASE271_DRM_MODE_VALIDATION_OBSERVER_V1\n * Phase270 hardware proved that DSI returns two modes to SDE, while the DRM\n * fill_modes helper returns zero and prunes both. Observe each validation\n * stage for the exact exec-latched vendor Composer and DSI connector only.\n */\nstatic bool a52_p271_trace(const struct drm_connector *connector)\n{\n\treturn connector &&\n\t\tconnector->connector_type == DRM_MODE_CONNECTOR_DSI &&\n\t\ta52_ackfr_phase269_is_composer_tgid(current->tgid);\n}\n'''
    text = one(text, anchor, helper, 'P271 helper')

    old = '''\t/* Step 1: Validate against connector */\n\tret = drm_connector_mode_valid(connector, mode, ctx, status);\n\tif (ret || *status != MODE_OK)\n\t\treturn ret;\n'''
    new = '''\t/* Step 1: Validate against connector */\n\tret = drm_connector_mode_valid(connector, mode, ctx, status);\n\tif (a52_p271_trace(connector))\n\t\ta52_ackfr_record("P271 Q id=%u k=C ret=%d st=%d",\n\t\t\tconnector->base.id, ret, *status);\n\tif (ret || *status != MODE_OK)\n\t\treturn ret;\n'''
    text = one(text, old, new, 'connector validation stage')

    old = '''\t\t*status = drm_encoder_mode_valid(encoder, mode);\n\t\tif (*status != MODE_OK) {\n'''
    new = '''\t\t*status = drm_encoder_mode_valid(encoder, mode);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 Q id=%u k=E eid=%u pc=%x st=%d",\n\t\t\t\tconnector->base.id, encoder->base.id,\n\t\t\t\tencoder->possible_crtcs, *status);\n\t\tif (*status != MODE_OK) {\n'''
    text = one(text, old, new, 'encoder validation stage')

    old = '''\t\tbridge = drm_bridge_chain_get_first_bridge(encoder);\n\t\t*status = drm_bridge_chain_mode_valid(bridge,\n\t\t\t\t\t\t      &connector->display_info,\n\t\t\t\t\t\t      mode);\n\t\tif (*status != MODE_OK) {\n'''
    new = '''\t\tbridge = drm_bridge_chain_get_first_bridge(encoder);\n\t\t*status = drm_bridge_chain_mode_valid(bridge,\n\t\t\t\t\t\t      &connector->display_info,\n\t\t\t\t\t\t      mode);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 Q id=%u k=B eid=%u br=%u st=%d",\n\t\t\t\tconnector->base.id, encoder->base.id, bridge ? 1 : 0,\n\t\t\t\t*status);\n\t\tif (*status != MODE_OK) {\n'''
    text = one(text, old, new, 'bridge validation stage')

    old = '''\t\t\t*status = drm_crtc_mode_valid(crtc, mode);\n\t\t\tif (*status == MODE_OK) {\n'''
    new = '''\t\t\t*status = drm_crtc_mode_valid(crtc, mode);\n\t\t\tif (a52_p271_trace(connector))\n\t\t\t\ta52_ackfr_record("P271 Q id=%u k=R eid=%u cid=%u st=%d",\n\t\t\t\t\tconnector->base.id, encoder->base.id, crtc->base.id,\n\t\t\t\t\t*status);\n\t\t\tif (*status == MODE_OK) {\n'''
    text = one(text, old, new, 'CRTC validation stage')

    old = '''\tlist_for_each_entry(mode, &connector->modes, head) {\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tmode->status = drm_mode_validate_driver(dev, mode);\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tmode->status = drm_mode_validate_size(mode, maxX, maxY);\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tmode->status = drm_mode_validate_flag(mode, mode_flags);\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tret = drm_mode_validate_pipeline(mode, connector, ctx,\n\t\t\t\t\t\t &mode->status);\n'''
    new = '''\tlist_for_each_entry(mode, &connector->modes, head) {\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 M id=%u hv=%ux%u r=%u clk=%d fl=%x ty=%x",\n\t\t\t\tconnector->base.id, mode->hdisplay, mode->vdisplay,\n\t\t\t\tmode->vrefresh, mode->clock, mode->flags, mode->type);\n\n\t\tmode->status = drm_mode_validate_driver(dev, mode);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 V id=%u k=D st=%d",\n\t\t\t\tconnector->base.id, mode->status);\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tmode->status = drm_mode_validate_size(mode, maxX, maxY);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 V id=%u k=S st=%d max=%ux%u",\n\t\t\t\tconnector->base.id, mode->status, maxX, maxY);\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tmode->status = drm_mode_validate_flag(mode, mode_flags);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 V id=%u k=F st=%d mf=%x",\n\t\t\t\tconnector->base.id, mode->status, mode_flags);\n\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\n\t\tret = drm_mode_validate_pipeline(mode, connector, ctx,\n\t\t\t\t\t\t &mode->status);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 V id=%u k=P ret=%d st=%d",\n\t\t\t\tconnector->base.id, ret, mode->status);\n'''
    text = one(text, old, new, 'top-level validation chain')

    old = '''\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\t\tmode->status = drm_mode_validate_ycbcr420(mode, connector);\n\t}\n'''
    new = '''\t\tif (mode->status != MODE_OK)\n\t\t\tcontinue;\n\t\tmode->status = drm_mode_validate_ycbcr420(mode, connector);\n\t\tif (a52_p271_trace(connector))\n\t\t\ta52_ackfr_record("P271 V id=%u k=Y st=%d",\n\t\t\t\tconnector->base.id, mode->status);\n\t}\n'''
    text = one(text, old, new, 'YCbCr420 validation stage')
    return text


def apply() -> None:
    for path, fn in ((REC, patch_recorder), (PROBE, patch_probe)):
        if not path.is_file():
            raise RuntimeError(f'missing {path}')
        src = path.read_text(encoding='utf-8')
        dst = fn(src)
        if dst == src:
            raise RuntimeError(f'no change for {path}')
        path.write_text(dst, encoding='utf-8')
    print(MARKER)
    print('Phase271 diagnostic DRM mode-validation observer applied')


if __name__ == '__main__':
    apply()
