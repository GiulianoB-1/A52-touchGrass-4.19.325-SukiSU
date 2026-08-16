#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
PROBE = ROOT / 'drivers/gpu/drm/drm_probe_helper.c'
CORE = ROOT / 'drivers/gpu/drm/drm_connector.c'
SDE = ROOT / 'drivers/a52_display/msm/sde/sde_connector.c'
MARKER = 'A52_PHASE271B_BROAD_ZERO_STATE_OBSERVER_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected exactly one anchor, found {n}')
    return text.replace(old, new, 1)


def patch_probe(text: str) -> str:
    helper_anchor = '''static bool a52_p271_trace(const struct drm_connector *connector)
{
\treturn connector &&
\t\tconnector->connector_type == DRM_MODE_CONNECTOR_DSI &&
\t\ta52_ackfr_phase269_is_composer_tgid(current->tgid);
}
'''
    helper_new = helper_anchor + '''
/* A52_PHASE271B_BROAD_ZERO_STATE_OBSERVER_V1 */
static unsigned int a52_p271_list_count(const struct list_head *head)
{
\tconst struct list_head *pos;
\tunsigned int n = 0;

\tlist_for_each(pos, head)
\t\tn++;
\treturn n;
}
'''
    text = one(text, helper_anchor, helper_new, 'mode-list counter')

    old = '\tcount = (*connector_funcs->get_modes)(connector);\n'
    new = old + '''\tif (a52_p271_trace(connector))
\t\ta52_ackfr_record("P271 L id=%u k=G ret=%d m=%u pm=%u st=%u",
\t\t\tconnector->base.id, count,
\t\t\ta52_p271_list_count(&connector->modes),
\t\t\ta52_p271_list_count(&connector->probed_modes),
\t\t\tconnector->status);
'''
    text = one(text, old, new, 'post get_modes lifecycle')

    old = '\tdrm_connector_list_update(connector);\n'
    new = old + '''\tif (a52_p271_trace(connector))
\t\ta52_ackfr_record("P271 L id=%u k=U ret=%d m=%u pm=%u st=%u",
\t\t\tconnector->base.id, count,
\t\t\ta52_p271_list_count(&connector->modes),
\t\t\ta52_p271_list_count(&connector->probed_modes),
\t\t\tconnector->status);
'''
    text = one(text, old, new, 'post list_update lifecycle')

    old = '''prune:
\tdrm_mode_prune_invalid(dev, &connector->modes, verbose_prune);
'''
    new = '''prune:
\tif (a52_p271_trace(connector)) {
\t\tlist_for_each_entry(mode, &connector->modes, head)
\t\t\ta52_ackfr_record("P271 Z id=%u hv=%ux%u r=%u fl=%x st=%d",
\t\t\t\tconnector->base.id, mode->hdisplay, mode->vdisplay,
\t\t\t\tmode->vrefresh, mode->flags, mode->status);
\t\ta52_ackfr_record("P271 L id=%u k=B ret=%d m=%u pm=%u st=%u",
\t\t\tconnector->base.id, count,
\t\t\ta52_p271_list_count(&connector->modes),
\t\t\ta52_p271_list_count(&connector->probed_modes),
\t\t\tconnector->status);
\t}
\tdrm_mode_prune_invalid(dev, &connector->modes, verbose_prune);
\tif (a52_p271_trace(connector))
\t\ta52_ackfr_record("P271 L id=%u k=A ret=%d m=%u pm=%u st=%u",
\t\t\tconnector->base.id, count,
\t\t\ta52_p271_list_count(&connector->modes),
\t\t\ta52_p271_list_count(&connector->probed_modes),
\t\t\tconnector->status);
'''
    return one(text, old, new, 'pre/post prune lifecycle')


