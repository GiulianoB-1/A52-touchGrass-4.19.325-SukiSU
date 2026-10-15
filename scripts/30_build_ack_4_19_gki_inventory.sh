#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/gki-sources.lock"

WORK_DIR="${WORK_DIR:-$ROOT_DIR/work-gki-4.19-inventory}"
SRC_DIR="$WORK_DIR/common"
OUT_DIR="$WORK_DIR/out"
DEST="$ROOT_DIR/artifacts/compatibility/gki"
JOBS="${JOBS:-8}"
LLVM_BIN="/usr/lib/llvm-${LLVM_MAJOR}/bin"

rm -rf "$WORK_DIR" "$DEST"
mkdir -p "$WORK_DIR" "$DEST/logs"
export PATH="$LLVM_BIN:$PATH"

for tool in clang ld.lld llvm-ar llvm-nm llvm-objcopy llvm-objdump llvm-strip aarch64-linux-gnu-gcc; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done

git clone --no-tags --depth=1 --branch "$GKI_BRANCH" "$GKI_REPO" "$SRC_DIR" \
  2>&1 | tee "$DEST/logs/clone.log"

actual_commit="$(git -C "$SRC_DIR" rev-parse HEAD)"
if [[ "$actual_commit" != "$GKI_COMMIT" ]]; then
  git -C "$SRC_DIR" fetch --no-tags --depth=1 origin "$GKI_COMMIT"
  git -C "$SRC_DIR" checkout --detach FETCH_HEAD
  actual_commit="$(git -C "$SRC_DIR" rev-parse HEAD)"
fi
[[ "$actual_commit" == "$GKI_COMMIT" ]] || { echo "GKI commit mismatch" >&2; exit 1; }

make_args=(
  -C "$SRC_DIR"
  O="$OUT_DIR"
  ARCH="$GKI_ARCH"
  CROSS_COMPILE=aarch64-linux-gnu-
  CLANG_TRIPLE=aarch64-linux-gnu-
  CC=clang
  LD=ld.lld
  AR=llvm-ar
  NM=llvm-nm
  OBJCOPY=llvm-objcopy
  OBJDUMP=llvm-objdump
  STRIP=llvm-strip
  LLVM=1
  LLVM_IAS=1
)

make "${make_args[@]}" "$GKI_DEFCONFIG" 2>&1 | tee "$DEST/logs/defconfig.log"
make "${make_args[@]}" -j"$JOBS" Image modules 2>&1 | tee "$DEST/logs/build.log"

cp "$OUT_DIR/.config" "$DEST/config"
cp "$OUT_DIR/arch/arm64/boot/Image" "$DEST/Image"
cp "$OUT_DIR/System.map" "$DEST/System.map"
cp "$OUT_DIR/Module.symvers" "$DEST/Module.symvers"
make -s "${make_args[@]}" kernelrelease > "$DEST/kernel-release.txt"

find "$OUT_DIR" -type f -name '*.ko' -printf '%P\n' | sort > "$DEST/modules.list"
find "$SRC_DIR" -maxdepth 1 -type f -name 'abi_gki_aarch64*' -exec cp {} "$DEST/" \;

{
  echo "repository=$GKI_REPO"
  echo "branch=$GKI_BRANCH"
  echo "commit=$actual_commit"
  echo "defconfig=$GKI_DEFCONFIG"
  echo "image_bytes=$(stat -c %s "$DEST/Image")"
  echo "module_count=$(wc -l < "$DEST/modules.list")"
  echo "exported_symbol_count=$(wc -l < "$DEST/Module.symvers")"
} > "$DEST/metadata.txt"

(
  cd "$DEST"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
