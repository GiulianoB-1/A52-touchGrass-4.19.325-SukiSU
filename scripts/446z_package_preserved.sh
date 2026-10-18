#!/usr/bin/env bash
# P446Z: unchanged Phase446W-B/X packaging, extracted from oversized workflow job.
set -Eeuo pipefail
ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/f0bisect-gki-p6-out"
SOURCE_BOOT="$PWD/phase429-gki-out/package/boot-phase429-apex-sf-focused-census-v1.img"
FINAL="$OUT/package/boot-f0bisect-gki-p6-v1.img"
rm -rf "$OUT"
cp -a phase429-gki-out "$OUT"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
cp "$BUILD/System.map" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
mkdir -p "$OUT/source" "$OUT/audit"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/source/a52_ack_secure_flight_recorder.c"
cp "$ROOT/drivers/a52_display/msm/a52_phase445.c" "$OUT/source/a52_phase445.c"
cp "$ROOT/drivers/regulator/refgen.c" "$OUT/source/refgen.c"
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" "$OUT/source/msm_drv.c"
cp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$OUT/source/msm_smmu.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$OUT/source/sde_kms.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_encoder_phys_cmd.c" "$OUT/source/sde_encoder_phys_cmd.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c" "$OUT/source/dsi_display.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" "$OUT/source/dsi_ctrl.c"
for f in drivers/clk/clk.c drivers/clk/qcom/clk-rcg2.c drivers/clk/qcom/dispcc-lagoon.c drivers/a52_display/msm/dsi/dsi_clk_manager.c; do
  if test -f "$ROOT/$f"; then cp "$ROOT/$f" "$OUT/source/$(basename "$f")"; else echo "COLD0 audit: not found $f" >> "$OUT/audit/source-missing.txt"; fi