def patch_core(text: str) -> str:
    old = '\tencoders_count = hweight32(connector->possible_encoders);\n'
    new = old + '''
\tif (connector->connector_type == DRM_MODE_CONNECTOR_DSI &&
\t    a52_ackfr_phase269_is_composer_tgid(current->tgid)) {
\t\tstruct drm_crtc *a52_crtc;

\t\ta52_ackfr_record("P271 T id=%u nc=%d ne=%d nr=%d max=%dx%d",
\t\t\tconnector->base.id, dev->mode_config.num_connector,
\t\t\tdev->mode_config.num_encoder, dev->mode_config.num_crtc,
\t\t\tdev->mode_config.max_width, dev->mode_config.max_height);
\t\ta52_ackfr_record("P271 C id=%u stp=%u best=%u scrtc=%u legacy=%u pe=%x ne=%d",
\t\t\tconnector->base.id, connector->state ? 1 : 0,
\t\t\t(connector->state && connector->state->best_encoder) ?
\t\t\t\tconnector->state->best_encoder->base.id : 0,
\t\t\t(connector->state && connector->state->crtc) ?
\t\t\t\tconnector->state->crtc->base.id : 0,
\t\t\tconnector->encoder ? connector->encoder->base.id : 0,
\t\t\tconnector->possible_encoders, encoders_count);
\t\tdrm_connector_for_each_possible_encoder(connector, encoder) {
\t\t\ta52_ackfr_record("P271 E id=%u eid=%u pc=%x pcl=%x ec=%u ecs=%u eca=%u",
\t\t\t\tconnector->base.id, encoder->base.id,
\t\t\t\tencoder->possible_crtcs, encoder->possible_clones,
\t\t\t\tencoder->crtc ? encoder->crtc->base.id : 0,
\t\t\t\t(encoder->crtc && encoder->crtc->state) ? 1 : 0,
\t\t\t\t(encoder->crtc && encoder->crtc->state &&
\t\t\t\t encoder->crtc->state->active) ? 1 : 0);
\t\t}
\t\tdrm_for_each_crtc(a52_crtc, dev)
\t\t\ta52_ackfr_record("P271 R cid=%u sp=%u act=%u en=%u",
\t\t\t\ta52_crtc->base.id, a52_crtc->state ? 1 : 0,
\t\t\t\t(a52_crtc->state && a52_crtc->state->active) ? 1 : 0,
\t\t\t\t(a52_crtc->state && a52_crtc->state->enable) ? 1 : 0);
\t}
'''
    text = one(text, old, new, 'GETCONNECTOR topology')

    old = '''\tdrm_modeset_lock(&dev->mode_config.connection_mutex, NULL);
\tencoder = drm_connector_get_encoder(connector);
\tif (encoder)
'''
    new = '''\tdrm_modeset_lock(&dev->mode_config.connection_mutex, NULL);
\tencoder = drm_connector_get_encoder(connector);
\tif (connector->connector_type == DRM_MODE_CONNECTOR_DSI &&
\t    a52_ackfr_phase269_is_composer_tgid(current->tgid))
\t\ta52_ackfr_record("P271 G id=%u sel=%u stp=%u best=%u scrtc=%u legacy=%u",
\t\t\tconnector->base.id, encoder ? encoder->base.id : 0,
\t\t\tconnector->state ? 1 : 0,
\t\t\t(connector->state && connector->state->best_encoder) ?
\t\t\t\tconnector->state->best_encoder->base.id : 0,
\t\t\t(connector->state && connector->state->crtc) ?
\t\t\t\tconnector->state->crtc->base.id : 0,
\t\t\tconnector->encoder ? connector->encoder->base.id : 0);
\tif (encoder)
'''
    return one(text, old, new, 'GETCONNECTOR selected encoder')


def patch_sde(text: str) -> str:
    old = '''\t/*
\t * This is true for now, revisit this code when multiple encoders are
\t * supported.
\t */
\treturn c_conn->encoder;
'''
    new = '''\t/*
\t * This is true for now, revisit this code when multiple encoders are
\t * supported.
\t */
\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))
\t\ta52_ackfr_record("P271 SB id=%u eid=%u stp=%u best=%u",
\t\t\tconnector->base.id, c_conn->encoder ? c_conn->encoder->base.id : 0,
\t\t\tconnector->state ? 1 : 0,
\t\t\t(connector->state && connector->state->best_encoder) ?
\t\t\t\tconnector->state->best_encoder->base.id : 0);
\treturn c_conn->encoder;
'''
    text = one(text, old, new, 'SDE best_encoder')

    old = '''\trc = drm_connector_attach_encoder(&c_conn->base, encoder);
\tif (rc) {
\t\tSDE_ERROR("failed to attach encoder to connector, %d\\n", rc);
\t\tgoto error_cleanup_fence;
\t}
'''
    new = '''\trc = drm_connector_attach_encoder(&c_conn->base, encoder);
\tif (rc) {
\t\tSDE_ERROR("failed to attach encoder to connector, %d\\n", rc);
\t\tgoto error_cleanup_fence;
\t}
\ta52_ackfr_record("P271 SI id=%u eid=%u pe=%x pc=%x hv2=%u ty=%d",
\t\tc_conn->base.base.id, encoder->base.id,
\t\tc_conn->base.possible_encoders, encoder->possible_crtcs,
\t\t(ops && ops->atomic_best_encoder && ops->atomic_check) ? 1 : 0,
\t\tconnector_type);
'''
    text = one(text, old, new, 'SDE connector attach')

    old = '''\tc_conn->encoder = encoder;

\treturn encoder;
'''
    new = '''\tc_conn->encoder = encoder;
\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))
\t\ta52_ackfr_record("P271 SA id=%u eid=%u scrtc=%u",
\t\t\tconnector->base.id, encoder ? encoder->base.id : 0,
\t\t\t(connector_state && connector_state->crtc) ?
\t\t\t\tconnector_state->crtc->base.id : 0);

\treturn encoder;
'''
    return one(text, old, new, 'SDE atomic_best_encoder')


def apply() -> None:
    for path, fn in ((PROBE, patch_probe), (CORE, patch_core), (SDE, patch_sde)):
        if not path.is_file():
            raise RuntimeError(f'missing {path}')
        src = path.read_text(encoding='utf-8')
        dst = fn(src)
        if dst == src:
            raise RuntimeError(f'no change for {path}')
        path.write_text(dst, encoding='utf-8')
    print(MARKER)
    print('Phase271b broad zero-state observer applied')


if __name__ == '__main__':
    apply()
