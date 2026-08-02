#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

OPEN_REL = Path('fs/open.c')
EXEC_REL = Path('fs/exec.c')
EXIT_REL = Path('kernel/exit.c')
DRM_DRV_REL = Path('drivers/gpu/drm/drm_drv.c')
DRM_FILE_REL = Path('drivers/gpu/drm/drm_file.c')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def patch_open(text: str) -> str:
    text = replace_once(text, '#include <linux/compat.h>\n',
        '#include <linux/compat.h>\n#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'open includes')
    anchor = '#include <trace/hooks/syscall_check.h>\n\n'
    helper = '''#include <trace/hooks/syscall_check.h>\n\n#define A52_R212_PATH_LIMIT 128\nstatic atomic_t a52_r212_path_sequence = ATOMIC_INIT(0);\n\nstatic bool a52_r212_interesting_path(const char *path)\n{\n\treturn path && (strstr(path, "/dev/dri/") ||\n\t\tstrstr(path, "/dev/graphics/") ||\n\t\tstrstr(path, "/dev/kgsl") ||\n\t\t!strcmp(path, "/dev/ion") ||\n\t\tstrstr(path, "/dev/dma_heap/") ||\n\t\tstrstr(path, "/sys/class/drm/"));\n}\n\n'''
    text = replace_once(text, anchor, helper, 'open helper')
    old = '''static long do_sys_openat2(int dfd, const char __user *filename,\n\t\t\t   struct open_how *how)\n{\n\tstruct open_flags op;\n\tint fd = build_open_flags(how, &op);\n\tstruct filename *tmp;\n\n\tif (fd)\n\t\treturn fd;\n\n\ttmp = getname(filename);\n\tif (IS_ERR(tmp))\n\t\treturn PTR_ERR(tmp);\n\n\tfd = get_unused_fd_flags(how->flags);\n\tif (fd >= 0) {\n\t\tstruct file *f = do_filp_open(dfd, tmp, &op);\n\t\tif (IS_ERR(f)) {\n\t\t\tput_unused_fd(fd);\n\t\t\tfd = PTR_ERR(f);\n\t\t} else {\n\t\t\tfsnotify_open(f);\n\t\t\tfd_install(fd, f);\n\t\t}\n\t}\n\tputname(tmp);\n\treturn fd;\n}\n'''
    new = '''static long do_sys_openat2(int dfd, const char __user *filename,\n\t\t\t   struct open_how *how)\n{\n\tstruct open_flags op;\n\tint fd = build_open_flags(how, &op);\n\tstruct filename *tmp;\n\tunsigned int trace_id = 0;\n\tbool trace = false;\n\n\tif (fd)\n\t\treturn fd;\n\n\ttmp = getname(filename);\n\tif (IS_ERR(tmp))\n\t\treturn PTR_ERR(tmp);\n\n\tif (a52_r212_interesting_path(tmp->name)) {\n\t\ttrace_id = atomic_inc_return(&a52_r212_path_sequence);\n\t\ttrace = trace_id <= A52_R212_PATH_LIMIT;\n\t\tif (trace)\n\t\t\ta52_ackfr_record("DRMPOST 212 path n=%u p=%d c=%.16s %.32s",\n\t\t\t\t\t  trace_id, current->pid, current->comm, tmp->name);\n\t}\n\n\tfd = get_unused_fd_flags(how->flags);\n\tif (fd >= 0) {\n\t\tstruct file *f = do_filp_open(dfd, tmp, &op);\n\t\tif (IS_ERR(f)) {\n\t\t\tput_unused_fd(fd);\n\t\t\tfd = PTR_ERR(f);\n\t\t} else {\n\t\t\tfsnotify_open(f);\n\t\t\tfd_install(fd, f);\n\t\t}\n\t}\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 path-ret n=%u fd=%d", trace_id, fd);\n\tputname(tmp);\n\treturn fd;\n}\n'''
    return replace_once(text, old, new, 'do_sys_openat2')


def patch_exec(text: str) -> str:
    text = replace_once(text, '#include <linux/io_uring.h>\n',
        '#include <linux/io_uring.h>\n#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'exec includes')
    anchor = '#include <trace/events/sched.h>\n\n'
    helper = '''#include <trace/events/sched.h>\n\n#define A52_R212_EXEC_LIMIT 96\nstatic atomic_t a52_r212_exec_sequence = ATOMIC_INIT(0);\n\nstatic bool a52_r212_graphics_exec(const char *path)\n{\n\treturn path && (strstr(path, "surfaceflinger") ||\n\t\tstrstr(path, "composer") || strstr(path, "display") ||\n\t\tstrstr(path, "gralloc") || strstr(path, "allocator") ||\n\t\tstrstr(path, "mapper"));\n}\n\n'''
    text = replace_once(text, anchor, helper, 'exec helper')
    old_start = '''static int do_execveat_common(int fd, struct filename *filename,\n\t\t\t      struct user_arg_ptr argv,\n\t\t\t      struct user_arg_ptr envp,\n\t\t\t      int flags)\n{\n\tstruct linux_binprm *bprm;\n\tint retval;\n\n\tif (IS_ERR(filename))\n\t\treturn PTR_ERR(filename);\n'''
    new_start = '''static int do_execveat_common(int fd, struct filename *filename,\n\t\t\t      struct user_arg_ptr argv,\n\t\t\t      struct user_arg_ptr envp,\n\t\t\t      int flags)\n{\n\tstruct linux_binprm *bprm;\n\tint retval;\n\tunsigned int trace_id = 0;\n\tbool trace = false;\n\n\tif (IS_ERR(filename))\n\t\treturn PTR_ERR(filename);\n\n\tif (a52_r212_graphics_exec(filename->name)) {\n\t\ttrace_id = atomic_inc_return(&a52_r212_exec_sequence);\n\t\ttrace = trace_id <= A52_R212_EXEC_LIMIT;\n\t\tif (trace)\n\t\t\ta52_ackfr_record("DRMPOST 212 exec n=%u p=%d %.40s",\n\t\t\t\t\t  trace_id, current->pid, filename->name);\n\t}\n'''
    text = replace_once(text, old_start, new_start, 'exec start')
    old_out = '''out_free:\n\tfree_bprm(bprm);\n\nout_ret:\n\tputname(filename);\n\treturn retval;\n}\n'''
    new_out = '''out_free:\n\tfree_bprm(bprm);\n\nout_ret:\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 exec-ret n=%u rc=%d c=%.16s",\n\t\t\t\t  trace_id, retval, current->comm);\n\tputname(filename);\n\treturn retval;\n}\n'''
    return replace_once(text, old_out, new_out, 'exec out')


def patch_exit(text: str) -> str:
    text = replace_once(text, '#include <linux/sysfs.h>\n',
        '#include <linux/sysfs.h>\n#include <linux/string.h>\n#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'exit includes')
    anchor = 'void __noreturn do_exit(long code)\n{\n\tstruct task_struct *tsk = current;\n\tint group_dead;\n'
    replacement = '''#define A52_R212_EXIT_LIMIT 96\nstatic atomic_t a52_r212_exit_sequence = ATOMIC_INIT(0);\n\nstatic bool a52_r212_graphics_comm(const char *comm)\n{\n\treturn comm && (strstr(comm, "surfaceflinger") ||\n\t\tstrstr(comm, "composer") || strstr(comm, "display") ||\n\t\tstrstr(comm, "gralloc") || strstr(comm, "allocator") ||\n\t\tstrstr(comm, "mapper") || strstr(comm, "android.hardware"));\n}\n\nvoid __noreturn do_exit(long code)\n{\n\tstruct task_struct *tsk = current;\n\tint group_dead;\n\n\tif (a52_r212_graphics_comm(current->comm)) {\n\t\tunsigned int trace_id = atomic_inc_return(&a52_r212_exit_sequence);\n\t\tif (trace_id <= A52_R212_EXIT_LIMIT)\n\t\t\ta52_ackfr_record("DRMPOST 212 exit n=%u p=%d c=%.16s code=%ld",\n\t\t\t\t\t  trace_id, current->pid, current->comm, code);\n\t}\n'''
    return replace_once(text, anchor, replacement, 'do_exit')


def patch_drm_drv(text: str) -> str:
    text = replace_once(text, '#include <linux/srcu.h>\n',
        '#include <linux/srcu.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'drm drv include')
    old_alloc_tail = '''\tminor->kdev = drm_sysfs_minor_alloc(minor);\n\tif (IS_ERR(minor->kdev))\n\t\treturn PTR_ERR(minor->kdev);\n\n\t*drm_minor_get_slot(dev, type) = minor;\n\treturn 0;\n}\n'''
    new_alloc_tail = '''\tminor->kdev = drm_sysfs_minor_alloc(minor);\n\tif (IS_ERR(minor->kdev)) {\n\t\tr = PTR_ERR(minor->kdev);\n\t\ta52_ackfr_record("DRMPOST 212 node type=%u idx=%d sysfs=%d",\n\t\t\t\t  type, minor->index, r);\n\t\treturn r;\n\t}\n\n\ta52_ackfr_record("DRMPOST 212 node type=%u idx=%d name=%.16s",\n\t\t\t  type, minor->index, dev_name(minor->kdev));\n\t*drm_minor_get_slot(dev, type) = minor;\n\treturn 0;\n}\n'''
    text = replace_once(text, old_alloc_tail, new_alloc_tail, 'minor alloc tail')
    old_device_add = '''\tret = device_add(minor->kdev);\n\tif (ret)\n\t\tgoto err_debugfs;\n'''
    new_device_add = '''\tret = device_add(minor->kdev);\n\ta52_ackfr_record("DRMPOST 212 node-add type=%u idx=%d rc=%d",\n\t\t\t  type, minor->index, ret);\n\tif (ret)\n\t\tgoto err_debugfs;\n'''
    return replace_once(text, old_device_add, new_device_add, 'minor device add')


def patch_drm_file(text: str) -> str:
    text = replace_once(text, '#include <linux/slab.h>\n',
        '#include <linux/slab.h>\n#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'drm file include')
    text = replace_once(text, 'DEFINE_MUTEX(drm_global_mutex);\n',
        'DEFINE_MUTEX(drm_global_mutex);\n\n#define A52_R212_DRM_OPEN_LIMIT 32\nstatic atomic_t a52_r212_drm_open_sequence = ATOMIC_INIT(0);\n',
        'drm open counter')
    old = '''int drm_open(struct inode *inode, struct file *filp)\n{\n\tstruct drm_device *dev;\n\tstruct drm_minor *minor;\n\tint retcode = 0;\n\tint need_setup = 0;\n\n\tminor = drm_minor_acquire(iminor(inode));\n\tif (IS_ERR(minor))\n\t\treturn PTR_ERR(minor);\n\n\tdev = minor->dev;\n\tif (drm_dev_needs_global_mutex(dev))\n\t\tmutex_lock(&drm_global_mutex);\n\n\tif (!atomic_fetch_inc(&dev->open_count))\n\t\tneed_setup = 1;\n\n\t/* share address_space across all char-devs of a single device */\n\tfilp->f_mapping = dev->anon_inode->i_mapping;\n\n\tretcode = drm_open_helper(filp, minor);\n\tif (retcode)\n\t\tgoto err_undo;\n\tif (need_setup) {\n\t\tretcode = drm_legacy_setup(dev);\n\t\tif (retcode) {\n\t\t\tdrm_close_helper(filp);\n\t\t\tgoto err_undo;\n\t\t}\n\t}\n\n\tif (drm_dev_needs_global_mutex(dev))\n\t\tmutex_unlock(&drm_global_mutex);\n\n\treturn 0;\n\nerr_undo:\n\tatomic_dec(&dev->open_count);\n\tif (drm_dev_needs_global_mutex(dev))\n\t\tmutex_unlock(&drm_global_mutex);\n\tdrm_minor_release(minor);\n\treturn retcode;\n}\n'''
    new = '''int drm_open(struct inode *inode, struct file *filp)\n{\n\tstruct drm_device *dev;\n\tstruct drm_minor *minor;\n\tint retcode = 0;\n\tint need_setup = 0;\n\tunsigned int trace_id;\n\tbool trace;\n\n\ttrace_id = atomic_inc_return(&a52_r212_drm_open_sequence);\n\ttrace = trace_id <= A52_R212_DRM_OPEN_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 drm-open n=%u id=%u p=%d",\n\t\t\t\t  trace_id, iminor(inode), current->pid);\n\n\tminor = drm_minor_acquire(iminor(inode));\n\tif (IS_ERR(minor)) {\n\t\tretcode = PTR_ERR(minor);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("DRMPOST 212 drm-acquire n=%u rc=%d",\n\t\t\t\t\t  trace_id, retcode);\n\t\treturn retcode;\n\t}\n\n\tdev = minor->dev;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 drm-minor n=%u type=%u idx=%d power=%d",\n\t\t\t\t  trace_id, minor->type, minor->index,\n\t\t\t\t  dev->switch_power_state);\n\tif (drm_dev_needs_global_mutex(dev))\n\t\tmutex_lock(&drm_global_mutex);\n\n\tif (!atomic_fetch_inc(&dev->open_count))\n\t\tneed_setup = 1;\n\n\t/* share address_space across all char-devs of a single device */\n\tfilp->f_mapping = dev->anon_inode->i_mapping;\n\n\tretcode = drm_open_helper(filp, minor);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 drm-helper n=%u rc=%d",\n\t\t\t\t  trace_id, retcode);\n\tif (retcode)\n\t\tgoto err_undo;\n\tif (need_setup) {\n\t\tretcode = drm_legacy_setup(dev);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("DRMPOST 212 drm-setup n=%u rc=%d",\n\t\t\t\t\t  trace_id, retcode);\n\t\tif (retcode) {\n\t\t\tdrm_close_helper(filp);\n\t\t\tgoto err_undo;\n\t\t}\n\t}\n\n\tif (drm_dev_needs_global_mutex(dev))\n\t\tmutex_unlock(&drm_global_mutex);\n\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 drm-open-ret n=%u rc=0", trace_id);\n\treturn 0;\n\nerr_undo:\n\tatomic_dec(&dev->open_count);\n\tif (drm_dev_needs_global_mutex(dev))\n\t\tmutex_unlock(&drm_global_mutex);\n\tdrm_minor_release(minor);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 drm-open-ret n=%u rc=%d",\n\t\t\t\t  trace_id, retcode);\n\treturn retcode;\n}\n'''
    return replace_once(text, old, new, 'drm_open')


def patch_all(root: Path) -> None:
    patches = [
        (OPEN_REL, patch_open), (EXEC_REL, patch_exec), (EXIT_REL, patch_exit),
        (DRM_DRV_REL, patch_drm_drv), (DRM_FILE_REL, patch_drm_file),
    ]
    for rel, func in patches:
        path = root / rel
        original = path.read_text(encoding='utf-8')
        path.write_text(func(original), encoding='utf-8')
    print('Phase212 graphics startup trace applied')


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_root = Path(tmp)
        for rel in [OPEN_REL, EXEC_REL, EXIT_REL, DRM_DRV_REL, DRM_FILE_REL]:
            dst = test_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text((root / rel).read_text(encoding='utf-8'), encoding='utf-8')
        patch_all(test_root)
        checks = {
            OPEN_REL: ['DRMPOST 212 path n=%u', 'DRMPOST 212 path-ret n=%u'],
            EXEC_REL: ['DRMPOST 212 exec n=%u', 'DRMPOST 212 exec-ret n=%u'],
            EXIT_REL: ['DRMPOST 212 exit n=%u'],
            DRM_DRV_REL: ['DRMPOST 212 node type=%u', 'DRMPOST 212 node-add type=%u'],
            DRM_FILE_REL: ['DRMPOST 212 drm-open n=%u', 'DRMPOST 212 drm-helper n=%u'],
        }
        for rel, markers in checks.items():
            text = (test_root / rel).read_text(encoding='utf-8')
            for marker in markers:
                if marker not in text:
                    raise RuntimeError(f'missing marker {marker} in {rel}')
        try:
            patch_all(test_root)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('patcher accepted already-patched source')
    print('phase212 graphics startup patcher self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        patch_all(args.root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
