#!/usr/bin/env python3
"""Add precise DRM property-blob fingerprints to the TouchGrass golden recorder."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_composer_drm_reference_blob_fix.py <kernel-root>')

p = Path(sys.argv[1]) / 'drivers/gpu/drm/drm_property.c'
s = p.read_text()

old = '''\tstruct drm_mode_get_blob *out_resp = data;\n\tstruct drm_property_blob *blob;\n\tint ret = 0;\n\n\tif (!drm_core_check_feature(dev, DRIVER_MODESET))'''
new = '''\tstruct drm_mode_get_blob *out_resp = data;\n\tstruct drm_property_blob *blob;\n\tint ret = 0;\n\tu64 tg_hash = 1469598103934665603ULL;\n\tu32 tg_i;\n\n\tif (!drm_core_check_feature(dev, DRIVER_MODESET))'''
if s.count(old) != 1:
    raise SystemExit(f'GETPROPBLOB declaration anchor mismatch: {s.count(old)}')
s = s.replace(old, new, 1)

old = '''\tblob = drm_property_lookup_blob(dev, out_resp->blob_id);\n\tif (!blob)\n\t\treturn -ENOENT;\n\n\tif (out_resp->length == blob->length) {'''
new = '''\tblob = drm_property_lookup_blob(dev, out_resp->blob_id);\n\tif (!blob) {\n\t\tTG_DISP_REF("DRM_BLOB_MISS", NULL, -ENOENT,\n\t\t\t    out_resp->blob_id, out_resp->length, 0, 0);\n\t\treturn -ENOENT;\n\t}\n\n\tif (tg_disp_ref_tracked()) {\n\t\tconst u8 *tg_bytes = blob->data;\n\n\t\tfor (tg_i = 0; tg_i < blob->length; tg_i++) {\n\t\t\ttg_hash ^= tg_bytes[tg_i];\n\t\t\ttg_hash *= 1099511628211ULL;\n\t\t}\n\t\tTG_DISP_REF("DRM_BLOB", NULL, 0, out_resp->blob_id,\n\t\t\t    blob->length, tg_hash, out_resp->length);\n\t}\n\n\tif (out_resp->length == blob->length) {'''
if s.count(old) != 1:
    raise SystemExit(f'GETPROPBLOB lookup anchor mismatch: {s.count(old)}')
s = s.replace(old, new, 1)

p.write_text(s)
print('touchgrass_composer_drm_reference_v1: DRM property-blob fingerprint capture applied')
