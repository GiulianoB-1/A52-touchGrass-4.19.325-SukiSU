#!/usr/bin/env bash
set -Eeuo pipefail

# Preserve the reviewed Phase319 replay through 2da61048, but restore the exact
# Workflow140 run32 producer script bytes before the historical source boundary
# is executed. Run32 Actions checkout used PR merge 46c95749; only scripts 141
# and 143 differ from the later a51d9 branch snapshot. Keep all source identity
# enforcement fail-closed, including the immutable Phase175 SHA256 gate.
BASE_REF=2da61048dc896f3ab7fe2427997a10e53b944a4b
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

: "${GITHUB_REPOSITORY:?}"
curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/${BASE_REF}/scripts/319_regenerate_phase175_base.sh" \
  -o "$TMP"
test -s "$TMP"

# Repair only 2da61048's diagnostic self-check. The authoritative expected
# Phase175 SHA256 check itself remains untouched.
python3 - "$TMP" <<'PYDIAGVERIFY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '''if old in text or text.count('cp "$OUT" /tmp/p319gki-phase175-regenerated.patch') != 1:
    raise SystemExit("Phase175 mismatch diagnostic insertion failed")
'''
new = '''if text.count('cp "$OUT" /tmp/p319gki-phase175-regenerated.patch') != 1:
    raise SystemExit("Phase175 mismatch diagnostic patch-copy insertion failed")
if text.count("Phase319 regeneration: Phase175 patch identity expected=%s actual=%s") != 1:
    raise SystemExit("Phase175 mismatch diagnostic identity-print insertion failed")
