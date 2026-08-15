#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MSM_DRV = Path("drivers/a52_display/msm/msm_drv.c")
DRM_CRTC = Path("drivers/gpu/drm/drm_crtc.c")
DRM_CONNECTOR = Path("drivers/gpu/drm/drm_connector.c")
DRM_MODE_OBJECT = Path("drivers/gpu/drm/drm_mode_object.c")
DRM_PROPERTY = Path("drivers/gpu/drm/drm_property.c")
MARKER = "A52_PHASE269_COMPOSER_DRM_UAPI_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def patch_msm(text: str) -> str:
    text = one(text,
        "#define A52_R211_IOCTL_LIMIT 24\n",
        "#define A52_R211_IOCTL_LIMIT 4096\n#define A52_R269_IOCTL_LIMIT 4096\n",
        "ioctl limit")
    old = '''static long a52_r211_drm_ioctl(struct file *filp, unsigned int cmd,\n\t\t\t\tunsigned long arg)\n{\n\tunsigned int trace_id;\n\tbool trace;\n\tlong rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_ioctl_sequence);\n\ttrace = trace_id <= A52_R211_IOCTL_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd));\n\trc = drm_ioctl(filp, cmd, arg);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 ioctl-exit n=%u rc=%ld",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n'''
    new = '''static long a52_r211_drm_ioctl(struct file *filp, unsigned int cmd,\n\t\t\t\tunsigned long arg)\n{\n\tunsigned int trace_id;\n\tbool trace;\n\tlong rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_ioctl_sequence);\n\ttrace = trace_id <= A52_R211_IOCTL_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd));\n\trc = drm_ioctl(filp, cmd, arg);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 ioctl-exit n=%u rc=%ld",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n'''
    text = one(text, old, new, "ioctl wrapper invariant")
    return text


def inject_include(text: str) -> str:
    inc = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if inc in text:
        return text
    pos = text.find('\n#include ')
    if pos < 0:
        raise RuntimeError("include anchor missing")
    return text[:pos+1] + inc + text[pos+1:]


def patch_connector(text: str) -> str:
    text = inject_include(text)
    old = '''\tout_resp->connector_id = connector->base.id;\n\tout_resp->connector_type = connector->connector_type;\n\tout_resp->connector_type_id = connector->connector_type_id;\n'''
    new = '''\tout_resp->connector_id = connector->base.id;\n\tout_resp->connector_type = connector->connector_type;\n\tout_resp->connector_type_id = connector->connector_type_id;\n\ta52_ackfr_record("P269 GETCONN pid=%d id=%u enc=%u encs=%u modes=%u props=%u status=%u",\n\t\tcurrent->pid, out_resp->connector_id, out_resp->encoder_id,\n\t\tout_resp->count_encoders, out_resp->count_modes, out_resp->count_props,\n\t\tout_resp->connection);\n'''
    return one(text, old, new, "GETCONNECTOR payload")


def patch_crtc(text: str) -> str:
    text = inject_include(text)
    old = '''\tout_resp->count_fbs = fb_count;\n\tout_resp->count_crtcs = crtc_count;\n\tout_resp->count_connectors = connector_count;\n\tout_resp->count_encoders = encoder_count;\n'''
    new = '''\tout_resp->count_fbs = fb_count;\n\tout_resp->count_crtcs = crtc_count;\n\tout_resp->count_connectors = connector_count;\n\tout_resp->count_encoders = encoder_count;\n\ta52_ackfr_record("P269 GETRES pid=%d crtcs=%u conns=%u encs=%u fbs=%u",\n\t\tcurrent->pid, out_resp->count_crtcs, out_resp->count_connectors,\n\t\tout_resp->count_encoders, out_resp->count_fbs);\n'''
    text = one(text, old, new, "GETRESOURCES payload")

    old2 = '''\tout_resp->encoder_id = encoder->base.id;\n\tout_resp->encoder_type = encoder->encoder_type;\n\tout_resp->crtc_id = encoder->crtc ? encoder->crtc->base.id : 0;\n\tout_resp->possible_crtcs = encoder->possible_crtcs;\n\tout_resp->possible_clones = encoder->possible_clones;\n'''
    new2 = '''\tout_resp->encoder_id = encoder->base.id;\n\tout_resp->encoder_type = encoder->encoder_type;\n\tout_resp->crtc_id = encoder->crtc ? encoder->crtc->base.id : 0;\n\tout_resp->possible_crtcs = encoder->possible_crtcs;\n\tout_resp->possible_clones = encoder->possible_clones;\n\ta52_ackfr_record("P269 GETENC pid=%d id=%u crtc=%u pcrtc=0x%x pcln=0x%x type=%u",\n\t\tcurrent->pid, out_resp->encoder_id, out_resp->crtc_id,\n\t\tout_resp->possible_crtcs, out_resp->possible_clones, out_resp->encoder_type);\n'''
    return one(text, old2, new2, "GETENCODER payload")


