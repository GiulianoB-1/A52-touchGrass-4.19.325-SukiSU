#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
python3 - <<'PY'
from pathlib import Path
import zlib,base64,hashlib
payload=zlib.decompress(base64.b64decode(Path('scripts/446t_apply_smmu_hold_no_nested.py.z64').read_text().strip(),validate=True))
expected='c589faa4a829d56a394a839349b8d23f250c467522aa3781927504c2873edc37'
assert hashlib.sha256(payload).hexdigest()==expected
Path('scripts/446t_apply_smmu_hold_no_nested.py').write_bytes(payload)
print('P446T GENERATOR SHA VERIFIED')
PY
python3 -m py_compile scripts/446t_apply_smmu_hold_no_nested.py scripts/446t_early_frame_trace.py
python3 scripts/446t_apply_smmu_hold_no_nested.py --root "$ROOT"
python3 scripts/446t_apply_smmu_hold_no_nested.py --root "$ROOT" --check-only
python3 scripts/446t_early_frame_trace.py --root "$ROOT"
python3 scripts/446t_early_frame_trace.py --root "$ROOT" --check-only
python3 scripts/446q_reconcile_phase_markers.py --root "$ROOT" --output "$PWD/phase446q-ion-audit"
python3 scripts/u2_ab_quiet_legacy_observers.py --root "$ROOT" --check-only
grep -Eq '^CONFIG_CMDLINE=".*no_console_suspend' "$BUILD/.config"
for opt in USB_G_SERIAL USB_CONFIGFS USB_CONFIGFS_ACM USB_CONFIGFS_F_FS U_SERIAL_CONSOLE; do
  grep -Fxq "CONFIG_$opt=y" "$BUILD/.config"
done
python3 -m py_compile scripts/446u_apply_atomic_request_trace.py scripts/446u_expand_p445_store.py scripts/446u_apply_usb_console_handover.py scripts/446u_toggle_usb_handover.py
python3 scripts/446u_apply_atomic_request_trace.py --root "$ROOT"
python3 scripts/446u_apply_atomic_request_trace.py --root "$ROOT" --check-only
python3 scripts/446u_expand_p445_store.py --root "$ROOT"
python3 scripts/446u_expand_p445_store.py --root "$ROOT" --check-only
python3 scripts/446u_apply_usb_console_handover.py --root "$ROOT"
python3 scripts/446u_apply_usb_console_handover.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446v_apply_hwc_read_trace.py scripts/446v_apply_state_diff.py scripts/446v_apply_pmk8350_rtc.py
python3 scripts/446v_apply_hwc_read_trace.py --root "$ROOT"
python3 scripts/446v_apply_hwc_read_trace.py --root "$ROOT" --check-only
python3 scripts/446v_apply_state_diff.py --root "$ROOT"
python3 scripts/446v_apply_state_diff.py --root "$ROOT" --check-only
python3 scripts/446v_apply_pmk8350_rtc.py --root "$ROOT"
python3 scripts/446v_apply_pmk8350_rtc.py --root "$ROOT" --check-only
"$ROOT/scripts/config" --file "$BUILD/.config" --enable RTC_DRV_PM8XXX --enable RTC_HCTOSYS --set-str RTC_HCTOSYS_DEVICE rtc0
python3 -m py_compile scripts/446w_apply_splash_select.py scripts/446w_apply_handoff_option_b.py scripts/446w_apply_ttygs3.py scripts/446w_retarget_boot_console.py
python3 scripts/446w_retarget_boot_console.py --self-test
python3 scripts/446w_apply_splash_select.py --root "$ROOT"
python3 scripts/446w_apply_splash_select.py --root "$ROOT" --check-only
python3 scripts/446w_apply_handoff_option_b.py --root "$ROOT"
python3 scripts/446w_apply_handoff_option_b.py --root "$ROOT" --check-only
python3 scripts/446w_apply_ttygs3.py --root "$ROOT"
python3 scripts/446w_apply_ttygs3.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446w_b_apply_first_hwc_modeset.py scripts/446w_b_apply_dsi_path_trace.py
python3 scripts/446w_b_apply_first_hwc_modeset.py --root "$ROOT"
python3 scripts/446w_b_apply_first_hwc_modeset.py --root "$ROOT" --check-only
python3 scripts/446w_b_apply_dsi_path_trace.py --root "$ROOT"
python3 scripts/446w_b_apply_dsi_path_trace.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446x_apply_stm_touch.py scripts/446x_check_final_touch_dtb.py scripts/446x_apply_touch_boot_dtb.py
python3 scripts/446x_apply_stm_touch.py \
  --tg "$PWD/workspace/touchgrass-a52xq" --root "$ROOT"