done
cp "$ROOT/drivers/a52_display/msm/sde/sde_plane.c" "$OUT/source/sde_plane.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_crtc.c" "$OUT/source/sde_crtc.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_fence.c" "$OUT/source/sde_fence.c"
cp scripts/crash1_fix_gki_dma_fence_union.py "$OUT/source/crash1_fix_gki_dma_fence_union.py"
cp scripts/fence1b_disable_sde_dbg_panic.py "$OUT/source/fence1b_disable_sde_dbg_panic.py"
cp scripts/fence1c_quiet_sde_evtlog.py "$OUT/source/fence1c_quiet_sde_evtlog.py"
cp scripts/fence1c_fix_dsi_pll_hw_init.py "$OUT/source/fence1c_fix_dsi_pll_hw_init.py"
cp scripts/fence1g_nopanic_trace_cold_on.py "$OUT/source/fence1g_nopanic_trace_cold_on.py"
cp scripts/446k_apply_matched_cold_compare.py "$OUT/source/446k_apply_matched_cold_compare.py"
cp scripts/446l_apply_post_trigger_twin.py "$OUT/source/446l_apply_post_trigger_twin.py"
cp scripts/446l_apply_smmu_cb2_live.py "$OUT/source/446l_apply_smmu_cb2_live.py"
cp scripts/446m_apply_gdsc_irq_ab.py "$OUT/source/446m_apply_gdsc_irq_ab.py"
cp scripts/446n_apply_dsi_irq_rearm.py "$OUT/source/446n_apply_dsi_irq_rearm.py"
cp scripts/446p_apply_qcom_dsi_packet.py "$OUT/source/446p_apply_qcom_dsi_packet.py"
cp scripts/446q_reconcile_phase_markers.py "$OUT/source/446q_reconcile_phase_markers.py"
cp scripts/446q_apply_ion_prime_pipeline_trace.py "$OUT/source/446q_apply_ion_prime_pipeline_trace.py"
cp scripts/446r_apply_commit_gem_lifetime.py "$OUT/source/446r_apply_commit_gem_lifetime.py"
cp scripts/446r2_bound_rpmh_tcs.py "$OUT/source/446r2_bound_rpmh_tcs.py"
cp scripts/446r2_apply_pon_wake_diag.py "$OUT/source/446r2_apply_pon_wake_diag.py"
cp scripts/a52_final_tree_manifest_audit.py "$OUT/source/a52_final_tree_manifest_audit.py"
cp scripts/446u_apply_atomic_request_trace.py scripts/446u_expand_p445_store.py scripts/446u_apply_usb_console_handover.py scripts/446u_toggle_usb_handover.py "$OUT/source/"
cp scripts/446v_apply_hwc_read_trace.py scripts/446v_apply_state_diff.py scripts/446v_apply_pmk8350_rtc.py "$OUT/source/"
cp scripts/446w_apply_splash_select.py scripts/446w_apply_handoff_option_b.py scripts/446w_apply_ttygs3.py scripts/446w_retarget_boot_console.py "$OUT/source/"
cp scripts/446w_b_apply_first_hwc_modeset.py scripts/446w_b_apply_dsi_path_trace.py "$OUT/source/"
cp scripts/446x_apply_stm_touch.py scripts/446x_check_final_touch_dtb.py scripts/446x_apply_touch_boot_dtb.py scripts/446y_apply_handoff_i2c.py scripts/446y_apply_handoff_i2c.py.z64 scripts/446y_fix_geni_clock_unwind.py scripts/446z_apply_qup_core_touch.py scripts/446aa_trace_touch_drm.py scripts/446ab_probe_fifo_and_property_epochs.py scripts/446ac_stage_gpi.py scripts/446ac_gsi_i2c.py scripts/446ac_hwc_ring.py scripts/446z_integrate_resukisu.py scripts/446z_setup_resukisu.sh "$OUT/source/" 
mkdir -p "$OUT/source/touchscreen_fts5cu56a" "$OUT/source/sec_input"
cp "$ROOT/drivers/input/touchscreen/stm/fts5cu56a/"{fts_ts.c,fts_fwu.c,fts_sec.c,fts_ts.h,Kconfig,Makefile} "$OUT/source/touchscreen_fts5cu56a/"
git -C "$ROOT/KernelSU" rev-parse HEAD > "$OUT/source/446z-resukisu-commit.txt"
printf "%s\n" "35233" > "$OUT/source/446ac-resukisu-driver.txt"
printf "%s\n" "BakaSU b2c595ddfa64b685e5a144ad37b7c95f912fb92a builtin tracepoint" > "$OUT/source/446ac-resukisu-pin.txt"
cp "$ROOT/KernelSU/kernel/core/init.c" "$OUT/source/446z-resukisu-init.c"
cp "$ROOT/drivers/i2c/busses/i2c-qcom-geni.c" "$OUT/source/446z-i2c-qcom-geni.c"
cp "$ROOT/drivers/gpu/drm/drm_atomic_uapi.c" "$OUT/source/446aa-drm-atomic-uapi.c"
cp "$ROOT/drivers/gpu/drm/drm_mode_object.c" "$OUT/source/446ab-drm-mode-object.c"
cp "$ROOT/drivers/dma/qcom/gpi.c" "$OUT/source/446ac-tg-gpi.c"
cp "$ROOT/drivers/dma/qcom/msm_gpi_mmio.h" "$OUT/source/446ac-msm_gpi_mmio.h"
cp "$ROOT/include/linux/msm_gpi.h" "$OUT/source/446ac-msm_gpi.h"
cp "$ROOT/drivers/gpu/drm/drm_atomic_uapi.c" "$OUT/source/446ac-hwc-ring.c"
python3 scripts/446ac_stage_gpi.py --root "$ROOT" --check-only
python3 scripts/446ac_gsi_i2c.py --root "$ROOT" --check-only
python3 scripts/446ac_hwc_ring.py --root "$ROOT" --check-only
cp "$ROOT/drivers/soc/qcom/qcom-geni-se.c" "$OUT/source/446z-qcom-geni-se.c"

cp "$ROOT/drivers/input/common/"{sec_cmd.c,sec_tclm_v2.c,sec_secure_touch.c} "$OUT/source/sec_input/"
cp "$ROOT/include/linux/input/"{sec_cmd.h,sec_tclm_v2.h,sec_secure_touch.h,sec_tsp_log.h} "$OUT/source/sec_input/"
for f in drivers/gpu/drm/drm_mode_object.c drivers/gpu/drm/drm_property.c drivers/gpu/drm/drm_connector.c drivers/gpu/drm/drm_crtc.c drivers/gpu/drm/drm_encoder.c drivers/rtc/rtc-pm8xxx.c drivers/a52_display/msm/sde/sde_encoder.c; do
  cp "$ROOT/$f" "$OUT/source/$(basename "$f")"
