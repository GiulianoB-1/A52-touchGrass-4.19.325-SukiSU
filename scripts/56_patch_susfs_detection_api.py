#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: 56_patch_susfs_detection_api.py GENERATED_BUILD_SCRIPT")

    path = Path(sys.argv[1])
    text = path.read_text()

    anchor = "# No unsupported newer SUSFS API may remain.\nunsupported = []\n"
    if text.count(anchor) != 1:
        raise SystemExit(
            f"unsupported-API scan anchor: expected one match, found {text.count(anchor)}"
        )

    compatibility = r'''# Backport only the modern userspace detection ABI. This lets ReSukiSU and
# ksu_susfs discover the real legacy implementation without pretending that
# disabled SUSFS subfeatures are available.
dispatch = resukisu / 'kernel/supercall/dispatch.c'
text = dispatch.read_text()
old_dispatcher = """#ifdef CONFIG_KSU_SUSFS
int ksu_handle_susfs_cmd(unsigned int cmd, void __user **arg)
{
    void __user *uarg = *arg;
    switch (cmd) {
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
    case CMD_SUSFS_ADD_SUS_PATH:
        return susfs_add_sus_path((struct st_susfs_sus_path __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
    case CMD_SUSFS_ADD_SUS_KSTAT:
    case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY:
        return susfs_add_sus_kstat((struct st_susfs_sus_kstat __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
    case CMD_SUSFS_SET_UNAME:
        return susfs_set_uname((struct st_susfs_uname __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
    case CMD_SUSFS_ENABLE_LOG: {
        bool enabled;
        if (copy_from_user(&enabled, uarg, sizeof(enabled)))
            return -EFAULT;
        susfs_set_log(enabled);
        return 0;
    }
#endif
    default:
        return -EOPNOTSUPP;
    }
}
#endif
"""
new_dispatcher = """#ifdef CONFIG_KSU_SUSFS
#ifndef CMD_SUSFS_SHOW_VERSION
#define CMD_SUSFS_SHOW_VERSION 0x555e1
#endif
#ifndef CMD_SUSFS_SHOW_ENABLED_FEATURES
#define CMD_SUSFS_SHOW_ENABLED_FEATURES 0x555e2
#endif
#ifndef CMD_SUSFS_SHOW_VARIANT
#define CMD_SUSFS_SHOW_VARIANT 0x555e3
#endif

#define KSU_SUSFS_COMPAT_FEATURES_SIZE 8192

struct ksu_susfs_version_compat {
    char susfs_version[16];
    int err;
};

struct ksu_susfs_features_compat {
    char enabled_features[KSU_SUSFS_COMPAT_FEATURES_SIZE];
    int err;
};

struct ksu_susfs_variant_compat {
    char susfs_variant[16];
    int err;
};

static int ksu_susfs_show_version_compat(void __user *uarg)
{
    const struct ksu_susfs_version_compat info = { "v1.4.2", 0 };

    return copy_to_user(uarg, &info, sizeof(info)) ? -EFAULT : 0;
}

static int ksu_susfs_show_enabled_features_compat(void __user *uarg)
{
    struct ksu_susfs_features_compat *info;
    int ret = 0;

    info = kzalloc(sizeof(*info), GFP_KERNEL);
    if (!info)
        return -ENOMEM;

    /* Every optional SUSFS feature is intentionally disabled in this profile. */
    info->err = 0;
    if (copy_to_user(uarg, info, sizeof(*info)))
        ret = -EFAULT;

    kfree(info);
    return ret;
}

static int ksu_susfs_show_variant_compat(void __user *uarg)
{
    const struct ksu_susfs_variant_compat info = { "NON-GKI", 0 };

    return copy_to_user(uarg, &info, sizeof(info)) ? -EFAULT : 0;
}

int ksu_handle_susfs_cmd(unsigned int cmd, void __user **arg)
{
    void __user *uarg = *arg;

    switch (cmd) {
    case CMD_SUSFS_SHOW_VERSION:
        return ksu_susfs_show_version_compat(uarg);
    case CMD_SUSFS_SHOW_ENABLED_FEATURES:
        return ksu_susfs_show_enabled_features_compat(uarg);
    case CMD_SUSFS_SHOW_VARIANT:
        return ksu_susfs_show_variant_compat(uarg);
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
    case CMD_SUSFS_ADD_SUS_PATH:
        return susfs_add_sus_path((struct st_susfs_sus_path __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
    case CMD_SUSFS_ADD_SUS_KSTAT:
    case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY:
        return susfs_add_sus_kstat((struct st_susfs_sus_kstat __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
    case CMD_SUSFS_SET_UNAME:
        return susfs_set_uname((struct st_susfs_uname __user *)uarg);
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
    case CMD_SUSFS_ENABLE_LOG: {
        bool enabled;
        if (copy_from_user(&enabled, uarg, sizeof(enabled)))
            return -EFAULT;
        susfs_set_log(enabled);
        return 0;
    }
#endif
    default:
        return -EOPNOTSUPP;
    }
}
#endif
"""
if text.count(old_dispatcher) != 1:
    raise SystemExit(
        f'ReSukiSU legacy dispatcher mismatch: expected one match, found {text.count(old_dispatcher)}'
    )
updated = text.replace(old_dispatcher, new_dispatcher, 1)
for required in (
    '#define CMD_SUSFS_SHOW_VERSION 0x555e1',
    '#define CMD_SUSFS_SHOW_ENABLED_FEATURES 0x555e2',
    '#define CMD_SUSFS_SHOW_VARIANT 0x555e3',
    'const struct ksu_susfs_version_compat info = { "v1.4.2", 0 };',
    'const struct ksu_susfs_variant_compat info = { "NON-GKI", 0 };',
):
    if required not in updated:
        raise SystemExit(f'SUSFS detection API patch is incomplete: {required}')
dispatch.write_text(updated)

'''

    path.write_text(text.replace(anchor, compatibility + anchor, 1))


if __name__ == "__main__":
    main()
