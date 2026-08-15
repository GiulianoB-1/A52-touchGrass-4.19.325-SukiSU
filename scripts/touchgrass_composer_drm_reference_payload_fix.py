#!/usr/bin/env python3
"""Add exact DRM capability/topology ioctl payloads to the golden display recorder."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_composer_drm_reference_payload_fix.py <kernel-root>')

p = Path(sys.argv[1]) / 'drivers/gpu/drm/drm_ioctl.c'
s = p.read_text()

anchor = '''\tretcode = drm_ioctl_kernel(filp, func, kdata, ioctl->flags);\n\tif (copy_to_user((void __user *)arg, kdata, out_size) != 0)'''
block = '''\tretcode = drm_ioctl_kernel(filp, func, kdata, ioctl->flags);

\t/* Golden UAPI payload snapshot. Generic ioctl nr/rc alone is not enough:
\t * Composer can negotiate a subtly different capability or topology while
\t * every ioctl still returns 0. Record exactly what successful userspace sees. */
\tif (kdata && tg_disp_ref_tracked()) {
\t\tswitch (nr) {
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_GET_CAP): {
\t\t\tstruct drm_get_cap *r = (struct drm_get_cap *)kdata;
\t\t\tTG_DISP_REF("DRM_CAP", NULL, retcode,
\t\t\t\t    r->capability, r->value, 0, 0);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_SET_CLIENT_CAP): {
\t\t\tstruct drm_set_client_cap *r = (struct drm_set_client_cap *)kdata;
\t\t\tTG_DISP_REF("DRM_CLIENT_CAP", NULL, retcode,
\t\t\t\t    r->capability, r->value,
\t\t\t\t    file_priv->atomic, file_priv->universal_planes);
\t\t\tTG_DISP_REF("DRM_CLIENT_STATE", NULL, retcode,
\t\t\t\t    file_priv->aspect_ratio_allowed,
\t\t\t\t    file_priv->writeback_connectors,
\t\t\t\t    file_priv->stereo_allowed, 0);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETRESOURCES): {
\t\t\tstruct drm_mode_card_res *r = (struct drm_mode_card_res *)kdata;
\t\t\tTG_DISP_REF("DRM_RES_COUNTS", NULL, retcode,
\t\t\t\t    r->count_fbs, r->count_crtcs,
\t\t\t\t    r->count_connectors, r->count_encoders);
\t\t\tTG_DISP_REF("DRM_RES_LIMITS", NULL, retcode,
\t\t\t\t    r->min_width, r->max_width,
\t\t\t\t    r->min_height, r->max_height);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETCONNECTOR): {
\t\t\tstruct drm_mode_get_connector *r = (struct drm_mode_get_connector *)kdata;
\t\t\tTG_DISP_REF("DRM_CONN_A", NULL, retcode,
\t\t\t\t    r->connector_id, r->connector_type,
\t\t\t\t    r->connector_type_id, r->connection);
\t\t\tTG_DISP_REF("DRM_CONN_B", NULL, retcode,
\t\t\t\t    r->count_modes, r->count_props,
\t\t\t\t    r->count_encoders, r->encoder_id);
\t\t\tTG_DISP_REF("DRM_CONN_SIZE", NULL, retcode,
\t\t\t\t    r->mm_width, r->mm_height, r->subpixel, 0);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETPLANERESOURCES): {
\t\t\tstruct drm_mode_get_plane_res *r = (struct drm_mode_get_plane_res *)kdata;
\t\t\tTG_DISP_REF("DRM_PLANE_RES", NULL, retcode,
\t\t\t\t    r->count_planes, 0, 0, 0);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETPLANE): {
\t\t\tstruct drm_mode_get_plane *r = (struct drm_mode_get_plane *)kdata;
\t\t\tTG_DISP_REF("DRM_PLANE_A", NULL, retcode,
\t\t\t\t    r->plane_id, r->crtc_id,
\t\t\t\t    r->fb_id, r->possible_crtcs);
\t\t\tTG_DISP_REF("DRM_PLANE_B", NULL, retcode,
\t\t\t\t    r->gamma_size, r->count_format_types, 0, 0);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETENCODER): {
\t\t\tstruct drm_mode_get_encoder *r = (struct drm_mode_get_encoder *)kdata;
\t\t\tTG_DISP_REF("DRM_ENCODER", NULL, retcode,
\t\t\t\t    r->encoder_id, r->encoder_type,
\t\t\t\t    r->crtc_id, r->possible_crtcs);
\t\t\tTG_DISP_REF("DRM_ENCODER_CLONE", NULL, retcode,
\t\t\t\t    r->encoder_id, r->possible_clones, 0, 0);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETCRTC): {
\t\t\tstruct drm_mode_crtc *r = (struct drm_mode_crtc *)kdata;
\t\t\tTG_DISP_REF("DRM_CRTC_A", NULL, retcode,
\t\t\t\t    r->crtc_id, r->fb_id, r->gamma_size, r->mode_valid);
\t\t\tTG_DISP_REF("DRM_CRTC_B", r->mode.name, retcode,
\t\t\t\t    r->x, r->y, r->mode.hdisplay, r->mode.vdisplay);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_GETPROPERTY): {
\t\t\tstruct drm_mode_get_property *r = (struct drm_mode_get_property *)kdata;
\t\t\tTG_DISP_REF("DRM_PROP_SUM", r->name, retcode,
\t\t\t\t    r->prop_id, r->flags,
\t\t\t\t    r->count_values, r->count_enum_blobs);
\t\t\tbreak;
\t\t}
\t\tcase DRM_IOCTL_NR(DRM_IOCTL_MODE_OBJ_GETPROPERTIES): {
\t\t\tstruct drm_mode_obj_get_properties *r =
\t\t\t\t(struct drm_mode_obj_get_properties *)kdata;
\t\t\tTG_DISP_REF("DRM_OBJ_SUM", NULL, retcode,
\t\t\t\t    r->obj_id, r->obj_type, r->count_props, 0);
\t\t\tbreak;
\t\t}
\t\tdefault:
\t\t\tbreak;
\t\t}
\t}

\tif (copy_to_user((void __user *)arg, kdata, out_size) != 0)'''

if s.count(anchor) != 1:
    raise SystemExit(f'DRM payload anchor mismatch: {s.count(anchor)}')
s = s.replace(anchor, block, 1)
p.write_text(s)
print('touchgrass_composer_drm_reference_v1: precise DRM UAPI payload capture applied')
