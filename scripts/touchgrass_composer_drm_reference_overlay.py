#!/usr/bin/env python3
"""Add an observation-only golden Composer/DRM/SDM boundary recorder to TouchGrass 4.19.200."""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "touchgrass_composer_drm_reference_v1"


def fail(msg: str) -> None:
    raise SystemExit(msg)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def add_include(text: str, inc: str, label: str) -> str:
    if inc in text:
        return text
    pos = text.find("#include ")
    if pos < 0:
        fail(f"{label}: no include anchor")
    return text[:pos] + inc + "\n" + text[pos:]


def patch_file(root: Path, rel: str, fn) -> None:
    p = root / rel
    if not p.is_file():
        fail(f"missing target: {rel}")
    old = p.read_text()
    new = fn(old)
    if new == old:
        fail(f"{rel}: patch produced no change")
    p.write_text(new)
    print(f"TG_DISPLAY_PATCHED {rel}")


HEADER = r'''#ifndef _LINUX_TG_DISPLAY_REFERENCE_H
#define _LINUX_TG_DISPLAY_REFERENCE_H

#include <linux/types.h>

void tg_disp_ref_record(const char *tag, const char *name, int rc,
                        u64 a, u64 b, u64 c, u64 d);
void tg_disp_ref_track_exec(const char *path);
bool tg_disp_ref_tracked(void);

#define TG_DISP_REF(tag, name, rc, a, b, c, d) do { \
    if (tg_disp_ref_tracked()) \
        tg_disp_ref_record((tag), (name), (rc), (u64)(a), (u64)(b), \
                           (u64)(c), (u64)(d)); \
} while (0)

#define TG_DISP_REF_GLOBAL(tag, name, rc, a, b, c, d) \
    tg_disp_ref_record((tag), (name), (rc), (u64)(a), (u64)(b), \
                       (u64)(c), (u64)(d))

#endif
'''