python3 scripts/446x_apply_stm_touch.py --root "$ROOT" --check-only
python3 - <<'PY'
import hashlib,base64,zlib
from pathlib import Path
raw=zlib.decompress(base64.b64decode(
    Path('scripts/446y_apply_handoff_i2c.py.z64').read_text().strip(),validate=True))
assert hashlib.sha256(raw).hexdigest()=='0cd9a1eea938e8f3aa74e48d9b11b89cdfacfce3defb476e069d292774beb81d'
# The legacy GENI wrapper guard in the pinned generator had a
# two-line unbraced if followed by a tab-indented resource lookup.
# Clang -Werror=misleading-indentation rejects that generated C.
# Apply only this precisely checked generator correction before
# materializing the source, then pin its corrected SHA-256.
old=b'    if (!gi2c->se.wrapper)\n        return -EPROBE_DEFER;'
new=b'    if (!gi2c->se.wrapper) {\n        return -EPROBE_DEFER;\n    }'
assert raw.count(old)==1, 'P446Y GENI wrapper guard anchor drift'
raw=raw.replace(old,new,1)
assert hashlib.sha256(raw).hexdigest()=='d9b95c7f7ee9ccc14e590d82a5d6b91f737c504babc63132d0488fc0447016b7'
Path('scripts/446y_apply_handoff_i2c.py').write_bytes(raw)
print('P446Y PATCH PAYLOAD SHA-256 VERIFIED; GENI wrapper guard braced, Clang -Wmisleading-indentation avoided')
PY
python3 -m py_compile scripts/446y_apply_handoff_i2c.py
python3 scripts/446y_apply_handoff_i2c.py --root "$ROOT"
python3 -m py_compile scripts/446y_fix_geni_clock_unwind.py
python3 scripts/446y_fix_geni_clock_unwind.py --root "$ROOT"
python3 scripts/446y_fix_geni_clock_unwind.py --root "$ROOT" --check-only
python3 scripts/446y_apply_handoff_i2c.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446z_apply_qup_core_touch.py
python3 scripts/446z_apply_qup_core_touch.py --root "$ROOT"
python3 scripts/446z_apply_qup_core_touch.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446aa_trace_touch_drm.py
python3 scripts/446aa_trace_touch_drm.py --root "$ROOT"
python3 scripts/446aa_trace_touch_drm.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446ab_probe_fifo_and_property_epochs.py
python3 scripts/446ab_probe_fifo_and_property_epochs.py --root "$ROOT"
python3 scripts/446ab_probe_fifo_and_property_epochs.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446ac_stage_gpi.py scripts/446ac_gsi_i2c.py scripts/446ac_hwc_ring.py
python3 scripts/446ac_stage_gpi.py --root "$ROOT"
python3 scripts/446ac_stage_gpi.py --root "$ROOT" --check-only
python3 scripts/446ac_gsi_i2c.py --root "$ROOT"
python3 scripts/446ac_gsi_i2c.py --root "$ROOT" --check-only
python3 scripts/446ac_hwc_ring.py --root "$ROOT"
python3 scripts/446ac_hwc_ring.py --root "$ROOT" --check-only
# 447: distinct dmesg one-shot 40 PRE + trigger CRTC_ID=0 + 20 POST.
# Original 446AC ring is debugfs-only 16/16 and MUST remain intact.
python3 -m py_compile scripts/447_flicker_ring.py
python3 scripts/447_flicker_ring.py --root "$ROOT"
python3 scripts/447_flicker_ring.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446ad_fix_ipc_logging_compat.py
python3 scripts/446ad_fix_ipc_logging_compat.py --root "$ROOT"
python3 scripts/446ad_fix_ipc_logging_compat.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446ad_gsi_crash_guard.py
python3 scripts/446ad_gsi_crash_guard.py --root "$ROOT"
python3 scripts/446ad_gsi_crash_guard.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446ae_dma_wrapper_guard.py
python3 scripts/446ae_dma_wrapper_guard.py --root "$ROOT"
python3 scripts/446ae_dma_wrapper_guard.py --root "$ROOT" --check-only
python3 -m py_compile scripts/446af_gpi_callback_abi.py
python3 scripts/446af_gpi_callback_abi.py --root "$ROOT"
python3 scripts/446af_gpi_callback_abi.py --root "$ROOT" --check-only
python3 -m py_compile scripts/447b_restore_446ag_touch.py
python3 scripts/447b_restore_446ag_touch.py --root "$ROOT"
python3 scripts/447b_restore_446ag_touch.py --root "$ROOT" --check-only
# Phase447: gates for future ports only. No existing W0/W1 driver changed.
python3 -m py_compile scripts/447_stage_control.py scripts/447_manifest_audit.py
python3 scripts/447_stage_control.py --root "$ROOT"
python3 scripts/447_stage_control.py --root "$ROOT" --check-only
# Phase447D: actually bring SE7+SE10 online, keep every other SE OFF.
python3 -m py_compile scripts/447d_se7_se10_i2c.py scripts/447d_readonly_sm5714_fg.py
python3 scripts/447d_se7_se10_i2c.py --root "$ROOT"
python3 scripts/447d_se7_se10_i2c.py --root "$ROOT" --check-only
python3 scripts/447d_readonly_sm5714_fg.py --root "$ROOT"
python3 scripts/447d_readonly_sm5714_fg.py --root "$ROOT" --check-only
# P447E: same SM5714 read-only source, Android-facing battery under
# the existing samsung_mobile_device:battery DT platform device.
python3 -m py_compile scripts/447e_samsung_battery_platform.py scripts/447e_alias_audit.py
python3 scripts/447e_samsung_battery_platform.py --root "$ROOT"
python3 scripts/447e_samsung_battery_platform.py --root "$ROOT" --check-only
python3 scripts/447e_alias_audit.py --root "$ROOT"
python3 scripts/447e_alias_audit.py --root "$ROOT" --check-only
# P447F exact A52 TG numbers and MFD-style FG child path;
# no unported charger/MUIC/PDIC/camera-flash drivers are enabled.
python3 -m py_compile scripts/447f_tg_i2c_nr.py scripts/447f_sm5714_fg_child.py scripts/447f_soc_scaling.py
python3 scripts/447f_tg_i2c_nr.py --root "$ROOT"
python3 scripts/447f_tg_i2c_nr.py --root "$ROOT" --check-only
python3 scripts/447f_sm5714_fg_child.py --root "$ROOT"
python3 scripts/447f_sm5714_fg_child.py --root "$ROOT" --check-only
python3 scripts/447f_soc_scaling.py --root "$ROOT"
python3 scripts/447f_soc_scaling.py --root "$ROOT" --check-only
# 447G is a safety blocker. Emergency charge inhibit is the ONLY
# charger write path; do not enable raw TG charger probe/option bits.
python3 -m py_compile scripts/447g_chg_guard.py
python3 scripts/447g_chg_guard.py --root "$ROOT"
python3 scripts/447g_chg_guard.py --root "$ROOT" --check-only
python3 -m py_compile scripts/447g_ntc_iio.py
python3 scripts/447g_ntc_iio.py --root "$ROOT"
python3 scripts/447g_ntc_iio.py --root "$ROOT" --check-only
# ADC5 read-only compatibility: 0x53 rfc_cam_therm was rejected.
# No claim of battery NTC until DT-to-channel and temp table verify.
python3 -m py_compile scripts/447g_adc5_gpio2.py
python3 scripts/447g_adc5_gpio2.py --root "$ROOT"
python3 scripts/447g_adc5_gpio2.py --root "$ROOT" --check-only
# P447H addendum: ALL FOUR GPIO1..4 (0x52..0x55) must be
# available. Preserve TG's voltage/uV + Samsung NTC DT table
# rather than double-converting with ADC5_CHAN_TEMP.
python3 -m py_compile scripts/447h_adc5_tg_ntc.py
python3 scripts/447h_adc5_tg_ntc.py --root "$ROOT"
python3 scripts/447h_adc5_tg_ntc.py --root "$ROOT" --check-only
# P447H: IIO deferral must RECOVER; capacity_max is learned via
# bounded Samsung-compatible battery sysfs, never set to 920.
python3 -m py_compile scripts/447h_ntc_retry.py scripts/447h_tchg_ntc.py scripts/447h_soc_policy.py
python3 scripts/447h_ntc_retry.py --root "$ROOT"
python3 scripts/447h_ntc_retry.py --root "$ROOT" --check-only
python3 scripts/447h_tchg_ntc.py --root "$ROOT"
python3 scripts/447h_tchg_ntc.py --root "$ROOT" --check-only
python3 scripts/447h_soc_policy.py --root "$ROOT"
python3 scripts/447h_soc_policy.py --root "$ROOT" --check-only
if [ "${A52_VARIANT_ID}" = F ] || [ "${A52_VARIANT_ID}" = G ]; then
  python3 -m py_compile scripts/449f_chg_ntc_guard.py
  python3 scripts/449f_chg_ntc_guard.py --root "$ROOT"
  python3 scripts/449f_chg_ntc_guard.py --root "$ROOT" --check-only
  # P450: no charger enable. Extend F with TG fuel-gauge current/status
  # and a bounded startup IIO wait only when charger HW is confirmed OFF.
  python3 -m py_compile scripts/450_daily_fg_and_guard_diag.py
  python3 scripts/450_daily_fg_and_guard_diag.py --root "$ROOT"
  python3 scripts/450_daily_fg_and_guard_diag.py --root "$ROOT" --check-only
  python3 -m py_compile scripts/451_daily_readonly.py
  python3 scripts/451_daily_readonly.py --root "$ROOT"
  python3 scripts/451_daily_readonly.py --root "$ROOT" --check-only
  if [ "${A52_VARIANT_ID}" = F ]; then
    # 451-DAILY capture 2026-10-24 11:42: real charger status only.
    # No charging enable path or policy transition.
    python3 -m py_compile scripts/453_daily_battery_status.py
    python3 scripts/453_daily_battery_status.py --root "$ROOT"
    python3 scripts/453_daily_battery_status.py --root "$ROOT" --check-only
  fi
  if [ "${A52_VARIANT_ID}" = G ]; then
    python3 -m py_compile scripts/452_h0_sm5714_conservative.py
    python3 scripts/452_h0_sm5714_conservative.py --root "$ROOT"
    python3 scripts/452_h0_sm5714_conservative.py --root "$ROOT" --check-only
  fi
