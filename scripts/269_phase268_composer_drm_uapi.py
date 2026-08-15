#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(sys.argv[1])
MSM = ROOT / 'drivers/a52_display/msm/msm_drv.c'
REC = ROOT / 'drivers/a52_secure/a52_ack_secure_flight_recorder.c'
MARKER = 'A52_PHASE269_COMPOSER_DRM_UAPI_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


rec = REC.read_text(encoding='utf-8')
rec = one(
    rec,
    'return !strncmp(message, "P268 ", 5) ||',
    'return !strncmp(message, "P269 ", 5) ||\n       !strncmp(message, "P268 ", 5) ||',
    'critical P269 retention',
)
rec = one(
    rec,
    'if (strncmp(fmt, "P268", 4) &&',
    'if (strncmp(fmt, "P269", 4) &&\n    strncmp(fmt, "P268", 4) &&',
    'P269 admission',
)
REC.write_text(rec, encoding='utf-8')

msm = MSM.read_text(encoding='utf-8')
msm = one(
    msm,
    '#include <linux/sched.h>\n',
    '#include <linux/sched.h>\n#include <linux/uaccess.h>\n',
    'uaccess include',
)
msm = one(
    msm,
    'static atomic_t a52_r211_close_sequence = ATOMIC_INIT(0);\n',
    r'''static atomic_t a52_r211_close_sequence = ATOMIC_INIT(0);

/* A52_PHASE269_COMPOSER_DRM_UAPI_V1
 * Preserve Phase268 exactly, and add an independent Composer-only UAPI stream.
 * Data is sampled after drm_ioctl() returns, i.e. from the exact structures
 * userspace receives. No return value or DRM state is modified.
 */
#define A52_R269_IOCTL_LIMIT 1024U
#define A52_R269_EVENT_LIMIT 3072U
#define A52_R269_ARRAY_LIMIT 64U
#define A52_R269_BLOB_HASH_LIMIT 8192U
static atomic_t a52_r269_ioctl_sequence = ATOMIC_INIT(0);
static atomic_t a52_r269_event_sequence = ATOMIC_INIT(0);

#define A52_R269_REC(fmt, ...) do { \
	if ((unsigned int)atomic_inc_return(&a52_r269_event_sequence) <= A52_R269_EVENT_LIMIT) \
		a52_ackfr_record("P269 " fmt, ##__VA_ARGS__); \
} while (0)

static bool a52_r269_is_composer_task(void)
{
	return current->group_leader &&
		!strncmp(current->group_leader->comm, "composer", 8);
}

static void a52_r269_record_uapi(unsigned int n, unsigned int cmd,
				 unsigned long arg, long rc)
{
	unsigned int nr = _IOC_NR(cmd);
	void __user *up = (void __user *)arg;
	unsigned int i, count;

	A52_R269_REC("IO n=%u nr=0x%x rc=%ld", n, nr, rc);
	if (rc)
		return;

	switch (nr) {
	case 0x0d: {
		struct drm_set_client_cap v;
		if (!copy_from_user(&v, up, sizeof(v)))
			A52_R269_REC("CAP n=%u cap=%llu val=%llu", n,
				v.capability, v.value);
		break;
	}
	case 0xA0: {
		struct drm_mode_card_res v;
		if (!copy_from_user(&v, up, sizeof(v)))
			A52_R269_REC("RES n=%u c=%u k=%u e=%u f=%u", n,
				v.count_crtcs, v.count_connectors,
				v.count_encoders, v.count_fbs);
		break;
	}
	case 0xA1: {
		struct drm_mode_crtc v;
		if (!copy_from_user(&v, up, sizeof(v)))
			A52_R269_REC("CRTC n=%u id=%u fb=%u xy=%u,%u gam=%u mv=%u", n,
				v.crtc_id, v.fb_id, v.x, v.y,
				v.gamma_size, v.mode_valid);
		break;
	}
	case 0xA6: {
		struct drm_mode_get_encoder v;
		if (!copy_from_user(&v, up, sizeof(v)))
			A52_R269_REC("ENC n=%u id=%u crtc=%u pc=0x%x pcl=0x%x ty=%u", n,
				v.encoder_id, v.crtc_id, v.possible_crtcs,
				v.possible_clones, v.encoder_type);
		break;
	}
	case 0xA7: {
		struct drm_mode_get_connector v;
		u32 p[A52_R269_ARRAY_LIMIT];
		u64 val[A52_R269_ARRAY_LIMIT];

		if (copy_from_user(&v, up, sizeof(v)))
			break;
		A52_R269_REC("CONN n=%u id=%u enc=%u ne=%u nm=%u np=%u st=%u ty=%u", n,
			v.connector_id, v.encoder_id, v.count_encoders,
			v.count_modes, v.count_props, v.connection,
			v.connector_type);
		count = min_t(unsigned int, v.count_props, A52_R269_ARRAY_LIMIT);
		if (count && v.props_ptr && v.prop_values_ptr &&
		    !copy_from_user(p, (void __user *)(unsigned long)v.props_ptr,
			count * sizeof(*p)) &&
		    !copy_from_user(val,
			(void __user *)(unsigned long)v.prop_values_ptr,
			count * sizeof(*val)))
			for (i = 0; i < count; i++)
				A52_R269_REC("CVAL n=%u i=%u p=%u v=%llx",
					n, i, p[i], val[i]);
		break;
	}
	case 0xAA: {
		struct drm_mode_get_property v;
		u64 vals[16];
		struct drm_mode_property_enum enums[16];

		if (copy_from_user(&v, up, sizeof(v)))
			break;
		A52_R269_REC("PROP n=%u id=%u fl=0x%x nv=%u ne=%u name=%.31s", n,
			v.prop_id, v.flags, v.count_values,
			v.count_enum_blobs, v.name);
		count = min_t(unsigned int, v.count_values, ARRAY_SIZE(vals));
		if (count && v.values_ptr &&
		    !copy_from_user(vals, (void __user *)(unsigned long)v.values_ptr,
			count * sizeof(*vals)))
			for (i = 0; i < count; i++)
				A52_R269_REC("PVAL n=%u i=%u v=%llx", n, i, vals[i]);
		count = min_t(unsigned int, v.count_enum_blobs, ARRAY_SIZE(enums));
		if (count && v.enum_blob_ptr &&
		    !copy_from_user(enums,
			(void __user *)(unsigned long)v.enum_blob_ptr,
			count * sizeof(*enums)))
			for (i = 0; i < count; i++)
				A52_R269_REC("PENUM n=%u i=%u v=%llx name=%.31s", n, i,
					enums[i].value, enums[i].name);
		break;
	}
	case 0xAC: {
		struct drm_mode_get_blob v;
		u8 b[64];
		u64 h = 1469598103934665603ULL;
		u32 done = 0, take, limit;

		if (copy_from_user(&v, up, sizeof(v)))
			break;
		limit = min_t(u32, v.length, A52_R269_BLOB_HASH_LIMIT);
		while (done < limit) {
			take = min_t(u32, sizeof(b), limit - done);
			if (copy_from_user(b,
				(void __user *)(unsigned long)(v.data + done), take))
				break;
			for (i = 0; i < take; i++) {
				h ^= b[i];
				h *= 1099511628211ULL;
			}
			done += take;
		}
		A52_R269_REC("BLOB n=%u id=%u len=%u scan=%u h=%llx", n,
			v.blob_id, v.length, done, h);
		break;
	}
	case 0xB5: {
		struct drm_mode_get_plane_res v;
		if (!copy_from_user(&v, up, sizeof(v)))
			A52_R269_REC("PRES n=%u np=%u", n, v.count_planes);
		break;
	}
	case 0xB6: {
		struct drm_mode_get_plane v;
		if (!copy_from_user(&v, up, sizeof(v)))
			A52_R269_REC("PLANE n=%u id=%u crtc=%u fb=%u pc=0x%x nf=%u", n,
				v.plane_id, v.crtc_id, v.fb_id,
				v.possible_crtcs, v.count_format_types);
		break;
	}
	case 0xB9: {
		struct drm_mode_obj_get_properties v;
		u32 p[A52_R269_ARRAY_LIMIT];
		u64 val[A52_R269_ARRAY_LIMIT];

		if (copy_from_user(&v, up, sizeof(v)))
			break;
		A52_R269_REC("OBJ n=%u id=%u ty=0x%x np=%u", n,
			v.obj_id, v.obj_type, v.count_props);
		count = min_t(unsigned int, v.count_props, A52_R269_ARRAY_LIMIT);
		if (count && v.props_ptr && v.prop_values_ptr &&
		    !copy_from_user(p, (void __user *)(unsigned long)v.props_ptr,
			count * sizeof(*p)) &&
		    !copy_from_user(val,
			(void __user *)(unsigned long)v.prop_values_ptr,
			count * sizeof(*val)))
			for (i = 0; i < count; i++)
				A52_R269_REC("OVAL n=%u i=%u p=%u v=%llx",
					n, i, p[i], val[i]);
		break;
	}
	default:
		break;
	}
}
''',
    'Phase269 state and UAPI decoder',
)