RECORDER = r'''// SPDX-License-Identifier: GPL-2.0
/* Observation-only golden display/composer recorder for A52 TouchGrass. */
#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/sched.h>
#include <linux/seq_file.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <linux/tg_display_reference.h>

#define TG_DISP_REF_MAX 32768U
#define TG_DISP_TAG_LEN 28U
#define TG_DISP_NAME_LEN 72U

struct tg_disp_ref_entry {
    u64 seq;
    u64 ns;
    u64 a;
    u64 b;
    u64 c;
    u64 d;
    s32 rc;
    s32 tgid;
    s32 tid;
    u16 cpu;
    char comm[TASK_COMM_LEN];
    char tag[TG_DISP_TAG_LEN];
    char name[TG_DISP_NAME_LEN];
};

static struct tg_disp_ref_entry tg_disp_entries[TG_DISP_REF_MAX];
static atomic64_t tg_disp_seq = ATOMIC64_INIT(0);
static atomic_t tg_disp_composer_tgid = ATOMIC_INIT(-1);

bool tg_disp_ref_tracked(void)
{
    int tracked = atomic_read(&tg_disp_composer_tgid);

    return tracked > 0 && task_tgid_nr(current) == tracked;
}
EXPORT_SYMBOL_GPL(tg_disp_ref_tracked);

void tg_disp_ref_record(const char *tag, const char *name, int rc,
                        u64 a, u64 b, u64 c, u64 d)
{
    struct tg_disp_ref_entry *e;
    u64 seq = atomic64_inc_return(&tg_disp_seq);
    u32 slot = (u32)((seq - 1) % TG_DISP_REF_MAX);

    e = &tg_disp_entries[slot];
    WRITE_ONCE(e->seq, 0);
    e->ns = ktime_get_ns();
    e->a = a;
    e->b = b;
    e->c = c;
    e->d = d;
    e->rc = rc;
    e->tgid = task_tgid_nr(current);
    e->tid = task_pid_nr(current);
    e->cpu = raw_smp_processor_id();
    strlcpy(e->comm, current->comm, sizeof(e->comm));
    strlcpy(e->tag, tag ? tag : "-", sizeof(e->tag));
    strlcpy(e->name, name ? name : "-", sizeof(e->name));
    smp_wmb();
    WRITE_ONCE(e->seq, seq);
}
EXPORT_SYMBOL_GPL(tg_disp_ref_record);

void tg_disp_ref_track_exec(const char *path)
{
    int tgid;

    if (!path)
        return;
    if (!strstr(path, "vendor.qti.hardware.display.composer-service") &&
        !strstr(path, "vendor.qti.hardware.display.composer"))
        return;

    tgid = task_tgid_nr(current);
    atomic_set(&tg_disp_composer_tgid, tgid);
    tg_disp_ref_record("COMPOSER_EXEC", path, 0, tgid, task_pid_nr(current), 0, 0);
}
EXPORT_SYMBOL_GPL(tg_disp_ref_track_exec);

static int tg_disp_ref_show(struct seq_file *m, void *v)
{
    u64 total = atomic64_read(&tg_disp_seq);
    u64 first = total > TG_DISP_REF_MAX ? total - TG_DISP_REF_MAX + 1 : 1;
    u64 seq;

    seq_puts(m, "# touchgrass_composer_drm_reference_v1\n");
    seq_printf(m, "# tracked_tgid=%d total=%llu retained=%llu\n",
               atomic_read(&tg_disp_composer_tgid),
               (unsigned long long)total,
               (unsigned long long)(total >= first ? total - first + 1 : 0));
    seq_puts(m, "# seq ns tgid tid cpu comm tag rc a b c d name\n");

    for (seq = first; seq <= total; seq++) {
        struct tg_disp_ref_entry *e = &tg_disp_entries[(seq - 1) % TG_DISP_REF_MAX];
        u64 seen = READ_ONCE(e->seq);

        smp_rmb();
        if (seen != seq)
            continue;
        seq_printf(m,
                   "%llu %llu %d %d %u %s %s %d 0x%llx 0x%llx 0x%llx 0x%llx %s\n",
                   (unsigned long long)e->seq,
                   (unsigned long long)e->ns,
                   e->tgid, e->tid, e->cpu, e->comm, e->tag, e->rc,
                   (unsigned long long)e->a, (unsigned long long)e->b,
                   (unsigned long long)e->c, (unsigned long long)e->d,
                   e->name);
    }
    return 0;
}

static int tg_disp_ref_open(struct inode *inode, struct file *file)
{
    return single_open(file, tg_disp_ref_show, NULL);
}

static const struct file_operations tg_disp_ref_fops = {
    .owner = THIS_MODULE,
    .open = tg_disp_ref_open,
    .read = seq_read,
    .llseek = seq_lseek,
    .release = single_release,
};

static int __init tg_disp_ref_init(void)
{
    if (!proc_create("tg_display_reference", 0444, NULL, &tg_disp_ref_fops))
        return -ENOMEM;
    pr_info("touchgrass_composer_drm_reference_v1 ready\n");
    return 0;
}
late_initcall(tg_disp_ref_init);
'''


def patch_exec(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "exec")
    old = '''\tbprm->interp = bprm->filename;\n\n\tretval = bprm_mm_init(bprm);'''
    new = '''\ttg_disp_ref_track_exec(bprm->filename);\n\tbprm->interp = bprm->filename;\n\n\tretval = bprm_mm_init(bprm);'''
    return replace_once(s, old, new, "exec track")


def patch_open(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "open")
    old = '''\ttmp = getname(filename);\n\tif (IS_ERR(tmp))\n\t\treturn PTR_ERR(tmp);\n\n\tfd = get_unused_fd_flags(flags);'''
    new = '''\ttmp = getname(filename);\n\tif (IS_ERR(tmp))\n\t\treturn PTR_ERR(tmp);\n\n\tTG_DISP_REF("SYS_OPEN_IN", tmp->name, 0, dfd, flags, mode, 0);\n\tfd = get_unused_fd_flags(flags);'''
    s = replace_once(s, old, new, "open entry")
    old = '''\tputname(tmp);\n\treturn fd;\n}\n\nSYSCALL_DEFINE3(open,'''
    new = '''\tTG_DISP_REF("SYS_OPEN_OUT", tmp->name, fd < 0 ? fd : 0, dfd, flags, mode, fd);\n\tputname(tmp);\n\treturn fd;\n}\n\nSYSCALL_DEFINE3(open,'''
    return replace_once(s, old, new, "open exit")