fi
# P447J passive vblank diagnostics retained unchanged.
python3 -m py_compile scripts/447j_vblank_diag.py
python3 scripts/447j_vblank_diag.py --root "$ROOT"
python3 scripts/447j_vblank_diag.py --root "$ROOT" --check-only
# P447K: connect 5.10 KMS CRTC hooks to the original TG
# vblank_ctrl_queue_work path. NO USB/charger/modeset changes.
python3 -m py_compile scripts/447k_vblank_hooks.py
python3 scripts/447k_vblank_hooks.py --root "$ROOT"
python3 scripts/447k_vblank_hooks.py --root "$ROOT" --check-only
# P447I touch: first 48 parsed actions; no event-semantic change.
python3 -m py_compile scripts/447i_touch_lifecycle_trace.py
python3 scripts/447i_touch_lifecycle_trace.py --root "$ROOT"
python3 scripts/447i_touch_lifecycle_trace.py --root "$ROOT" --check-only
# P447L USB: composite bind/UDC start/connect and system PM stages.
# Read-only: no forced USB role, regulator change or wakelock.
python3 -m py_compile scripts/447l_usb_stage_diag.py
python3 scripts/447l_usb_stage_diag.py --root "$ROOT"
python3 scripts/447l_usb_stage_diag.py --root "$ROOT" --check-only
# P447N: passively capture the Android-boot-only touch crash window,
# and name the ConfigFS function returning -ENODEV at composite bind.
python3 -m py_compile scripts/447n_late_touch_usb_function.py
python3 scripts/447n_late_touch_usb_function.py --root "$ROOT"
python3 scripts/447n_late_touch_usb_function.py --root "$ROOT" --check-only
if [ "${A52_VARIANT_ID}" = F ] || [ "${A52_VARIANT_ID}" = G ]; then
  # P460B integration: P453 QUSB regulator vote and tune/PLL recovery
  # apply to BOTH F and G. This is independent of H0 charging permission.
  python3 -m py_compile scripts/453_daily_usb_resume.py scripts/453_daily_usb_vbus_wake.py
  python3 scripts/453_daily_usb_resume.py --root "$ROOT"
  python3 scripts/453_daily_usb_resume.py --root "$ROOT" --check-only
  python3 scripts/453_daily_usb_vbus_wake.py --root "$ROOT"
  python3 scripts/453_daily_usb_vbus_wake.py --root "$ROOT" --check-only
