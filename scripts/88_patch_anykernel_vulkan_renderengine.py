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
  local modzip=\\$AKHOME/touchgrass-vulkan-re-module.zip
  local ksud=""
  local moddir=/data/adb/modules/touchgrass-vulkan-re

  [ -x /data/adb/ksud ] && ksud=/data/adb/ksud
  [ ! "\\$ksud" ] && command -v ksud >/dev/null 2>&1 && ksud=\\$(command -v ksud)

  if [ "\\$ksud" ]; then
    ui_print "Installing touchGrass Vulkan RE KernelSU module...";
    "\\$ksud" module install "\\$modzip" || abort "KernelSU module install failed. Aborting before kernel flash...";
    "\\$ksud" initrc refresh >/dev/null 2>&1 || true;
    return 0;
  fi

  if [ -d /data/adb ] && mkdir -p "\\$moddir" 2>/dev/null; then
    ui_print "KernelSU CLI unavailable; installing Vulkan RE module directly...";
    rm -rf "\\$moddir";
    mkdir -p "\\$moddir" || abort "Cannot create KernelSU module directory. Aborting before kernel flash...";
    unzip -oq "\\$modzip" -d "\\$moddir" || abort "Cannot extract Vulkan RE module. Aborting before kernel flash...";
    chown -R 0:0 "\\$moddir" 2>/dev/null || true;
    chmod 755 "\\$moddir" "\\$moddir/initrc" 2>/dev/null || true;
    chmod 644 "\\$moddir/module.prop" "\\$moddir/system.prop" "\\$moddir/initrc/touchgrass-vulkan-re.rc" 2>/dev/null || true;
    return 0;
  fi

  abort "KernelSU /data/adb is unavailable; refusing to flash without Vulkan RE persistence.";
}

dump_boot;''',
    "KernelSU module installer",
)

replace_once(
    'patch_cmdline "sched_cass" "sched_cass=1";\nwrite_boot;',
    'patch_cmdline "sched_cass" "sched_cass=1";\n'
    'install_vulkan_re_module;\n'
    'write_boot;',
    "AnyKernel Vulkan install hook",
)

replace_once(
    'cp "$IMAGE_PATH" "$PACKAGE_DIR/Image"\n',
    '''mkdir -p "$PACKAGE_DIR/vulkan_module/initrc"
cat > "$PACKAGE_DIR/vulkan_module/module.prop" <<'EOF_VULKAN_MODULE'
id=touchgrass-vulkan-re
name=touchGrass SurfaceFlinger Vulkan RenderEngine
version=1.0
versionCode=1
author=touchGrass A52xq
description=Sets SurfaceFlinger RenderEngine to skiavkthreaded at early init
EOF_VULKAN_MODULE
cat > "$PACKAGE_DIR/vulkan_module/system.prop" <<'EOF_VULKAN_PROP'
debug.renderengine.backend=skiavkthreaded
EOF_VULKAN_PROP
cat > "$PACKAGE_DIR/vulkan_module/initrc/touchgrass-vulkan-re.rc" <<'EOF_VULKAN_RC'
# touchGrass A52xq SurfaceFlinger Vulkan RenderEngine
on early-init
    setprop debug.renderengine.backend skiavkthreaded
EOF_VULKAN_RC
touch "$PACKAGE_DIR/vulkan_module/skip_mount"
(
  cd "$PACKAGE_DIR/vulkan_module"
  zip -qr9 ../touchgrass-vulkan-re-module.zip .
)
rm -rf "$PACKAGE_DIR/vulkan_module"

cp "$IMAGE_PATH" "$PACKAGE_DIR/Image"
''',
    "KernelSU module payload creation",
)

replace_once(
    'physical_boot_test=not-performed\nEOF\n',
    'surfaceflinger_renderengine=skiavkthreaded\n'
    'surfaceflinger_vulkan_integration=kernelsu-initrc-plus-system-prop\n'
    'physical_boot_test=not-performed\nEOF\n',
    "BUILD-INFO metadata",
)

replace_once(
    'zip -r9 "$OUTPUT_ZIP" anykernel.sh Image BUILD-INFO.txt META-INF tools\n',
    'zip -r9 "$OUTPUT_ZIP" anykernel.sh Image BUILD-INFO.txt META-INF tools touchgrass-vulkan-re-module.zip\n',
    "ZIP payload list",
)

replace_once(
    '        "tools/ak3-core.sh",\n    }',
    '        "tools/ak3-core.sh",\n'
    '        "touchgrass-vulkan-re-module.zip",\n'
    '    }',
    "ZIP verifier required entries",
)

replace_once(
    '        "write_boot;",\n    )',
    '        "install_vulkan_re_module;",\n'
    '        "write_boot;",\n'
    '    )',
    "AnyKernel verifier checks",
)

replace_once(
    'from __future__ import annotations\n\nimport hashlib\nimport stat\n',
    'from __future__ import annotations\n\nimport hashlib\nimport io\nimport stat\n',
    "ZIP verifier io import",
)

replace_once(
    '    embedded_hash = hashlib.sha256(archive.read("Image")).hexdigest()\n',
    '''    module_blob = archive.read("touchgrass-vulkan-re-module.zip")
    with zipfile.ZipFile(io.BytesIO(module_blob)) as module:
        module_names = set(module.namelist())
        required_module = {
            "module.prop",
            "system.prop",
            "initrc/touchgrass-vulkan-re.rc",
            "skip_mount",
        }
        missing_module = sorted(required_module.difference(module_names))
        if missing_module:
            raise SystemExit(f"Vulkan RE KernelSU module missing entries: {missing_module}")
        system_prop = module.read("system.prop").decode("utf-8").strip()
        if system_prop != "debug.renderengine.backend=skiavkthreaded":
            raise SystemExit("Vulkan RE system.prop is incorrect")
        initrc = module.read("initrc/touchgrass-vulkan-re.rc").decode("utf-8")
        if "on early-init" not in initrc or "setprop debug.renderengine.backend skiavkthreaded" not in initrc:
            raise SystemExit("Vulkan RE initrc is incomplete")

    embedded_hash = hashlib.sha256(archive.read("Image")).hexdigest()
''',
    "ZIP verifier Vulkan module",
)

replace_once(
    'Physical boot test: not performed\nEOF\n',
    'SurfaceFlinger RenderEngine: skiavkthreaded\n'
    'Vulkan persistence: KernelSU initrc injection + system.prop fallback\n'
    'Physical boot test: not performed\nEOF\n',
    "verification metadata",
)

path.write_text(text)
print("[A52 Vulkan RE] AnyKernel package script patched")
print("[A52 Vulkan RE] backend=debug.renderengine.backend=skiavkthreaded")
print("[A52 Vulkan RE] persistence=KernelSU initrc injection + system.prop fallback")
