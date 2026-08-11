#!/usr/bin/env python3
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: touchgrass_definitive_fdr_resukisu_wrapper.py <safe-build-template>")

path = Path(sys.argv[1])
project = Path(__file__).resolve().parent.parent

subprocess.run([
    sys.executable,
    str(project / "scripts/touchgrass_final_boot_reference_resukisu_wrapper.py"),
    str(path),
], check=True)

text = path.read_text()
anchor = '''info "Building Linux 4.19.200 + ReSukiSU-safe + GPU + final boot reference recorders"
build_kernel "$LABEL"
'''

block = r'''info "Applying TouchGrass definitive FDR v1 backend"
python3 -m py_compile "$PROJECT_DIR/scripts/touchgrass_definitive_fdr_overlay.py"
python3 "$PROJECT_DIR/scripts/touchgrass_definitive_fdr_overlay.py" "$ROOT"

python3 -m py_compile "$PROJECT_DIR/scripts/generate_touchgrass_fdr_dictionary.py"
python3 "$PROJECT_DIR/scripts/generate_touchgrass_fdr_dictionary.py" \
  "$ROOT" "$ARTIFACTS_DIR/touchgrass-fdr-event-dictionary.json"

git -C "$ROOT" diff --check

test -s "$ROOT/include/linux/tg_fdr.h" || fail "FDR public header missing"
test -s "$ROOT/kernel/tg_fdr.c" || fail "FDR core missing"
grep -Fq 'obj-y += tg_fdr.o' "$ROOT/kernel/Makefile" || fail "FDR Kbuild hook missing"
grep -Fq 'TG_FDR_RECORDS_PER_BANK' "$ROOT/kernel/tg_fdr.c" || fail "FDR banks missing"
grep -Fq 'name = "tg_fdr"' "$ROOT/kernel/tg_fdr.c" || fail "FDR misc device missing"
grep -Fq 'backend=touchgrass_definitive_fdr_v1' "$ROOT/kernel/tg_boot_reference.c" || fail "boot adapter missing"
grep -Fq 'backend=touchgrass_definitive_fdr_v1' "$ROOT/kernel/tg_gpu_reference.c" || fail "GPU adapter missing"

info "Building Linux 4.19.200 + ReSukiSU-safe + definitive FDR v1"
build_kernel "$LABEL"
'''

if text.count(anchor) != 1:
    raise SystemExit(
        f"definitive FDR build anchor mismatch: expected 1, found {text.count(anchor)}"
    )

path.write_text(text.replace(anchor, block, 1))
print(f"Injected definitive FDR into {path}")
