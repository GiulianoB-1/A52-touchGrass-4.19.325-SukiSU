#!/usr/bin/env bash
# Phase446Z reproducible source+object preflight using LAST SUCCESSFUL
# Phase446Y final source artifact. Does not depend on moving build cache.
set -Eeuo pipefail
set -x
test -n "$GH_TOKEN"
REPO="GiulianoB-1/A52-touchGrass-4.19.325-SukiSU"
RUN=39121080081
NAME="A52XQ-PHASE446WX-FINAL-INPUTS-61"
BASE="$PWD/workspace/p446z-preflight"
IN="$BASE/inputs"
ROOT="$BASE/gki/common"
BUILD="$BASE/build"
mkdir -p "$IN" "$ROOT" "$BUILD"
gh run download "$RUN" --repo "$REPO" --name "$NAME" --dir "$IN"
TAR="$(find "$IN" -type f -name final-patched-gki-source.tar.zst -print -quit)"
CFG="$(find "$IN" -type f -name final.config -print -quit)"
test -n "$TAR" && test -s "$TAR"
test -n "$CFG" && test -s "$CFG"
cp "$CFG" "$BUILD/.config"
echo "P446Z PREFLIGHT inherited source=$(sha256sum "$TAR" | cut -d' ' -f1)"
tar --use-compress-program=zstd -xf "$TAR" -C "$ROOT"
test -f "$ROOT/drivers/i2c/busses/i2c-qcom-geni.c"
test -f "$ROOT/drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c"
python3 -m py_compile scripts/446z_apply_qup_core_touch.py scripts/446z_integrate_resukisu.py
python3 scripts/446z_apply_qup_core_touch.py --root "$ROOT"
python3 scripts/446z_apply_qup_core_touch.py --root "$ROOT" --check-only
bash scripts/446z_setup_resukisu.sh "$ROOT" "$BUILD"
python3 scripts/446z_integrate_resukisu.py --root "$ROOT" --check-only
make -C "$ROOT" O="$BUILD" ARCH=arm64 \
     CROSS_COMPILE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
grep -Fxq CONFIG_KSU=y "$BUILD/.config"
grep -Fxq CONFIG_KSU_TRACEPOINT_HOOK=y "$BUILD/.config"
grep -Fxq CONFIG_I2C_QCOM_GENI=y "$BUILD/.config"
grep -Fxq CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y "$BUILD/.config"
grep -Fxq '# CONFIG_KSU_SUSFS is not set' "$BUILD/.config"
# Object-only Kbuild does not visit init/ or SELinux, unlike full Image.
# Generate the two real prerequisites explicitly instead of faking headers.
make -C "$ROOT" O="$BUILD" ARCH=arm64 \
     CROSS_COMPILE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
     init/version.o security/selinux/flask.h
test -s "$BUILD/include/generated/compile.h"
test -s "$BUILD/security/selinux/flask.h"
make -C "$ROOT" O="$BUILD" ARCH=arm64 \
     CROSS_COMPILE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
     -j"$(nproc)" \
     drivers/i2c/busses/i2c-qcom-geni.o \
     drivers/input/touchscreen/stm/fts5cu56a/fts_ts.o \
     drivers/soc/qcom/qcom-geni-se.o \
     drivers/kernelsu/
test -s "$BUILD/drivers/kernelsu/kernelsu.o"
echo 'P446Z PREFLIGHT PASS: SE8 I2C bus vote, STM fail-fast, KSU built-in object all compiled'
