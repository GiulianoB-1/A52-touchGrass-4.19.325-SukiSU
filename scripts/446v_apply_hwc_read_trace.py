#!/usr/bin/env python3
"""446V Part A: passive DRM read-side userspace ABI dump.

All GET* captures are from the actual DRM object/UMODE property paths. The
kernel's return values and object values are not changed. Object 32/107,
their encoders, and associated caps/info blobs can be diffed boot vs AOD.
"""
import argparse
from pathlib import Path
MODE=Path("drivers/gpu/drm/drm_mode_object.c")
PROP=Path("drivers/gpu/drm/drm_property.c")
CONN=Path("drivers/gpu/drm/drm_connector.c")
CRTC=Path("drivers/gpu/drm/drm_crtc.c")
ENC=Path("drivers/gpu/drm/drm_encoder.c")
TAG="A52_PHASE446V_HWC_READ_SIDE_V1"
def one(s,old,new,context):
    n=s.count(old)
    if n!=1:raise RuntimeError("446V "+context+": expected 1 anchor found "+str(n))
    return s.replace(old,new,1)
INCLUDES='#include <linux/ktime.h>\n#include <linux/sched.h>\n'

def patch_mode(s):
    if TAG in s:return s
    s=INCLUDES+s
    needle='''			ret = __drm_object_property_get_value(obj, prop, &val);
			if (ret)
				return ret;'''
    inject=r'''			ret = __drm_object_property_get_value(obj, prop, &val);
			if (obj->id == 32 || obj->id == 107)
				pr_info("P446V READ_PROP ts=%llu pid=%d comm=%s obj=%u type=%#x property=%s propid=%u rc=%d value=%llu hex=%#llx\n",
					(unsigned long long)ktime_get_boottime_ns(),
					task_pid_nr(current),current->comm,obj->id,obj->type,
					prop->name,prop->base.id,ret,
					(unsigned long long)val,(unsigned long long)val);
			if (ret)
				return ret;'''
    s=one(s,needle,inject,"GETPROPERTIES each actual property value")
    needle='''	ret = drm_mode_object_get_properties(obj, file_priv->atomic,'''
    s=one(s,needle,r'''	if (obj->id == 32 || obj->id == 107)
		pr_info("P446V READ_OBJ ts=%llu pid=%d comm=%s id=%u type=%#x capacity=%u\n",
			(unsigned long long)ktime_get_boottime_ns(),
			task_pid_nr(current),current->comm,obj->id,obj->type,
			arg->count_props);
'''+needle,"GETPROPERTIES ioctl request")
    needle='''	if (drm_drv_uses_atomic_modeset(property->dev))
		ret = set_property_atomic(arg_obj, file_priv, property, arg->value);'''
    s=one(s,needle,r'''	pr_info("P446V SETPROPERTY ts=%llu pid=%d comm=%s obj=%u type=%#x property=%s value=%llu hex=%#llx\n",
		(unsigned long long)ktime_get_boottime_ns(),
		task_pid_nr(current),current->comm,arg_obj->id,arg_obj->type,
		property->name,(unsigned long long)arg->value,
		(unsigned long long)arg->value);
'''+needle,"legacy SETPROPERTY")
    # Note static reference used by source manifest, no behavior change.
    s=s.replace("int drm_mode_obj_get_properties_ioctl(", "/* "+TAG+" */\nint drm_mode_obj_get_properties_ioctl(",1)
    return s

def patch_prop(s):
    if TAG in s:return s
    s=INCLUDES+s
    needle='''	strncpy(out_resp->name, property->name, DRM_PROP_NAME_LEN);'''
    s=one(s,needle,r'''	pr_info("P446V GETPROPERTY ts=%llu pid=%d comm=%s prop=%u name=%s flags=%#x count_values=%u\n",
		(unsigned long long)ktime_get_boottime_ns(),task_pid_nr(current),
		current->comm,property->base.id,property->name,property->flags,
		property->num_values);
'''+needle,"GETPROPERTY metadata")
    needle='''		if (i < out_resp->count_values &&
		    put_user(property->values[i], values_ptr + i)) {'''
    s=one(s,needle,r'''		pr_info("P446V GETPROPERTY_VALUE ts=%llu pid=%d prop=%u index=%d value=%llu\n",
			(unsigned long long)ktime_get_boottime_ns(),
			task_pid_nr(current),property->base.id,i,
			(unsigned long long)property->values[i]);
'''+needle,"GETPROPERTY enumerated values")
    needle='''	blob = drm_property_lookup_blob(dev, out_resp->blob_id);
	if (!blob)
		return -ENOENT;'''
    s=one(s,needle,r'''	blob = drm_property_lookup_blob(dev, out_resp->blob_id);
	if (!blob)
		return -ENOENT;
	/* Full capability/info payload up to 32KiB, then explicit truncate. */
	pr_info("P446V GETBLOB ts=%llu pid=%d comm=%s id=%u length=%zu capacity=%u\n",
		(unsigned long long)ktime_get_boottime_ns(),task_pid_nr(current),
		current->comm,blob->base.id,blob->length,out_resp->length);
	{
		size_t pos, nbytes= min_t(size_t,blob->length,32768);
		for (pos=0;pos<nbytes;pos+=32) {
			size_t count=min_t(size_t,32,nbytes-pos);
			pr_info("P446V GETBLOB_DATA ts=%llu pid=%d id=%u pos=%zu data=%*phN\n",
				(unsigned long long)ktime_get_boottime_ns(),
				task_pid_nr(current),blob->base.id,pos,
				(int)count,(const u8 *)blob->data+pos);
		}
		if (nbytes<blob->length)
			pr_info("P446V GETBLOB_TRUNCATED id=%u captured=%zu total=%zu\n",
				blob->base.id,nbytes,blob->length);
	}
''',"GETBLOB content")
    s=s.replace("int drm_mode_getproperty_ioctl(", "/* "+TAG+" */\nint drm_mode_getproperty_ioctl(",1)
    return s