def patch_ioctl_sys(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "ioctl")
    old = '''int ksys_ioctl(unsigned int fd, unsigned int cmd, unsigned long arg)\n{\n\tint error;\n\tstruct fd f = fdget(fd);\n\n\tif (!f.file)\n\t\treturn -EBADF;'''
    new = '''int ksys_ioctl(unsigned int fd, unsigned int cmd, unsigned long arg)\n{\n\tint error;\n\tstruct fd f = fdget(fd);\n\n\tTG_DISP_REF("SYS_IOCTL_IN", NULL, 0, fd, cmd, _IOC_NR(cmd), _IOC_SIZE(cmd));\n\tif (!f.file) {\n\t\tTG_DISP_REF("SYS_IOCTL_OUT", NULL, -EBADF, fd, cmd, _IOC_NR(cmd), 0);\n\t\treturn -EBADF;\n\t}'''
    s = replace_once(s, old, new, "ioctl entry")
    old = '''\tfdput(f);\n\treturn error;\n}\n\nSYSCALL_DEFINE3(ioctl,'''
    new = '''\tfdput(f);\n\tTG_DISP_REF("SYS_IOCTL_OUT", NULL, error, fd, cmd, _IOC_NR(cmd), 0);\n\treturn error;\n}\n\nSYSCALL_DEFINE3(ioctl,'''
    return replace_once(s, old, new, "ioctl exit")


def patch_drm_file(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "drm_file")
    old = '''int drm_open(struct inode *inode, struct file *filp)\n{\n\tstruct drm_device *dev;\n\tstruct drm_minor *minor;\n\tint retcode;\n\tint need_setup = 0;\n\n\tminor = drm_minor_acquire(iminor(inode));'''
    new = '''int drm_open(struct inode *inode, struct file *filp)\n{\n\tstruct drm_device *dev;\n\tstruct drm_minor *minor;\n\tint retcode;\n\tint need_setup = 0;\n\n\tTG_DISP_REF("DRM_OPEN_IN", NULL, 0, iminor(inode), filp->f_flags, 0, 0);\n\tminor = drm_minor_acquire(iminor(inode));'''
    s = replace_once(s, old, new, "drm open entry")
    old = '''\tif (need_setup) {\n\t\tretcode = drm_setup(dev);\n\t\tif (retcode) {\n\t\t\tdrm_close_helper(filp);\n\t\t\tgoto err_undo;\n\t\t}\n\t}\n\treturn 0;\n\nerr_undo:'''
    new = '''\tif (need_setup) {\n\t\tretcode = drm_setup(dev);\n\t\tif (retcode) {\n\t\t\tdrm_close_helper(filp);\n\t\t\tgoto err_undo;\n\t\t}\n\t}\n\tTG_DISP_REF("DRM_OPEN_OUT", NULL, 0, minor->index, dev->open_count, need_setup, 0);\n\treturn 0;\n\nerr_undo:'''
    s = replace_once(s, old, new, "drm open success")
    old = '''\tdev->open_count--;\n\tdrm_minor_release(minor);\n\treturn retcode;\n}\nEXPORT_SYMBOL(drm_open);'''
    new = '''\tdev->open_count--;\n\tTG_DISP_REF("DRM_OPEN_OUT", NULL, retcode, minor->index, dev->open_count, need_setup, 0);\n\tdrm_minor_release(minor);\n\treturn retcode;\n}\nEXPORT_SYMBOL(drm_open);'''
    s = replace_once(s, old, new, "drm open failure")
    old = '''int drm_release(struct inode *inode, struct file *filp)\n{\n\tstruct drm_file *file_priv = filp->private_data;\n\tstruct drm_minor *minor = file_priv->minor;\n\tstruct drm_device *dev = minor->dev;\n\n\tmutex_lock(&drm_global_mutex);'''
    new = '''int drm_release(struct inode *inode, struct file *filp)\n{\n\tstruct drm_file *file_priv = filp->private_data;\n\tstruct drm_minor *minor = file_priv->minor;\n\tstruct drm_device *dev = minor->dev;\n\n\tTG_DISP_REF("DRM_RELEASE", NULL, 0, minor->index, dev->open_count, 0, 0);\n\tmutex_lock(&drm_global_mutex);'''
    return replace_once(s, old, new, "drm release")


