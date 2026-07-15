#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$ROOT_DIR/gki-sources.lock"
WORK_DIR="${WORK_DIR:-$ROOT_DIR/work-gki-4.19}"
SRC_DIR="$WORK_DIR/common"
OUT_DIR="$WORK_DIR/out"
ARTIFACT_DIR="$ROOT_DIR/artifacts/gki-4.19-minimal"
JOBS="${JOBS:-4}"

if [[ ! -f "$LOCK_FILE" ]]; then
  echo "Missing $LOCK_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$LOCK_FILE"

required_vars=(GKI_REPO GKI_BRANCH GKI_COMMIT GKI_DEFCONFIG GKI_ARCH LLVM_MAJOR)
for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing $var_name in $LOCK_FILE" >&2
    exit 1
  fi
done

LLVM_BIN="/usr/lib/llvm-${LLVM_MAJOR}/bin"
if [[ ! -x "$LLVM_BIN/clang" || ! -x "$LLVM_BIN/ld.lld" ]]; then
  echo "LLVM ${LLVM_MAJOR} was not found in $LLVM_BIN" >&2
  exit 1
fi
export PATH="$LLVM_BIN:$PATH"

rm -rf "$WORK_DIR" "$ARTIFACT_DIR"
mkdir -p "$WORK_DIR" "$ARTIFACT_DIR/logs"

{
  echo "Repository: $GKI_REPO"
  echo "Branch: $GKI_BRANCH"
  echo "Pinned commit: $GKI_COMMIT"
  echo "Architecture: $GKI_ARCH"
  echo "Defconfig: $GKI_DEFCONFIG"
} | tee "$ARTIFACT_DIR/source-selection.txt"

echo "Cloning the pinned Android Common Kernel revision"
git clone --no-tags --depth=1 --branch "$GKI_BRANCH" "$GKI_REPO" "$SRC_DIR" \
  2>&1 | tee "$ARTIFACT_DIR/logs/01-clone.log"

actual_commit="$(git -C "$SRC_DIR" rev-parse HEAD)"
if [[ "$actual_commit" != "$GKI_COMMIT" ]]; then
  echo "Branch head is $actual_commit, fetching the pinned commit $GKI_COMMIT"
  git -C "$SRC_DIR" fetch --no-tags --depth=1 origin "$GKI_COMMIT" \
    2>&1 | tee -a "$ARTIFACT_DIR/logs/01-clone.log"
  git -C "$SRC_DIR" checkout --detach FETCH_HEAD
  actual_commit="$(git -C "$SRC_DIR" rev-parse HEAD)"
fi

if [[ "$actual_commit" != "$GKI_COMMIT" ]]; then
  echo "Pinned source verification failed: expected $GKI_COMMIT, got $actual_commit" >&2
  exit 1
fi

{
  echo "git_commit=$actual_commit"
  git -C "$SRC_DIR" show -s --format='commit_date=%cI%nsubject=%s' HEAD
} > "$ARTIFACT_DIR/source-revision.txt"

{
  clang --version
  ld.lld --version
  llvm-ar --version | head -n 1
  make --version | head -n 1
} > "$ARTIFACT_DIR/toolchain.txt"

make_args=(
  -C "$SRC_DIR"
  O="$OUT_DIR"
  ARCH="$GKI_ARCH"
  LLVM=1
  LLVM_IAS=1
)

echo "Generating the official GKI defconfig"
make "${make_args[@]}" "$GKI_DEFCONFIG" \
  2>&1 | tee "$ARTIFACT_DIR/logs/02-defconfig.log"

cp "$OUT_DIR/.config" "$ARTIFACT_DIR/gki_defconfig.expanded"
make "${make_args[@]}" savedefconfig \
  2>&1 | tee "$ARTIFACT_DIR/logs/03-savedefconfig.log"
cp "$OUT_DIR/defconfig" "$ARTIFACT_DIR/gki_defconfig.minimal"

kernel_version="$(make -s "${make_args[@]}" kernelversion)"
printf '%s\n' "$kernel_version" > "$ARTIFACT_DIR/kernel-version.txt"

echo "Building the stock arm64 GKI Image and modules"
set +e
make "${make_args[@]}" -j"$JOBS" Image modules \
  2>&1 | tee "$ARTIFACT_DIR/logs/04-build.log"
build_status=${PIPESTATUS[0]}
set -e
printf '%s\n' "$build_status" > "$ARTIFACT_DIR/build-exit-code.txt"

# Always collect diagnostics, including on a failed build.
for file_name in Image vmlinux System.map Module.symvers .config; do
  if [[ -f "$OUT_DIR/$file_name" ]]; then
    cp "$OUT_DIR/$file_name" "$ARTIFACT_DIR/$file_name"
  fi
done

mkdir -p "$ARTIFACT_DIR/modules"
while IFS= read -r -d '' module_path; do
  relative_path="${module_path#"$OUT_DIR/"}"
  mkdir -p "$ARTIFACT_DIR/modules/$(dirname "$relative_path")"
  cp "$module_path" "$ARTIFACT_DIR/modules/$relative_path"
done < <(find "$OUT_DIR" -type f -name '*.ko' -print0)

tar -C "$ARTIFACT_DIR" -czf "$ARTIFACT_DIR/modules.tar.gz" modules
rm -rf "$ARTIFACT_DIR/modules"

for abi_file in "$SRC_DIR"/abi_gki_aarch64*; do
  [[ -f "$abi_file" ]] && cp "$abi_file" "$ARTIFACT_DIR/"
done

if [[ -f "$OUT_DIR/.config" ]]; then
  grep -E '^(CONFIG_MODULES|CONFIG_MODVERSIONS|CONFIG_MODULE_SIG|CONFIG_ANDROID|CONFIG_ANDROID_BINDER|CONFIG_DM_VERITY|CONFIG_VIRTUALIZATION|CONFIG_KALLSYMS|CONFIG_BPF|CONFIG_CGROUP_BPF)=' \
    "$OUT_DIR/.config" > "$ARTIFACT_DIR/config-summary.txt" || true
fi

if [[ -f "$ARTIFACT_DIR/Image" ]]; then
  file "$ARTIFACT_DIR/Image" > "$ARTIFACT_DIR/image-file.txt"
  stat "$ARTIFACT_DIR/Image" > "$ARTIFACT_DIR/image-stat.txt"
fi

find "$ARTIFACT_DIR" -maxdepth 1 -type f -print0 \
  | sort -z \
  | xargs -0 sha256sum > "$ARTIFACT_DIR/SHA256SUMS"

cat > "$ARTIFACT_DIR/FLASHING-NOTICE.txt" <<'EOF'
THIS OUTPUT IS NOT FLASHABLE.

It is a stock Android Common Kernel GKI build probe. It does not yet contain
the Samsung Galaxy A52 5G SM7125 board support, Samsung boot integration,
device tree integration, or the vendor-driver arrangement required by the
phone. Do not place this Image in AnyKernel3 or flash it to the boot partition.
EOF

if (( build_status != 0 )); then
  echo "The GKI build failed. Diagnostics were collected in $ARTIFACT_DIR." >&2
  exit "$build_status"
fi

echo "Minimal GKI build completed: $kernel_version"
echo "Artifacts: $ARTIFACT_DIR"
