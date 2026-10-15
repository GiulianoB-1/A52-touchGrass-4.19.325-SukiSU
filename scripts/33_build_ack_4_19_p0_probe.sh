#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/gki-sources.lock"

WORK_DIR="${WORK_DIR:-$ROOT_DIR/work-gki-4.19-p0-probe}"
SRC_DIR="$WORK_DIR/common"
OUT_DIR="$WORK_DIR/out"
DEST="$ROOT_DIR/artifacts/p0-probe"
JOBS="${JOBS:-8}"
LLVM_BIN="/usr/lib/llvm-${LLVM_MAJOR}/bin"

rm -rf "$WORK_DIR" "$DEST"
mkdir -p "$WORK_DIR" "$DEST/logs"
export PATH="$LLVM_BIN:$PATH"

for tool in clang ld.lld llvm-ar llvm-nm llvm-objcopy llvm-objdump llvm-strip aarch64-linux-gnu-gcc gzip; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done

git init -q "$SRC_DIR"
git -C "$SRC_DIR" remote add origin "$GKI_REPO"
git -C "$SRC_DIR" fetch --no-tags --depth=1 origin "$GKI_COMMIT" \
  2>&1 | tee "$DEST/logs/fetch.log"
git -C "$SRC_DIR" checkout --quiet --detach FETCH_HEAD
actual_commit="$(git -C "$SRC_DIR" rev-parse HEAD)"
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
cp "$OUT_DIR/.config" "$DEST/gki-base.config"

cat > "$DEST/requested-options.txt" <<'EOF'
CONFIG_ARCH_QCOM
CONFIG_QCOM_SCM
CONFIG_QCOM_RPMH
CONFIG_QCOM_SMEM
CONFIG_QCOM_SMP2P
CONFIG_QCOM_SMEM_STATE
CONFIG_QCOM_COMMAND_DB
CONFIG_QCOM_GLINK
CONFIG_RPMSG_QCOM_GLINK_NATIVE
CONFIG_RPMSG_QCOM_GLINK_SMEM
CONFIG_RPMSG_QCOM_GLINK_RPM
CONFIG_QRTR
CONFIG_QCOM_PDC
CONFIG_QCOM_LLCC
CONFIG_QCOM_LAGOON_LLCC
CONFIG_QCOM_GDSC
CONFIG_COMMON_CLK_QCOM
CONFIG_QCOM_CLK_RPMH
CONFIG_SDM_GCC_LAGOON
CONFIG_PINCTRL_MSM
CONFIG_PINCTRL_LAGOON
CONFIG_PINCTRL_QCOM_SPMI_PMIC
CONFIG_REGULATOR_QCOM_RPMH
CONFIG_REGULATOR_RPMH
CONFIG_REGULATOR_QCOM_SPMI
CONFIG_QCOM_GENI_SE
CONFIG_SERIAL_MSM_GENI
CONFIG_SERIAL_MSM_GENI_CONSOLE
CONFIG_SERIAL_MSM_GENI_EARLY_CONSOLE
CONFIG_ARM_SMMU
CONFIG_QTI_IOMMU_SUPPORT
CONFIG_SCSI_UFSHCD
CONFIG_SCSI_UFSHCD_PLATFORM
CONFIG_SCSI_UFS_QCOM
CONFIG_PHY_QCOM_UFS
CONFIG_MMC_CQHCI
CONFIG_MMC_SDHCI
CONFIG_MMC_SDHCI_PLTFM
CONFIG_MMC_SDHCI_MSM
CONFIG_PSTORE
CONFIG_PSTORE_RAM
CONFIG_PSTORE_CONSOLE
CONFIG_PSTORE_PMSG
CONFIG_ANDROID_BINDER_IPC
CONFIG_ANDROID_BINDERFS
CONFIG_SECURITY_SELINUX
CONFIG_DM_VERITY
CONFIG_EXT4_FS
CONFIG_F2FS_FS
CONFIG_EROFS_FS
EOF

while IFS= read -r option; do
  "$SRC_DIR/scripts/config" --file "$OUT_DIR/.config" --enable "${option#CONFIG_}"
done < "$DEST/requested-options.txt"

make "${make_args[@]}" olddefconfig 2>&1 | tee "$DEST/logs/olddefconfig.log"
cp "$OUT_DIR/.config" "$DEST/final.config"

python3 - "$SRC_DIR" "$DEST" <<'PY'
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
requested = [line.strip() for line in (dest / "requested-options.txt").read_text().splitlines() if line.strip()]


def parse_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = "n"
    return values


def definitions(symbol: str) -> list[str]:
    found: list[str] = []
    pattern = re.compile(rf"^\s*(?:menu)?config\s+{re.escape(symbol)}\s*$")
    for path in src.rglob("Kconfig*"):
        if not path.is_file():
            continue
        try:
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if pattern.match(line):
                    found.append(f"{path.relative_to(src)}:{number}")
        except OSError:
            pass
    return found