def patch_drm_ioctl(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "drm_ioctl")
    old = '''\tdev = file_priv->minor->dev;\n\n\tif (drm_dev_is_unplugged(dev))'''
    new = '''\tdev = file_priv->minor->dev;\n\n\tTG_DISP_REF("DRM_IOCTL_IN", NULL, 0, cmd, DRM_IOCTL_NR(cmd), _IOC_SIZE(cmd), file_priv->minor->index);\n\tif (drm_dev_is_unplugged(dev))'''
    s = replace_once(s, old, new, "drm ioctl entry")
    old = '''\t/* Do not trust userspace, use our own definition */\n\tfunc = ioctl->func;'''
    new = '''\tTG_DISP_REF("DRM_IOCTL_DESC", ioctl->name, 0, cmd, nr, ioctl->flags, ksize);\n\n\t/* Do not trust userspace, use our own definition */\n\tfunc = ioctl->func;'''
    s = replace_once(s, old, new, "drm ioctl desc")
    old = '''\tif (retcode)\n\t\tDRM_DEBUG("pid=%d, ret = %d\\n", task_pid_nr(current), retcode);\n\treturn retcode;'''
    new = '''\tif (retcode)\n\t\tDRM_DEBUG("pid=%d, ret = %d\\n", task_pid_nr(current), retcode);\n\tTG_DISP_REF("DRM_IOCTL_OUT", ioctl ? ioctl->name : NULL, retcode, cmd, nr, in_size, out_size);\n\treturn retcode;'''
    return replace_once(s, old, new, "drm ioctl exit")


def patch_property(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "drm_property")
    old = '''\tstrncpy(property->name, name, DRM_PROP_NAME_LEN);\n\tproperty->name[DRM_PROP_NAME_LEN-1] = '\\0';\n\n\tlist_add_tail(&property->head, &dev->mode_config.property_list);'''
    new = '''\tstrncpy(property->name, name, DRM_PROP_NAME_LEN);\n\tproperty->name[DRM_PROP_NAME_LEN-1] = '\\0';\n\n\tTG_DISP_REF_GLOBAL("PROP_CREATE", property->name, 0, property->base.id, flags, num_values, 0);\n\tlist_add_tail(&property->head, &dev->mode_config.property_list);'''
    s = replace_once(s, old, new, "property create")
    old = '''\tproperty->values[index] = value;\n\tlist_add_tail(&prop_enum->head, &property->enum_list);\n\treturn 0;'''
    new = '''\tproperty->values[index] = value;\n\tlist_add_tail(&prop_enum->head, &property->enum_list);\n\tTG_DISP_REF_GLOBAL("PROP_ENUM_DEF", prop_enum->name, 0, property->base.id, value, index, property->flags);\n\treturn 0;'''
    s = replace_once(s, old, new, "property enum definition")
    old = '''\tproperty = drm_property_find(dev, file_priv, out_resp->prop_id);\n\tif (!property)\n\t\treturn -ENOENT;\n\n\tstrncpy(out_resp->name, property->name, DRM_PROP_NAME_LEN);'''
    new = '''\tproperty = drm_property_find(dev, file_priv, out_resp->prop_id);\n\tif (!property) {\n\t\tTG_DISP_REF("PROP_GET_MISS", NULL, -ENOENT, out_resp->prop_id, 0, 0, 0);\n\t\treturn -ENOENT;\n\t}\n\n\tTG_DISP_REF("PROP_GET", property->name, 0, property->base.id, property->flags, property->num_values, out_resp->count_values);\n\tstrncpy(out_resp->name, property->name, DRM_PROP_NAME_LEN);'''
    s = replace_once(s, old, new, "property get")
    old = '''\tfor (i = 0; i < value_count; i++) {\n\t\tif (i < out_resp->count_values &&\n\t\t    put_user(property->values[i], values_ptr + i)) {'''
    new = '''\tfor (i = 0; i < value_count; i++) {\n\t\tTG_DISP_REF("PROP_VALUE", property->name, 0, property->base.id, i, property->values[i], property->flags);\n\t\tif (i < out_resp->count_values &&\n\t\t    put_user(property->values[i], values_ptr + i)) {'''
    s = replace_once(s, old, new, "property values")
    old = '''\t\tlist_for_each_entry(prop_enum, &property->enum_list, head) {\n\t\t\tenum_count++;'''
    new = '''\t\tlist_for_each_entry(prop_enum, &property->enum_list, head) {\n\t\t\tenum_count++;\n\t\t\tTG_DISP_REF("PROP_ENUM", prop_enum->name, 0, property->base.id, prop_enum->value, enum_count - 1, property->flags);'''
    return replace_once(s, old, new, "property enums")


