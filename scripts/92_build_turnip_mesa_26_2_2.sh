#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
NDK="${2:-${ANDROID_NDK_ROOT:-}}"
OUT="${3:-$ROOT/artifacts/turnip-mesa-26.2.2}"

MESA_VERSION="26.2.2"
MESA_SHA256="eeb29ca7e56cfaa8e8a79538dcf834e3b18e501c31bef5145e959ea437cc4216"
MESA_URL="https://archive.mesa3d.org/mesa-${MESA_VERSION}.tar.xz"
ANDROID_API="36"

if [ -z "$NDK" ] || [ ! -d "$NDK/toolchains/llvm/prebuilt/linux-x86_64" ]; then
  echo "invalid Android NDK path: $NDK" >&2
  exit 2
fi

mkdir -p "$OUT"
WORK="$OUT/work"
SRC="$WORK/mesa-${MESA_VERSION}"
BUILD="$WORK/build-aarch64"
DIST="$OUT/dist"
rm -rf "$WORK" "$DIST"
mkdir -p "$WORK" "$DIST"

echo "==> Fetch Mesa ${MESA_VERSION}"
curl -fL --retry 5 --retry-delay 3 "$MESA_URL" -o "$WORK/mesa.tar.xz"
echo "$MESA_SHA256  $WORK/mesa.tar.xz" | sha256sum -c -
tar -C "$WORK" -xf "$WORK/mesa.tar.xz"

echo "==> Verify A619 + KGSL source support"
grep -Fq 'GPUId(619)' "$SRC/src/freedreno/common/freedreno_devices.py"
grep -Fq "freedreno_kmds.contains('kgsl')" "$SRC/src/freedreno/vulkan/meson.build"
grep -Fq "libtu_files += files('tu_knl_kgsl.cc')" "$SRC/src/freedreno/vulkan/meson.build"
grep -Fq 'PUBLIC struct hwvulkan_module_t HAL_MODULE_INFO_SYM' "$SRC/src/vulkan/runtime/vk_android.c"

echo "==> Backport Android YV12 explicit-layout support for Mesa 26.2.2"
python3 - "$SRC" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])

def replace_once(path, old, new, label):
    p = src / path
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {count}")
    p.write_text(text.replace(old, new, 1))

# Backport the FDL part of Turnip-Enhanced commit
# aeaf924c56adf7eddb0a9033b33474b48367e33d onto Mesa 26.2.2.
# The source commit is based on a newer Mesa tree, so applying the full patch
# directly is intentionally avoided.  These changes are limited to exact
# imported level-0 pitch preservation and the narrowly validated Android YV12
# AHB shape that our SurfaceFlinger probe reproduces.

replace_once(
    "src/freedreno/fdl/freedreno_layout.h",
    """struct fdl_explicit_layout {
   uint32_t offset;
   uint32_t pitch;
};""",
    """struct fdl_explicit_layout {
   uint32_t offset;
   uint32_t pitch;

   /* Optional validation alignment for an imported single-level linear
    * image. Zero keeps the ordinary hardware-layout requirement.
    */
   uint32_t pitch_alignment;

   /* Imported sampled-only Android images do not need Turnip's private
    * four-row tail padding. This is only accepted for a single-level,
    * single-layer, linear, non-UBWC image.
    */
   bool skip_last_level_padding;
};""",
    "fdl_explicit_layout fields",
)

replace_once(
    "src/freedreno/fdl/freedreno_layout.h",
    """   bool ubwc : 1;
   bool layer_first : 1; /* see above description */
   bool tile_all : 1;
   bool is_mutable : 1;
""",
    """   bool ubwc : 1;
   bool layer_first : 1; /* see above description */
   bool tile_all : 1;
   bool is_mutable : 1;
   bool has_explicit_pitch : 1;
""",
    "fdl_layout explicit pitch flag",
)

replace_once(
    "src/freedreno/fdl/freedreno_layout.h",
    """static inline uint32_t
fdl_pitch(const struct fdl_layout *layout, unsigned level)
{
   return align(u_minify(layout->pitch0, level), 1 << layout->pitchalign);
}""",
    """static inline uint32_t
fdl_pitch(const struct fdl_layout *layout, unsigned level)
{
   /* An imported level-0 row pitch is authoritative. pitchalign remains the
    * minimum derived-mip pitch encoded in the descriptor.
    */
   if (level == 0 && layout->has_explicit_pitch)
      return layout->pitch0;

   return align(u_minify(layout->pitch0, level), 1 << layout->pitchalign);
}""",
    "fdl_pitch explicit level0",
)

