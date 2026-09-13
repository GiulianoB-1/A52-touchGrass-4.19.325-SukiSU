#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1])

cfg = root / "arch/arm64/configs/a52xq_defconfig"
text = cfg.read_text()

disabled = "# CONFIG_ANDROID_BINDERFS is not set"
if disabled not in text:
    raise SystemExit("Phase81 BinderFS-disabled anchor missing")

text = text.replace(disabled, "CONFIG_ANDROID_BINDERFS=y", 1)
cfg.write_text(text)

binderfs = root / "drivers/android/binderfs.c"
src = binderfs.read_text()

signature = "static int init_binder_logs(struct super_block *sb)"
start = src.find(signature)
if start < 0:
    raise SystemExit("init_binder_logs() missing from retained BinderFS")

brace = src.find("{", start)
if brace < 0:
    raise SystemExit("init_binder_logs() opening brace missing")

depth = 0
end = None
for pos in range(brace, len(src)):
    if src[pos] == "{":
        depth += 1
    elif src[pos] == "}":
        depth -= 1
        if depth == 0:
            end = pos + 1
            break

if end is None:
    raise SystemExit("init_binder_logs() closing brace missing")

body = r'''{
	struct dentry *binder_logs_root_dir, *dentry, *proc_log_dir;
	const struct binder_debugfs_entry *db_entry;
	struct binderfs_info *info;
	int ret = 0;

	binder_logs_root_dir = binderfs_create_dir(sb->s_root,
					   "binder_logs");
	if (IS_ERR(binder_logs_root_dir)) {
		ret = PTR_ERR(binder_logs_root_dir);
		goto out;
	}

	/*
	 * Android 17 Binder exports its debug/stat surfaces through
	 * binder_debugfs_entries[] instead of the old individual
	 * binder_*_fops globals used by the original 4.19 BinderFS.
	 * Reuse that table while retaining the 4.19 BinderFS itself.
	 */
	binder_for_each_debugfs_entry(db_entry) {
		dentry = binderfs_create_file(binder_logs_root_dir,
					      db_entry->name,
					      db_entry->fops,
					      db_entry->data);
		if (IS_ERR(dentry)) {
			ret = PTR_ERR(dentry);
			goto out;
		}
	}

	proc_log_dir = binderfs_create_dir(binder_logs_root_dir, "proc");
	if (IS_ERR(proc_log_dir)) {
		ret = PTR_ERR(proc_log_dir);
		goto out;
	}

	info = sb->s_fs_info;
	info->proc_log_dir = proc_log_dir;

out:
	return ret;
}'''

src = src[:brace] + body + src[end:]
# Android 17 Binder's builtin-unload path expects BinderFS to expose the
# teardown wrapper added in newer kernels. The retained 4.19 BinderFS already
# owns the same filesystem type and chrdev range, so provide the equivalent.
if "void unload_binderfs(void)" not in src:
    init_sig = "int __init init_binderfs(void)"
    init_start = src.find(init_sig)
    if init_start < 0:
        raise SystemExit("init_binderfs() missing for unload bridge")
    init_brace = src.find("{", init_start)
    if init_brace < 0:
        raise SystemExit("init_binderfs() opening brace missing")
    depth = 0
    init_end = None
    for pos in range(init_brace, len(src)):
        if src[pos] == "{":
            depth += 1
        elif src[pos] == "}":
            depth -= 1
            if depth == 0:
                init_end = pos + 1
                break
    if init_end is None:
        raise SystemExit("init_binderfs() closing brace missing")

    unload = r'''

void unload_binderfs(void)
{
	unregister_filesystem(&binder_fs_type);
	unregister_chrdev_region(binderfs_dev, BINDERFS_MAX_MINOR);
}
'''
    src = src[:init_end] + unload + src[init_end:]

binderfs.write_text(src)

print("Phase82: retained Linux 4.19 BinderFS re-enabled with Android17 log/unload bridges")