base = parse_config(dest / "gki-base.config")
final = parse_config(dest / "final.config")
rows: list[list[str]] = []
counts = {"built-in": 0, "module": 0, "dependency-blocked": 0, "source-missing": 0}

for option in requested:
    symbol = option.removeprefix("CONFIG_")
    locations = definitions(symbol)
    resolved = final.get(option, "missing")
    if not locations:
        status = "source-missing"
    elif resolved == "y":
        status = "built-in"
    elif resolved == "m":
        status = "module"
    else:
        status = "dependency-blocked"
    counts[status] += 1
    rows.append([option, base.get(option, "missing"), resolved, status, ";".join(locations)])

with (dest / "p0-option-resolution.csv").open("w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["option", "gki_base", "p0_probe", "status", "kconfig_definition"])
    writer.writerows(rows)

platform_patterns = ("lagoon", "sm7125", "sm7225", "a52xq")
platform_hits: list[str] = []
for path in src.rglob("*"):
    if not path.is_file() or path.stat().st_size > 2_000_000:
        continue
    try:
        text = path.read_text(errors="replace")
    except OSError:
        continue
    lower = text.lower()
    matched = [pattern for pattern in platform_patterns if pattern in lower]
    if matched:
        platform_hits.append(f"{path.relative_to(src)}\t{','.join(matched)}")
(dest / "platform-source-hits.txt").write_text("\n".join(platform_hits) + ("\n" if platform_hits else ""))

missing = [row[0] for row in rows if row[3] == "source-missing"]
blocked = [row[0] for row in rows if row[3] == "dependency-blocked"]
builtin = [row[0] for row in rows if row[3] == "built-in"]
modules = [row[0] for row in rows if row[3] == "module"]

report = f"""# ACK 4.19 P0 implementation probe

## Purpose

This is the first implementation build after the compatibility inventory. It keeps the official Android 4.19 GKI source as the base, requests the A52 early-boot configuration set, resolves dependencies through Kconfig, and compiles a non-flashable kernel payload.

## Option result

- Requested options: **{len(rows)}**
- Resolved built-in: **{counts['built-in']}**
- Resolved as modules: **{counts['module']}**
- Defined but dependency-blocked: **{counts['dependency-blocked']}**
- Missing from ACK source: **{counts['source-missing']}**
- ACK files mentioning Lagoon, SM7125, SM7225 or a52xq: **{len(platform_hits)}**

## Built-in options

{chr(10).join(f'- `{option}`' for option in builtin) or '- None'}

## Module results

{chr(10).join(f'- `{option}`' for option in modules) or '- None'}

## Dependency-blocked options

{chr(10).join(f'- `{option}`' for option in blocked) or '- None'}

## Missing source options

{chr(10).join(f'- `{option}`' for option in missing) or '- None'}

## Interpretation

Options that resolve to built-in can stay in the ACK-derived core. Options missing from ACK source identify the first TouchGrass or Qualcomm vendor code that must be ported. A successful compile does not make the output bootable because the A52 device tree and all missing Lagoon platform drivers are still absent.
"""
(dest / "P0-PROBE-REPORT.md").write_text(report)
PY

set +e
make "${make_args[@]}" -j"$JOBS" Image modules 2>&1 | tee "$DEST/logs/build.log"
build_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$build_rc" > "$DEST/build.exit-code"

if [[ "$build_rc" -eq 0 ]]; then
  cp "$OUT_DIR/arch/arm64/boot/Image" "$DEST/Image"
  gzip -n -9 -c "$DEST/Image" > "$DEST/Image.gz"
  cp "$OUT_DIR/Module.symvers" "$DEST/Module.symvers"
  cp "$OUT_DIR/System.map" "$DEST/System.map"
  find "$OUT_DIR" -type f -name '*.ko' -printf '%P\n' | sort > "$DEST/modules.list"
  make -s "${make_args[@]}" kernelrelease > "$DEST/kernel-release.txt"
else
  echo "The P0 configuration did not compile. Review logs/build.log." > "$DEST/BUILD-FAILED.txt"
fi

{
  echo "repository=$GKI_REPO"
  echo "branch=$GKI_BRANCH"
  echo "commit=$actual_commit"
  echo "defconfig=$GKI_DEFCONFIG"
  echo "build_exit_code=$build_rc"
  if [[ -s "$DEST/Image" ]]; then
    echo "image_bytes=$(stat -c %s "$DEST/Image")"
    echo "image_gz_bytes=$(stat -c %s "$DEST/Image.gz")"
    echo "un1ca_kernel_member=Image.gz"
    echo "flashable=no"
  fi
} > "$DEST/metadata.txt"

cat > "$DEST/FLASHING-NOTICE.txt" <<'EOF'
DO NOT FLASH THIS OUTPUT.

This is a source and configuration feasibility probe. It has no A52 DTB integration and may lack mandatory Qualcomm/Samsung platform drivers. Image.gz exists only to validate the UN1CA payload format.
EOF

(
  cd "$DEST"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

exit "$build_rc"