replace_once(
    "src/freedreno/fdl/fd6_layout.c",
    """   if (explicit_layout) {
      offset = explicit_layout->offset;
      layout->pitch0 = explicit_layout->pitch;
      if (align(layout->pitch0, 1 << layout->pitchalign) != layout->pitch0)
         return false;
   }""",
    """   if (explicit_layout) {
      offset = explicit_layout->offset;
      layout->pitch0 = explicit_layout->pitch;
      layout->has_explicit_pitch = true;

      if (explicit_layout->skip_last_level_padding &&
          (layout->tile_mode != TILE6_LINEAR || layout->ubwc ||
           params->mip_levels != 1 || params->array_size != 1 ||
           params->depth0 != 1 || params->is_3d))
         return false;

      uint32_t pitch_alignment = 1u << layout->pitchalign;
      if (explicit_layout->pitch_alignment) {
         if (layout->tile_mode != TILE6_LINEAR || params->mip_levels != 1 ||
             !util_is_power_of_two_nonzero(explicit_layout->pitch_alignment) ||
             explicit_layout->pitch_alignment < layout->cpp)
            return false;

         pitch_alignment = explicit_layout->pitch_alignment;
      }

      if (align(layout->pitch0, pitch_alignment) != layout->pitch0)
         return false;
   }""",
    "fdl explicit pitch validation",
)

replace_once(
    "src/freedreno/fdl/fd6_layout.c",
    """      if (level == params->mip_levels - 1)
         nblocksy = align(nblocksy, 4);""",
    """      if (level == params->mip_levels - 1 &&
          !(explicit_layout && explicit_layout->skip_last_level_padding))
         nblocksy = align(nblocksy, 4);""",
    "fdl imported tail padding",
)

tu = src / "src/freedreno/vulkan/tu_image.cc"
text = tu.read_text()
anchor = """template <chip CHIP>
VkResult
tu_image_init(struct tu_device *device, struct tu_image *image,
              const VkImageCreateInfo *pCreateInfo, uint64_t modifier,
              const VkSubresourceLayout *plane_layouts)
{"""
if text.count(anchor) != 1:
    raise SystemExit(f"tu_image_init anchor count: {text.count(anchor)}")

helper = r"""static bool
tu_is_android_yv12_import(struct tu_image *image, uint64_t modifier,
                          const VkSubresourceLayout *plane_layouts)
{
   const bool is_android_buffer =
      vk_image_is_android_hardware_buffer(&image->vk) ||
      vk_image_is_android_native_buffer(&image->vk) ||
      vk_image_is_android_native_buffer_alias(&image->vk);

   if (!plane_layouts || modifier != DRM_FORMAT_MOD_LINEAR ||
       !is_android_buffer ||
       image->vk.format != VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM ||
       image->vk.image_type != VK_IMAGE_TYPE_2D ||
       image->vk.samples != VK_SAMPLE_COUNT_1_BIT ||
       image->vk.mip_levels != 1 || image->vk.array_layers != 1 ||
       image->vk.extent.depth != 1 ||
       image->vk.usage != VK_IMAGE_USAGE_SAMPLED_BIT ||
       (image->vk.extent.width & 1) || (image->vk.extent.height & 1))
      return false;

   const uint64_t y_pitch = plane_layouts[0].rowPitch;
   const uint64_t cb_pitch = plane_layouts[1].rowPitch;
   const uint64_t cr_pitch = plane_layouts[2].rowPitch;

   if (!y_pitch || !cb_pitch || y_pitch > UINT32_MAX ||
       cb_pitch > UINT32_MAX || cr_pitch != cb_pitch ||
       y_pitch < image->vk.extent.width ||
       cb_pitch < image->vk.extent.width / 2 ||
       (y_pitch & 15) ||
       cb_pitch != ((y_pitch / 2 + 15) & ~UINT64_C(15)))
      return false;

   const uint64_t height = image->vk.extent.height;
   if (height > UINT64_MAX / y_pitch ||
       height / 2 > UINT64_MAX / cb_pitch)
      return false;

   const uint64_t y_size = y_pitch * height;
   const uint64_t chroma_size = cb_pitch * (height / 2);
   if (y_size > UINT32_MAX ||
       chroma_size > (UINT32_MAX - y_size) / 2)
      return false;

   /* vk_android already converts DRM Y-V-U order into Vulkan Y-U-V order. */
   return plane_layouts[0].offset == 0 &&
          plane_layouts[2].offset == y_size &&
          plane_layouts[1].offset == y_size + chroma_size;
}

"""
text = text.replace(anchor, helper + anchor, 1)

