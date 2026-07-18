#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def remove_regex_once(text: str, pattern: str, label: str) -> str:
    updated, count = re.subn(pattern, "", text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return updated


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: 56_patch_resukisu_susfs_manual_core.py GENERATED_BUILD_SCRIPT")

    path = Path(sys.argv[1])
    text = path.read_text()

    # Keep the working ReSukiSU automatic LSM setuid hook. The SUSFS generator
    # normally injects a second manual setresuid hook for inline mode.
    text = remove_regex_once(
        text,
        r'''python3 - "\$KERNEL_DIR/kernel/sys\.c" <<'HOOKPY'\n.*?\nHOOKPY\n''',
        "inline setresuid hook block",
    )
    text = re.sub(
        r'''grep -Fq 'ksu_handle_setresuid\(ruid, euid, suid\);' "\$KERNEL_DIR/kernel/sys\.c" \|\| \\\n  fail "ReSukiSU setresuid hook is missing"\n''',
        "",
        text,
        count=1,
    )

    # Keep the working automatic init.rc and input hooks. Remove the explicit
    # read/input hooks that are added only for ReSukiSU's SUSFS-inline mode.
    block_start = '\ninfo "Restoring SUSFS inline read and input hooks after legacy cleanup"\n'
    block_end = '\ninfo "Adding ReSukiSU reboot and fstat hooks"\n'
    start = text.find(block_start)
    end = text.find(block_end, start + 1)
    if start < 0 or end < 0:
        raise SystemExit("inline read/input hook block markers missing")
    text = text[:start] + block_end + text[end + len(block_end):]

    # The old generator adds a per-user field even when SUS_PATH is disabled.
    # Core-only mode removes that structural delta entirely.
    text = remove_regex_once(
        text,
        r'''python3 - "\$KERNEL_DIR/include/linux/sched/user\.h" <<'USERPY'\n.*?\nUSERPY\n''',
        "SUSFS per-user field block",
    )
    text = re.sub(
        r'''grep -Fq 'unsigned long android_kabi_reserved1;' "\$KERNEL_DIR/include/linux/sched/user\.h" \|\| \\\n  fail "SUSFS user state field is missing"\n''',
        "",
        text,
        count=1,
    )

    # Apply only the compatibility needed to compile SUSFS 1.4.2 alongside the
    # exact working manual-hook ReSukiSU configuration.
    insertion_anchor = (
        "sed -i 's/[[:space:]]\\+$//' \"$KERNEL_DIR/fs/namespace.c\" "
        '"$KERNEL_DIR/fs/overlayfs/readdir.c"\n'
    )
    compatibility = r"""
python3 - "$KERNEL_DIR" "$RESUKISU_DIR" <<'MANUALSUSFSCOMPATPY'
from pathlib import Path
import sys

kernel = Path(sys.argv[1])
resukisu = Path(sys.argv[2])

# KSU_SUSFS is a hook choice upstream. Move it outside the choice so manual
# hooks and the SUSFS feature layer can be enabled together.
kconfig = resukisu / 'kernel/Kconfig'
text = kconfig.read_text()
start = text.find('config KSU_SUSFS\n')
end = text.find('\nendchoice\n', start)
if start < 0 or end < 0:
    raise SystemExit('ReSukiSU Kconfig SUSFS choice block not found')
susfs_block = text[start:end]
text = text[:start] + 'endchoice\n\n' + susfs_block + text[end + len('\nendchoice\n'):]
kconfig.write_text(text)

# Kbuild already prefers manual hooks. Include only the lightweight SUSFS
# compatibility flags when both symbols are enabled.
kbuild = resukisu / 'kernel/Kbuild'
text = kbuild.read_text()
old = ('else ifdef CONFIG_KSU_MANUAL_HOOK\n'
       '  $(info -- $(REPO_NAME): using Manual Hook)\n'
       '  include $(KSU_SRC)/tools/manual_hook_check.mk\n'
       'else ifdef CONFIG_KSU_SUSFS\n')
new = ('else ifdef CONFIG_KSU_MANUAL_HOOK\n'
       '  $(info -- $(REPO_NAME): using Manual Hook)\n'
       '  include $(KSU_SRC)/tools/manual_hook_check.mk\n'
       '  ifdef CONFIG_KSU_SUSFS\n'
       '    include $(KSU_SRC)/tools/susfs_compat.mk\n'
       '  endif\n'
       'else ifdef CONFIG_KSU_SUSFS\n')
if text.count(old) != 1:
    raise SystemExit('ReSukiSU Kbuild manual-hook branch mismatch')
kbuild.write_text(text.replace(old, new, 1))

# runtime/ksud_integration.c checks SUSFS before manual hooks. Preserve the
# working manual implementation whenever both are configured.
ksud = resukisu / 'kernel/runtime/ksud_integration.c'
text = ksud.read_text()
old = ('#elif defined(CONFIG_KSU_SUSFS)\n'
       '    DEFINE_STATIC_KEY_TRUE(ksu_is_init_rc_hook_enabled);')
new = ('#elif defined(CONFIG_KSU_SUSFS) && !defined(CONFIG_KSU_MANUAL_HOOK)\n'
       '    DEFINE_STATIC_KEY_TRUE(ksu_is_init_rc_hook_enabled);')
if text.count(old) != 1:
    raise SystemExit('ReSukiSU runtime hook-selection branch mismatch')
ksud.write_text(text.replace(old, new, 1))

# The manual-hook init branch normally skips susfs_init(). Initialize the
# standalone SUSFS feature layer without changing the working hook path.
core_init = resukisu / 'kernel/core/init.c'
text = core_init.read_text()
old = ('#elif defined(CONFIG_KSU_MANUAL_HOOK)\n'
       '// only lsm hook need call init\n'
       '#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 8, 0)\n'
       '    ksu_lsm_hook_built_in_init();\n'
       '#endif\n'
       '#elif defined(CONFIG_KSU_SUSFS)\n')
new = ('#elif defined(CONFIG_KSU_MANUAL_HOOK)\n'
       '// only lsm hook need call init\n'
       '#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 8, 0)\n'
       '    ksu_lsm_hook_built_in_init();\n'
       '#endif\n'
       '#ifdef CONFIG_KSU_SUSFS\n'
       '    susfs_init();\n'
       '#endif\n'
       '#elif defined(CONFIG_KSU_SUSFS)\n')
if text.count(old) != 1:
    raise SystemExit('ReSukiSU core init manual/SUSFS branch mismatch')
core_init.write_text(text.replace(old, new, 1))

# SUSFS 1.4.2 exposes susfs.h rather than the newer susfs_def.h API.
susfs_header = kernel / 'include/linux/susfs.h'
text = susfs_header.read_text()
if '#define SUSFS_MAGIC 0xFAFAFAFA\n' not in text:
    guard = '#define KSU_SUSFS_H\n'
    if text.count(guard) != 1:
        raise SystemExit('SUSFS header guard mismatch')
    text = text.replace(guard, guard + '\n#define SUSFS_MAGIC 0xFAFAFAFA\n', 1)
    susfs_header.write_text(text)

susfs_c = kernel / 'fs/susfs.c'
text = susfs_c.read_text()
text = text.replace('#include "../drivers/kernelsu/core_hook.h"\n', '')
susfs_c.write_text(text)

# Adapt ReSukiSU source references to the older SUSFS API.
old_include = '#include <linux/susfs_def.h>\n'
for source in (resukisu / 'kernel').rglob('*'):
    if not source.is_file() or source.suffix not in {'.c', '.h'}:
        continue
    content = source.read_text()
    updated = content.replace(old_include, '#include <linux/susfs.h>\n')
    if updated != content:
        source.write_text(updated)

sucompat = resukisu / 'kernel/feature/sucompat.c'
text = sucompat.read_text()
umount_state = ('#ifdef CONFIG_KSU_SUSFS\n'
                '            if (!susfs_is_current_proc_umounted())\n'
                '                susfs_set_current_proc_umounted();\n'
                '#endif\n')
if text.count(umount_state) == 1:
    text = text.replace(umount_state, '', 1)
sucompat.write_text(text)

kernel_umount = resukisu / 'kernel/feature/kernel_umount.c'
text = kernel_umount.read_text()
extern_block = ('#ifdef CONFIG_KSU_SUSFS\n'
                'extern struct work_struct susfs_extra_works;\n'
                '#endif\n')
state_block = ('    // do susfs setuid when susfs enabled\n'
               '#ifdef CONFIG_KSU_SUSFS\n'
               '    schedule_work(&susfs_extra_works);\n'
               '    susfs_set_current_proc_umounted();\n'
               '#endif\n')
if text.count(extern_block) != 1:
    raise SystemExit('unsupported susfs_extra_works declaration mismatch')
if text.count(state_block) != 1:
    raise SystemExit('unsupported SUSFS unmount-state block mismatch')
text = text.replace(extern_block, '', 1).replace(state_block, '', 1)
kernel_umount.write_text(text)

# Remove the newer monitor call and translate the dispatcher to 1.4.2 APIs.
dispatch = resukisu / 'kernel/supercall/dispatch.c'
text = dispatch.read_text()
text = text.replace(
    '#ifdef CONFIG_KSU_SUSFS\n                susfs_start_sdcard_monitor_fn();\n#endif\n',
    '',
    1,
)
start_marker = '#ifdef CONFIG_KSU_SUSFS\nint ksu_handle_susfs_cmd(unsigned int cmd, void __user **arg)\n'
end_marker = '#endif\n\n#ifdef CONFIG_KSU_TOOLKIT_SUPPORT\n'
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit('ReSukiSU SUSFS dispatcher block not found')
replacement = '''#ifdef CONFIG_KSU_SUSFS
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

#ifdef CONFIG_KSU_TOOLKIT_SUPPORT
'''
dispatch.write_text(text[:start] + replacement + text[end + len(end_marker):])

# No unsupported newer SUSFS API may remain.
unsupported = []
for source in (resukisu / 'kernel').rglob('*'):
    if not source.is_file() or source.suffix not in {'.c', '.h'}:
        continue
    content = source.read_text()
    for symbol in ('susfs_def.h', 'susfs_is_current_proc_umounted',
                   'susfs_set_current_proc_umounted', 'susfs_extra_works'):
        if symbol in content:
            unsupported.append(f'{source}:{symbol}')
if unsupported:
    raise SystemExit('unsupported SUSFS API remains: ' + ', '.join(unsupported))
MANUALSUSFSCOMPATPY
"""
    text = replace_once(
        text,
        insertion_anchor,
        insertion_anchor + compatibility,
        "SUSFS compatibility insertion anchor",
    )

    # Keep manual hooks selected while enabling only the SUSFS core symbol.
    inline_config = (
        '  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -d KSU_MANUAL_HOOK \\\n'
        '  -e KSU_SUSFS -d KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
        '  -d KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -d KSU_MANUAL_HOOK_AUTO_INPUT_HOOK'
    )
    manual_core_config = (
        '  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -e KSU_MANUAL_HOOK \\\n'
        '  -e KSU_SUSFS -e KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
        '  -e KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -e KSU_MANUAL_HOOK_AUTO_INPUT_HOOK \\\n'
        '  -d KSU_SUSFS_SUS_PATH -d KSU_SUSFS_SUS_MOUNT \\\n'
        '  -d KSU_SUSFS_SUS_KSTAT -d KSU_SUSFS_SPOOF_UNAME \\\n'
        '  -d KSU_SUSFS_ENABLE_LOG -d KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS \\\n'
        '  -d KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG -d KSU_SUSFS_OPEN_REDIRECT \\\n'
        '  -d KSU_SUSFS_SUS_MAP'
    )
    text = replace_once(
        text,
        inline_config,
        manual_core_config,
        "ReSukiSU hybrid config block",
    )

    # Return source checks to the exact working automatic-hook expectations.
    inline_validation = (
        '! grep -Fq \'ksu_vfs_read_hook\' "$KERNEL_DIR/fs/read_write.c" || fail "Incompatible legacy ReSukiSU read-hook flag remains"\n'
        'grep -Fq \'ksu_handle_sys_read(fd, &buf, &count);\' "$KERNEL_DIR/fs/read_write.c" || fail "ReSukiSU sys_read hook is missing"'
    )
    manual_validation = (
        'require_absent "$KERNEL_DIR/fs/read_write.c" "ksu_vfs_read_hook"\n'
        'require_absent "$KERNEL_DIR/fs/read_write.c" "ksu_handle_sys_read(fd, &buf, &count);"\n'
        'require_absent "$KERNEL_DIR/drivers/input/input.c" "ksu_handle_input_handle_event(&type, &code, &value);"'
    )
    text = replace_once(
        text,
        inline_validation,
        manual_validation,
        "inline hook validation block",
    )

    text = text.replace(
        'resukisu-v4.1.0-susfs-v1.4.2-safe',
        'resukisu-v4.1.0-susfs-v1.4.2-manual-core',
    )
    text = replace_once(
        text,
        'require_line "$FINAL_CONFIG" \'# CONFIG_KSU_MANUAL_HOOK is not set\'',
        'require_line "$FINAL_CONFIG" \'CONFIG_KSU_MANUAL_HOOK=y\'',
        "final manual-hook assertion",
    )

    config_assert_anchor = 'require_line "$FINAL_CONFIG" \'CONFIG_KSU_SUSFS=y\'\n'
    config_assertions = '''require_line "$FINAL_CONFIG" 'CONFIG_KSU_MANUAL_HOOK_AUTO_SETUID_HOOK=y'
require_line "$FINAL_CONFIG" 'CONFIG_KSU_MANUAL_HOOK_AUTO_INITRC_HOOK=y'
require_line "$FINAL_CONFIG" 'CONFIG_KSU_MANUAL_HOOK_AUTO_INPUT_HOOK=y'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_SUS_PATH is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_SUS_MOUNT is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_SUS_KSTAT is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_SPOOF_UNAME is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_ENABLE_LOG is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_OPEN_REDIRECT is not set'
require_line "$FINAL_CONFIG" '# CONFIG_KSU_SUSFS_SUS_MAP is not set'
'''
    text = replace_once(
        text,
        config_assert_anchor,
        config_assert_anchor + config_assertions,
        "final SUSFS config assertion anchor",
    )

    report_anchor = "  printf 'susfs_version=%s\\n' \"$SUSFS_VERSION\"\n"
    report_extension = (
        "  printf 'hook_mode=manual-auto\\n'\n"
        "  printf 'susfs_profile=core-only-all-features-off\\n'\n"
    )
    text = replace_once(
        text,
        report_anchor,
        report_anchor + report_extension,
        "build report SUSFS anchor",
    )

    # Include every hybrid compatibility edit in the diagnostic patch.
    old_diff = (
        'git -C "$RESUKISU_DIR" diff --binary -- kernel/supercall/dispatch.c '
        'kernel/policy/allowlist.c kernel/feature/kernel_umount.c > "$RESUKISU_PATCH"'
    )
    new_diff = (
        'git -C "$RESUKISU_DIR" diff --binary -- kernel/Kconfig kernel/Kbuild '
        'kernel/core/init.c kernel/runtime/ksud_integration.c kernel/supercall/dispatch.c '
        'kernel/policy/allowlist.c kernel/feature/sucompat.c '
        'kernel/feature/kernel_umount.c > "$RESUKISU_PATCH"'
    )
    text = replace_once(text, old_diff, new_diff, "ReSukiSU diagnostic diff list")

    path.write_text(text)


if __name__ == "__main__":
    main()
