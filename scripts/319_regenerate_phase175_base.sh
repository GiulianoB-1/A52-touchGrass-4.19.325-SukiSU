#!/usr/bin/env bash
set -Eeuo pipefail

# Recreate the complete Phase175 source boundary from immutable source inputs.
# This exists only because the original Phase175 Actions artifact expired.
# The regenerated patch is accepted only if its SHA256 is byte-identical to the
# original heap19-display-bindcore-source.patch.

ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="${1:-$PWD/workspace/phase319-regenerated-phase175.patch}"
HIST_REF=53a91f777fc132fc39c013f4c6bb8131d9ddd037
RUN32_REF=a51d9b5107821176950cb9a235ba953b95cd6e7c
EXPECTED_PHASE175_SHA256=8604330234635526495004951ac27a9dd6d091f5c7dc19cf6ece90425a5a6b1f
UPSTREAM_LINUX_SHA=830b3c68c1fb1e9176028d02ef86f3cf76aa2476

: "${GKI_COMMON_SHA:?}"
: "${TOUCHGRASS_COMMIT:?}"
test "$GKI_COMMON_SHA" = f960ed27302b1ff8e61e152fc202554d778deccd
test "$TOUCHGRASS_COMMIT" = 6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
test -d "$ROOT/.git"
test -d "$TG/.git"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
test "$(git -C "$TG" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT"

WORK="$PWD/workspace/phase319-phase175-regeneration"
HIST="$WORK/historical-$HIST_REF/scripts"
R32="$WORK/run32-$RUN32_REF/scripts"
STAGE="$WORK/stage"
UP="$WORK/linux-v6.1-sm6350"
rm -rf "$WORK"
mkdir -p "$HIST" "$R32" "$STAGE" "$UP/drivers/interconnect/qcom" "$UP/include/dt-bindings/interconnect" "$(dirname "$OUT")"

raw_base="https://raw.githubusercontent.com/${GITHUB_REPOSITORY}"
fetch_script() {
  local ref="$1" name="$2" dest="$3"
  curl -fL --retry 5 --retry-all-errors --silent --show-error \
    "$raw_base/$ref/scripts/$name" -o "$dest/$name"
  test -s "$dest/$name"
}

# Workflow68 -> Workflow99 source staging. Pin every local historical script to
# the commit that corrected the Samsung downstream RPMh mode ABI after Run32.
for name in \
  53_probe_a52xq_lagoon_phase1.py \
  54_probe_a52xq_lagoon_clocks_phase2.py \
  55_probe_a52xq_lagoon_remaining_clocks.py \
  56_probe_a52xq_lagoon_earlyboot_dt.py \
  59_probe_a52xq_sm6350_interconnect.py \
  67_stage_a52xq_native_qsmmuv500.py \
  68_stage_a52xq_qsmmuv500_handoff.py \
  75_stage_a52xq_msm_watchdog.py \
  83_stage_a52xq_early_start_kernel_breadcrumbs.py \
  91_stage_a52xq_first_irq_trace.py \
  92_stage_a52xq_post_init_flight_recorder.py \
  93_stage_a52xq_pid1_exit_trace.py \
  94_stage_a52xq_ufs_live_trace.py \
  94b_stage_a52xq_ufs_phy_bridge.py \
  95_stage_a52xq_rpmh_provider_bridge.py \
  a52_diag94_common.py a52_diag94_core.py a52_diag94_extra.py a52_diag94_printk.py \
  a52_diag94_core_scoped.py a52_diag94_core_scoped_base.py \
  a52_diag94_live_scoped.py a52_diag94_sd_scoped.py; do
  fetch_script "$HIST_REF" "$name" "$HIST"
done

