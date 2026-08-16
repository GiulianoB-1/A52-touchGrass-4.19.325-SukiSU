#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
PANEL="$ROOT/drivers/a52_display/msm/dsi/dsi_panel.c"
DISPLAY="$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"

fail_report(){ set +e; rm -rf phase276r-failure; mkdir -p phase276r-failure/source phase276r-failure/audit phase276r-failure/logs; cp phase276r-compile.log phase276r-failure/logs/ 2>/dev/null||true; cp phase276r-deep-path-parity-before.txt phase276r-failure/audit/ 2>/dev/null||true; for p in "$PANEL" "$DISPLAY" "$CTRL"; do [ -f "$p" ]&&cp "$p" phase276r-failure/source/||true; done; }
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Build/reconstruct the exact Phase276 shallow candidate first. This is not the final artifact.
bash scripts/276_ci_build.sh
test -s phase276-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/p276r-base.config
cp "$PANEL" /tmp/p276r-panel.c
cp "$DISPLAY" /tmp/p276r-display.c
cp "$CTRL" /tmp/p276r-ctrl.c

# Exact Golden-source gate for every newly instrumented lower function.
python3 -m py_compile scripts/276r_deep_dsi_parity_probe.py scripts/276r_deep_dsi_frontier.py scripts/276r_audit_candidate.py
python3 scripts/276r_deep_dsi_parity_probe.py "$ROOT" "$TG"
grep -Fq 'all_exact_match=1' phase276r-deep-path-parity-before.txt

python3 scripts/276r_deep_dsi_frontier.py "$ROOT"
! cmp -s /tmp/p276r-panel.c "$PANEL"
! cmp -s /tmp/p276r-display.c "$DISPLAY"
! cmp -s /tmp/p276r-ctrl.c "$CTRL"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p276r-base.config "$OUT/.config"
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase276r-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"; test -s "$IMAGE"

rm -rf phase276r-out
mkdir -p phase276r-out/{compile,config,package,audit,source}
cp "$IMAGE" phase276r-out/compile/Image
cp "$OUT/.config" phase276r-out/config/final.config
cp /tmp/p276r-base.config phase276r-out/audit/phase276-final.config
cp phase276r-deep-path-parity-before.txt phase276r-out/audit/
cp phase276r-compile.log phase276r-out/audit/
cp scripts/276r_*.py phase276r-out/audit/
cp "$PANEL" phase276r-out/source/dsi_panel.c
cp "$DISPLAY" phase276r-out/source/dsi_display.c
cp "$CTRL" phase276r-out/source/dsi_ctrl.c

gzip -n -c "$IMAGE" > phase276r-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py --source phase276-out/package/boot.img --kernel phase276r-out/package/Image.gz --output phase276r-out/package/boot.img --report phase276r-out/package/repack-report.json
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase276r-out')
idn={'phase':'276R','name':'DEEP-DSI-ROOT-CAUSE-RECORDER-V1','git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,'supersedes_phase276_for_hardware':True,'hardware_question':'In the exact TX_LEVEL1_KEY_ENABLE call, identify cmd_lock owner or follow Qualcomm DSI host/controller/message path through hardware kickoff and DMA completion wait.','functional_change':'none; observation only'}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=['compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json','audit/phase276-final.config','audit/phase276r-deep-path-parity-before.txt','source/dsi_panel.c','source/dsi_display.c','source/dsi_ctrl.c']
with (r/'SHA256SUMS').open('w') as f:
 for n in files:f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase276r-out && sha256sum -c SHA256SUMS)
python3 scripts/276r_audit_candidate.py phase276r-out
trap - EXIT
echo 'Phase276R deep DSI root-cause recorder build/repack: PASS'