done
cp "$ROOT/drivers/usb/gadget/legacy/serial.c" "$ROOT/drivers/usb/gadget/configfs.c" "$ROOT/drivers/usb/gadget/function/u_serial.c" "$OUT/source/"
cp scripts/446s_apply_memlat_guards.py scripts/446s_apply_frame_trace.py scripts/446s_apply_suspend_timing.py scripts/446s_apply_arm_memlat_pmu.py scripts/446s_port_legacy_pmic_gpio.py scripts/446s_publish_final_inputs.py scripts/446s_apply_gpu_slumber_trace.py scripts/446s_apply_kgsl_smmu_runtime_pm.py "$OUT/source/"
cp "$ROOT/drivers/devfreq/arm-memlat-mon.c" "$ROOT/drivers/pinctrl/qcom/a52-pinctrl-spmi-gpio.c" "$OUT/source/"
cp "$ROOT/drivers/gpu/msm/kgsl_gmu.c" "$ROOT/drivers/gpu/msm/kgsl_device.h" "$ROOT/drivers/regulator/core.c" "$ROOT/include/linux/a52_gpu_cx_trace.h" "$OUT/source/"
cp "$ROOT/drivers/devfreq/governor_memlat.c" "$ROOT/drivers/devfreq/devfreq.c" "$ROOT/drivers/gpu/drm/drm_atomic_uapi.c" "$ROOT/drivers/base/power/main.c" "$OUT/source/"
cp "$ROOT/include/generated/a52_446s_manifest.h" "$OUT/source/"
cp "$ROOT/drivers/a52_secure/a52_pon_wake_diag.c" "$OUT/source/a52_pon_wake_diag.c"
cp "$ROOT/drivers/soc/qcom/rpmh-rsc.c" "$OUT/source/446r2-rpmh-rsc.c"
cp "$PWD/phase446r2-manifest/final-tree.json" "$OUT/audit/final-tree-manifest.json"
cp "$PWD/phase446r2-manifest/final-tree.json" "$OUT/package/PHASE446S-MANIFEST.json"
cp "$ROOT/drivers/a52_display/msm/msm_gem.c" "$OUT/source/446r-msm_gem.c"
cp "$ROOT/drivers/staging/android/ion/ion_dma_buf.c" "$OUT/source/446q-ion_dma_buf.c"
cp "$ROOT/drivers/a52_display/msm/msm_gem_prime.c" "$OUT/source/446q-msm_gem_prime.c"
cp "$PWD/phase446q-ion-audit/phase446q-final-tree-marker-audit.json" "$OUT/audit/"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" "$OUT/source/446n-dsi_ctrl.c"
cp "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" "$OUT/source/446m-a52-legacy-gdsc-regulator.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_irq.c" "$OUT/source/446m-sde_irq.c"
cp "$ROOT/drivers/a52_display/pll/dsi_pll_10nm.c" "$OUT/source/dsi_pll_10nm.c"
for p in dsi_pll_7nm.c dsi_pll_14nm_util.c; do
  test ! -f "$ROOT/drivers/a52_display/pll/$p" || cp "$ROOT/drivers/a52_display/pll/$p" "$OUT/source/$p"
