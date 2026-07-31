#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_sde_kms(path: Path) -> None:
    text = path.read_text()

    text = replace_once(
        text,
        '''\trc = _sde_kms_get_splash_data(&sde_kms->splash_data);\n\tif (rc)\n\t\tSDE_DEBUG("sde splash data fetch failed: %d\\n", rc);\n\n\trc = pm_runtime_get_sync(sde_kms->dev->dev);\n''',
        '''\trc = _sde_kms_get_splash_data(&sde_kms->splash_data);\n\ta52_ackfr_record("KMSPOST splash rc=%d regions=%u displays=%u",\n\t\trc, sde_kms->splash_data.num_splash_regions,\n\t\tsde_kms->splash_data.num_splash_displays);\n\tif (rc)\n\t\tSDE_DEBUG("sde splash data fetch failed: %d\\n", rc);\n\n\ta52_ackfr_record("KMSPOST pm-get enter");\n\trc = pm_runtime_get_sync(sde_kms->dev->dev);\n\ta52_ackfr_record("KMSPOST pm-get exit rc=%d", rc);\n''',
        "sde splash and pm get",
    )

    text = replace_once(
        text,
        '''\trc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);\n\tif (rc)\n\t\tgoto hw_init_err;\n\n\tdev->mode_config.min_width = sde_kms->catalog->min_display_width;\n''',
        '''\ta52_ackfr_record("KMSPOST blocks enter");\n\trc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);\n\ta52_ackfr_record("KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d",\n\t\trc, priv->num_crtcs, priv->num_encoders,\n\t\tpriv->num_connectors, priv->num_planes);\n\tif (rc)\n\t\tgoto hw_init_err;\n\n\tdev->mode_config.min_width = sde_kms->catalog->min_display_width;\n''',
        "sde blocks",
    )

    text = replace_once(
        text,
        '''\tif (sde_kms->splash_data.num_splash_displays) {\n\t\tSDE_DEBUG("Skipping MDP Resources disable\\n");\n\t} else {\n\t\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX; i++)\n\t\t\tsde_power_data_bus_set_quota(&priv->phandle, i,\n\t\t\t\tSDE_POWER_HANDLE_ENABLE_BUS_AB_QUOTA,\n\t\t\t\tSDE_POWER_HANDLE_ENABLE_BUS_IB_QUOTA);\n\n\t\tpm_runtime_put_sync(sde_kms->dev->dev);\n\t}\n''',
        '''\ta52_ackfr_record("KMSPOST power-decision displays=%u regions=%u",\n\t\tsde_kms->splash_data.num_splash_displays,\n\t\tsde_kms->splash_data.num_splash_regions);\n\tif (sde_kms->splash_data.num_splash_displays) {\n\t\ta52_ackfr_record("KMSPOST power keep reason=continuous-splash");\n\t\tSDE_DEBUG("Skipping MDP Resources disable\\n");\n\t} else {\n\t\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX; i++)\n\t\t\tsde_power_data_bus_set_quota(&priv->phandle, i,\n\t\t\t\tSDE_POWER_HANDLE_ENABLE_BUS_AB_QUOTA,\n\t\t\t\tSDE_POWER_HANDLE_ENABLE_BUS_IB_QUOTA);\n\n\t\ta52_ackfr_record("KMSPOST pm-put enter reason=no-splash");\n\t\tpm_runtime_put_sync(sde_kms->dev->dev);\n\t\ta52_ackfr_record("KMSPOST pm-put exit");\n\t}\n''',
        "sde power decision",
    )

    text = replace_once(
        text,
        '''\tirq_num = platform_get_irq(to_platform_device(sde_kms->dev->dev), 0);\n\tSDE_DEBUG("Registering for notification of irq_num: %d\\n", irq_num);\n\tirq_set_affinity_notifier(irq_num, &sde_kms->affinity_notify);\n\n\treturn 0;\n\nhw_init_err:\n''',
        '''\tirq_num = platform_get_irq(to_platform_device(sde_kms->dev->dev), 0);\n\ta52_ackfr_record("KMSPOST affinity enter irq=%d", irq_num);\n\tSDE_DEBUG("Registering for notification of irq_num: %d\\n", irq_num);\n\tirq_set_affinity_notifier(irq_num, &sde_kms->affinity_notify);\n\ta52_ackfr_record("KMSPOST affinity exit");\n\n\ta52_ackfr_record("KMSPOST hw-init success crtc=%d enc=%d conn=%d plane=%d",\n\t\tpriv->num_crtcs, priv->num_encoders,\n\t\tpriv->num_connectors, priv->num_planes);\n\treturn 0;\n\nhw_init_err:\n''',
        "sde affinity and return",
    )

    text = replace_once(
        text,
        '''hw_init_err:\n\tpm_runtime_put_sync(sde_kms->dev->dev);\nerror:\n\t_sde_kms_hw_destroy(sde_kms, platformdev);\nend:\n\treturn rc;\n''',
        '''hw_init_err:\n\ta52_ackfr_record("KMSPOST fail stage=blocks rc=%d", rc);\n\tpm_runtime_put_sync(sde_kms->dev->dev);\nerror:\n\ta52_ackfr_record("KMSPOST fail stage=destroy rc=%d", rc);\n\t_sde_kms_hw_destroy(sde_kms, platformdev);\nend:\n\ta52_ackfr_record("KMSPOST hw-init exit rc=%d", rc);\n\treturn rc;\n''',
        "sde error path",
    )

    path.write_text(text)