anchor2 = """   assert(!(image->vk.create_flags & VK_IMAGE_CREATE_SPARSE_RESIDENCY_BIT) ||
          tile_mode == TILE6_3);

   for (uint32_t i = 0; i < tu6_plane_count(image->vk.format); i++) {"""
new2 = """   assert(!(image->vk.create_flags & VK_IMAGE_CREATE_SPARSE_RESIDENCY_BIT) ||
          tile_mode == TILE6_3);

   /* Android YV12 guarantees 16-byte row-pitch alignment. Only relax the
    * imported-layout validation for the exact sampled-only linear AHB shape.
    */
   const bool android_yv12_import =
      tu_is_android_yv12_import(image, modifier, plane_layouts);

   for (uint32_t i = 0; i < tu6_plane_count(image->vk.format); i++) {"""
if text.count(anchor2) != 1:
    raise SystemExit(f"YV12 insertion anchor count: {text.count(anchor2)}")
text = text.replace(anchor2, new2, 1)

anchor3 = """      struct fdl_explicit_layout plane_layout;

      if (plane_layouts) {"""
new3 = """      struct fdl_explicit_layout plane_layout = {
         .pitch_alignment = android_yv12_import ? 16u : 0u,
         .skip_last_level_padding = android_yv12_import,
      };

      if (plane_layouts) {"""
if text.count(anchor3) != 1:
    raise SystemExit(f"plane layout init anchor count: {text.count(anchor3)}")
text = text.replace(anchor3, new3, 1)
tu.write_text(text)
PY

grep -Fq 'bool has_explicit_pitch : 1;' "$SRC/src/freedreno/fdl/freedreno_layout.h"
grep -Fq 'if (level == 0 && layout->has_explicit_pitch)' "$SRC/src/freedreno/fdl/freedreno_layout.h"
grep -Fq 'pitch_alignment = android_yv12_import ? 16u : 0u' "$SRC/src/freedreno/vulkan/tu_image.cc"
grep -Fq 'skip_last_level_padding = android_yv12_import' "$SRC/src/freedreno/vulkan/tu_image.cc"
grep -Fq 'tu_is_android_yv12_import' "$SRC/src/freedreno/vulkan/tu_image.cc"

echo "==> Cap Turnip bring-up to Vulkan 1.3"
python3 - "$SRC" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dev = src / "src/freedreno/vulkan/tu_device.cc"
meson = src / "src/freedreno/vulkan/meson.build"

s = dev.read_text()
old = "#define TU_API_VERSION VK_MAKE_VERSION(1, 4, VK_HEADER_VERSION)"
new = "#define TU_API_VERSION VK_MAKE_VERSION(1, 3, VK_HEADER_VERSION)"
if s.count(old) != 1:
    raise SystemExit(f"unexpected TU_API_VERSION anchor count: {s.count(old)}")
dev.write_text(s.replace(old, new, 1))

s = meson.read_text()
old = "'--api-version', '1.4'"
if s.count(old) != 2:
    raise SystemExit(f"unexpected ICD api-version anchor count: {s.count(old)}")
meson.write_text(s.replace(old, "'--api-version', '1.3'"))
PY

grep -Fq '#define TU_API_VERSION VK_MAKE_VERSION(1, 3, VK_HEADER_VERSION)'   "$SRC/src/freedreno/vulkan/tu_device.cc"
test "$(grep -F "'--api-version', '1.3'" "$SRC/src/freedreno/vulkan/meson.build" | wc -l)" -eq 2

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
CROSS="$WORK/android-aarch64.ini"
cat > "$CROSS" <<EOF
[constants]
ndk_path = '$NDK'
toolchain = ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64'

