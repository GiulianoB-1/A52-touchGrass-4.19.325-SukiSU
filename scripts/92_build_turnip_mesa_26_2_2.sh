#!/usr/bin/env bash
set -Eeuo pipefail

MESA_VERSION="${MESA_VERSION:-26.2.2}"
MESA_SHA256="${MESA_SHA256:-eeb29ca7e56cfaa8e8a79538dcf834e3b18e501c31bef5145e959ea437cc4216}"
ANDROID_API="${ANDROID_API:-34}"
NDK_VERSION="${NDK_VERSION:-28.2.13676358}"
ROOT_DIR="${GITHUB_WORKSPACE:-$(pwd)}"
WORK_DIR="${ROOT_DIR}/workspace/turnip"
SRC_DIR="${WORK_DIR}/mesa-${MESA_VERSION}"
BUILD_DIR="${WORK_DIR}/build-android-aarch64"
OUT_DIR="${ROOT_DIR}/release/turnip"
ARTIFACTS_DIR="${ROOT_DIR}/artifacts/turnip"

mkdir -p "${WORK_DIR}" "${OUT_DIR}" "${ARTIFACTS_DIR}"

SDKMANAGER="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "${SDKMANAGER}" ]; then
    SDKMANAGER="$(command -v sdkmanager || true)"
fi
[ -n "${SDKMANAGER}" ] && [ -x "${SDKMANAGER}" ] || {
    echo "Android sdkmanager was not found" >&2
    exit 1
}

yes | "${SDKMANAGER}" --licenses >/dev/null 2>&1 || true
"${SDKMANAGER}" "ndk;${NDK_VERSION}"

NDK_PATH="${ANDROID_SDK_ROOT:-${ANDROID_HOME}}/ndk/${NDK_VERSION}"
TOOLCHAIN="${NDK_PATH}/toolchains/llvm/prebuilt/linux-x86_64"
[ -x "${TOOLCHAIN}/bin/aarch64-linux-android${ANDROID_API}-clang" ] || {
    echo "Missing Android AArch64 clang for API ${ANDROID_API}" >&2
    exit 1
}

TARBALL="${WORK_DIR}/mesa-${MESA_VERSION}.tar.xz"
if [ ! -f "${TARBALL}" ]; then
    curl -fL --retry 5 --retry-delay 3         "https://archive.mesa3d.org/mesa-${MESA_VERSION}.tar.xz"         -o "${TARBALL}"
fi

printf '%s  %s\n' "${MESA_SHA256}" "${TARBALL}" | sha256sum -c -

rm -rf "${SRC_DIR}" "${BUILD_DIR}"
tar -C "${WORK_DIR}" -xf "${TARBALL}"

CROSS_FILE="${WORK_DIR}/android-aarch64.cross"
cat > "${CROSS_FILE}" <<EOF
[constants]
ndk_path = '${NDK_PATH}'

[binaries]
ar = ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-ar'
c = ['ccache', ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android${ANDROID_API}-clang']
cpp = ['ccache', ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android${ANDROID_API}-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = 'lld'
cpp_ld = 'lld'
strip = ndk_path / 'toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
EOF

meson setup "${BUILD_DIR}" "${SRC_DIR}"     --cross-file "${CROSS_FILE}"     --buildtype=release     -Dstrip=true     -Dplatforms=android     -Dplatform-sdk-version="${ANDROID_API}"     -Dandroid-stub=true     -Dandroid-libbacktrace=disabled     -Degl=disabled     -Dgallium-drivers=     -Dvulkan-drivers=freedreno     -Dfreedreno-kmds=kgsl     -Dllvm=disabled     2>&1 | tee "${ARTIFACTS_DIR}/meson-setup.log"

meson compile -C "${BUILD_DIR}" -j "${JOBS:-4}"     2>&1 | tee "${ARTIFACTS_DIR}/meson-build.log"

DRIVER="$(find "${BUILD_DIR}" -type f -name 'libvulkan_freedreno.so' -print -quit)"
if [ -z "${DRIVER}" ] || [ ! -s "${DRIVER}" ]; then
    echo "Turnip libvulkan_freedreno.so was not produced" >&2
    find "${BUILD_DIR}" -type f \( -name '*vulkan*.so' -o -name '*freedreno*.so' \) -print || true
    exit 1
fi

file "${DRIVER}" | tee "${ARTIFACTS_DIR}/driver-file.txt"
readelf -h "${DRIVER}" | tee "${ARTIFACTS_DIR}/driver-elf-header.txt"
readelf -d "${DRIVER}" | tee "${ARTIFACTS_DIR}/driver-dynamic.txt"
sha256sum "${DRIVER}" | tee "${ARTIFACTS_DIR}/driver-sha256.txt"

grep -Fq 'Machine:                           AArch64' "${ARTIFACTS_DIR}/driver-elf-header.txt"
if readelf -d "${DRIVER}" | grep -Fq 'libdrm'; then
    echo "Unexpected libdrm dependency in KGSL-only Turnip build" >&2
    exit 1
fi

cp "${DRIVER}" "${OUT_DIR}/libvulkan_freedreno.so"
cp "${CROSS_FILE}" "${ARTIFACTS_DIR}/android-aarch64.cross"
cp "${BUILD_DIR}/meson-logs/meson-log.txt" "${ARTIFACTS_DIR}/meson-log.txt"

DRIVER_SHA="$(sha256sum "${DRIVER}" | awk '{print $1}')"
DRIVER_BYTES="$(stat -c %s "${DRIVER}")"

cat > "${OUT_DIR}/TURNIP-BUILD-INFO.txt" <<EOF
device=Samsung Galaxy A52 5G
codename=a52xq
soc=SM7225
gpu=Adreno 619
mesa=${MESA_VERSION}
mesa_tarball_sha256=${MESA_SHA256}
driver=Turnip
vulkan_target=A6xx Vulkan 1.3
kernel_interface=KGSL
meson_vulkan_driver=freedreno
meson_freedreno_kmds=kgsl
android_arch=aarch64
android_api=${ANDROID_API}
ndk=${NDK_VERSION}
driver_filename=libvulkan_freedreno.so
driver_sha256=${DRIVER_SHA}
driver_bytes=${DRIVER_BYTES}
integration=build-only
vendor_driver_replacement=no
runtime_test=not-performed
EOF

echo "Turnip Mesa ${MESA_VERSION} AArch64 KGSL build completed"
echo "Driver: ${OUT_DIR}/libvulkan_freedreno.so"
echo "SHA256: ${DRIVER_SHA}"
