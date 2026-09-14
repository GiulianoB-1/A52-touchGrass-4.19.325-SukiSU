#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 88_patch_anykernel_vulkan_renderengine.py <generated-package-script>")

path = Path(sys.argv[1])
text = path.read_text()

def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    text = text.replace(old, new, 1)

replace_once(
    '. tools/ak3-core.sh;\n\ndump_boot;',
    '''. tools/ak3-core.sh;

install_vulkan_re_module() {
  local moddir=/data/adb/modules/touchgrass-vulkan-re

  ui_print "Installing touchGrass Vulkan RE persistence...";

  [ -d /data/adb ] || abort "/data/adb is unavailable. Aborting before kernel flash...";

  rm -rf "\$moddir" || abort "Cannot remove old Vulkan RE module. Aborting before kernel flash...";
  mkdir -p "\$moddir" || abort "Cannot create Vulkan RE module directory. Aborting before kernel flash...";

  cat > "\$moddir/module.prop" <<'EOF_VULKAN_MODULE'
id=touchgrass-vulkan-re
name=touchGrass SurfaceFlinger Vulkan RenderEngine
version=1.1
versionCode=2
author=touchGrass A52xq
description=Sets SurfaceFlinger RenderEngine to skiavkthreaded using KernelSU system.prop
EOF_VULKAN_MODULE

  cat > "\$moddir/system.prop" <<'EOF_VULKAN_PROP'
debug.renderengine.backend=skiavkthreaded
EOF_VULKAN_PROP

  touch "\$moddir/skip_mount" || abort "Cannot create KernelSU skip_mount flag. Aborting before kernel flash...";

  chown -R 0:0 "\$moddir" 2>/dev/null || true;
  chmod 755 "\$moddir" 2>/dev/null || true;
  chmod 644 "\$moddir/module.prop" "\$moddir/system.prop" "\$moddir/skip_mount" 2>/dev/null || true;

  grep -Fxq 'debug.renderengine.backend=skiavkthreaded' "\$moddir/system.prop" \
    || abort "Vulkan RE property verification failed. Aborting before kernel flash...";

  ui_print "Vulkan RE persistence staged in /data/adb/modules/touchgrass-vulkan-re";
}

dump_boot;''',
    "direct KernelSU module staging",
)

replace_once(
    'patch_cmdline "sched_cass" "sched_cass=1";\nwrite_boot;',
    'patch_cmdline "sched_cass" "sched_cass=1";\n'
    'install_vulkan_re_module;\n'
    'write_boot;',
    "AnyKernel Vulkan install hook",
)

replace_once(
    'physical_boot_test=not-performed\nEOF\n',
    'surfaceflinger_renderengine=skiavkthreaded\n'
    'surfaceflinger_vulkan_integration=kernelsu-direct-system-prop\n'
    'physical_boot_test=not-performed\nEOF\n',
    "BUILD-INFO metadata",
)

replace_once(
    '        "write_boot;",\n    )',
    '        "install_vulkan_re_module;",\n'
    '        "write_boot;",\n'
    '    )',
    "AnyKernel verifier checks",
)

replace_once(
    'Physical boot test: not performed\nEOF\n',
    'SurfaceFlinger RenderEngine: skiavkthreaded\n'
    'Vulkan persistence: direct KernelSU system.prop staging\n'
    'Physical boot test: not performed\nEOF\n',
    "verification metadata",
)

path.write_text(text)
print("[A52 Vulkan RE] AnyKernel package script patched")
print("[A52 Vulkan RE] backend=debug.renderengine.backend=skiavkthreaded")
print("[A52 Vulkan RE] persistence=direct /data/adb/modules system.prop")