# Run32 complete-source producer scripts, pinned to the surviving historical
# a52-keymaster-real-ion branch head whose final commit changed evidence-session
# tooling only and explicitly left kernel source unchanged. Keep the full local
# dependency closure for the secure startup stages because the wrappers execute
# sibling scripts by filename.
for name in \
  a52xq_downstream_port_probe_v3.py \
  103_apply_a52xq_phase1_compat.py \
  104_apply_a52xq_phase2_kernel_api.py \
  105_apply_a52xq_display_contract.py \
  106_apply_a52xq_prfmt_pm_qos.py \
  107_apply_a52xq_clk_div_debugfs.py \
  108_apply_a52xq_v4l2_format_abi.py \
  109_apply_a52xq_mmio_drm_helpers.py \
  110_apply_a52xq_cache_fallbacks.py \
  111_apply_a52xq_secure_ion_highmem.py \
  112_apply_a52xq_ddr_topology.py \
  113_apply_a52xq_clk_set_flags.py \
  114_apply_a52xq_drm_legacy_fields.py \
  115_apply_a52xq_all_known_compat.py \
  115_apply_a52xq_all_known_compat_base.py \
  115_apply_a52xq_secondary_compat.py \
  116_apply_a52xq_final_residuals.py \
  123_apply_a52xq_legacy_ion_free_compat.py \
  124_apply_a52xq_ion_qsee_runtime_trace.py \
  140_apply_a52xq_unified_secure_startup_recorder.py \
  141_apply_a52xq_ack_secure_parameter_probe.py \
  143_run_a52xq_early_mirrored_boot_probe.py \
  144_apply_a52xq_qseecom_reserved_mem_shmbridge.py \
  146_apply_a52xq_legacy_system_heap_mask.py \
  148_apply_a52xq_ion_dmabuf_contract.py \
  149_apply_a52xq_ion_system_heap_secure_gate.py \
  153_apply_a52xq_qseecom_ion_heaps.py \
  154_apply_a52xq_failure_window_probe.py; do
  fetch_script "$RUN32_REF" "$name" "$R32"
done

for spec in \
  drivers/interconnect/qcom/sm6350.c \
  drivers/interconnect/qcom/sm6350.h \
  include/dt-bindings/interconnect/qcom,sm6350.h; do
  curl -fL --retry 5 --retry-all-errors --silent --show-error \
    "https://raw.githubusercontent.com/torvalds/linux/${UPSTREAM_LINUX_SHA}/${spec}" \
    -o "$UP/$spec"
  test -s "$UP/$spec"
done

# Source-only placeholders. Historical inspection proved these two artifact
# contents never control source mutation: Workflow52 config is copied only into
# the Phase53 report, and preserved.dts is used only for ownership inventory.
printf '# Phase319 source-only placeholder; content is not consulted by Phase53 staging.\n' > "$WORK/workflow52-resolved.config"
cat > "$WORK/preserved.dts" <<'EOF'
/dts-v1/;
/ { };
EOF

git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" clean -fd

# Historical Run29/30/31 wrappers use Workflow68's integrated config only as a
# deterministic mutation target for compile-only quarantine symbols. Recreate
# exactly one entry for each symbol before replaying 94b. This config never
# becomes the Phase319 build config; exact Phase175 patch identity and the later
# Phase316 source/config oracle still reject any source drift.
WF68_CONFIG="$PWD/workflow68/extracted/integrated.config"
mkdir -p "$(dirname "$WF68_CONFIG")"
cat > "$WF68_CONFIG" <<'EOF'
CONFIG_CAM_CC_LAGOON=y
CONFIG_DISP_CC_LAGOON=y
CONFIG_GPU_CC_LAGOON=y
CONFIG_VIDEO_CC_LAGOON=y
CONFIG_KASAN=y
CONFIG_NPU_CC_LAGOON=y
CONFIG_QCOM_CLK_DEBUG=y
CONFIG_DEBUG_CC_LAGOON=y
EOF

export PYTHONPATH="$HIST${PYTHONPATH:+:$PYTHONPATH}"
python3 "$HIST/53_probe_a52xq_lagoon_phase1.py" stage --gki "$ROOT" --touchgrass "$TG" --seed-config "$WORK/workflow52-resolved.config" --output "$STAGE/53"
python3 "$HIST/54_probe_a52xq_lagoon_clocks_phase2.py" stage --gki "$ROOT" --touchgrass "$TG" --output "$STAGE/54"
python3 "$HIST/55_probe_a52xq_lagoon_remaining_clocks.py" stage --gki "$ROOT" --touchgrass "$TG" --output "$STAGE/55"
python3 "$HIST/56_probe_a52xq_lagoon_earlyboot_dt.py" stage --gki "$ROOT" --touchgrass "$TG" --output "$STAGE/56"
python3 "$HIST/59_probe_a52xq_sm6350_interconnect.py" stage --gki "$ROOT" --upstream "$UP" --output "$STAGE/59"
python3 "$HIST/67_stage_a52xq_native_qsmmuv500.py" --gki "$ROOT" --output "$STAGE/67"
python3 "$HIST/68_stage_a52xq_qsmmuv500_handoff.py" --gki "$ROOT" --output "$STAGE/68"

