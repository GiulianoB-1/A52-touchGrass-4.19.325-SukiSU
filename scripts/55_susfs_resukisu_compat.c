// SPDX-License-Identifier: GPL-2.0
/*
 * Compatibility bridge between ReSukiSU v4.1.0's current SUSFS command ABI
 * and the maintained SUSFS v1.5.9 implementation for Linux 4.19.
 *
 * Keep this bridge deliberately narrow. It translates only the conservative
 * feature set selected by the A52 build: mount hiding, kstat spoofing, uname
 * spoofing, and version/feature reporting. Unsupported commands report
 * -EOPNOTSUPP instead of silently claiming success.
 */

#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/susfs.h>
#include <linux/types.h>
#include <linux/uaccess.h>

#define RESUKISU_SUSFS_FEATURES_SIZE 8192
#define RESUKISU_SUSFS_TEXT_SIZE 16

struct resukisu_susfs_mount_policy {
	bool enabled;
	int err;
};

struct resukisu_susfs_kstat {
	int is_statically;
	unsigned long target_ino;
	char target_pathname[SUSFS_MAX_LEN_PATHNAME];
	unsigned long spoofed_ino;
	unsigned long spoofed_dev;
	unsigned int spoofed_nlink;
	long long spoofed_size;
	long spoofed_atime_tv_sec;
	unsigned long spoofed_atime_tv_nsec;
	long spoofed_mtime_tv_sec;
	unsigned long spoofed_mtime_tv_nsec;
	long spoofed_ctime_tv_sec;
	unsigned long spoofed_ctime_tv_nsec;
	long long spoofed_blocks;
	long spoofed_blksize;
	int flags;
	int err;
};

struct resukisu_susfs_uname {
	char release[__NEW_UTS_LEN + 1];
	char version[__NEW_UTS_LEN + 1];
	int err;
};

struct resukisu_susfs_avc_policy {
	bool enabled;
	int err;
};

struct resukisu_susfs_features {
	char enabled_features[RESUKISU_SUSFS_FEATURES_SIZE];
	int err;
};

struct resukisu_susfs_text_result {
	char text[RESUKISU_SUSFS_TEXT_SIZE];
	int err;
};

extern bool susfs_hide_sus_mnts_for_all_procs;

static int compat_copy_err(void __user *field, int err)
{
	if (copy_to_user(field, &err, sizeof(err)))
		return -EFAULT;
	return err;
}

static int compat_call_legacy_kstat(struct st_susfs_sus_kstat *legacy,
				    bool update)
{
	mm_segment_t old_fs;
	int ret;

	old_fs = get_fs();
	set_fs(KERNEL_DS);
	if (update)
		ret = susfs_update_sus_kstat((struct st_susfs_sus_kstat __user *)legacy);
	else
		ret = susfs_add_sus_kstat((struct st_susfs_sus_kstat __user *)legacy);
	set_fs(old_fs);

	return ret;
}