def patch_msm_drv(path: Path) -> None:
    text = path.read_text()

    text = replace_once(
        text,
        '''static int msm_drm_display_thread_create(struct sched_param param,\n\tstruct msm_drm_private *priv, struct drm_device *ddev,\n\tstruct device *dev)\n{\n\tint i, ret = 0;\n''',
        '''static int msm_drm_display_thread_create(struct sched_param param,\n\tstruct msm_drm_private *priv, struct drm_device *ddev,\n\tstruct device *dev)\n{\n\tint i, ret = 0;\n\n\ta52_ackfr_record("DRMPOST threads enter crtc=%d", priv->num_crtcs);\n''',
        "thread create enter",
    )

    text = replace_once(
        text,
        '''\t\tpriv->disp_thread[i].thread =\n\t\t\tkthread_run(kthread_worker_fn,\n\t\t\t\t&priv->disp_thread[i].worker,\n\t\t\t\t"crtc_commit:%d", priv->disp_thread[i].crtc_id);\n\t\tret = sched_setscheduler(priv->disp_thread[i].thread,\n''',
        '''\t\ta52_ackfr_record("DRMPOST commit-thread enter i=%d crtc=%u",\n\t\t\ti, priv->disp_thread[i].crtc_id);\n\t\tpriv->disp_thread[i].thread =\n\t\t\tkthread_run(kthread_worker_fn,\n\t\t\t\t&priv->disp_thread[i].worker,\n\t\t\t\t"crtc_commit:%d", priv->disp_thread[i].crtc_id);\n\t\ta52_ackfr_record("DRMPOST commit-thread exit i=%d err=%d",\n\t\t\ti, IS_ERR(priv->disp_thread[i].thread));\n\t\ta52_ackfr_record("DRMPOST commit-sched enter i=%d", i);\n\t\tret = sched_setscheduler(priv->disp_thread[i].thread,\n''',
        "commit thread",
    )

    text = replace_once(
        text,
        '''\t\tret = sched_setscheduler(priv->disp_thread[i].thread,\n\t\t\t\t\t\t\tSCHED_FIFO, &param);\n\t\tif (ret)\n''',
        '''\t\tret = sched_setscheduler(priv->disp_thread[i].thread,\n\t\t\t\t\t\t\tSCHED_FIFO, &param);\n\t\ta52_ackfr_record("DRMPOST commit-sched exit i=%d rc=%d", i, ret);\n\t\tif (ret)\n''',
        "commit scheduler exit",
    )

    text = replace_once(
        text,
        '''\t\tpriv->event_thread[i].thread =\n\t\t\tkthread_run(kthread_worker_fn,\n\t\t\t\t&priv->event_thread[i].worker,\n\t\t\t\t"crtc_event:%d", priv->event_thread[i].crtc_id);\n''',
        '''\t\ta52_ackfr_record("DRMPOST event-thread enter i=%d crtc=%u",\n\t\t\ti, priv->event_thread[i].crtc_id);\n\t\tpriv->event_thread[i].thread =\n\t\t\tkthread_run(kthread_worker_fn,\n\t\t\t\t&priv->event_thread[i].worker,\n\t\t\t\t"crtc_event:%d", priv->event_thread[i].crtc_id);\n\t\ta52_ackfr_record("DRMPOST event-thread exit i=%d err=%d",\n\t\t\ti, IS_ERR(priv->event_thread[i].thread));\n''',
        "event thread",
    )

    text = replace_once(
        text,
        '''\t\tret = sched_setscheduler(priv->event_thread[i].thread,\n\t\t\t\t\t\t\tSCHED_FIFO, &param);\n''',
        '''\t\ta52_ackfr_record("DRMPOST event-sched enter i=%d", i);\n\t\tret = sched_setscheduler(priv->event_thread[i].thread,\n\t\t\t\t\t\t\tSCHED_FIFO, &param);\n\t\ta52_ackfr_record("DRMPOST event-sched exit i=%d rc=%d", i, ret);\n''',
        "event scheduler",
    )

    text = replace_once(
        text,
        '''\tpriv->pp_event_thread = kthread_run(kthread_worker_fn,\n\t\t\t&priv->pp_event_worker, "pp_event");\n\n\tret = sched_setscheduler(priv->pp_event_thread,\n''',
        '''\ta52_ackfr_record("DRMPOST pp-thread enter");\n\tpriv->pp_event_thread = kthread_run(kthread_worker_fn,\n\t\t\t&priv->pp_event_worker, "pp_event");\n\ta52_ackfr_record("DRMPOST pp-thread exit err=%d",\n\t\tIS_ERR(priv->pp_event_thread));\n\ta52_ackfr_record("DRMPOST pp-sched enter");\n\n\tret = sched_setscheduler(priv->pp_event_thread,\n''',
        "pp thread",
    )

    text = replace_once(
        text,
        '''\tret = sched_setscheduler(priv->pp_event_thread,\n\t\t\t\t\t\tSCHED_FIFO, &param);\n\tif (ret)\n''',
        '''\tret = sched_setscheduler(priv->pp_event_thread,\n\t\t\t\t\t\tSCHED_FIFO, &param);\n\ta52_ackfr_record("DRMPOST pp-sched exit rc=%d", ret);\n\tif (ret)\n''',
        "pp scheduler exit",
    )

    text = replace_once(
        text,
        '''\treturn 0;\n\n}\nstatic struct msm_kms *_msm_drm_init_helper''',
        '''\ta52_ackfr_record("DRMPOST threads exit rc=0");\n\treturn 0;\n\n}\nstatic struct msm_kms *_msm_drm_init_helper''',
        "thread function success",
    )

    text = replace_once(
        text,
        '''\tkms = _msm_drm_init_helper(priv, ddev, dev, pdev);\n\tif (IS_ERR_OR_NULL(kms)) {\n''',
        '''\ta52_ackfr_record("DRMPOST helper enter");\n\tkms = _msm_drm_init_helper(priv, ddev, dev, pdev);\n\ta52_ackfr_record("DRMPOST helper exit err=%d null=%d crtc=%d enc=%d conn=%d plane=%d",\n\t\tIS_ERR(kms), !kms, priv->num_crtcs, priv->num_encoders,\n\t\tpriv->num_connectors, priv->num_planes);\n\tif (IS_ERR_OR_NULL(kms)) {\n''',
        "helper result",
    )

    text = replace_once(
        text,
        '''\tret = msm_drm_display_thread_create(param, priv, ddev, dev);\n\tif (ret) {\n''',
        '''\ta52_ackfr_record("DRMPOST thread-create enter");\n\tret = msm_drm_display_thread_create(param, priv, ddev, dev);\n\ta52_ackfr_record("DRMPOST thread-create exit rc=%d", ret);\n\tif (ret) {\n''',
        "thread result",
    )

    text = replace_once(
        text,
        '''\tret = drm_vblank_init(ddev, priv->num_crtcs);\n\tif (ret < 0) {\n''',
        '''\ta52_ackfr_record("DRMPOST vblank enter crtc=%d", priv->num_crtcs);\n\tret = drm_vblank_init(ddev, priv->num_crtcs);\n\ta52_ackfr_record("DRMPOST vblank exit rc=%d", ret);\n\tif (ret < 0) {\n''',
        "vblank",
    )

    text = replace_once(
        text,
        '''\tif (kms) {\n\t\tpm_runtime_get_sync(dev);\n\t\tret = drm_irq_install(ddev, platform_get_irq(pdev, 0));\n\t\tpm_runtime_put_sync(dev);\n''',
        '''\tif (kms) {\n\t\ta52_ackfr_record("DRMPOST irq-pm-get enter");\n\t\tpm_runtime_get_sync(dev);\n\t\ta52_ackfr_record("DRMPOST irq-pm-get exit");\n\t\ta52_ackfr_record("DRMPOST irq-install enter irq=%d",\n\t\t\tplatform_get_irq(pdev, 0));\n\t\tret = drm_irq_install(ddev, platform_get_irq(pdev, 0));\n\t\ta52_ackfr_record("DRMPOST irq-install exit rc=%d", ret);\n\t\ta52_ackfr_record("DRMPOST irq-pm-put enter");\n\t\tpm_runtime_put_sync(dev);\n\t\ta52_ackfr_record("DRMPOST irq-pm-put exit");\n''',
        "irq stage",
    )

    text = replace_once(
        text,
        '''\tret = drm_dev_register(ddev, 0);\n\tif (ret)\n\t\tgoto fail;\n\tpriv->registered = true;\n\n\tdrm_mode_config_reset(ddev);\n''',
        '''\ta52_ackfr_record("DRMPOST dev-register enter");\n\tret = drm_dev_register(ddev, 0);\n\ta52_ackfr_record("DRMPOST dev-register exit rc=%d", ret);\n\tif (ret)\n\t\tgoto fail;\n\tpriv->registered = true;\n\n\ta52_ackfr_record("DRMPOST mode-reset enter");\n\tdrm_mode_config_reset(ddev);\n\ta52_ackfr_record("DRMPOST mode-reset exit");\n''',
        "device register and reset",
    )

    text = replace_once(
        text,
        '''\tif (kms && kms->funcs && kms->funcs->cont_splash_config) {\n\t\tret = kms->funcs->cont_splash_config(kms);\n''',
        '''\tif (kms && kms->funcs && kms->funcs->cont_splash_config) {\n\t\ta52_ackfr_record("DRMPOST splash-config enter");\n\t\tret = kms->funcs->cont_splash_config(kms);\n\t\ta52_ackfr_record("DRMPOST splash-config exit rc=%d", ret);\n''',
        "splash config",
    )

    text = replace_once(
        text,
        '''\tret = sde_dbg_debugfs_register(dev);\n''',
        '''\ta52_ackfr_record("DRMPOST debugfs enter");\n\tret = sde_dbg_debugfs_register(dev);\n\ta52_ackfr_record("DRMPOST debugfs exit rc=%d", ret);\n''',
        "debugfs",
    )

    text = replace_once(
        text,
        '''\tif (kms && kms->funcs && kms->funcs->postinit) {\n\t\tret = kms->funcs->postinit(kms);\n''',
        '''\tif (kms && kms->funcs && kms->funcs->postinit) {\n\t\ta52_ackfr_record("DRMPOST postinit enter");\n\t\tret = kms->funcs->postinit(kms);\n\t\ta52_ackfr_record("DRMPOST postinit exit rc=%d", ret);\n''',
        "postinit",
    )

    text = replace_once(
        text,
        '''\tdrm_kms_helper_poll_init(ddev);\n\n\treturn 0;\n\nfail:\n''',
        '''\ta52_ackfr_record("DRMPOST poll-init enter");\n\tdrm_kms_helper_poll_init(ddev);\n\ta52_ackfr_record("DRMPOST poll-init exit");\n\n\ta52_ackfr_record("DRMPOST init success");\n\treturn 0;\n\nfail:\n\ta52_ackfr_record("DRMPOST init fail rc=%d", ret);\n''',
        "poll and final result",
    )

    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root)
    patch_sde_kms(root / 'drivers/a52_display/msm/sde/sde_kms.c')
    patch_msm_drv(root / 'drivers/a52_display/msm/msm_drv.c')
    print('phase195 DRM post-KMS and splash trace applied')


if __name__ == '__main__':
    main()
