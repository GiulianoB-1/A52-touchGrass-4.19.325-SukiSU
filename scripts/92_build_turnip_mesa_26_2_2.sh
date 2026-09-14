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
build_id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac
EOF

echo "==> Turnip build complete"
