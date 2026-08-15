#!/usr/bin/env python3
"""Add exact DRM capability/topology ioctl payloads to the golden display recorder."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_composer_drm_reference_payload_fix.py <kernel-root>')

p = Path(sys.argv[1]) / 'drivers/gpu/drm/drm_ioctl.c'
s = p.read_text()

anchor = '''\tretcode = drm_ioctl_kernel(filp, func, kdata, ioctl->flags);\n\tif (copy_to_user((void __user *)arg, kdata, out_size) != 0)'''
block = r'''\tretcode = drm_ioctl_kernel(filp, func, kdata, ioctl->flags);

	/* Golden UAPI payload snapshot. Generic ioctl nr/rc alone is not enough:
	 * Composer can negotiate a subtly different capability or topology while
	 * every ioctl still returns 0. Record exactly what successful userspace sees. */
	if (kdata && tg_disp_ref_tracked()) {
		switch (nr) {
		case DRM_IOCTL_NR(DRM_IOCTL_GET_CAP): {
			struct drm_get_cap *r = (struct drm_get_cap *)kdata;
			TG_DISP_REF("DRM_CAP", NULL, retcode,
				    r->capability, r->value, 0, 0);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_SET_CLIENT_CAP): {
			struct drm_set_client_cap *r = (struct drm_set_client_cap *)kdata;
			TG_DISP_REF("DRM_CLIENT_CAP", NULL, retcode,
				    r->capability, r->value,
				    file_priv->atomic, file_priv->universal_planes);
			TG_DISP_REF("DRM_CLIENT_STATE", NULL, retcode,
				    file_priv->aspect_ratio_allowed,
				    file_priv->writeback_connectors,
				    file_priv->stereo_allowed, 0);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETRESOURCES): {
			struct drm_mode_card_res *r = (struct drm_mode_card_res *)kdata;
			TG_DISP_REF("DRM_RES_COUNTS", NULL, retcode,
				    r->count_fbs, r->count_crtcs,
				    r->count_connectors, r->count_encoders);
			TG_DISP_REF("DRM_RES_LIMITS", NULL, retcode,
				    r->min_width, r->max_width,
				    r->min_height, r->max_height);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETCONNECTOR): {
			struct drm_mode_get_connector *r = (struct drm_mode_get_connector *)kdata;
			TG_DISP_REF("DRM_CONN_A", NULL, retcode,
				    r->connector_id, r->connector_type,
				    r->connector_type_id, r->connection);
			TG_DISP_REF("DRM_CONN_B", NULL, retcode,
				    r->count_modes, r->count_props,
				    r->count_encoders, r->encoder_id);
			TG_DISP_REF("DRM_CONN_SIZE", NULL, retcode,
				    r->mm_width, r->mm_height, r->subpixel, 0);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETPLANERESOURCES): {
			struct drm_mode_get_plane_res *r = (struct drm_mode_get_plane_res *)kdata;
			TG_DISP_REF("DRM_PLANE_RES", NULL, retcode,
				    r->count_planes, 0, 0, 0);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETPLANE): {
			struct drm_mode_get_plane *r = (struct drm_mode_get_plane *)kdata;
			TG_DISP_REF("DRM_PLANE_A", NULL, retcode,
				    r->plane_id, r->crtc_id,
				    r->fb_id, r->possible_crtcs);
			TG_DISP_REF("DRM_PLANE_B", NULL, retcode,
				    r->gamma_size, r->count_format_types, 0, 0);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETENCODER): {
			struct drm_mode_get_encoder *r = (struct drm_mode_get_encoder *)kdata;
			TG_DISP_REF("DRM_ENCODER", NULL, retcode,
				    r->encoder_id, r->encoder_type,
				    r->crtc_id, r->possible_crtcs);
			TG_DISP_REF("DRM_ENCODER_CLONE", NULL, retcode,
				    r->encoder_id, r->possible_clones, 0, 0);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETCRTC): {
			struct drm_mode_crtc *r = (struct drm_mode_crtc *)kdata;
			TG_DISP_REF("DRM_CRTC_A", NULL, retcode,
				    r->crtc_id, r->fb_id, r->gamma_size, r->mode_valid);
			TG_DISP_REF("DRM_CRTC_B", r->mode.name, retcode,
				    r->x, r->y, r->mode.hdisplay, r->mode.vdisplay);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_GETPROPERTY): {
			struct drm_mode_get_property *r = (struct drm_mode_get_property *)kdata;
			TG_DISP_REF("DRM_PROP_SUM", r->name, retcode,
				    r->prop_id, r->flags,
				    r->count_values, r->count_enum_blobs);
			break;
		}
		case DRM_IOCTL_NR(DRM_IOCTL_MODE_OBJ_GETPROPERTIES): {
			struct drm_mode_obj_get_properties *r =
				(struct drm_mode_obj_get_properties *)kdata;
			TG_DISP_REF("DRM_OBJ_SUM", NULL, retcode,
				    r->obj_id, r->obj_type, r->count_props, 0);
			break;
		}
		default:
			break;
		}
	}

	if (copy_to_user((void __user *)arg, kdata, out_size) != 0)'''

if s.count(anchor) != 1:
    raise SystemExit(f'DRM payload anchor mismatch: {s.count(anchor)}')
s = s.replace(anchor, block, 1)
p.write_text(s)
print('touchgrass_composer_drm_reference_v1: precise DRM UAPI payload capture applied')