fi
if [ "${A52_VARIANT_IPC}" = 1 ]; then
  python3 -m py_compile scripts/448a_ipc_stage1.py
  python3 scripts/448a_ipc_stage1.py --root "$ROOT" --tg "$PWD/workspace/touchgrass-a52xq"
fi
if [ "${A52_VARIANT_ID}" = E ] || [ "${A52_VARIANT_ID}" = F ] || [ "${A52_VARIANT_ID}" = G ]; then
  python3 -m py_compile scripts/448b_glink_qrtr_stage2.py
  python3 scripts/448b_glink_qrtr_stage2.py --root "$ROOT" --tg "$PWD/workspace/touchgrass-a52xq"
fi
if [ "${A52_VARIANT_ID}" = G ]; then
  python3 -m py_compile scripts/450_dsp_aoss_source_prep.py scripts/451_dsp_pas_source.py scripts/451_dsp_boot_dtb.py
  python3 scripts/450_dsp_aoss_source_prep.py --root "$ROOT"
  python3 scripts/451_dsp_pas_source.py --root "$ROOT"
  python3 -m py_compile scripts/452_scm_ufs_ice_audit.py
  python3 scripts/452_scm_ufs_ice_audit.py --root "$ROOT"
  python3 scripts/450_dsp_aoss_source_prep.py --root "$ROOT" --check-only
  python3 scripts/451_dsp_pas_source.py --root "$ROOT" --check-only