def patch_mode_object(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "mode_object")
    old = '''\t\tif (*arg_count_props > count) {\n\t\t\tret = __drm_object_property_get_value(obj, prop, &val);\n\t\t\tif (ret)\n\t\t\t\treturn ret;'''
    new = '''\t\tif (*arg_count_props > count) {\n\t\t\tret = __drm_object_property_get_value(obj, prop, &val);\n\t\t\tif (ret) {\n\t\t\t\tTG_DISP_REF("OBJ_PROP_ERR", prop->name, ret, obj->id, obj->type, prop->base.id, count);\n\t\t\t\treturn ret;\n\t\t\t}\n\t\t\tTG_DISP_REF("OBJ_PROP", prop->name, 0, obj->id, obj->type, prop->base.id, val);'''
    s = replace_once(s, old, new, "object property value")
    old = '''\tobj = drm_mode_object_find(dev, file_priv, arg->obj_id, arg->obj_type);\n\tif (!obj) {'''
    new = '''\tTG_DISP_REF("OBJ_GET_IN", NULL, 0, arg->obj_id, arg->obj_type, arg->count_props, file_priv->atomic);\n\tobj = drm_mode_object_find(dev, file_priv, arg->obj_id, arg->obj_type);\n\tif (!obj) {'''
    s = replace_once(s, old, new, "object get entry")
    old = '''out:\n\tdrm_modeset_unlock_all(dev);\n\treturn ret;\n}\n\nstruct drm_property *drm_mode_obj_find_prop_id'''
    new = '''out:\n\tdrm_modeset_unlock_all(dev);\n\tTG_DISP_REF("OBJ_GET_OUT", NULL, ret, arg->obj_id, arg->obj_type, arg->count_props, file_priv->atomic);\n\treturn ret;\n}\n\nstruct drm_property *drm_mode_obj_find_prop_id'''
    return replace_once(s, old, new, "object get exit")


def patch_atomic(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "drm_atomic")
    old = '''\tint ret = 0;\n\tunsigned int i, j, num_fences;\n\n\t/* disallow for drivers not supporting atomic: */'''
    new = '''\tint ret = 0;\n\tunsigned int i, j, num_fences;\n\n\tTG_DISP_REF("ATOMIC_IN", NULL, 0, arg->flags, arg->count_objs, arg->reserved, file_priv->atomic);\n\t/* disallow for drivers not supporting atomic: */'''
    s = replace_once(s, old, new, "atomic entry")
    old = '''\t\tobj = drm_mode_object_find(dev, file_priv, obj_id, DRM_MODE_OBJECT_ANY);\n\t\tif (!obj) {'''
    new = '''\t\tobj = drm_mode_object_find(dev, file_priv, obj_id, DRM_MODE_OBJECT_ANY);\n\t\tif (!obj) {\n\t\t\tTG_DISP_REF("ATOMIC_OBJ_MISS", NULL, -ENOENT, obj_id, i, copied_objs, copied_props);'''
    s = replace_once(s, old, new, "atomic object miss")
    old = '''\t\tif (get_user(count_props, count_props_ptr + copied_objs)) {\n\t\t\tdrm_mode_object_put(obj);\n\t\t\tret = -EFAULT;\n\t\t\tgoto out;\n\t\t}\n\n\t\tcopied_objs++;'''
    new = '''\t\tif (get_user(count_props, count_props_ptr + copied_objs)) {\n\t\t\tdrm_mode_object_put(obj);\n\t\t\tret = -EFAULT;\n\t\t\tgoto out;\n\t\t}\n\n\t\tTG_DISP_REF("ATOMIC_OBJ", NULL, 0, obj_id, obj->type, count_props, i);\n\t\tcopied_objs++;'''
    s = replace_once(s, old, new, "atomic object")
    old = '''\t\t\tret = drm_atomic_set_property(state, obj, prop,\n\t\t\t\t\t\t      prop_value);\n\t\t\tif (ret) {'''
    new = '''\t\t\tTG_DISP_REF("ATOMIC_PROP", prop->name, 0, obj_id, prop_id, prop_value, obj->type);\n\t\t\tret = drm_atomic_set_property(state, obj, prop,\n\t\t\t\t\t\t      prop_value);\n\t\t\tif (ret) {\n\t\t\t\tTG_DISP_REF("ATOMIC_PROP_ERR", prop->name, ret, obj_id, prop_id, prop_value, obj->type);'''
    s = replace_once(s, old, new, "atomic property")
    old = '''\tdrm_modeset_acquire_fini(&ctx);\n\n\treturn ret;\n}'''
    new = '''\tdrm_modeset_acquire_fini(&ctx);\n\n\tTG_DISP_REF("ATOMIC_OUT", NULL, ret, arg->flags, arg->count_objs, copied_objs, copied_props);\n\treturn ret;\n}'''
    return replace_once(s, old, new, "atomic exit")


