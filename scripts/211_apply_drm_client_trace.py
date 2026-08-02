#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

MSM_DRV_REL = Path('drivers/a52_display/msm/msm_drv.c')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def patch_msm_drv(text: str) -> str:
    text = replace_once(
        text,
        '#include <linux/a52_ack_secure_flight_recorder.h>\n',
        '#include <linux/a52_ack_secure_flight_recorder.h>\n'
        '#include <linux/atomic.h>\n'
        '#include <linux/sched.h>\n',
        'trace includes',
    )

    text = replace_once(
        text,
        '#define MSM_VERSION_PATCHLEVEL\t0\n\nstatic DEFINE_MUTEX(msm_release_lock);\n',
        '''#define MSM_VERSION_PATCHLEVEL\t0\n\n#define A52_R211_OPEN_LIMIT 8\n#define A52_R211_IOCTL_LIMIT 24\n#define A52_R211_CHECK_LIMIT 8\n#define A52_R211_CLOSE_LIMIT 8\n\nstatic atomic_t a52_r211_open_sequence = ATOMIC_INIT(0);\nstatic atomic_t a52_r211_ioctl_sequence = ATOMIC_INIT(0);\nstatic atomic_t a52_r211_check_sequence = ATOMIC_INIT(0);\nstatic atomic_t a52_r211_close_sequence = ATOMIC_INIT(0);\n\nstatic DEFINE_MUTEX(msm_release_lock);\n''',
        'trace counters',
    )

    old_check = '''int msm_atomic_check(struct drm_device *dev,\n\t\t\t    struct drm_atomic_state *state)\n{\n\tstruct msm_drm_private *priv;\n\n\tpriv = dev->dev_private;\n\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->atomic_check)\n\t\treturn priv->kms->funcs->atomic_check(priv->kms, state);\n\n\treturn drm_atomic_helper_check(dev, state);\n}\n'''
    new_check = '''int msm_atomic_check(struct drm_device *dev,\n\t\t\t    struct drm_atomic_state *state)\n{\n\tstruct msm_drm_private *priv;\n\tunsigned int trace_id;\n\tbool trace;\n\tint rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_check_sequence);\n\ttrace = trace_id <= A52_R211_CHECK_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 check n=%u pid=%d comm=%.16s",\n\t\t\t\t  trace_id, current->pid, current->comm);\n\n\tpriv = dev->dev_private;\n\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->atomic_check)\n\t\trc = priv->kms->funcs->atomic_check(priv->kms, state);\n\telse\n\t\trc = drm_atomic_helper_check(dev, state);\n\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 check-exit n=%u rc=%d",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n'''
    text = replace_once(text, old_check, new_check, 'atomic check')

    old_open = '''static int msm_open(struct drm_device *dev, struct drm_file *file)\n{\n\treturn context_init(dev, file);\n}\n'''
    new_open = '''static int msm_open(struct drm_device *dev, struct drm_file *file)\n{\n\tunsigned int trace_id;\n\tbool trace;\n\tint rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_open_sequence);\n\ttrace = trace_id <= A52_R211_OPEN_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 open n=%u pid=%d comm=%.16s",\n\t\t\t\t  trace_id, current->pid, current->comm);\n\trc = context_init(dev, file);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 open-exit n=%u rc=%d",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n'''
    text = replace_once(text, old_open, new_open, 'msm open')

    old_postclose = '''static void msm_postclose(struct drm_device *dev, struct drm_file *file)\n{\n\tstruct msm_drm_private *priv = dev->dev_private;\n\tstruct msm_file_private *ctx = file->driver_priv;\n\tstruct msm_kms *kms = priv->kms;\n\n\tif (kms && kms->funcs && kms->funcs->postclose)\n\t\tkms->funcs->postclose(kms, file);\n\n\tmutex_lock(&dev->struct_mutex);\n\tif (ctx == priv->lastctx)\n\t\tpriv->lastctx = NULL;\n\tmutex_unlock(&dev->struct_mutex);\n\n\tmutex_lock(&ctx->power_lock);\n\tif (ctx->enable_refcnt) {\n\t\tSDE_EVT32(ctx->enable_refcnt);\n\t\tpm_runtime_put_sync(dev->dev);\n\t}\n\tmutex_unlock(&ctx->power_lock);\n\n\tcontext_close(ctx);\n}\n'''
    new_postclose = '''static void msm_postclose(struct drm_device *dev, struct drm_file *file)\n{\n\tstruct msm_drm_private *priv = dev->dev_private;\n\tstruct msm_file_private *ctx = file->driver_priv;\n\tstruct msm_kms *kms = priv->kms;\n\tunsigned int trace_id;\n\tbool trace;\n\n\ttrace_id = atomic_inc_return(&a52_r211_close_sequence);\n\ttrace = trace_id <= A52_R211_CLOSE_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 close n=%u pid=%d comm=%.16s",\n\t\t\t\t  trace_id, current->pid, current->comm);\n\n\tif (kms && kms->funcs && kms->funcs->postclose)\n\t\tkms->funcs->postclose(kms, file);\n\n\tmutex_lock(&dev->struct_mutex);\n\tif (ctx == priv->lastctx)\n\t\tpriv->lastctx = NULL;\n\tmutex_unlock(&dev->struct_mutex);\n\n\tmutex_lock(&ctx->power_lock);\n\tif (ctx->enable_refcnt) {\n\t\tSDE_EVT32(ctx->enable_refcnt);\n\t\tpm_runtime_put_sync(dev->dev);\n\t}\n\tmutex_unlock(&ctx->power_lock);\n\n\tcontext_close(ctx);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 close-exit n=%u", trace_id);\n}\n'''
    text = replace_once(text, old_postclose, new_postclose, 'msm postclose')

    old_fops = '''static const struct file_operations fops = {\n\t.owner              = THIS_MODULE,\n\t.open               = drm_open,\n\t.release            = msm_release,\n\t.unlocked_ioctl     = drm_ioctl,\n\t.compat_ioctl       = drm_compat_ioctl,\n\t.poll               = drm_poll,\n\t.read               = drm_read,\n\t.llseek             = no_llseek,\n\t.mmap               = msm_gem_mmap,\n};\n'''
    new_fops = '''static long a52_r211_drm_ioctl(struct file *filp, unsigned int cmd,\n\t\t\t\tunsigned long arg)\n{\n\tunsigned int trace_id;\n\tbool trace;\n\tlong rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_ioctl_sequence);\n\ttrace = trace_id <= A52_R211_IOCTL_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd));\n\trc = drm_ioctl(filp, cmd, arg);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 ioctl-exit n=%u rc=%ld",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n\nstatic long a52_r211_drm_compat_ioctl(struct file *filp, unsigned int cmd,\n\t\t\t\t       unsigned long arg)\n{\n\tunsigned int trace_id;\n\tbool trace;\n\tlong rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_ioctl_sequence);\n\ttrace = trace_id <= A52_R211_IOCTL_LIMIT;\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 compat n=%u pid=%d nr=0x%x",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd));\n\trc = drm_compat_ioctl(filp, cmd, arg);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 compat-exit n=%u rc=%ld",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n\nstatic const struct file_operations fops = {\n\t.owner              = THIS_MODULE,\n\t.open               = drm_open,\n\t.release            = msm_release,\n\t.unlocked_ioctl     = a52_r211_drm_ioctl,\n\t.compat_ioctl       = a52_r211_drm_compat_ioctl,\n\t.poll               = drm_poll,\n\t.read               = drm_read,\n\t.llseek             = no_llseek,\n\t.mmap               = msm_gem_mmap,\n};\n'''
    text = replace_once(text, old_fops, new_fops, 'drm fops')

    required = [
        'DRMPOST 211 open n=%u pid=%d comm=%.16s',
        'DRMPOST 211 ioctl n=%u pid=%d nr=0x%x',
        'DRMPOST 211 check n=%u pid=%d comm=%.16s',
        'DRMPOST 211 close n=%u pid=%d comm=%.16s',
        '.unlocked_ioctl     = a52_r211_drm_ioctl,',
        '.compat_ioctl       = a52_r211_drm_compat_ioctl,',
        '.atomic_commit = msm_atomic_commit,',
    ]
    for marker in required:
        if marker not in text:
            raise RuntimeError(f'phase211 invariant missing: {marker}')
    return text


def apply(root: Path) -> None:
    path = root / MSM_DRV_REL
    original = path.read_text(encoding='utf-8')
    patched = patch_msm_drv(original)
    path.write_text(patched, encoding='utf-8')
    print('Phase211 DRM client trace applied')


def self_test(root: Path) -> None:
    source = (root / MSM_DRV_REL).read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as temporary:
        target_root = Path(temporary)
        target = target_root / MSM_DRV_REL
        target.parent.mkdir(parents=True)
        target.write_text(source, encoding='utf-8')
        apply(target_root)
        patched = target.read_text(encoding='utf-8')
        if patched.count('DRMPOST 211 open n=%u') != 1:
            raise RuntimeError('open trace count mismatch')
        if patched.count('DRMPOST 211 ioctl n=%u') != 1:
            raise RuntimeError('ioctl trace count mismatch')
        if patched.count('DRMPOST 211 check n=%u') != 1:
            raise RuntimeError('check trace count mismatch')
        try:
            patch_msm_drv(patched)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('patcher unexpectedly accepted already-patched source')
    print('phase211 DRM client patcher self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