fi
python3 -m py_compile scripts/447c_quiet_frame_trace.py
python3 scripts/447c_quiet_frame_trace.py --root "$ROOT"
python3 scripts/447c_quiet_frame_trace.py --root "$ROOT" --check-only
"$ROOT/scripts/config" --file "$BUILD/.config" --enable QCOM_GPI_DMA --enable DMA_ENGINE
"$ROOT/scripts/config" --file "$BUILD/.config" --enable A52_447D_SM5714_RO --enable QCOM_SPMI_ADC5
"$ROOT/scripts/config" --file "$BUILD/.config" --enable A52_447F_TG_I2C_NR --enable A52_447G_CHG_GUARD
# No charger or PDIC write-capable driver is allowed in 447D stage 1.
bash scripts/446z_setup_resukisu.sh "$ROOT" "$BUILD"
"$ROOT/scripts/config" --file "$BUILD/.config" \
  --enable INPUT_TOUCHSCREEN \
  --enable TOUCHSCREEN_STM_FTS5CU56A \
  --enable I2C_QCOM_GENI \
  --enable INPUT_TOUCHSCREEN_TCLMV2 \
  --disable INPUT_SEC_SECURE_TOUCH \
  --disable TOUCHSCREEN_DUMP_MODE \
  --disable SEC_DEBUG_TSP_LOG \
  --disable TOUCHSCREEN_DUAL_FOLDABLE
sed -i 's/console=ttyGS0,115200/console=ttyGS3,115200/g' "$BUILD/.config"
grep -q 'CONFIG_CMDLINE=".*console=ttyGS3,115200' "$BUILD/.config"
if grep -q '^CONFIG_CMDLINE=".*console=ttyGS0,115200' "$BUILD/.config"; then
  echo 'FAIL: stale ttyGS0 builtin console'; exit 1