done
cp "$ROOT/arch/arm64/mm/fault.c" "$OUT/source/arm64_fault.c"
cp scripts/crash1_apply_fault_and_frameevent.py "$OUT/source/crash1_apply_fault_and_frameevent.py"
cp "$ROOT/drivers/a52_display/msm/msm_atomic.c" "$OUT/source/msm_atomic.c"
cp "$ROOT/drivers/a52_display/msm/sde_power_handle.c" "$OUT/source/sde_power_handle.c"
cp "$ROOT/drivers/soc/qcom/msm_bus/msm_bus_arb_rpmh.c" "$OUT/source/msm_bus_arb_rpmh.c"
cp "$ROOT/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c" "$OUT/source/msm_bus_fabric_rpmh.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" "$OUT/source/arm-smmu.c"
cp "$ROOT/drivers/a52_display/msm/sde_dbg.c" "$OUT/source/sde_dbg.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" "$OUT/source/dsi_ctrl.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c" "$OUT/source/dsi_ctrl_hw_cmn.c"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c" "$OUT/source/dsi_display.c"
cp "$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_sysfs.c" "$OUT/source/ss_dsi_panel_sysfs.c"
cp "$ROOT/kernel/exit.c" "$OUT/source/kernel_exit.c"
cp "$ROOT/arch/arm64/kernel/syscall.c" "$OUT/source/arm64_syscall.c"
cp "$ROOT/drivers/base/core.c" "$OUT/source/base_core.c"
cp "$ROOT/drivers/base/component.c" "$OUT/source/component.c"
cp "$ROOT/drivers/clk/qcom/dispcc-lagoon.c" "$OUT/source/dispcc-lagoon.c"
cp "$ROOT/drivers/pinctrl/qcom/pinctrl-msm.c" "$OUT/source/pinctrl-msm.c"
cp "$ROOT/drivers/pinctrl/qcom/pinctrl-lagoon.c" "$OUT/source/pinctrl-lagoon.c"
PLL_DRV="$(find "$ROOT" -path "*/pll/pll_drv.c" | head -1)"
DSI_PLL7="$(find "$ROOT" -path "*/pll/dsi_pll_7nm.c" | head -1)"
[ -n "$PLL_DRV" ] && cp "$PLL_DRV" "$OUT/source/pll_drv.c"
[ -n "$DSI_PLL7" ] && cp "$DSI_PLL7" "$OUT/source/dsi_pll_7nm.c"
cp "$ROOT/drivers/base/power/runtime.c" "$OUT/source/runtime_pm.c"
cp "$ROOT/drivers/base/power/domain.c" "$OUT/source/genpd_domain.c"
cp "$ROOT/drivers/regulator/core.c" "$OUT/source/regulator_core.c"
cp scripts/437_apply_kgsl_open_corridor.py \
   scripts/438_apply_devbw_semantic_parity.py \
   scripts/439_apply_dsi_autopsy.py \
   scripts/440_apply_early_f0_cleanup_forensics.py \
   scripts/441_apply_vrr_state_compat.py \
   scripts/444_apply_deep_passive_compare.py \
   scripts/444_apply_deep_passive_compare.py.z64 \
   scripts/445_apply_recovery_ladder.py \
   scripts/445_apply_recovery_ladder.py.z64 \
   scripts/decode_phase444.py \
   scripts/decode_phase445.py \
   scripts/decode_phase445.py.z64 \
   scripts/decode_phase444.py.z64 \
   scripts/decode_phase439_dsi.py \
   scripts/decode_phase437_open.py \
   scripts/446_apply_heartbeat_cb2.py scripts/446_apply_heartbeat_cb2.py.z64 \
   scripts/446b_apply_heartbeat_fixes.py scripts/446c_apply_takeover_window.py \
   scripts/446g_apply_te_irq_test.py \
   scripts/446h_apply_panel_f0_microscope.py \
   scripts/446i_apply_passive_f0_twin.py \
   scripts/446d_apply_power_clock_microscope.py scripts/446d_apply_power_clock_microscope.py.z64 \
   scripts/446d_apply_capacity_guard.py \
   scripts/446d_apply_extra_observers.py \
   scripts/446d_collect_boot_identity.sh \
   scripts/decode_phase446.py scripts/decode_phase446.py.z64 \
   scripts/decode_phase446d.py scripts/decode_phase446d.py.z64 \
   scripts/decode_phase446g.py \
   scripts/decode_phase446_pair.py \
   scripts/446_append_boot_cmdline.py \
   scripts/u2_apply_gki_usb_composite_handover.py \
   scripts/u2_ab_quiet_legacy_observers.py "$OUT/audit/"
cp /tmp/p435-overlap.txt /tmp/p436-overlap.txt /tmp/p439-sync-state.txt /tmp/p441-vrr-audit.txt /tmp/u2-ab-quiet-report.json "$OUT/audit/" 2>/dev/null || true
gzip -n -c "$BUILD/arch/arm64/boot/Image" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source "$SOURCE_BOOT" --kernel "$OUT/package/Image.gz" \
  --output "$FINAL" --report "$OUT/package/phase446-repack-report.json"