old_ioctl = r'''static long a52_r211_drm_ioctl(struct file *filp, unsigned int cmd,
				unsigned long arg)
{
	unsigned int trace_id;
	bool trace;
	long rc;

	trace_id = atomic_inc_return(&a52_r211_ioctl_sequence);
	trace = trace_id <= A52_R211_IOCTL_LIMIT;
	if (trace)
		a52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x g=%d",
				  trace_id, current->pid, _IOC_NR(cmd), current->tgid);
	rc = drm_ioctl(filp, cmd, arg);
	if (trace)
		a52_ackfr_record("DRMPOST 211 ioctl-exit n=%u rc=%ld",
				  trace_id, rc);
	return rc;
}
'''
new_ioctl = r'''static long a52_r211_drm_ioctl(struct file *filp, unsigned int cmd,
				unsigned long arg)
{
	unsigned int trace_id, composer_id = 0;
	bool trace, composer;
	long rc;

	trace_id = atomic_inc_return(&a52_r211_ioctl_sequence);
	trace = trace_id <= A52_R211_IOCTL_LIMIT;
	composer = a52_r269_is_composer_task();
	if (composer)
		composer_id = atomic_inc_return(&a52_r269_ioctl_sequence);
	if (trace)
		a52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x g=%d",
				  trace_id, current->pid, _IOC_NR(cmd), current->tgid);
	rc = drm_ioctl(filp, cmd, arg);
	if (trace)
		a52_ackfr_record("DRMPOST 211 ioctl-exit n=%u rc=%ld", trace_id, rc);
	if (composer && composer_id <= A52_R269_IOCTL_LIMIT)
		a52_r269_record_uapi(composer_id, cmd, arg, rc);
	return rc;
}
'''
msm = one(msm, old_ioctl, new_ioctl, 'native DRM ioctl wrapper')

