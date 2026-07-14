#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/08_build_resukisu_safe_checkpoint.sh"
GENERATED="$SCRIPT_DIR/.generated-resukisu-susfs-compat.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

python3 - "$SOURCE" "$GENERATED" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
text = source.read_text()
anchor = 'cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"\n'
compat = r'''cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"
# SUSFS 1.4.2 only needs core_hook.h for the disabled legacy SUS_SU mode.
sed -i '/#include "\.\.\/drivers\/kernelsu\/core_hook\.h"/d' "$KERNEL_DIR/fs/susfs.c"
# ReSukiSU v4.1.0 includes susfs_def.h from its supercall dispatcher. Supply
# the userspace command ABI plus the process-state helpers required on 4.19.
cat > "$KERNEL_DIR/include/linux/susfs_def.h" <<'SUSFSDEFEOT'
#ifndef KSU_SUSFS_DEF_H
#define KSU_SUSFS_DEF_H

#include <linux/cred.h>
#include <linux/thread_info.h>

#define SUSFS_MAGIC 0xFAFAFAFA
#define CMD_SUSFS_ADD_SUS_PATH 0x55550
#define CMD_SUSFS_ADD_SUS_PATH_LOOP 0x55553
#define CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS 0x55561
#define CMD_SUSFS_ADD_SUS_KSTAT 0x55570
#define CMD_SUSFS_UPDATE_SUS_KSTAT 0x55571
#define CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY 0x55572
#define CMD_SUSFS_SET_UNAME 0x55590
#define CMD_SUSFS_ENABLE_LOG 0x555a0
#define CMD_SUSFS_SET_CMDLINE_OR_BOOTCONFIG 0x555b0
#define CMD_SUSFS_ADD_OPEN_REDIRECT 0x555c0
#define CMD_SUSFS_SHOW_VERSION 0x555e1
#define CMD_SUSFS_SHOW_ENABLED_FEATURES 0x555e2
#define CMD_SUSFS_SHOW_VARIANT 0x555e3
#define CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING 0x60010
#define CMD_SUSFS_ADD_SUS_MAP 0x60020

#define TIF_PROC_UMOUNTED 33

static inline bool susfs_is_current_proc_umounted(void)
{
	return likely(test_thread_flag(TIF_PROC_UMOUNTED));
}

static inline void susfs_set_current_proc_umounted(void)
{
	set_thread_flag(TIF_PROC_UMOUNTED);
}

static inline bool susfs_is_current_proc_umounted_app(void)
{
	return likely(test_thread_flag(TIF_PROC_UMOUNTED)) &&
	       from_kuid(&init_user_ns, current_uid()) >= 10000;
}

void susfs_start_sdcard_monitor_fn(void);

#endif
SUSFSDEFEOT
# susfs.h is also included directly by supercall.c, which uses SUSFS_MAGIC.
python3 - "$KERNEL_DIR/include/linux/susfs.h" <<'SUSFSHEADERPY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
include = '#include <linux/susfs_def.h>\n'
if include not in text:
    guard = '#define KSU_SUSFS_H\n'
    if text.count(guard) != 1:
        raise SystemExit('include/linux/susfs.h guard anchor mismatch')
    text = text.replace(guard, guard + '\n' + include, 1)
    path.write_text(text)
SUSFSHEADERPY

# ReSukiSU v4.1.0 targets a newer SUSFS API than the pinned 1.4.2 kernel
# implementation. Provide only the missing compatibility entry points. Existing
# 1.4.2 symbols remain untouched, while unsupported newer commands safely no-op.
cat > "$KERNEL_DIR/KernelSU/kernel/susfs_legacy_compat.c" <<'SUSFSCOMPATC'
#include <linux/fs.h>
#include <linux/stat.h>
#include <linux/workqueue.h>
#include <linux/uaccess.h>

static void susfs_legacy_noop_work(struct work_struct *work) { }
DECLARE_WORK(susfs_extra_works, susfs_legacy_noop_work);

void susfs_add_sus_path_loop(void __user **arg) { }
void susfs_set_hide_sus_mnts_for_non_su_procs(void __user **arg) { }
void susfs_enable_log(void __user **arg) { }
void susfs_set_cmdline_or_bootconfig(void __user **arg) { }
void susfs_add_open_redirect(void __user **arg) { }
void susfs_add_sus_map(void __user **arg) { }
void susfs_set_avc_log_spoofing(void __user **arg) { }
void susfs_get_enabled_features(void __user **arg) { }
void susfs_show_variant(void __user **arg) { }
void susfs_show_version(void __user **arg) { }
void susfs_start_sdcard_monitor_fn(void) { }

void ksu_handle_newfstat_ret(unsigned int *fd, struct stat __user **statbuf_ptr) { }
#if defined(__ARCH_WANT_STAT64) || defined(__ARCH_WANT_COMPAT_STAT64)
void ksu_handle_fstat64_ret(unsigned long *fd, struct stat64 __user **statbuf_ptr) { }
#endif
SUSFSCOMPATC
# This Linux 4.19 Kbuild evaluates the composite object list when kernelsu.o is
# declared. Insert the compatibility unit at the start instead of appending it.
python3 - "$KERNEL_DIR/KernelSU/kernel/Kbuild" <<'SUSFSKBUILDPY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = 'kernelsu-objs := core/init.o\n'
new = 'kernelsu-objs := susfs_legacy_compat.o\nkernelsu-objs += core/init.o\n'
if text.count(old) != 1:
    raise SystemExit('ReSukiSU Kbuild object-list anchor mismatch')
text = text.replace(old, new, 1)
text += '\nccflags-y += -include $(srctree)/include/linux/susfs_def.h\n'
path.write_text(text)
SUSFSKBUILDPY
'''
if text.count(anchor) != 1:
    raise SystemExit('SUSFS header-copy anchor mismatch')
text = text.replace(anchor, compat, 1)
out.write_text(text)
out.chmod(0o755)
PY

exec "$GENERATED" "$@"