def patch_conn(s):
    if TAG in s:return s
    s=INCLUDES+s
    needle='''	encoders_count = hweight32(connector->possible_encoders);'''
    s=one(s,needle,r'''	if (connector->base.id == 32)
		pr_info("P446V GETCONNECTOR ts=%llu pid=%d comm=%s conn=%u status=%d encoder_mask=%#x modes_cap=%u props_cap=%u\n",
			(unsigned long long)ktime_get_boottime_ns(),
			task_pid_nr(current),current->comm,connector->base.id,
			connector->status,connector->possible_encoders,
			out_resp->count_modes,out_resp->count_props);
'''+needle,"connector ioctl start")
    needle='''		WARN_ON(mode->expose_to_userspace);'''
    s=one(s,needle,r'''		if (connector->base.id == 32)
			pr_info("P446V CONNECTOR_MODE ts=%llu pid=%d name=%s clock=%d h=%d/%d/%d/%d v=%d/%d/%d/%d flags=%#x type=%#x\n",
				(unsigned long long)ktime_get_boottime_ns(),
				task_pid_nr(current),mode->name,mode->clock,
				mode->hdisplay,mode->hsync_start,mode->hsync_end,mode->htotal,
				mode->vdisplay,mode->vsync_start,mode->vsync_end,mode->vtotal,
				mode->flags,mode->type);
'''+needle,"connector complete mode list")
    needle='''	drm_connector_put(connector);

	return ret;'''
    s=one(s,needle,r'''	if (out_resp->connector_id == 32)
		pr_info("P446V GETCONNECTOR_DONE ts=%llu pid=%d encoder=%u modes=%u props=%u status=%d rc=%d\n",
			(unsigned long long)ktime_get_boottime_ns(),
			task_pid_nr(current),out_resp->encoder_id,out_resp->count_modes,
			out_resp->count_props,out_resp->connection,ret);
'''+needle,"connector reply")
    return s.replace("int drm_mode_getconnector(", "/* "+TAG+" */\nint drm_mode_getconnector(",1)

def patch_crtc(s):
    if TAG in s:return s
    s=INCLUDES+s
    needle='''	drm_modeset_unlock(&crtc->mutex);

	return 0;'''
    s=one(s,needle,r'''	drm_modeset_unlock(&crtc->mutex);
	if (crtc_resp->crtc_id == 107)
		pr_info("P446V GETCRTC ts=%llu pid=%d comm=%s crtc=%u fb=%u xy=%d,%d valid=%u mode=%s h=%u v=%u clock=%u\n",
			(unsigned long long)ktime_get_boottime_ns(),
			task_pid_nr(current),current->comm,crtc_resp->crtc_id,
			crtc_resp->fb_id,crtc_resp->x,crtc_resp->y,
			crtc_resp->mode_valid,crtc_resp->mode.name,
			crtc_resp->mode.hdisplay,crtc_resp->mode.vdisplay,
			crtc_resp->mode.clock);

	return 0;''',"CRTC ioctl reply")
    return s.replace("int drm_mode_getcrtc(", "/* "+TAG+" */\nint drm_mode_getcrtc(",1)

def patch_encoder(s):
    if TAG in s:return s
    s=INCLUDES+s
    needle='''	enc_resp->possible_clones = encoder->possible_clones;

	return 0;'''
    s=one(s,needle,r'''	enc_resp->possible_clones = encoder->possible_clones;
	pr_info("P446V GETENCODER ts=%llu pid=%d comm=%s encoder=%u type=%u crtc=%u possible_crtcs=%#x clones=%#x\n",
		(unsigned long long)ktime_get_boottime_ns(),
		task_pid_nr(current),current->comm,enc_resp->encoder_id,
		enc_resp->encoder_type,enc_resp->crtc_id,
		enc_resp->possible_crtcs,enc_resp->possible_clones);

	return 0;''',"encoder ioctl reply")
    return s.replace("int drm_mode_getencoder(", "/* "+TAG+" */\nint drm_mode_getencoder(",1)

def check(root):
    checks={MODE:(TAG,"P446V READ_PROP","P446V SETPROPERTY","P446V READ_OBJ"),
            PROP:(TAG,"P446V GETPROPERTY","P446V GETBLOB_DATA"),
            CONN:(TAG,"P446V CONNECTOR_MODE","P446V GETCONNECTOR_DONE"),
            CRTC:(TAG,"P446V GETCRTC"),
            ENC:(TAG,"P446V GETENCODER")}
    for f,terms in checks.items():
        s=(root/f).read_text()
        for t in terms:
            if t not in s:raise RuntimeError("446V missing "+t+" in "+str(f))
    print("P446V HWC READ ABI PASS: GETPROPERTIES/GETPROPERTY/GETBLOB/GETCONNECTOR/GETCRTC/GETENCODER and legacy SETPROPERTY")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--check-only",action="store_true");x=ap.parse_args()
    if not x.check_only:
        for p,func in [(MODE,patch_mode),(PROP,patch_prop),(CONN,patch_conn),
                        (CRTC,patch_crtc),(ENC,patch_encoder)]:
            file=x.root/p;file.write_text(func(file.read_text()))
    check(x.root)
if __name__=="__main__":main()