old_compat = r'''static long a52_r211_drm_compat_ioctl(struct file *filp, unsigned int cmd,
				       unsigned long arg)
{
	unsigned int trace_id;
	bool trace;
	long rc;

	trace_id = atomic_inc_return(&a52_r211_ioctl_sequence);
	trace = trace_id <= A52_R211_IOCTL_LIMIT;
	if (trace)
		a52_ackfr_record("DRMPOST 211 compat n=%u pid=%d nr=0x%x g=%d",
				  trace_id, current->pid, _IOC_NR(cmd), current->tgid);
	rc = drm_compat_ioctl(filp, cmd, arg);
	if (trace)
		a52_ackfr_record("DRMPOST 211 compat-exit n=%u rc=%ld",
				  trace_id, rc);
	return rc;
}
'''
new_compat = r'''static long a52_r211_drm_compat_ioctl(struct file *filp, unsigned int cmd,
				       unsigned long arg)
{
	unsigned int trace_id, composer_id = 0;
	bool trace, composer;
	long rc;

	trace_id = atomic_inc_return(&a52_r211_ioctl_sequence);
	trace = trace_id <= A52_R211_IOCTL_LIMIT;
	composer = a52_r269_is_composer_task();
	if (composer)
		composer_id = atomic_inc_return(&a52_r269_ioctl_sequence);
	if (trace)
		a52_ackfr_record("DRMPOST 211 compat n=%u pid=%d nr=0x%x g=%d",
				  trace_id, current->pid, _IOC_NR(cmd), current->tgid);
	rc = drm_compat_ioctl(filp, cmd, arg);
	if (trace)
		a52_ackfr_record("DRMPOST 211 compat-exit n=%u rc=%ld", trace_id, rc);
	if (composer && composer_id <= A52_R269_IOCTL_LIMIT)
		a52_r269_record_uapi(composer_id, cmd, arg, rc);
	return rc;
}
'''
msm = one(msm, old_compat, new_compat, 'compat DRM ioctl wrapper')
MSM.write_text(msm, encoding='utf-8')

print(MARKER)
print('Phase269 Composer-only post-ioctl UAPI observer applied')