def patch_msm(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "msm_drv")
    old = '''int msm_atomic_check(struct drm_device *dev,\n\t\t\t    struct drm_atomic_state *state)\n{\n\tstruct msm_drm_private *priv;\n\n\tpriv = dev->dev_private;\n\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->atomic_check)\n\t\treturn priv->kms->funcs->atomic_check(priv->kms, state);\n\n\treturn drm_atomic_helper_check(dev, state);\n}'''
    new = '''int msm_atomic_check(struct drm_device *dev,\n\t\t\t    struct drm_atomic_state *state)\n{\n\tstruct msm_drm_private *priv;\n\tint rc;\n\n\tTG_DISP_REF("MSM_ATOMIC_IN", NULL, 0, 0, 0, 0, 0);\n\tpriv = dev->dev_private;\n\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->atomic_check)\n\t\trc = priv->kms->funcs->atomic_check(priv->kms, state);\n\telse\n\t\trc = drm_atomic_helper_check(dev, state);\n\tTG_DISP_REF("MSM_ATOMIC_OUT", NULL, rc, 0, 0, 0, 0);\n\treturn rc;\n}'''
    s = replace_once(s, old, new, "msm atomic")
    old = '''static int msm_open(struct drm_device *dev, struct drm_file *file)\n{\n\treturn context_init(dev, file);\n}'''
    new = '''static int msm_open(struct drm_device *dev, struct drm_file *file)\n{\n\tint rc;\n\n\tTG_DISP_REF("MSM_OPEN_IN", NULL, 0, file->minor ? file->minor->index : ~0U, 0, 0, 0);\n\trc = context_init(dev, file);\n\tTG_DISP_REF("MSM_OPEN_OUT", NULL, rc, file->minor ? file->minor->index : ~0U, 0, 0, 0);\n\treturn rc;\n}'''
    return replace_once(s, old, new, "msm open")


def patch_mmap(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "mmap")
    old = '''unsigned long ksys_mmap_pgoff(unsigned long addr, unsigned long len,\n\t\t\t      unsigned long prot, unsigned long flags,\n\t\t\t      unsigned long fd, unsigned long pgoff)\n{\n\tstruct file *file = NULL;\n\tunsigned long retval;\n\n\tif (!(flags & MAP_ANONYMOUS)) {'''
    new = '''unsigned long ksys_mmap_pgoff(unsigned long addr, unsigned long len,\n\t\t\t      unsigned long prot, unsigned long flags,\n\t\t\t      unsigned long fd, unsigned long pgoff)\n{\n\tstruct file *file = NULL;\n\tunsigned long retval;\n\n\tTG_DISP_REF("SYS_MMAP_IN", NULL, 0, fd, len, prot, flags);\n\tif (!(flags & MAP_ANONYMOUS)) {'''
    s = replace_once(s, old, new, "mmap entry")
    old = '''out_fput:\n\tif (file)\n\t\tfput(file);\n\treturn retval;\n}'''
    new = '''out_fput:\n\tif (file)\n\t\tfput(file);\n\tTG_DISP_REF("SYS_MMAP_OUT", NULL, IS_ERR_VALUE(retval) ? (long)retval : 0, fd, len, pgoff, retval);\n\treturn retval;\n}'''
    return replace_once(s, old, new, "mmap exit")


