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
    'patch_cmdline "sched_cass" "sched_cass=1";\nwrite_boot;',
    'patch_cmdline "sched_cass" "sched_cass=1";\n'
    'cd "\\$RAMDISK";\n'
    'if [ ! -f init.rc ]; then\n'
    '  abort "touchGrass Vulkan RE: boot ramdisk init.rc not found";\n'
    'fi;\n'
    'prepend_file init.rc "import /init.touchgrass-vulkan.rc" init.touchgrass-vulkan.import;\n'
    'write_boot;',
    "AnyKernel install hook",
)

replace_once(
    'cp "$IMAGE_PATH" "$PACKAGE_DIR/Image"\n',
    'mkdir -p "$PACKAGE_DIR/ramdisk" "$PACKAGE_DIR/patch"\n'
    'cat > "$PACKAGE_DIR/ramdisk/init.touchgrass-vulkan.rc" <<\'EOF_VULKAN_RC\'\n'
    '# touchGrass A52xq: enable Android SurfaceFlinger Vulkan RenderEngine\n'
    '# Runtime-validated on Android 16 / UN1CA with Adreno 619.\n'
    'on early-init\n'
    '    setprop debug.renderengine.backend skiavkthreaded\n'
    'EOF_VULKAN_RC\n'
    '\n'
    'cat > "$PACKAGE_DIR/patch/init.touchgrass-vulkan.import" <<\'EOF_VULKAN_IMPORT\'\n'
    'import /init.touchgrass-vulkan.rc\n'
    'EOF_VULKAN_IMPORT\n'
    '\n'
    'cp "$IMAGE_PATH" "$PACKAGE_DIR/Image"\n',
    "ramdisk payload creation",
)

replace_once(
    'physical_boot_test=not-performed\nEOF\n',
    'surfaceflinger_renderengine=skiavkthreaded\n'
    'surfaceflinger_vulkan_integration=boot-ramdisk-early-init\n'
    'physical_boot_test=not-performed\nEOF\n',
    "BUILD-INFO metadata",
)

replace_once(
    'zip -r9 "$OUTPUT_ZIP" anykernel.sh Image BUILD-INFO.txt META-INF tools\n',
    'zip -r9 "$OUTPUT_ZIP" anykernel.sh Image BUILD-INFO.txt META-INF tools ramdisk patch\n',
    "ZIP payload list",
)

replace_once(
    '        "tools/ak3-core.sh",\n    }',
    '        "tools/ak3-core.sh",\n'
    '        "ramdisk/init.touchgrass-vulkan.rc",\n'
    '        "patch/init.touchgrass-vulkan.import",\n'
    '    }',
    "ZIP verifier required entries",
)

replace_once(
    '        "write_boot;",\n    )',
    '        \'prepend_file init.rc "import /init.touchgrass-vulkan.rc" init.touchgrass-vulkan.import;\',\n'
    '        "write_boot;",\n'
    '    )',
    "AnyKernel verifier checks",
)

replace_once(
    '    embedded_hash = hashlib.sha256(archive.read("Image")).hexdigest()\n',
    '    rc = archive.read("ramdisk/init.touchgrass-vulkan.rc").decode("utf-8")\n'
    '    if "on early-init" not in rc or "setprop debug.renderengine.backend skiavkthreaded" not in rc:\n'
    '        raise SystemExit("Vulkan RenderEngine ramdisk rc is incomplete")\n'
    '\n'
    '    rc_import = archive.read("patch/init.touchgrass-vulkan.import").decode("utf-8").strip()\n'
    '    if rc_import != "import /init.touchgrass-vulkan.rc":\n'
    '        raise SystemExit("Vulkan RenderEngine init import is incorrect")\n'
    '\n'
    '    embedded_hash = hashlib.sha256(archive.read("Image")).hexdigest()\n',
    "ZIP verifier Vulkan payload",
)

replace_once(
    'Physical boot test: not performed\nEOF\n',
    'SurfaceFlinger RenderEngine: skiavkthreaded\n'
    'Vulkan persistence: boot ramdisk early-init hook\n'
    'Physical boot test: not performed\nEOF\n',
    "verification metadata",
)

path.write_text(text)
print("[A52 Vulkan RE] AnyKernel package script patched")
print("[A52 Vulkan RE] backend=debug.renderengine.backend=skiavkthreaded")
print("[A52 Vulkan RE] trigger=early-init")