python3 scripts/446_append_boot_cmdline.py \
  --source "$FINAL" --output "$FINAL.usb" \
  --append "a52.usb_handover=1" \
  --report "$OUT/package/phase446u-usb-cmdline-report.json"
mv "$FINAL.usb" "$FINAL"
grep -aFq 'a52.usb_handover=1' "$FINAL"
python3 scripts/446w_retarget_boot_console.py \
  --source "$FINAL" --output "$FINAL.tty3" \
  --kernel-config "$BUILD/.config" \
  --from-line 0 --to-line 3 \
  --report "$OUT/package/phase446w-console-retarget.json"
mv "$FINAL.tty3" "$FINAL"
grep -Fq '"status": "bootloader-console-null-preserved"' "$OUT/package/phase446w-console-retarget.json"
grep -Fq '"image_all_bytes_unchanged": true' "$OUT/package/phase446w-console-retarget.json"
python3 scripts/446_append_boot_cmdline.py \
  --source "$FINAL" --output "$FINAL.cold" \
  --append "a52.446w_cold=0" \
  --report "$OUT/package/phase446w-cold-off.json"
mv "$FINAL.cold" "$FINAL"
python3 scripts/446_append_boot_cmdline.py \
  --source "$FINAL" --output "$FINAL.boff" \
  --append "a52.446w_handoff=0" \
  --report "$OUT/package/phase446w-handoff-off.json"
mv "$FINAL.boff" "$FINAL"
python3 scripts/446_append_boot_cmdline.py \
  --source "$FINAL" --output "$FINAL.port" \
  --append "a52.446w_tty=3" \
  --report "$OUT/package/phase446w-port3.json"
mv "$FINAL.port" "$FINAL"
grep -aFq 'console=null' "$FINAL"
grep -Fq 'CONFIG_CMDLINE_EXTEND=y' "$BUILD/.config"
grep -q 'CONFIG_CMDLINE=".*console=ttyGS3,115200' "$BUILD/.config"
grep -aFq 'console=ttyGS3,115200' "$BUILD/arch/arm64/boot/Image"
grep -aFq 'a52.446w_cold=0' "$FINAL"
grep -aFq 'a52.446w_handoff=0' "$FINAL"
if grep -aFq 'a52.446w_handoff=1' "$FINAL"; then echo "P446AB BOOT FAIL: conflicting handoff=1"; exit 1; fi
python3 scripts/446_append_boot_cmdline.py \
  --source "$FINAL" --output "$FINAL.touch" \
  --append "a52.touch=1" \
  --report "$OUT/package/phase446x-touch-enabled.json"
mv "$FINAL.touch" "$FINAL"
grep -aFq 'a52.touch=1' "$FINAL"
# Published 446T FDT does NOT contain TG's R02 touchscreen and
# qupv3_se8_i2c is disabled; apply the exact R02 target here.
python3 scripts/446x_apply_touch_boot_dtb.py \
  --boot "$FINAL" --output "$FINAL.touchdtb" \
  --report "$OUT/package/phase446x-touch-dtb-patch.json"
mv "$FINAL.touchdtb" "$FINAL"
grep -aFq 'a52.446w_tty=3' "$FINAL"
python3 scripts/446x_check_final_touch_dtb.py \
  --boot "$FINAL" \
  --report "$OUT/package/phase446x-final-touch-dtb.json"
cp scripts/446u_toggle_usb_handover.py "$OUT/package/"
python3 - "$SOURCE_BOOT" "$FINAL" "$OUT/package" <<'PY'
import runpy,json,sys
from pathlib import Path
parse=runpy.run_path('scripts/38_repack_a52_p1_boot.py')['parse_boot']
previous=parse(Path(sys.argv[1]).read_bytes())
rebuilt=parse(Path(sys.argv[2]).read_bytes())
for key in ('ramdisk','recovery_dtbo'):
    assert rebuilt[key]==previous[key], 'Phase446T boot must preserve '+key
