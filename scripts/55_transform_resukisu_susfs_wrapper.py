#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

HELPER_START = "python3 - scripts/08_build_resukisu_safe_checkpoint.sh <<'PY'\n"
HELPER_END = '\nPY\n\ninfo "Building Linux 4.19.206 with ReSukiSU and SUSFS inline hooks"'
HELPER_REPLACEMENT = (
    'python3 scripts/55_transform_resukisu_susfs_wrapper.py '
    'scripts/08_build_resukisu_safe_checkpoint.sh'
)

NEW_CLONE = r"""info "Integrating maintained SUSFS v1.5.9 Linux 4.19 patch"
SUSFS_DIR="$KERNEL_DIR/../susfs4ksu-kernel-4.19"
rm -rf "$SUSFS_DIR"
git init -q "$SUSFS_DIR"
git -C "$SUSFS_DIR" remote add origin "$SUSFS_REPO"
git -C "$SUSFS_DIR" fetch --quiet --depth=1 origin "refs/heads/kernel-4.19"
git -C "$SUSFS_DIR" checkout --quiet --detach FETCH_HEAD
SUSFS_COMMIT="$(git -C "$SUSFS_DIR" rev-parse HEAD)"
cp "$SUSFS_DIR/kernel_patches/fs/susfs.c" "$KERNEL_DIR/fs/susfs.c"
cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"
cp "$SUSFS_DIR/kernel_patches/include/linux/susfs_def.h" "$KERNEL_DIR/include/linux/susfs_def.h"
grep -Fq '#define SUSFS_VERSION "v1.5.9"' "$KERNEL_DIR/include/linux/susfs.h" || fail "Unexpected SUSFS branch version"
patch -d "$KERNEL_DIR" -p1 --forward --batch --fuzz=3 < "$SUSFS_DIR/kernel_patches/50_add_susfs_in_kernel-4.19.patch"
sed -i 's/[[:space:]]\+$//' "$KERNEL_DIR/fs/namespace.c" "$KERNEL_DIR/fs/overlayfs/readdir.c"
python3 - "$KERNEL_DIR/include/linux/susfs_def.h" "$RESUKISU_DIR/kernel/feature/kernel_umount.c" <<'SUSFSCOMPATPY'
from pathlib import Path
import sys

header = Path(sys.argv[1])
text = header.read_text()
anchor = '#endif // #ifndef KSU_SUSFS_DEF_H\n'
compat = r'''

/* ReSukiSU compatibility names for the maintained 4.19 SUSFS state bit. */
static inline bool susfs_is_current_proc_umounted(void)
{
    return susfs_is_current_non_root_user_app_proc();
}

static inline void susfs_set_current_proc_umounted(void)
{
    susfs_set_current_non_root_user_app_proc();
}

/* SUS_PATH is disabled in this conservative build, so no monitor is needed. */
static inline void susfs_start_sdcard_monitor_fn(void)
{
}

'''
if 'susfs_is_current_proc_umounted' not in text:
    if text.count(anchor) != 1:
        raise SystemExit('susfs_def.h final guard anchor mismatch')
    header.write_text(text.replace(anchor, compat + anchor, 1))

umount = Path(sys.argv[2])
text = umount.read_text()
line = '    schedule_work(&susfs_extra_works);\n'
if text.count(line) != 1:
    raise SystemExit('ReSukiSU susfs_extra_works anchor mismatch')
umount.write_text(text.replace(line, '', 1))
SUSFSCOMPATPY
"""

CONFIG_EXTENSION = r"""    safe_susfs_config = new_config + (' \\\n'
        '  -d KSU_SUSFS_SUS_PATH -e KSU_SUSFS_SUS_MOUNT -e KSU_SUSFS_SUS_KSTAT \\\n'
        '  -e KSU_SUSFS_SPOOF_UNAME -d KSU_SUSFS_ENABLE_LOG \\\n'
        '  -d KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS -d KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG \\\n'
        '  -d KSU_SUSFS_OPEN_REDIRECT -d KSU_SUSFS_SUS_MAP')
    text = text.replace(new_config, safe_susfs_config, 1)
"""


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_helper(path: Path) -> None:
    text = path.read_text()
    start = text.find(HELPER_START)
    if start < 0:
        if HELPER_REPLACEMENT in text:
            return
        raise SystemExit("helper embedded transformer start marker missing")
    end = text.find(HELPER_END, start)
    if end < 0:
        raise SystemExit("helper embedded transformer end marker missing")
    text = text[:start] + HELPER_REPLACEMENT + text[end + len("\nPY") :]
    path.write_text(text)


def patch_wrapper(path: Path) -> None:
    text = path.read_text()

    clone_pattern = re.compile(
        r'info "Integrating pinned SUSFS kernel 4\.19 patch"\n.*?'
        r'(?=python3 - "\$KERNEL_DIR/include/linux/sched/user\.h" <<\'USERPY\')',
        re.S,
    )
    text, count = clone_pattern.subn(NEW_CLONE, text, count=1)
    if count != 1:
        raise SystemExit(f"legacy SUSFS clone block: expected one match, found {count}")

    user_pattern = re.compile(
        r'python3 - "\$KERNEL_DIR/include/linux/sched/user\.h" <<\'USERPY\'\n.*?\nUSERPY\n',
        re.S,
    )
    text, count = user_pattern.subn("", text, count=1)
    if count != 1:
        raise SystemExit(f"legacy SUSFS user_struct block: expected one match, found {count}")

    validation_pattern = re.compile(
        r'grep -Fq \'unsigned long android_kabi_reserved1;\' '
        r'"\$KERNEL_DIR/include/linux/sched/user\.h" \|\| \\\n'
        r'  fail "SUSFS user state field is missing"\n'
    )
    text, count = validation_pattern.subn("", text, count=1)
    if count != 1:
        raise SystemExit(f"legacy SUSFS user-state validation: expected one match, found {count}")

    replace_anchor = '    text = text.replace(old_config, new_config, 1)\n'
    text = replace_once(
        text,
        replace_anchor,
        replace_anchor + CONFIG_EXTENSION,
        "ReSukiSU SUSFS config replacement anchor",
    )

    text = text.replace("v1.4.2", "v1.5.9")
    text = text.replace(
        '''  printf 'susfs_version=%s\\n' "$SUSFS_VERSION"''',
        '''  printf 'susfs_version=%s\\n' "v1.5.9-kernel-4.19"\n  printf 'susfs_commit=%s\\n' "$SUSFS_COMMIT"''',
        1,
    )
    path.write_text(text)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--patch-helper":
        patch_helper(Path(sys.argv[2]))
        return
    if len(sys.argv) == 2:
        patch_wrapper(Path(sys.argv[1]))
        return
    raise SystemExit(
        "usage: 55_transform_resukisu_susfs_wrapper.py [--patch-helper] PATH"
    )


if __name__ == "__main__":
    main()