[binaries]
ar = toolchain / 'bin/llvm-ar'
c = ['ccache', toolchain / 'bin/aarch64-linux-android${ANDROID_API}-clang']
cpp = ['ccache', toolchain / 'bin/aarch64-linux-android${ANDROID_API}-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = 'lld'
cpp_ld = 'lld'
strip = toolchain / 'bin/llvm-strip'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
EOF

echo "==> Configure Mesa Turnip A619/KGSL"
meson setup "$BUILD" "$SRC"   --cross-file "$CROSS"   --buildtype release   -Db_ndebug=true   -Dplatforms=android   -Dplatform-sdk-version="$ANDROID_API"   -Dandroid-stub=true   -Dandroid-libbacktrace=disabled   -Dandroid-libperfetto=disabled   -Dexpat=disabled   -Dxmlconfig=disabled   -Degl=disabled   -Dgles1=disabled   -Dgles2=disabled   -Dopengl=false   -Dgbm=disabled   -Dglx=disabled   -Dgallium-drivers=   -Dvulkan-drivers=freedreno   -Dvulkan-layers=   -Dfreedreno-kmds=kgsl   -Dllvm=disabled   -Dvalgrind=disabled   -Dlibunwind=disabled   -Dlmsensors=disabled   -Dperfetto=false   -Dzstd=disabled   -Dzlib=enabled   -Dbuild-tests=false   -Dtools=   -Dvideo-codecs=   -Dtu-build-id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac

echo "==> Compile Turnip"
meson compile -C "$BUILD"

DRIVER="$BUILD/src/freedreno/vulkan/libvulkan_freedreno.so"
test -s "$DRIVER"

echo "==> Stage Android Vulkan HAL"
cp "$DRIVER" "$DIST/vulkan.adreno.so"
patchelf --set-soname vulkan.adreno.so "$DIST/vulkan.adreno.so"
patchelf --remove-rpath "$DIST/vulkan.adreno.so"
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$DIST/vulkan.adreno.so"

echo "==> Audit HAL"
readelf -d "$DIST/vulkan.adreno.so" | tee "$OUT/readelf-dynamic.txt"
readelf -Ws "$DIST/vulkan.adreno.so" | tee "$OUT/readelf-symbols.txt" >/dev/null
readelf -d "$DIST/vulkan.adreno.so" | grep -Fq '(SONAME)'
readelf -d "$DIST/vulkan.adreno.so" | grep -Fq 'vulkan.adreno.so'
! readelf -d "$DIST/vulkan.adreno.so" | grep -Fq '(RUNPATH)'
readelf -Ws "$DIST/vulkan.adreno.so" | grep -Eq '[[:space:]]HMI$'
for lib in libhardware.so liblog.so libnativewindow.so libsync.so libc.so; do
  readelf -d "$DIST/vulkan.adreno.so" | grep -Fq "Shared library: [$lib]"
done
file "$DIST/vulkan.adreno.so" | tee "$OUT/file.txt"
file "$DIST/vulkan.adreno.so" | grep -Fq 'ARM aarch64'
sha256sum "$DIST/vulkan.adreno.so" | tee "$OUT/vulkan.adreno.so.sha256"

echo "==> Build on-device Vulkan probe"
PROBE_SRC="$ROOT/scripts/92_turnip_vk_probe.c"
PROBE="$DIST/turnip-vk-probe"
test -s "$PROBE_SRC"
"$TOOLCHAIN/bin/aarch64-linux-android${ANDROID_API}-clang" \
  -O2 -Wall -Wextra -Werror \
  "$PROBE_SRC" -o "$PROBE" -lvulkan
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$PROBE"
chmod 0755 "$PROBE"
file "$PROBE" | tee "$OUT/probe-file.txt"
file "$PROBE" | grep -Fq 'ARM aarch64'
readelf -d "$PROBE" | tee "$OUT/probe-readelf-dynamic.txt"
readelf -d "$PROBE" | grep -Fq 'Shared library: [libvulkan.so]'
sha256sum "$PROBE" | tee "$OUT/turnip-vk-probe.sha256"

echo "==> Build on-device Android hardware-buffer import probe"
AHB_PROBE_SRC="$ROOT/scripts/92_turnip_ahb_probe.c"
AHB_PROBE="$DIST/turnip-ahb-probe"
test -s "$AHB_PROBE_SRC"
"$TOOLCHAIN/bin/aarch64-linux-android${ANDROID_API}-clang" \
  -O2 -Wall -Wextra -Werror \
  "$AHB_PROBE_SRC" -o "$AHB_PROBE" -lvulkan -landroid
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$AHB_PROBE"
chmod 0755 "$AHB_PROBE"
file "$AHB_PROBE" | tee "$OUT/ahb-probe-file.txt"
file "$AHB_PROBE" | grep -Fq 'ARM aarch64'
readelf -d "$AHB_PROBE" | tee "$OUT/ahb-probe-readelf-dynamic.txt"
readelf -d "$AHB_PROBE" | grep -Fq 'Shared library: [libvulkan.so]'
readelf -d "$AHB_PROBE" | grep -Fq 'Shared library: [libandroid.so]'
sha256sum "$AHB_PROBE" | tee "$OUT/turnip-ahb-probe.sha256"

echo "==> Build on-device YV12 GPU sampling probe"
YV12_SHADER_SRC="$ROOT/scripts/92_turnip_yv12_sample.comp"
YV12_PROBE_SRC="$ROOT/scripts/92_turnip_yv12_sample_probe.c"
YV12_SPV="$OUT/yv12_sample.spv"
YV12_HDR="$OUT/yv12_sample_spv.h"
YV12_PROBE="$DIST/turnip-yv12-sample-probe"
test -s "$YV12_SHADER_SRC"
test -s "$YV12_PROBE_SRC"
glslangValidator -V --target-env vulkan1.1 -S comp \
  "$YV12_SHADER_SRC" -o "$YV12_SPV"
python3 - "$YV12_SPV" "$YV12_HDR" <<'PY'
from pathlib import Path
import struct
import sys

spv = Path(sys.argv[1]).read_bytes()
if len(spv) % 4:
    raise SystemExit("SPIR-V size is not uint32 aligned")
words = struct.unpack("<%dI" % (len(spv) // 4), spv)
with open(sys.argv[2], "w") as out:
    out.write("#pragma once\n#include <stddef.h>\n#include <stdint.h>\n")
    out.write("static const uint32_t yv12_sample_spv[] = {\n")
    for i in range(0, len(words), 8):
        chunk = words[i:i+8]
        out.write("    " + ", ".join(f"0x{w:08x}u" for w in chunk) + ",\n")
    out.write("};\n")
    out.write("static const size_t yv12_sample_spv_size = sizeof(yv12_sample_spv);\n")
PY
"$TOOLCHAIN/bin/aarch64-linux-android${ANDROID_API}-clang" \
  -O2 -Wall -Wextra -Werror -I"$OUT" \
  "$YV12_PROBE_SRC" -o "$YV12_PROBE" -lvulkan -landroid
"$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$YV12_PROBE"
chmod 0755 "$YV12_PROBE"
file "$YV12_PROBE" | tee "$OUT/yv12-sample-probe-file.txt"
file "$YV12_PROBE" | grep -Fq 'ARM aarch64'
readelf -d "$YV12_PROBE" | tee "$OUT/yv12-sample-probe-readelf-dynamic.txt"
readelf -d "$YV12_PROBE" | grep -Fq 'Shared library: [libvulkan.so]'
readelf -d "$YV12_PROBE" | grep -Fq 'Shared library: [libandroid.so]'
sha256sum "$YV12_PROBE" "$YV12_SPV" | tee "$OUT/turnip-yv12-sample-probe.sha256"

cat > "$OUT/BUILD-INFO.txt" <<EOF
project=touchGrass Turnip A619 KGSL bring-up
mesa_version=26.2.2
mesa_archive_sha256=$MESA_SHA256
mesa_release_commit=3281a69a8bfd9f997e91c15ed0e6290cae12dd32
gpu=Adreno 619
kmd=KGSL
android_api=36
ndk=$(basename "$NDK")
turnip_api_cap=Vulkan-1.3
turnip_upstream_api=Vulkan-1.4
driver_filename=vulkan.adreno.so
soname=vulkan.adreno.so
architecture=aarch64
probe=turnip-vk-probe
probe_api_request=Vulkan-1.3
probe_mode=device-submit-memory-verify-offscreen-dynamic-render-readback
ahb_probe=turnip-ahb-probe
ahb_probe_mode=rgba-yuv420-yv12-import-bind-lifetime-forensics
yv12_sample_probe=turnip-yv12-sample-probe
yv12_sample_mode=940x1670-postfill-vs-importfirst-stock-reference
android_yv12_fix=mesa-26.2.2-explicit-layout-16byte-pitch
android_yv12_reference_commit=aeaf924c56adf7eddb0a9033b33474b48367e33d
build_id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac
EOF

echo "==> Turnip build complete"