fi
# P446AF: retain DWARF in vmlinux for source-line oops decoding.
"$ROOT/scripts/config" --file "$BUILD/.config" --enable DEBUG_KERNEL --enable DEBUG_INFO --enable DEBUG_INFO_DWARF4 --enable DEBUG_INFO_REDUCED
# P447C: compiled fallback per variant; extra_cmdline is truncated by ABL.
"$ROOT/scripts/config" --file "$BUILD/.config" --set-val A52_WAVE_DEFAULT "${A52_VARIANT_WAVE}"
"$ROOT/scripts/config" --file "$BUILD/.config" --set-val LOG_BUF_SHIFT 23
# Phase447M brand: uname -r -> 5.10.x-Mangrove. No Kconfig
# ABI, display, charger or touchscreen runtime behavior changed.
"$ROOT/scripts/config" --file "$BUILD/.config" --set-str LOCALVERSION "-Mangrove" --disable LOCALVERSION_AUTO
# P448 D: wave=2 explicitly, add IPC drivers independently; no W3
# generic subsystem gates, no Q6/PAS, RMTFS or IPA. A/B/C unchanged.
if [ "${A52_VARIANT_IPC}" = 1 ]; then
  "$ROOT/scripts/config" --file "$BUILD/.config" \
    --enable MAILBOX --enable HWSPINLOCK --enable HWSPINLOCK_QCOM \
    --enable QCOM_IPCC --enable QCOM_SMEM --enable QCOM_SMP2P \
    --enable QCOM_AOSS_QMP \
    --disable QCOM_Q6V5_PAS --disable QCOM_Q6V5_ADSP \
    --disable QCOM_Q6V5_MSS --disable QCOM_Q6V5_WCSS \
    --disable QCOM_WCNSS_PIL --disable QCOM_RMTFS_MEM
fi
if [ "${A52_VARIANT_ID}" = E ] || [ "${A52_VARIANT_ID}" = F ] || [ "${A52_VARIANT_ID}" = G ]; then
  "$ROOT/scripts/config" --file "$BUILD/.config" \
    --enable RPMSG --enable RPMSG_CHAR \
    --enable RPMSG_QCOM_GLINK --enable RPMSG_QCOM_GLINK_SMEM \
    --enable QRTR --enable QRTR_SMD --enable QCOM_SYSMON \
    --enable QCOM_QMI_HELPERS
fi
if [ "${A52_VARIANT_MSS}" = 1 ]; then
  "$ROOT/scripts/config" --file "$BUILD/.config" --enable A52_MSS_DEFAULT
else
  "$ROOT/scripts/config" --file "$BUILD/.config" --disable A52_MSS_DEFAULT
fi
if [ "${A52_VARIANT_IPA}" = 1 ]; then
  "$ROOT/scripts/config" --file "$BUILD/.config" --enable A52_IPA_DEFAULT
else
  "$ROOT/scripts/config" --file "$BUILD/.config" --disable A52_IPA_DEFAULT
fi
if [ "${A52_VARIANT_ID}" = G ]; then
  "$ROOT/scripts/config" --file "$BUILD/.config" \
    --enable REMOTEPROC --enable RPMSG_QCOM_SMD \
    --enable QCOM_Q6V5_PAS --enable QCOM_SCM \
    --disable QCOM_SCM_DOWNLOAD_MODE_DEFAULT
fi
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
grep -Fxq 'CONFIG_LOCALVERSION="-Mangrove"' "$BUILD/.config"
grep -Fxq '# CONFIG_LOCALVERSION_AUTO is not set' "$BUILD/.config"
if [ "${A52_VARIANT_IPC}" = 1 ]; then
  if [ "${A52_VARIANT_ID}" = G ]; then
    python3 scripts/448a_ipc_stage1.py --root "$ROOT" --config "$BUILD/.config" --check-only --manual-pas
  else
    python3 scripts/448a_ipc_stage1.py --root "$ROOT" --config "$BUILD/.config" --check-only
  fi
fi
if [ "${A52_VARIANT_ID}" = E ] || [ "${A52_VARIANT_ID}" = F ] || [ "${A52_VARIANT_ID}" = G ]; then
  if [ "${A52_VARIANT_ID}" = G ]; then
    python3 scripts/448b_glink_qrtr_stage2.py --root "$ROOT" --config "$BUILD/.config" --check-only --manual-pas
    python3 scripts/450_dsp_aoss_source_prep.py --root "$ROOT" --check-only
    python3 scripts/451_dsp_pas_source.py --root "$ROOT" --check-only
    python3 scripts/452_scm_ufs_ice_audit.py --root "$ROOT" \
      --config "$BUILD/.config" --check-only \
      --audit-output "$PWD/phase447-inventory/P452-SCM-CALLERS.json"
  else
    python3 scripts/448b_glink_qrtr_stage2.py --root "$ROOT" --config "$BUILD/.config" --check-only
  fi