assert rebuilt['dtb']!=previous['dtb'], 'Phase446X STM overlay must change DTB'
assert b'stm,fts_touch' in rebuilt['dtb'], 'Phase446X must pack touch into FINAL DTB'
raw=Path(sys.argv[2]).read_bytes()
assert b'a52.cb2_reuse=1' in raw and b'no_console_suspend' in raw
target=Path(sys.argv[3])
(target/'phase446-cb2-cmdline-report.json').write_text(json.dumps({'token':'a52.cb2_reuse=1','preserved_from_phase446s':True},indent=2)+'\n')
(target/'fence1f-no-console-suspend-report.json').write_text(json.dumps({'token':'no_console_suspend','preserved_from_phase446s':True},indent=2)+'\n')
print('P446T preserved Phase446S DTB ramdisk and cmdline PASS')
PY
cp "$FINAL" "$OUT/package/boot.img"
cmp -s "$FINAL" "$OUT/package/boot.img"
grep -aFq 'no_console_suspend' "$OUT/package/boot.img"
grep -aFq 'a52.cb2_reuse=1' "$OUT/package/boot.img"
test "$(grep -Ec '^(CONFIG_PSTORE_CONSOLE|CONFIG_PSTORE_RAM)=y$' "$BUILD/.config")" -eq 2
grep -Fq 'A52_PHASE446R_COMMIT_GEM_LIFETIME_V1' "$ROOT/drivers/a52_display/msm/msm_atomic.c"
grep -Fq 'A52_PHASE446R_COMMIT_GEM_LIFETIME_V1' "$ROOT/drivers/a52_display/msm/msm_gem.c"
test "$(stat -c '%s' "$FINAL")" -eq 100663296
grep -Fq 'a52.cb2_reuse=1' "$OUT/package/phase446-cb2-cmdline-report.json"
test "$(stat -c '%s' "$FINAL")" -eq 100663296
grep -Fq 'a52.cb2_reuse=1' "$OUT/package/phase446-cb2-cmdline-report.json"
cp scripts/446d_collect_boot_identity.sh "$OUT/package/"
chmod +x "$OUT/package/446d_collect_boot_identity.sh"
sha256sum "$FINAL" > "$OUT/package/EXPECTED-BOOT-SHA256.txt"
cat > "$OUT/PHASE446D-NOTES.txt" <<'EOF'
Phase446AB: a52.446w_cold=0 and a52.446w_handoff=0 by default.
Splash rails/votes remain adopted, but forced first-HWC Plan B modeset is
OFF for this baseline diagnostic. a52.446w_handoff=1 remains available
as a controlled boot-time alternative via cmdline.
Early legacy USB serial on ttyGS3, U446 checks only line3;
ttyGS0 remains for Samsung smdexe/DUN. U446 decisions printk.
P446V PMK8350 RTC support is MUST-KEEP.
Full post-cycle SDE/DSI state snapshot.
Phase446V: retain Phase446T and 446U-v2 fixes. Passive read-side HWC GETPROPERTIES, GETPROPERTY, GETBLOB,
GETCONNECTOR, GETCRTC and GETENCODER with full property names/values, mode and blob contents.
Three SDE/DSI/Samsung state snapshots: first HWC atomic, before first panel off,
first HWC atomic after NORMAL DISPLAY_ON (not AOD/LP).
Second bounded full atomic window: 100 commits after normalized display on.
Minimal PMK8350 0x61xx RTC/0x62xx alarm register mapping; RTC built in and HCTOSYS rtc0.
Phase446U: keep every Phase446T fix and run Parts A and B in one boot.
At 85s atomic ioctl property requests are logged with names, object
types, values and join IDs. Plane91 pre/post, SDE validation, FB/CRTC
setter and brightness traces are passive. P445 cap now 240.
USB: single-flight work item outside ConfigFS gi->lock; ttyGS0 count preflight,
block new opens, 500ms bounded worker/request ack, console unregister,
g_serial unbind only on safe quiescence; Android ConfigFS binds UDC.
If ttyGS0 has an opener, return -EAGAIN (ConfigFS write must retry) and keep g_serial.
Boot flag a52.usb_handover=1 (default); toggle to =0 for COM3
using scripts/446u_toggle_usb_handover.py without repacking.
F0BAR: actual autorefresh-disable path instrumented in sde_encoder_phys_cmd.c.
F0BAR 1=no SW autorefresh, 2=before existing disable, 3=after.
AR status bit7, pending kickoff, encoder state, rd/wr pointer recorded.
P1 still means DSI prepare ENTRY and cannot establish logo idle.
Missing F0BAR or unavailable HW read means quiescence NOT proven.
CRASH1 GKI: F0B ACTIVE stage ZERO, NEVER send raw FIFO F0.
Original Samsung F0 remains unmodified. Frame-event breadcrumbs, ARM64
FAR/PC/LR and OOPS/PANIC last 4KiB kmsg x16 go to P445 reserved RAM.
kmsg dumping requires kmsg_dump to actually run: a silent hard hang
does not guarantee a report. Fault hook runs before printk.
COM3 remains with g_serial. panic=0; no auto reboot.
F0-BISECT-GKI P6: same continuous-COM3 observer-quiet GKI.
Exactly ONE guarded hardware-FIFO F0 after DSI_LINK_CLK_ON per boot.
P1..P6 snapshots logged to PuTTY and P445 RAM (one 16-copy section per stage).
P1 is dsi_display_prepare entry, NOT proof autorefresh stopped.
P2..P6 are after core clock, clock source, controller init, host on, link clock on.
Early P1..P4 are PASSIVE (not valid standalone F0 probes).
F0 has side effects; PASS TG P6 is prerequisite before causal inferences.
Skipped raw probe (rc=3) means prereqs unverified, NOT an F0 failure.
446U replaces this historical behavior with opt-out bounded USB handover.
Disabled: known Phase446 heartbeat/burst MMIO reads, Phase446i pre/post F0 sweeps,
and legacy P430/P431/P385 diagnostic sampling threads where present.
Audit report: audit/u2-ab-quiet-report.json.
This is not an exhaustive audit of every historical observer; no hardware proof.
With a52.usb_handover=1 Android ConfigFS should take the UDC after ttyGS0 quiesces.
With a52.usb_handover=0 PuTTY COM3 stays and ADB is unavailable.
Keep the known-good Phase446T image for hardware rollback.
Phase446i GKI matched passive F0 environment twin
==================================================
Established:
  - Phase446g/h TE IRQ skip keeps the inherited splash streaming until Android handoff.
  - F0 still enters DSI BUSY, lanes remain LP-11, DMA_DONE never asserts.
  - R1 controller reset and R2 ctrl/PHY/PLL rebuild already failed.
