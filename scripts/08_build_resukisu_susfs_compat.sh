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
'''
if text.count(anchor) != 1:
    raise SystemExit('SUSFS header-copy anchor mismatch')
text = text.replace(anchor, compat, 1)
out.write_text(text)
out.chmod(0o755)
PY

exec "$GENERATED" "$@"