fi
# Kernelrelease reads include/config/auto.conf. A restored build
# cache can leave it stale after olddefconfig updated .config.
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 syncconfig
KREL="$(make -s -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 kernelrelease)"
case "$KREL" in *-Mangrove) ;; *) echo "FAIL: Mangrove kernelrelease mismatch: $KREL"; exit 1;; esac
printf 'Mangrove Kernel release: %s\n' "$KREL" | tee -a "$GITHUB_STEP_SUMMARY"
grep -Fxq "CONFIG_A52_WAVE_DEFAULT=${A52_VARIANT_WAVE}" "$BUILD/.config"
grep -Fxq CONFIG_LOG_BUF_SHIFT=23 "$BUILD/.config"
grep -Fxq CONFIG_A52_447D_SM5714_RO=y "$BUILD/.config"
grep -Fxq CONFIG_QCOM_SPMI_ADC5=y "$BUILD/.config"
grep -Fxq CONFIG_A52_I2C_SE_ALLOW_DEFAULT=0x580 "$BUILD/.config"
grep -Fxq CONFIG_A52_447F_TG_I2C_NR=y "$BUILD/.config"
grep -Fxq CONFIG_A52_447G_CHG_GUARD=y "$BUILD/.config"
if [ "${A52_VARIANT_MSS}" = 1 ]; then grep -Fxq CONFIG_A52_MSS_DEFAULT=y "$BUILD/.config"; else grep -Fxq "# CONFIG_A52_MSS_DEFAULT is not set" "$BUILD/.config"; fi
if [ "${A52_VARIANT_IPA}" = 1 ]; then grep -Fxq CONFIG_A52_IPA_DEFAULT=y "$BUILD/.config"; else grep -Fxq "# CONFIG_A52_IPA_DEFAULT is not set" "$BUILD/.config"; fi
echo "P447C COMPILED_CONFIG wave=${A52_VARIANT_WAVE} mss=${A52_VARIANT_MSS} ipa=${A52_VARIANT_IPA} logbuf=8MiB" >> "$GITHUB_STEP_SUMMARY"
grep -Fxq CONFIG_DEBUG_INFO=y "$BUILD/.config"
grep -Fxq CONFIG_DEBUG_INFO_DWARF4=y "$BUILD/.config"
grep -Fxq CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y "$BUILD/.config"
grep -Fxq CONFIG_I2C_QCOM_GENI=y "$BUILD/.config"
grep -Fxq CONFIG_QCOM_GPI_DMA=y "$BUILD/.config"
grep -Fxq CONFIG_KSU=y "$BUILD/.config"
grep -Fxq CONFIG_KSU_TRACEPOINT_HOOK=y "$BUILD/.config"
grep -Fxq CONFIG_KALLSYMS_ALL=y "$BUILD/.config"
grep -Fxq "# CONFIG_KSU_SUSFS is not set" "$BUILD/.config"
grep -Fxq CONFIG_QCOM_GENI_SE=y "$BUILD/.config"
grep -Fxq CONFIG_INPUT_TOUCHSCREEN_TCLMV2=y "$BUILD/.config"
grep -Fxq CONFIG_INPUT_TOUCHSCREEN=y "$BUILD/.config"
grep -Fxq CONFIG_RTC_DRV_PM8XXX=y "$BUILD/.config"
grep -Fxq CONFIG_RTC_HCTOSYS=y "$BUILD/.config"
grep -Fxq 'CONFIG_RTC_HCTOSYS_DEVICE="rtc0"' "$BUILD/.config"
grep -q 'CONFIG_CMDLINE=".*console=ttyGS3,115200' "$BUILD/.config"
echo 'P446AC default cold=0 handoff=0, GPI DMA built-in, KSU rc3, ttyGS3 and PMK8350 RTC PASS'
