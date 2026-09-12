#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase343-gki-out"
FAIL="$PWD/phase343-gki-failure"

MODES="$ROOT/drivers/gpu/drm/drm_modes.c"
MODES_H="$ROOT/include/drm/drm_modes.h"
DSI_DRM="$ROOT/drivers/a52_display/msm/dsi/dsi_drm.c"
KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase343 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase343-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p343-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/343_apply_drm_vrefresh_roundtrip_parity.py \
     scripts/343_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$MODES" "$MODES_H" "$DSI_DRM" "$KMS" "$ATOMIC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase341 baseline"
bash scripts/341_ci_build_gki.sh 2>&1 | tee phase343-phase341.log

for f in \
  phase341-gki-out/package/boot.img \
  phase341-gki-out/compile/Image \
  phase341-gki-out/config/final.config \
  "$MODES" "$MODES_H" "$DSI_DRM" "$KMS" "$ATOMIC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase341-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'int vrefresh;' "$MODES_H"
grep -Fq 'dsi_mode->timing.refresh_rate = drm_mode->vrefresh;' "$DSI_DRM"
grep -Fq 'dsi_display_find_mode(display, &dsi_mode, &panel_dsi_mode);' "$DSI_DRM"
grep -Fq 'if (!dsi_mode.priv_info)' "$DSI_DRM"
grep -Fq 'return -EINVAL;' "$DSI_DRM"

python3 - "$MODES" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text()

def fn(needle):
    start = text.find(needle)
    if start < 0:
        raise SystemExit("missing function: " + needle)
    brace = text.find("{", start)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise SystemExit("unterminated function: " + needle)

conv = fn("int drm_mode_convert_umode(")
if "out->vrefresh = in->vrefresh;" in conv:
    raise SystemExit("Phase343 baseline unexpectedly already restores inbound vrefresh")
vr = fn("int drm_mode_vrefresh(")
if "if (mode->vrefresh > 0)" in vr:
    raise SystemExit("Phase343 baseline unexpectedly already honors explicit vrefresh")
print("Phase343 baseline semantic gap confirmed: PASS")
PY

cp phase341-gki-out/config/final.config /tmp/p343-phase341.config
cp "$MODES" /tmp/p343-modes-before
cp "$MODES_H" /tmp/p343-modes-h-before
cp "$DSI_DRM" /tmp/p343-dsi-drm-before
cp "$KMS" /tmp/p343-kms-before
cp "$ATOMIC" /tmp/p343-atomic-before

stage "apply DRM vrefresh roundtrip parity"
python3 -m py_compile scripts/343_apply_drm_vrefresh_roundtrip_parity.py
python3 scripts/343_apply_drm_vrefresh_roundtrip_parity.py --root "$ROOT"
python3 scripts/343_apply_drm_vrefresh_roundtrip_parity.py \
  --root "$ROOT" --check-only

! cmp -s /tmp/p343-modes-before "$MODES"
cmp -s /tmp/p343-modes-h-before "$MODES_H"
cmp -s /tmp/p343-dsi-drm-before "$DSI_DRM"
cmp -s /tmp/p343-kms-before "$KMS"
cmp -s /tmp/p343-atomic-before "$ATOMIC"

diff -u /tmp/p343-modes-before "$MODES" > /tmp/p343-modes.diff || true
git diff --no-index --check /tmp/p343-modes-before "$MODES" \
  > /tmp/p343-modes-whitespace-check 2>&1 || true
test ! -s /tmp/p343-modes-whitespace-check

stage "strict semantic scope audit"
python3 - "$MODES" <<'PY'
from pathlib import Path
import sys

after = Path(sys.argv[1]).read_text()
before = Path("/tmp/p343-modes-before").read_text()

required_added = (
    "A52_PHASE343_DRM_VREFRESH_ROUNDTRIP_PARITY_V1",
    "out->vrefresh = in->vrefresh;",
    "if (mode->vrefresh > 0)",
    "return mode->vrefresh;",
)
for token in required_added:
    if token not in after:
        raise SystemExit("Phase343 required token missing: " + token)

if after.count("out->vrefresh = in->vrefresh;") != before.count("out->vrefresh = in->vrefresh;") + 1:
    raise SystemExit("Phase343 expected exactly one inbound vrefresh assignment")
if after.count("if (mode->vrefresh > 0)") != before.count("if (mode->vrefresh > 0)") + 1:
    raise SystemExit("Phase343 expected exactly one explicit vrefresh precedence check")

protected = (
    "drm_mode_validate_driver(dev, out);",
    "drm_mode_set_crtcinfo(out, CRTC_INTERLACE_HALVE_V);",
    "out->clock = in->clock;",
    "out->hdisplay = in->hdisplay;",
    "out->vdisplay = in->vdisplay;",
    "out->flags = in->flags;",
)
for token in protected:
    if after.count(token) != before.count(token):
        raise SystemExit("Phase343 changed protected DRM mode behavior: " + token)

print("Phase343 strict semantic scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p343-phase341.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase343-olddefconfig.log 2>&1
cmp -s /tmp/p343-phase341.config "$BUILD/.config"

stage "compile incremental Phase343 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase343-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase343-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/343_apply_drm_vrefresh_roundtrip_parity.py \
     scripts/343_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p343-* "$OUT/audit/" 2>/dev/null || true
cp "$MODES" "$MODES_H" "$DSI_DRM" "$KMS" "$ATOMIC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase341-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase341-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE341-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase343-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "343",
    "flavor": "gki",
    "name": "DRM-VREFRESH-ROUNDTRIP-PARITY-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "341",
    "finding_before_phase": (
        "The port restored TouchGrass drm_display_mode.vrefresh storage but "
        "left Android 5.10 drm_mode_convert_umode semantics unchanged. A "
        "userspace MODE_ID therefore reaches the restored legacy field as zero. "
        "TouchGrass DSI directly consumes drm_mode->vrefresh for cached panel "
        "mode matching."
    ),
    "experiment": (
        "Restore only the TouchGrass inbound vrefresh conversion semantic and "
        "explicit-field precedence in drm_mode_vrefresh. Do not change DSI, SDE, "
        "display register writes, clocks, power, resets, regulators or recorder logic."
    ),
    "display_logic_changed": True,
    "register_writes_added": 0,
    "clock_power_reset_regulator_changed": False,
    "phase280_freeze_setter_changed": False,
    "automatic_warm_reboot_seconds": None,
    "image_sha256": sha(r / "compile/Image"),
    "boot_img_sha256": sha(r / "package/boot.img"),
    "boot_img_size": (r / "package/boot.img").stat().st_size,
}
(r / "BUILD-IDENTITY.json").write_text(
    json.dumps(identity, indent=2, sort_keys=True) + "\n"
)
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage complete
echo 'Phase343 DRM vrefresh roundtrip parity build: PASS'
trap - EXIT