'''
if text.count(old) != 1:
    raise SystemExit(f"Phase175 diagnostic postcheck anchor expected 1, found {text.count(old)}")
text = text.replace(old, new, 1)
if old in text or text.count(new) != 1:
    raise SystemExit("Phase175 diagnostic postcheck repair failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: Phase175 diagnostic postcheck repair PASS")
PYDIAGVERIFY

# 2da61048 transforms the immutable f27 replay and then executes it. Insert one
# final fail-closed transformation at that launch boundary. It restores the
# exact Workflow99 producer stages already proven missing, then overwrites only
# Run32 scripts 141 and 143 with the exact PR-merge blobs that actually produced
# artifact 8635093061. Because exact Run32 did not contain phases 148/149/153/154,
# also restore the unmodified historical Phase171 audit instead of the temporary
# post-Phase148 compatibility audit.
python3 - "$TMP" <<'PYINSERT'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
anchor = 'bash "$TMP" "$@"\n'
if text.count(anchor) != 1:
    raise SystemExit(f"2da replay launch anchor expected 1, found {text.count(anchor)}")

block = r"""# Phase319 exact producer reconstruction: operate on the fully transformed
# f27 replay immediately before it executes.
python3 - "$TMP" <<'PYRUN32EXACT'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

run32 = "RUN32_REF=a51d9b5107821176950cb9a235ba953b95cd6e7c\n"
refs = (
    "WF99_REF=657612e050f762d25d5bbe4eda91212364fb0cb6\n"
    "RUN32_EXEC_REF=46c95749a1277818f7c661eb46e29fddbffb8b30\n"
)
if text.count(run32) != 1:
    raise SystemExit(f"Run32 ref anchor expected 1, found {text.count(run32)}")
if "WF99_REF=" in text or "RUN32_EXEC_REF=" in text:
    raise SystemExit("exact replay refs unexpectedly already present")
text = text.replace(run32, run32 + refs, 1)

r32_dir = 'R32="$WORK/run32-$RUN32_REF/scripts"\n'
new_dirs = (
    r32_dir
    + 'WF99="$WORK/workflow99-$WF99_REF/scripts"\n'
)
if text.count(r32_dir) != 1:
    raise SystemExit(f"Run32 directory anchor expected 1, found {text.count(r32_dir)}")
text = text.replace(r32_dir, new_dirs, 1)

mkdir_old = 'mkdir -p "$HIST" "$R32" "$STAGE" "$UP/drivers/interconnect/qcom" "$UP/include/dt-bindings/interconnect" "$(dirname "$OUT")"\n'
mkdir_new = 'mkdir -p "$HIST" "$R32" "$WF99" "$STAGE" "$UP/drivers/interconnect/qcom" "$UP/include/dt-bindings/interconnect" "$(dirname "$OUT")"\n'
if text.count(mkdir_old) != 1:
    raise SystemExit(f"Workflow99 mkdir anchor expected 1, found {text.count(mkdir_old)}")
text = text.replace(mkdir_old, mkdir_new, 1)

run32_comment = "# Run32 complete-source producer scripts, pinned to the surviving historical\n"
fetch_block = (
    "# Exact missing source stages from Workflow99 artifact 8590238316.\n"
    "for name in \\\n"
    "  96_stage_a52xq_legacy_gdsc_regulator.py \\\n"
    "  97_stage_a52xq_ufs_ice_safe_bringup.py \\\n"
    "  98_stage_a52xq_ufs_dependency_audit.py \\\n"
    "  99_stage_a52xq_rpmh_mode_abi_fix.py; do\n"
    "  fetch_script \"$WF99_REF\" \"$name\" \"$WF99\"\n"
    "done\n\n"
)
if text.count(run32_comment) != 1:
    raise SystemExit(f"Workflow99 fetch anchor expected 1, found {text.count(run32_comment)}")
text = text.replace(run32_comment, fetch_block + run32_comment, 1)

old_exec = (
    'python3 "$HIST/94b_stage_a52xq_ufs_phy_bridge.py" --gki "$ROOT" --output "$STAGE/94b"\n'
    '\n'
    'git -C "$ROOT" diff --check\n'
    "printf '%s\\n' 'Phase319 regeneration: exact historical Workflow99 source staging PASS'\n"
)
new_exec = (
    'python3 "$HIST/94b_stage_a52xq_ufs_phy_bridge.py" --gki "$ROOT" --output "$STAGE/94b"\n'
    'python3 "$WF99/96_stage_a52xq_legacy_gdsc_regulator.py" --gki "$ROOT" --output "$STAGE/96"\n'
    'python3 "$WF99/97_stage_a52xq_ufs_ice_safe_bringup.py" --gki "$ROOT" --output "$STAGE/97"\n'
    'python3 "$WF99/98_stage_a52xq_ufs_dependency_audit.py" --gki "$ROOT" --output "$STAGE/98"\n'
    'python3 "$WF99/99_stage_a52xq_rpmh_mode_abi_fix.py" --gki "$ROOT" --output "$STAGE/99"\n'
    '\n'
    'git -C "$ROOT" diff --check\n'
    "printf '%s\\n' 'Phase319 regeneration: exact historical Workflow99 source staging PASS'\n"
)
if text.count(old_exec) != 1:
    raise SystemExit(f"Workflow99 execution anchor expected 1, found {text.count(old_exec)}")
text = text.replace(old_exec, new_exec, 1)

# The earlier replay compatibility transforms operate on the later a51d versions
# of 141/143 and run before this point. Overwrite them now with the exact files
# from Actions checkout 46c95749, immediately before any historical stage runs.
for_spec = "\nfor spec in \\\n"
override = (
    "\n# Exact Workflow140 run32 producer scripts from PR merge 46c95749.\n"
    'fetch_script "$RUN32_EXEC_REF" "141_apply_a52xq_ack_secure_parameter_probe.py" "$R32"\n'
    'fetch_script "$RUN32_EXEC_REF" "143_run_a52xq_early_mirrored_boot_probe.py" "$R32"\n'
    "printf '%s\\n' 'Phase319 regeneration: exact Run32 141/143 producer blobs restored'\n"
)
if text.count(for_spec) != 1:
    raise SystemExit(f"Run32 exact-blob override anchor expected 1, found {text.count(for_spec)}")
text = text.replace(for_spec, override + for_spec, 1)

# Workflow140 run32 explicitly executed the unified recorder generator after the
# 123 wrapper completed its 141 -> 143 -> 144/146 recursive staging. The f27
# replay omitted that top-level Workflow140 call because it had been compensating
# inside the later Phase141 script. Restore the exact producer ordering here.
run123 = 'python3 "$R32/123_apply_a52xq_legacy_ion_free_compat.py" --gki "$ROOT" --output "$STAGE/run32"\n'
run140 = 'python3 "$R32/140_apply_a52xq_unified_secure_startup_recorder.py" --gki "$ROOT" --output "$STAGE/run32"\n'
if text.count(run123) != 1:
    raise SystemExit(f"Run32 Phase123 execution anchor expected 1, found {text.count(run123)}")
if text.count(run140) != 0:
    raise SystemExit(f"Run32 explicit Phase140 execution unexpectedly present {text.count(run140)} times")
text = text.replace(run123, run123 + run140, 1)

# 2da61048 temporarily rewrote Phase171 to audit the later Phase148 contract.
# Exact Run32 never ran 148/149/153/154, so restore the original historical
# Phase171 call. Its checked-in script blob is already byte-identical to the
# successful Phase173 producer.
phase171_start = "# Run32 Phase148 deliberately changed the ACK global ION exporter"
phase171_call = 'python3 scripts/171_audit_touchgrass_qseecom_contract.py --gki "$ROOT" --touchgrass "$TGREF" --output "$STAGE/171"\n'
if text.count(phase171_start) != 1:
    raise SystemExit(f"Phase171 compatibility block start expected 1, found {text.count(phase171_start)}")
start = text.index(phase171_start)
call_at = text.find(phase171_call, start)
if call_at < 0:
    raise SystemExit("Phase171 historical execution call missing from compatibility block")
end = call_at + len(phase171_call)
replacement = (
    "# Phase319 exact Run32 fidelity: use the unmodified historical Phase171 audit.\n"
    + phase171_call
)
text = text[:start] + replacement + text[end:]

checks = (
    "WF99_REF=657612e050f762d25d5bbe4eda91212364fb0cb6",
    "RUN32_EXEC_REF=46c95749a1277818f7c661eb46e29fddbffb8b30",
    'fetch_script "$RUN32_EXEC_REF" "141_apply_a52xq_ack_secure_parameter_probe.py" "$R32"',
    'fetch_script "$RUN32_EXEC_REF" "143_run_a52xq_early_mirrored_boot_probe.py" "$R32"',
    'python3 "$R32/140_apply_a52xq_unified_secure_startup_recorder.py" --gki "$ROOT" --output "$STAGE/run32"',
    '96_stage_a52xq_legacy_gdsc_regulator.py" --gki "$ROOT" --output "$STAGE/96"',
    '99_stage_a52xq_rpmh_mode_abi_fix.py" --gki "$ROOT" --output "$STAGE/99"',
    "Phase319 exact Run32 fidelity: use the unmodified historical Phase171 audit.",
)
for token in checks:
    if text.count(token) != 1:
        raise SystemExit(f"exact replay verification failed for {token!r}: {text.count(token)}")
if "hydrated Phase171 Phase148 flags-fallback audit compatibility PASS" in text:
    raise SystemExit("stale post-Phase148 Phase171 compatibility block remains")

path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: exact Workflow99 + Run32 producer fidelity PASS")
PYRUN32EXACT
"""

text = text.replace(anchor, block + anchor, 1)
if text.count("PYRUN32EXACT") != 2:
    raise SystemExit("exact Run32 nested repair insertion failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: exact Run32 producer repair wrapper insertion PASS")
PYINSERT

bash "$TMP" "$@"