def patch_binder(s: str) -> str:
    s = add_include(s, "#include <linux/tg_display_reference.h>", "binder")
    if 'static long binder_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)' not in s:
        fail("binder ioctl signature missing")
    old = '''\tvoid __user *ubuf = (void __user *)arg;\n\n\t/*pr_info("binder_ioctl: %d:%d %x %lx\\n",'''
    new = '''\tvoid __user *ubuf = (void __user *)arg;\n\n\tTG_DISP_REF("BINDER_IOCTL_IN", proc->context ? proc->context->name : NULL, 0, cmd, _IOC_NR(cmd), _IOC_SIZE(cmd), proc->pid);\n\t/*pr_info("binder_ioctl: %d:%d %x %lx\\n",'''
    s = replace_once(s, old, new, "binder ioctl entry")
    old = '''err_unlocked:\n\ttrace_binder_ioctl_done(ret);\n\treturn ret;\n}'''
    new = '''err_unlocked:\n\ttrace_binder_ioctl_done(ret);\n\tTG_DISP_REF("BINDER_IOCTL_OUT", proc->context ? proc->context->name : NULL, ret, cmd, _IOC_NR(cmd), _IOC_SIZE(cmd), proc->pid);\n\treturn ret;\n}'''
    s = replace_once(s, old, new, "binder ioctl exit")
    old = '''\te->context_name = proc->context->name;\n\n\tif (reply) {'''
    new = '''\te->context_name = proc->context->name;\n\n\tTG_DISP_REF("BINDER_TX_IN", proc->context ? proc->context->name : NULL, 0, tr->target.handle, tr->code, tr->flags, tr->data_size);\n\tif (reply) {'''
    s = replace_once(s, old, new, "binder transaction entry")
    old = '''\tif (target_thread)\n\t\te->to_thread = target_thread->pid;\n\te->to_proc = target_proc->pid;\n\n\t/* TODO: reuse incoming transaction for reply */'''
    new = '''\tif (target_thread)\n\t\te->to_thread = target_thread->pid;\n\te->to_proc = target_proc->pid;\n\tTG_DISP_REF("BINDER_TX_TARGET", proc->context ? proc->context->name : NULL, 0, target_proc->pid, target_thread ? target_thread->pid : 0, tr->code, tr->flags);\n\n\t/* TODO: reuse incoming transaction for reply */'''
    return replace_once(s, old, new, "binder transaction target")


def apply(root: Path) -> None:
    if not (root / "Makefile").is_file():
        fail(f"not a kernel tree: {root}")

    header = root / "include/linux/tg_display_reference.h"
    impl = root / "kernel/tg_display_reference.c"
    if header.exists() or impl.exists():
        fail("display recorder already present")
    header.write_text(HEADER)
    impl.write_text(RECORDER)

    km = root / "kernel/Makefile"
    kms = km.read_text()
    hook = "obj-y += tg_display_reference.o\n"
    if hook in kms:
        fail("display recorder Kbuild hook already present")
    kms += "\n# TouchGrass golden Composer/DRM reference recorder\n" + hook
    km.write_text(kms)

    patch_file(root, "fs/exec.c", patch_exec)
    patch_file(root, "fs/open.c", patch_open)
    patch_file(root, "fs/ioctl.c", patch_ioctl_sys)
    patch_file(root, "drivers/gpu/drm/drm_file.c", patch_drm_file)
    patch_file(root, "drivers/gpu/drm/drm_ioctl.c", patch_drm_ioctl)
    patch_file(root, "drivers/gpu/drm/drm_property.c", patch_property)
    patch_file(root, "drivers/gpu/drm/drm_mode_object.c", patch_mode_object)
    patch_file(root, "drivers/gpu/drm/drm_atomic.c", patch_atomic)
    patch_file(root, "techpack/display/msm/msm_drv.c", patch_msm)
    patch_file(root, "mm/mmap.c", patch_mmap)
    patch_file(root, "drivers/android/binder.c", patch_binder)

    print(f"{MARKER}: overlay applied")


def self_test() -> None:
    required = [
        "COMPOSER_EXEC", "SYS_OPEN_IN", "SYS_IOCTL_IN", "SYS_MMAP_IN", "DRM_OPEN_IN",
        "DRM_IOCTL_DESC", "PROP_CREATE", "PROP_GET", "OBJ_PROP",
        "ATOMIC_PROP", "MSM_ATOMIC_IN", "BINDER_TX_TARGET",
        "tg_display_reference",
    ]
    blob = HEADER + RECORDER + Path(__file__).read_text()
    missing = [x for x in required if x not in blob]
    if missing:
        fail(f"self-test missing markers: {missing}")
    print(f"{MARKER}: self-test PASS")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        self_test()
    elif len(sys.argv) == 2:
        apply(Path(sys.argv[1]).resolve())
    else:
        fail(f"usage: {Path(sys.argv[0]).name} --self-test | <kernel-root>")
