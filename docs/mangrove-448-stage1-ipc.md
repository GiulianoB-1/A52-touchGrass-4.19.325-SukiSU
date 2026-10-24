# Mangrove Kernel — Phase448 W3 Stage 1 IPC-only experimental

Based on work order 448 (23 Oct 2026), pinned TG 6bf351bd / Android common f960ed27.

Only **D** is an IPC bring-up variant. It compiles **A52_WAVE_DEFAULT=2** and opts into IPC drivers explicitly, preventing unrelated generic Wave 3 gates (ADSP/CDSP/GLINK/MSS) from arming. A/B/C remain unchanged and are not evidence of modem support.

- Compiled-in upstream IPCC mailbox, Qualcomm SMEM (and its HWSPINLOCK), SMP2P and upstream AOSS QMP driver.
- Forced downstream Q6/PAS/MSS/ADSP/WCSS/rmtfs components OFF for D. No IPA on this build.
- TG contract audit checks IPCC @0x408000/IRQ228, SMEM @0x80900000 size 0x200000, SMP2P MPSS IDs 435/428 PID1, ADSP 443/429 PID2, CDSP 94/432 PID5, and entry names. D artifact requires matching packaged boot DTB, not only source.
- Probe-success log lines P448 IPC ipcc=PROBED, smem=PROBED, smp2p=PROBED remote=1|2|5.
- **AOSS blocked:** TG uses qcom,qmp-mbox and upstream qcom_aoss has no matching OF compatible. The upstream driver is compiled but is not assumed to bind. Do not force the compatible without validating QMP mailbox protocol. Full Stage1 not accepted until fixed and probed.
- No modem boot, NV/EFS/IMEI writes, rmt_storage UIO, remoteproc SSR or firmware load. Step 2 GLINK/QRTR, Step 3 ADSP, Step 4 CDSP/FastRPC, Step 5 modem, Step 6 IPA are not enabled.
- Before first MSS test, back up modemst1/2 and fsg from TG. Normal charging stays on TG until charger policy work 447I is complete.

Acceptance for Stage 1: A52 boots, no unexpected panic, actual runtime probe markers for IPCC/SMEM/SMP2P, no defer storm, no new SELinux denials. AOSS requires separate validated work. This document does not claim that Stage1 is complete or that the modem functions.

## Additional gates from the expanded work order (23 October)

- Stage 2 — GLINK+QRTR: no remoteproc boot. Validate the four TG edges and channels and that qrtr-ns, pd-mapper and PDR tolerate missing peers.
- Stage 3 — ADSP: mainline SM6350 PAS id 1, adsp firmware, TG memory 0x88d00000/0x2800000, crash SMEM 423, proxy/clock/smp2p/IRQs; no uncontrolled restart.
- Stage 4 — CDSP/FastRPC: PAS 18 and correct DMA SMMU owner; downstream ABI /dev/adsprpc-smd for both ADSP and CDSP, not /dev/cdsprpc-smd.
- Stage 5 — MSS: back up modemst1, modemst2 and fsg on TG first. PAS id 4, modem firmware under /vendor/firmware-modem/image, memory 0x8b800000/0xf800000, UIO rmtfs region size 0x280000 client 1. No kernel NV/EFS/IMEI/RPMB writes.
- Stage 6 — IPA/rmnet: only after stable MSS and validated downstream /dev/ipa, /dev/wwan_ioctl, rmnet_ctrl service ABI.
- All later stages: no AP panic from SSR/PDR, no 60s firmware stalls, no guessed SMMU SID, no intentional subsystem crash. NPU optional. Use collector v1.14; record git SHA, CI run, artifact, .config and exact tested DTB. Charging remains on TG.

## P448B next isolated build — Step 2 E

E is a new Wave 2 (no generic Wave 3 enablement) GLINK-SMEM, RPMSG,
QRTR-SMD, QCOM_SYSMON compilation and no-remoteproc boot-regression candidate,
built separately from D. Its packaged boot DTB and compiled .config are audited
before publishing. MPSS, ADSP, CDSP, IPA, and rmtfs remain OFF. NPU is deferred.

**Not a claim of completed GLINK bring-up:** upstream 5.10 registers GLINK
SMEM *via* remoteproc integration, rather than by binding the TouchGrass
standalone qcom,glink node. Without a remoteproc, there is no GLINK edge
probe, no QRTR IPCRTR endpoint, and no way to validate userspace pd-mapper or
qrtr-ns against real DSP/modem peers. P448 GLINK REGISTER and P448 QRTR
IPCRTR markers will appear only when future stages actually open links.
AOSS qcom,qmp-mbox also remains unmatched and Step 1 is unaccepted
pending a real driver/binding port and runtime probe evidence.