def patch_mode_object(text: str) -> str:
    text = inject_include(text)
    old = '''\tret = drm_mode_object_get_properties(obj, file_priv->atomic,\n\t\t\t(uint32_t __user *)(unsigned long)(arg->props_ptr),\n\t\t\t(uint64_t __user *)(unsigned long)(arg->prop_values_ptr),\n\t\t\t&arg->count_props);\n'''
    new = '''\tret = drm_mode_object_get_properties(obj, file_priv->atomic,\n\t\t\t(uint32_t __user *)(unsigned long)(arg->props_ptr),\n\t\t\t(uint64_t __user *)(unsigned long)(arg->prop_values_ptr),\n\t\t\t&arg->count_props);\n\ta52_ackfr_record("P269 OBJPROPS pid=%d obj=%u type=0x%x count=%u rc=%d",\n\t\tcurrent->pid, arg->obj_id, arg->obj_type, arg->count_props, ret);\n'''
    return one(text, old, new, "OBJ_GETPROPERTIES payload")


def patch_property(text: str) -> str:
    text = inject_include(text)
    old = '''\tout_resp->flags = property->flags;\n\tmemcpy(out_resp->name, property->name, DRM_PROP_NAME_LEN);\n\tout_resp->count_values = value_count;\n\tout_resp->count_enum_blobs = enum_count;\n'''
    new = '''\tout_resp->flags = property->flags;\n\tmemcpy(out_resp->name, property->name, DRM_PROP_NAME_LEN);\n\tout_resp->count_values = value_count;\n\tout_resp->count_enum_blobs = enum_count;\n\ta52_ackfr_record("P269 GETPROP pid=%d id=%u fl=0x%x vals=%u enum=%u name=%.31s",\n\t\tcurrent->pid, out_resp->prop_id, out_resp->flags,\n\t\tout_resp->count_values, out_resp->count_enum_blobs, out_resp->name);\n'''
    text = one(text, old, new, "GETPROPERTY payload")

    old2 = '''\tout_resp->length = blob->length;\n'''
    new2 = '''\tout_resp->length = blob->length;\n\ta52_ackfr_record("P269 GETBLOB pid=%d id=%u len=%u",\n\t\tcurrent->pid, out_resp->blob_id, out_resp->length);\n'''
    return one(text, old2, new2, "GETPROPBLOB payload")


def apply(root: Path) -> None:
    files = {
        MSM_DRV: patch_msm,
        DRM_CRTC: patch_crtc,
        DRM_CONNECTOR: patch_connector,
        DRM_MODE_OBJECT: patch_mode_object,
        DRM_PROPERTY: patch_property,
    }
    for rel, fn in files.items():
        p = root / rel
        if not p.exists():
            raise RuntimeError(f"missing {rel}")
        src = p.read_text(encoding="utf-8")
        dst = fn(src)
        if dst == src:
            raise RuntimeError(f"no change for {rel}")
        p.write_text(dst, encoding="utf-8")
    print(MARKER)
    print("Phase269 Composer DRM UAPI observer applied")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    apply(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
