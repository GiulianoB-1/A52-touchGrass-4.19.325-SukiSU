#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: 56_patch_manage_mark_for_manual_susfs.py GENERATED_BUILD_SCRIPT")

    path = Path(sys.argv[1])
    text = path.read_text()

    anchor = "# No unsupported newer SUSFS API may remain.\nunsupported = []\n"
    if text.count(anchor) != 1:
        raise SystemExit(f"unsupported-API scan anchor: expected one match, found {text.count(anchor)}")

    compatibility = r'''# do_manage_mark() treats CONFIG_KSU_SUSFS as the inline-hook method in
# upstream ReSukiSU. In this controlled profile SUSFS is only a feature layer,
# while manual hooks remain the active hook method. Remove the newer unmount-
# state branch so the function keeps the same fallback behavior as the known-
# working manual-hook baseline.
dispatch = resukisu / 'kernel/supercall/dispatch.c'
text = dispatch.read_text()
manage_mark_susfs = ('#elif defined(CONFIG_KSU_SUSFS)\n'
                     '        if (susfs_is_current_proc_umounted()) {\n'
                     '            ret = 0; // SYSCALL_TRACEPOINT is NOT flagged\n'
                     '        } else {\n'
                     '            ret = 1; // SYSCALL_TRACEPOINT is flagged\n'
                     '        }\n'
                     '        pr_info("manage_mark: ret for pid %d: %d\\n", cmd.pid, ret);\n'
                     '        cmd.result = (u32)ret;\n')
if text.count(manage_mark_susfs) != 1:
    raise SystemExit('ReSukiSU manage-mark SUSFS branch mismatch')
dispatch.write_text(text.replace(manage_mark_susfs, '', 1))

'''

    path.write_text(text.replace(anchor, compatibility + anchor, 1))


if __name__ == "__main__":
    main()
