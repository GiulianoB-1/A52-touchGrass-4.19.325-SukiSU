#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$1"
NDK="$2"
OUT="$3"

MESA_VERSION="26.2.2"
MESA_SHA256="eeb29ca7e56cfaa8e8a79538dcf834e3b18e501c31bef5145e959ea437cc4216"
MESA_URL="https://archive.mesa3d.org/mesa-$MESA_VERSION.tar.xz"
ANDROID_API="36"

if [ -z "$NDK" ] || [ ! -d "$NDK/toolchains/llvm/prebuilt/linux-x86_64" ]; then
  echo "invalid Android NDK path: $NDK" >&2
  exit 2
fi

mkdir -p "$OUT"
WORK="$OUT/work"
SRC="$WORK/mesa-$MESA_VERSION"
rm -rf "$WORK" "$OUT/arm64" "$OUT/arm32"
mkdir -p "$WORK" "$OUT/arm64" "$OUT/arm32"

echo "==> Fetch Mesa $MESA_VERSION"
curl -fL --retry 5 --retry-delay 3 "$MESA_URL" -o "$WORK/mesa.tar.xz"
echo "$MESA_SHA256  $WORK/mesa.tar.xz" | sha256sum -c -
tar -C "$WORK" -xf "$WORK/mesa.tar.xz"

echo "==> Verify A619 + KGSL + native Turnip Vulkan 1.4"
grep -Fq 'GPUId(619)' "$SRC/src/freedreno/common/freedreno_devices.py"
grep -Fq "freedreno_kmds.contains('kgsl')" "$SRC/src/freedreno/vulkan/meson.build"
grep -Fq "libtu_files += files('tu_knl_kgsl.cc')" "$SRC/src/freedreno/vulkan/meson.build"
grep -Fq 'PUBLIC struct hwvulkan_module_t HAL_MODULE_INFO_SYM' "$SRC/src/vulkan/runtime/vk_android.c"
grep -Fq '#define TU_API_VERSION VK_MAKE_VERSION(1, 4, VK_HEADER_VERSION)' "$SRC/src/freedreno/vulkan/tu_device.cc"
test "$(grep -F "'--api-version', '1.4'" "$SRC/src/freedreno/vulkan/meson.build" | wc -l)" -eq 2

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"

build_one() {
  name="$1"
  triple="$2"
  cpu_family="$3"
  cpu="$4"
  outdir="$5"

  build="$WORK/build-$name"
  cross="$WORK/android-$name.ini"

  cat > "$cross" <<EOF
[constants]
ndk_path = '$NDK'
toolchain = ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64'

[binaries]
ar = toolchain / 'bin/llvm-ar'
c = ['ccache', toolchain / 'bin/$triple$ANDROID_API-clang']
cpp = ['ccache', toolchain / 'bin/$triple$ANDROID_API-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = 'lld'
cpp_ld = 'lld'
strip = toolchain / 'bin/llvm-strip'

[host_machine]
system = 'android'
cpu_family = '$cpu_family'
cpu = '$cpu'
endian = 'little'
EOF

  echo "==> Configure Mesa Turnip A619/KGSL Vulkan 1.4 for $name"
  meson setup "$build" "$SRC"     --cross-file "$cross"     --buildtype release     -Db_ndebug=true     -Dplatforms=android     -Dplatform-sdk-version="$ANDROID_API"     -Dandroid-stub=true     -Dandroid-libbacktrace=disabled     -Dandroid-libperfetto=disabled     -Dexpat=disabled     -Dxmlconfig=disabled     -Degl=disabled     -Dgles1=disabled     -Dgles2=disabled     -Dopengl=false     -Dgbm=disabled     -Dglx=disabled     -Dgallium-drivers=     -Dvulkan-drivers=freedreno     -Dvulkan-layers=     -Dfreedreno-kmds=kgsl     -Dllvm=disabled     -Dvalgrind=disabled     -Dlibunwind=disabled     -Dlmsensors=disabled     -Dperfetto=false     -Dzstd=disabled     -Dzlib=enabled     -Dbuild-tests=false     -Dtools=     -Dvideo-codecs=     -Dtu-build-id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac

  echo "==> Compile Turnip for $name"
  meson compile -C "$build"

  driver="$build/src/freedreno/vulkan/libvulkan_freedreno.so"
  test -s "$driver"

  cp "$driver" "$outdir/vulkan.adreno.so"
  patchelf --set-soname vulkan.adreno.so "$outdir/vulkan.adreno.so"
  patchelf --remove-rpath "$outdir/vulkan.adreno.so"
  "$TOOLCHAIN/bin/llvm-strip" --strip-unneeded "$outdir/vulkan.adreno.so"

  readelf -d "$outdir/vulkan.adreno.so" > "$outdir/readelf-dynamic.txt"
  readelf -Ws "$outdir/vulkan.adreno.so" > "$outdir/readelf-symbols.txt"

  grep -Fq '(SONAME)' "$outdir/readelf-dynamic.txt"
  grep -Fq 'vulkan.adreno.so' "$outdir/readelf-dynamic.txt"
  ! grep -Fq '(RUNPATH)' "$outdir/readelf-dynamic.txt"
  grep -Eq '[[:space:]]HMI$' "$outdir/readelf-symbols.txt"
  for lib in libhardware.so liblog.so libnativewindow.so libsync.so libc.so; do
    grep -Fq "Shared library: [$lib]" "$outdir/readelf-dynamic.txt"
  done

  file "$outdir/vulkan.adreno.so" | tee "$outdir/file.txt"
  sha256sum "$outdir/vulkan.adreno.so" | tee "$outdir/vulkan.adreno.so.sha256"
}

build_one "arm64" "aarch64-linux-android" "aarch64" "armv8" "$OUT/arm64"
build_one "arm32" "armv7a-linux-androideabi" "arm" "armv7" "$OUT/arm32"

echo "==> Audit ELF architectures"
readelf -h "$OUT/arm64/vulkan.adreno.so" | grep -Fq 'Class:                             ELF64'
readelf -h "$OUT/arm64/vulkan.adreno.so" | grep -Fq 'Machine:                           AArch64'
readelf -h "$OUT/arm32/vulkan.adreno.so" | grep -Fq 'Class:                             ELF32'
readelf -h "$OUT/arm32/vulkan.adreno.so" | grep -Eq 'Machine:[[:space:]]+ARM'

cat > "$OUT/BUILD-INFO.txt" <<EOF
project=touchGrass Turnip A619 KGSL Vulkan 1.4 replacement
mesa_version=26.2.2
mesa_archive_sha256=$MESA_SHA256
mesa_release_commit=3281a69a8bfd9f997e91c15ed0e6290cae12dd32
gpu=Adreno 619
kmd=KGSL
android_api=36
ndk=$(basename "$NDK")
turnip_api=Vulkan-1.4
driver_filename=vulkan.adreno.so
architectures=arm64-v8a,armeabi-v7a
replacement_targets=/vendor/lib64/hw/vulkan.adreno.so,/vendor/lib/hw/vulkan.adreno.so
build_id=15799e6d32f2965a70353013be22dc22a9d57c012b9085f860e94bd349821eac
EOF

echo "==> Turnip Vulkan 1.4 dual-ABI build complete"