void susfs_compat_set_hide_sus_mnts_for_non_su_procs(void __user **user_info)
{
	struct resukisu_susfs_mount_policy info = { 0 };

	if (!user_info || !*user_info ||
	    copy_from_user(&info, (void __user *)*user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	/*
	 * The old 4.19 implementation has a stronger "hide for all" switch.
	 * Keep that switch disabled so suspicious mounts remain visible to the
	 * KernelSU domain and hidden from ordinary processes. This matches the
	 * safe non-su policy, but does not expose a runtime disable operation.
	 */
	WRITE_ONCE(susfs_hide_sus_mnts_for_all_procs, false);
	info.err = info.enabled ? 0 : -EOPNOTSUPP;

out:
	if (user_info && *user_info)
		compat_copy_err(&((struct resukisu_susfs_mount_policy __user *)*user_info)->err,
				info.err);
}

void susfs_compat_add_sus_kstat(void __user **user_info, bool statically)
{
	struct resukisu_susfs_kstat info = { 0 };
	struct st_susfs_sus_kstat legacy = { 0 };
	int ret;

	if (!user_info || !*user_info ||
	    copy_from_user(&info, (void __user *)*user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	legacy.is_statically = statically || info.is_statically;
	legacy.target_ino = info.target_ino;
	strncpy(legacy.target_pathname, info.target_pathname,
		SUSFS_MAX_LEN_PATHNAME - 1);
	legacy.spoofed_ino = info.spoofed_ino;
	legacy.spoofed_dev = info.spoofed_dev;
	legacy.spoofed_nlink = info.spoofed_nlink;
	legacy.spoofed_size = info.spoofed_size;
	legacy.spoofed_atime_tv_sec = info.spoofed_atime_tv_sec;
	legacy.spoofed_atime_tv_nsec = (long)info.spoofed_atime_tv_nsec;
	legacy.spoofed_mtime_tv_sec = info.spoofed_mtime_tv_sec;
	legacy.spoofed_mtime_tv_nsec = (long)info.spoofed_mtime_tv_nsec;
	legacy.spoofed_ctime_tv_sec = info.spoofed_ctime_tv_sec;
	legacy.spoofed_ctime_tv_nsec = (long)info.spoofed_ctime_tv_nsec;
	legacy.spoofed_blksize = (unsigned long)info.spoofed_blksize;
	legacy.spoofed_blocks = (unsigned long long)info.spoofed_blocks;

	ret = compat_call_legacy_kstat(&legacy, false);
	info.err = ret ? -EINVAL : 0;

out:
	if (user_info && *user_info)
		compat_copy_err(&((struct resukisu_susfs_kstat __user *)*user_info)->err,
				info.err);
}

void susfs_compat_update_sus_kstat(void __user **user_info)
{
	struct resukisu_susfs_kstat info = { 0 };
	struct st_susfs_sus_kstat legacy = { 0 };
	int ret;

	if (!user_info || !*user_info ||
	    copy_from_user(&info, (void __user *)*user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	legacy.is_statically = info.is_statically;
	legacy.target_ino = info.target_ino;
	strncpy(legacy.target_pathname, info.target_pathname,
		SUSFS_MAX_LEN_PATHNAME - 1);
	legacy.spoofed_size = info.spoofed_size;
	legacy.spoofed_blocks = (unsigned long long)info.spoofed_blocks;

	ret = compat_call_legacy_kstat(&legacy, true);
	info.err = ret ? -ENOENT : 0;

out:
	if (user_info && *user_info)
		compat_copy_err(&((struct resukisu_susfs_kstat __user *)*user_info)->err,
				info.err);
}

void susfs_compat_set_uname(void __user **user_info)
{
	struct resukisu_susfs_uname info = { 0 };
	struct st_susfs_uname legacy = { 0 };
	mm_segment_t old_fs;
	int ret;

	if (!user_info || !*user_info ||
	    copy_from_user(&info, (void __user *)*user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	strncpy(legacy.release, info.release, __NEW_UTS_LEN);
	strncpy(legacy.version, info.version, __NEW_UTS_LEN);

	old_fs = get_fs();
	set_fs(KERNEL_DS);
	ret = susfs_set_uname((struct st_susfs_uname __user *)&legacy);
	set_fs(old_fs);
	info.err = ret ? -EINVAL : 0;

out:
	if (user_info && *user_info)
		compat_copy_err(&((struct resukisu_susfs_uname __user *)*user_info)->err,
				info.err);
}

void susfs_compat_set_avc_log_spoofing(void __user **user_info)
{
	struct resukisu_susfs_avc_policy info = { 0 };

	if (!user_info || !*user_info ||
	    copy_from_user(&info, (void __user *)*user_info, sizeof(info)))
		info.err = -EFAULT;
	else
		info.err = -EOPNOTSUPP;

	if (user_info && *user_info)
		compat_copy_err(&((struct resukisu_susfs_avc_policy __user *)*user_info)->err,
				info.err);
}

static void compat_append_feature(char *buf, size_t size, size_t *used,
				  const char *feature)
{
	if (*used >= size)
		return;
	*used += scnprintf(buf + *used, size - *used, "%s\n", feature);
}

void susfs_compat_get_enabled_features(void __user **user_info)
{
	struct resukisu_susfs_features *info;
	size_t used = 0;

	if (!user_info || !*user_info)
		return;

	info = kzalloc(sizeof(*info), GFP_KERNEL);
	if (!info) {
		int err = -ENOMEM;
		compat_copy_err(&((struct resukisu_susfs_features __user *)*user_info)->err,
				err);
		return;
	}

#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_SUS_PATH");
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_SUS_MOUNT");
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_SUS_KSTAT");
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_SPOOF_UNAME");
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_ENABLE_LOG");
#endif
#ifdef CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS");
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG");
#endif
#ifdef CONFIG_KSU_SUSFS_OPEN_REDIRECT
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_OPEN_REDIRECT");
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MAP
	compat_append_feature(info->enabled_features, sizeof(info->enabled_features),
			      &used, "CONFIG_KSU_SUSFS_SUS_MAP");
#endif
	info->err = 0;

	if (copy_to_user((void __user *)*user_info, info, sizeof(*info)))
		info->err = -EFAULT;
	kfree(info);
}

static void compat_show_text(void __user **user_info, const char *text)
{
	struct resukisu_susfs_text_result info = { { 0 }, 0 };

	if (!user_info || !*user_info)
		return;

	strncpy(info.text, text, sizeof(info.text) - 1);
	if (copy_to_user((void __user *)*user_info, &info, sizeof(info)))
		return;
}

void susfs_compat_show_variant(void __user **user_info)
{
	compat_show_text(user_info, SUSFS_VARIANT);
}

void susfs_compat_show_version(void __user **user_info)
{
	compat_show_text(user_info, SUSFS_VERSION);
}
