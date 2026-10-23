# Phase 451 ADSP bring-up — source-verified correction and prerequisites
Date: 2026-10-23. This is a static implementation review, not a build or flash artifact.

## Runtime baseline (450 DAILY capture 21:25)
Collector v1.16 450 DAILY f199bad. Reported handover:
- 2.102s CHG_GUARD missing Tchg, VBUS=1, inhibit action
- 2.104s FG +181mA, AUX_STAT=0x8876
- 2.105s CNTL1 0x6c -> 0x64 after ENQ4FET clear; STATUS2 read can
  transiently remain 0x08 because status propagation is asynchronous
- 4.138s STATUS2=0, reported current -103mA, Tchg sensor ready
- 2 power supplies, no AOSS or remoteproc, no system_server restart
- USB hotplug-at-boot worked; no generic USB VBUS workaround justified
- Tchg rose to 44.5C, FG package die 46.8C with charging off:
  heating mechanism remains unknown, no assumption of safety for H1.
Raw GKI_20261023_212530_boot_plus120s and _212543_manual tarballs
were not attached to this conversation; reported evidence only.

## Critical P451 corrections to the supplied work order
1. The *Linux 5.10* AOSS resource index AOSS_QMP_LS_LPASS maps to
   downstream load_state string **"adsp"**. Thus the expected marker
   is `P448 AOSS LOAD_STATE name=adsp val=on rc=0`, NOT name=lpass.
   The PAS SSR identifier remains `lpass`: two different namespaces.
2. The 5.10 qcom_q6v5_pas.c `adsp_init_regulator()` unconditionally
   acquires `cx` and `px`. Adding only qcom,proxy-reg-names as in TG
   won't work: add an SM6350 descriptor-specific regulator path that
   obtains `vdd_lpi_cx` and `vdd_lpi_mx`, votes the TG level 384 via
   regulator_set_voltage(), and releases resources on ALL failure,
   handover, stop, removal paths. Do not leave the mandatory `cx/px`
   acquisition active for SM6350 or silently substitute a provider.
3. 5.10 qcom_q6v5_init() requires interrupt-names **wdog, fatal,
   ready, handover, stop-ack**, while TG pil-tz-generic uses names
   prefixed `qcom,`. Likewise 5.10 smem_state_get(..., "stop")
   requires qcom,smem-state-names = "stop", not TG "qcom,force-stop".
   Preserve TG's numeric PDC/SMP2P IRQ and SMEM state phandles.
4. 5.10 qcom_add_glink_subdev() looks for an ADSP remoteproc child
   **glink-edge**, and glink_smem requires qcom,remote-pid=2 and
   native SMEM transport plus valid IPCC mailbox/IRQ. TG's
   `glink_adsp: adsp` is a separate child of its GLINK controller;
   reparent/convert exactly one instance. Merely leaving the legacy
   node in place will NOT cause GLINK to open.
5. AOSS qmp_aop@c300000 conversion from "qcom,qmp-mbox" must be
   exclusive and use the working IPCC 0x408000 mailbox channel.
   Check all prior qmp_aop phandle consumers before removing its
   `#mbox-cells` provider role. Do NOT attach a second AOP driver.
6. Avoid sm6350 rpmhpd resource votes alongside the existing downstream
   rpmh-arc-regulators. Use TG L1A_LEVEL / L17A_LEVEL (lcx/lmx) at
   TURBO level 0x180/384 and correct regulator-unit conventions;
   get exact regulator_set_voltage() permissible ranges from the
   compiled downstream regulator driver before using INT_MAX.
7. adsp.mdt + segmented firmware must be reachable through Android's
   firmware loader path. Preserve TG pil_adsp_mem@88d00000 memory region,
   PAS ID 1, smem crash ID 423, SSR name lpass, sysmon name adsp,
   ssctl 0x14, xo clk. No modem/CDSP/IPA/rmtfs enables.
8. Gate remoteproc *autoboot* behind a dedicated explicit P451 ADSP
   opt-in, never the global W3 wave default. Capture PAS auth/reset,
   Q6 ready IRQ, load_state AOSS message and safe SSR errors.

## Safe CI milestones
- P451-A: AOSS source + converted packaged DTB (assert no legacy
  qmp_aop mailbox owner), matching source, Kconfig, DTB; no ADSP PAS.
- P451-B: SM6350 PAS descriptor + exclusive TG regulator votes +
  all 5.10 IRQ names + glink-edge conversion, with autoboot OFF by
  default; check compiled source and packed DTB.
- P451-C: explicit one-variant ADSP autoboot opt-in only after A/B pass
  and firmware/rails inspection. Retain DAILY artifact unchanged.
- Required runtime: P448 AOSS probe rc=0; P448 AOSS LOAD_STATE
  name=adsp val=on rc=0; P451 ADSP RAILS lcx=384 lmx=384 rc=0;
  FW adsp.mdt request success; /sys/class/remoteproc/.../state running;
  GLINK REGISTER peer=2, QRTR IPCRTR, subsystem recovery no panic.
A successful CI compile is NOT remoteproc acceptance; never label a
non-probing build as a validated DSP.

## H1 charger implications from the same capture
At boot the bootloader may leave charging ON (CNTL1 bit3=1). H1
must impose safe limits and verified sensors before retaining any
charge-enable state, never assume default is off. Log a one-time
pre-inhibit register snapshot, throttle P450 AFTER_INHIBIT telemetry to
30s, sample STATUS2 after settling and log AUX_STAT bit0x0008 suspect
updates separately. Do not treat an unverified 0x0008 flag as proven
invalidity. Preserve emergency inhibit until H1 verified.
