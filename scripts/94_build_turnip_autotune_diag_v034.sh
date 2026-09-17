#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-$PWD}"
NDK="${2:-${ANDROID_NDK_ROOT:-}}"
OUT="${3:-$ROOT/artifacts/turnip-mesa-26.2.2-v034}"
BASE="$ROOT/scripts/92_build_turnip_mesa_26_2_2.sh"
TMP="$ROOT/.tmp-94-build-turnip-autotune-diag.sh"

[ -f "$BASE" ] || { echo "missing base build script: $BASE" >&2; exit 2; }
cp "$BASE" "$TMP"
trap 'rm -f "$TMP"' EXIT

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()

anchor = 'TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"\n'
if text.count(anchor) != 1:
    raise SystemExit(f"v0.34 injection anchor count: {text.count(anchor)}")

inject = r'''echo "==> Enable v0.34 Turnip autotune diagnostics"
python3 - "$SRC" <<'PYDIAG'
from pathlib import Path
import sys

src = Path(sys.argv[1])
p = src / "src/freedreno/vulkan/tu_autotune.cc"
text = p.read_text()

checks = [
    ("#define TU_AUTOTUNE_DEBUG_LOG_BASE 0", "#define TU_AUTOTUNE_DEBUG_LOG_BASE 1"),
    ("#define TU_AUTOTUNE_DEBUG_LOG_BANDWIDTH 0", "#define TU_AUTOTUNE_DEBUG_LOG_BANDWIDTH 1"),
    ("#define TU_AUTOTUNE_DEBUG_LOG_PROFILED 0", "#define TU_AUTOTUNE_DEBUG_LOG_PROFILED 1"),
]

for old, new in checks:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"autotune diagnostic anchor count for {old!r}: {count}")
    text = text.replace(old, new, 1)

p.write_text(text)

patched = p.read_text()
for needle in (
    "#define TU_AUTOTUNE_DEBUG_LOG_BASE 1",
    "#define TU_AUTOTUNE_DEBUG_LOG_BANDWIDTH 1",
    "#define TU_AUTOTUNE_DEBUG_LOG_PROFILED 1",
    'mesa_logi("autotune: "',
    'mesa_logi("autotune-bw %016"',
    'mesa_logi("autotune-prof %016"',
):
    if needle not in patched:
        raise SystemExit(f"v0.34 autotune diagnostic audit failed: {needle}")

print("source_audit=Turnip autotune base logging enabled:PASS")
print("source_audit=Turnip autotune bandwidth logging enabled:PASS")
print("source_audit=Turnip autotune profiled logging enabled:PASS")
PYDIAG

'''
text = text.replace(anchor, inject + anchor, 1)

old_meta = 'runtime_logging=errors-warnings-only-no-bringup-success-traces\n'
new_meta = 'runtime_logging=autotune-diagnostic-base-bandwidth-profiled\n'
if text.count(old_meta) != 1:
    raise SystemExit(f"runtime logging metadata anchor count: {text.count(old_meta)}")
text = text.replace(old_meta, new_meta, 1)

mode_anchor = 'kgsl_sync_merge=fixed-mixed-ts-syncfd-and-cross-queue-ts\n'
if text.count(mode_anchor) != 1:
    raise SystemExit(f"BUILD-INFO diagnostic anchor count: {text.count(mode_anchor)}")
text = text.replace(
    mode_anchor,
    mode_anchor + 'autotune_diagnostics=base-bandwidth-profiled-compile-time-logs\n',
    1,
)

p.write_text(text)
PY

chmod +x "$TMP"
exec "$TMP" "$ROOT" "$NDK" "$OUT"
