# Phase 452 H0 and SCM: safety gate, source-verified

Status: STAGING ONLY; no flashable combined image published.
Reference: TG micr0softstore/samsung_android_kernel_a52xq @ 6bf351bd.

## H0 electrical and policy rules
- CHGCNTL2 register address is 0x18. Value 0x20 encodes 500mA according to TG chg_set_charging_current(); do NOT write to address 0x20.
- WDTCNTL register 0x22; timer field bits [2:1], enable bit0, kick bit3, expired-reset bit6. Verify TG WDT_TIME_S_90 constant from charger.h.
- CHG_CNTL1 0x13 bit3 ENQ4FET active high; 1 enables charging. WDT must be armed, kicked, and verified before enabling.
- CHGCNTL4 0x1a BATREG code 0x27 (mask 0x3f) encodes 4380mV. Read only in H0.
- STATUS2 0x0e: charging bit3, topoff bit5, expired-WDT bit7.
- No writes to FACTORY1, VBUSCNTL, CNTL2, factory/option/OTP, PD or bypass.
- Crucial incompatibility: TG's 500mA input-current ramp changes VBUSCNTL if prior limit >500mA. The requested H0 register allowlist FORBIDS VBUSCNTL. Consequently H0 MUST REFUSE enable if existing input limit exceeds 500mA; it cannot claim TG ramp compliance without that write.
- The TG charger starts ENQ4FET before WDT in sm5714_chg_charging(); H0 must intentionally use the opposite safe order.

## H0 missing controls that BLOCK charge enable
- Both ADC5 GPIO2 battery NTC and GPIO3 charger NTC fresh, mapped through TG tables, not FG die fallback; 15..41C battery, charger below 50C.
- Preflight: VBUS source, measured VBUS limit <=500mA, 4380mV float, valid battery voltage and topoff state, no watchdog faults.
- Mutex-owned single writer for the existing 2-second P449 guard and new H0; P449 a52g_inhibit_latched is ONE WAY. H0 may not simply clear or bypass its latch to permit charge.
- Kill switch defaults to OFF for entire boot. Re-arm explicitly; failed write, thermal trip, missing sensor, timer expiry, USB removal or suspend must fail closed.
- WDT 90s pre-arm and verify, <=30s refresh, 14,400s total charging timer, topoff FULL, recharge under 4310mV only with all safety checks valid.
- Android STATUS/ONLINE must reflect real guarded hardware states; preserve the existing single battery + readonly fuel-gauge supply model.
- No fake H1 7-supply count: H0 is not the full charger/MUIC/sec_battery port.

## SCM source audit staged
- scripts/451_dsp_boot_dtb.py adds /firmware/scm with qcom,scm-sm6350 + qcom,scm and #reset-cells=1.
- scripts/452_scm_ufs_ice_audit.py adds explicit Lagoon UFS ICE and Qualcomm SMMU availability preservation and enumerates other qcom_scm_* callers.
- Require CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT=n, test the actual compiled tree and packed DTB, manual PAS auto_boot=false.
- SCM/probing behavior remains hardware-unverified. Do not claim UFS unchanged until both code and runtime verified.

## Release status
- MANGROVE-452-DSP-CHG BLOCKED: the H0 current/WDT/thermal state machine has not been implemented or tested. Requested Claude H0 workorder path is not accessible in the repo and was not returned by Project file search.
- The correct existing fallback remains MANGROVE-451-DAILY-431. Do not relabel a guard-only build as charging-capable.
- No push to active CI branch and no new kernel build initiated by this document.