python3 "$HIST/75_stage_a52xq_msm_watchdog.py" --gki "$ROOT" --output "$STAGE/75"
python3 "$HIST/83_stage_a52xq_early_start_kernel_breadcrumbs.py" --gki "$ROOT" --output "$STAGE/83"
python3 "$HIST/91_stage_a52xq_first_irq_trace.py" --gki "$ROOT" --output "$STAGE/91"
python3 "$HIST/92_stage_a52xq_post_init_flight_recorder.py" --gki "$ROOT" --output "$STAGE/92"
python3 "$HIST/93_stage_a52xq_pid1_exit_trace.py" --gki "$ROOT" --output "$STAGE/93"
python3 "$HIST/94_stage_a52xq_ufs_live_trace.py" --gki "$ROOT" --output "$STAGE/94"
python3 "$HIST/94b_stage_a52xq_ufs_phy_bridge.py" --gki "$ROOT" --output "$STAGE/94b"

git -C "$ROOT" diff --check
printf '%s\n' 'Phase319 regeneration: exact historical Workflow99 source staging PASS'

# Recreate the Run32 full-source boundary. The minimal DTS above is deliberate:
# a52xq_downstream_port_probe_v3 uses it only to produce an ownership report;
# display/secure/IPC source import is independent of those compatible strings.
export PYTHONPATH="$R32${PYTHONPATH:+:$PYTHONPATH}"
python3 "$R32/a52xq_downstream_port_probe_v3.py" --touchgrass "$TG" --gki "$ROOT" --dts "$WORK/preserved.dts" --output "$STAGE/run32"
python3 "$R32/103_apply_a52xq_phase1_compat.py" --touchgrass "$TG" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/104_apply_a52xq_phase2_kernel_api.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/105_apply_a52xq_display_contract.py" --touchgrass "$TG" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/106_apply_a52xq_prfmt_pm_qos.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/107_apply_a52xq_clk_div_debugfs.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/108_apply_a52xq_v4l2_format_abi.py" --touchgrass "$TG" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/109_apply_a52xq_mmio_drm_helpers.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/110_apply_a52xq_cache_fallbacks.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/111_apply_a52xq_secure_ion_highmem.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/112_apply_a52xq_ddr_topology.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/113_apply_a52xq_clk_set_flags.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/114_apply_a52xq_drm_legacy_fields.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/115_apply_a52xq_all_known_compat.py" --touchgrass "$TG" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/116_apply_a52xq_final_residuals.py" --gki "$ROOT" --output "$STAGE/run32"
python3 "$R32/123_apply_a52xq_legacy_ion_free_compat.py" --gki "$ROOT" --output "$STAGE/run32"

grep -Fq 'A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE' "$ROOT/drivers/a52_secure/qseecom.c"
grep -Fq 'A52_ACKFR_EARLY_MIRRORED_BACKEND' "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
printf '%s\n' 'Phase319 regeneration: historical Run32 complete-source boundary PASS'

# Exact source lineage used by the Phase175 producer branch.
python3 scripts/154_apply_a52xq_failure_window_probe.py --gki "$ROOT" --output "$STAGE/154"
python3 scripts/160_apply_a52xq_refgen_regulator.py --gki "$ROOT" --output "$STAGE/160"
python3 scripts/164_apply_a52_refgen_critical_retention.py --gki "$ROOT" --output "$STAGE/164"
python3 scripts/165_apply_a52_active_display_scopes.py --gki "$ROOT" --output "$STAGE/165"
python3 scripts/166_apply_a52_qseecom_ta_heap19.py --gki "$ROOT" --output "$STAGE/166"
python3 scripts/169_apply_a52_heap19_kernel_map.py --gki "$ROOT" --output "$STAGE/169"

TGREF="$WORK/touchgrass-contract"
mkdir -p "$TGREF"
cp "$TG/drivers/staging/android/ion/ion_cma_heap.c" "$TGREF/ion_cma_heap.c"
cp "$TG/drivers/staging/android/ion/msm/msm_ion_of.c" "$TGREF/msm_ion_of.c"
cp "$TG/drivers/staging/android/ion/ion.c" "$TGREF/ion.c"
cp "$TG/drivers/misc/qseecom.c" "$TGREF/qseecom.c"
python3 scripts/171_audit_touchgrass_qseecom_contract.py --gki "$ROOT" --touchgrass "$TGREF" --output "$STAGE/171"
python3 scripts/174_apply_a52_combined_display_lifecycle.py --gki "$ROOT" --output "$STAGE/174"
python3 scripts/175_apply_a52_display_bindcore.py --gki "$ROOT" --output "$STAGE/175"

git -C "$ROOT" diff --check
git -C "$ROOT" add -N .
git -C "$ROOT" diff --binary --no-ext-diff > "$OUT"
test -s "$OUT"
printf '%s  %s\n' "$EXPECTED_PHASE175_SHA256" "$OUT" | sha256sum -c -
printf 'Phase319 regeneration: exact Phase175 patch PASS sha256=%s\n' "$EXPECTED_PHASE175_SHA256"
