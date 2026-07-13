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
# ReSukiSU v4.1.0 expects the modern process-state helpers supplied by
# susfs_def.h. Keep this shim deliberately limited to those helpers.
cat > "$KERNEL_DIR/include/linux/susfs_def.h" <<'SUSFSDEFEOT'
#ifndef KSU_SUSFS_DEF_H
#define KSU_SUSFS_DEF_H

#include <linux/cred.h>
#include <linux/thread_info.h>

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