Phase446i adds observation only:
  - I_SW_ENTRY: cached DSI controller states and requested byte/pixel/byte_intf/esc rates.
  - I_RAW_PRE / I_RAW_POST: full 0x88e7000..0x88e70ff refgen dump,
    known DISPCC RCG CMD/CFG/M/N/D, PLL0 registers, selected MDP/INTF state,
    direct DSI STATUS/LANE/CLK/INT and compact debug selectors.
  - I_BUS_PRE / I_BUS_POST: full Kona DSI debug selector table.
  - hot-path capture uses MMIO only: no clk API, regulator API, allocation, printk,
    runtime-PM, reset, force-clock, or recovery write.
Refgen source audit:
  - GKI port is Kona-only: qcom,refgen-kona-regulator + refgen_kona_ops.
  - generic +0x08/+0x14 BIAS_EN/BG_CTRL path is absent.
  - therefore the earlier generic-ops corruption theory is source-ruled-out.
GKI retains the proven TE IRQ skip solely so F0 is tested on a live display link.
Compare I_* sections directly against the Phase446i TouchGrass twin.
EOF
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | \
  xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
sha256sum "$FINAL"
INPUTS="$PWD/phase446s-final-inputs"
python3 scripts/446s_publish_final_inputs.py \
  --root "$ROOT" --build "$BUILD" --boot "$FINAL" \
  --manifest "$PWD/phase446r2-manifest/final-tree.json" \
  --output "$INPUTS"
test -s "$INPUTS/final-patched-gki-source.tar.zst"
test -s "$INPUTS/boot-final.dtb"
test -s "$INPUTS/boot-final.dts"
{
  echo '### Phase446S final build inputs'
  echo "Artifact: A52XQ-PHASE446WX-FINAL-INPUTS-$GITHUB_RUN_NUMBER"
  echo 'Files: final.config, System.map, modules.builtin/order (if built), boot-final.dtb and decompiled DTS, available DTBO/DTS, final-patched-gki-source.tar.zst and hash manifest.'
  echo 'Any unavailable separate DTBO is explicitly documented.'
} >> "$GITHUB_STEP_SUMMARY"
