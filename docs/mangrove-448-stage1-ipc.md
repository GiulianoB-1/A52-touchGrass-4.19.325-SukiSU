# Mangrove Kernel — Phase448 W3 Stage 1 IPC-only experimental

Based on work order 448 (23 Oct 2026), pinned TG 6bf351bd / Android common f960ed27.

Only **D** is an IPC bring-up variant. A/B/C remain unchanged and are not evidence of modem support.

- Compiled-in upstream IPCC mailbox, Qualcomm SMEM (and its HWSPINLOCK), SMP2P and upstream AOSS QMP driver.
- Forced downstream Q6/PAS/MSS/ADSP/WCSS/rmtfs components OFF for D. No IPA on this build.
- TG contract audit checks IPCC @0x408000/IRQ228, SMEM @0x80900000 size 0x200000, SMP2P MPSS IDs 435/428 PID1, ADSP 443/429 PID2, CDSP 94/432 PID5, and entry names. D artifact requires matching packaged boot DTB, not only source.
- Probe-success log lines P448 IPC ipcc=PROBED, smem=PROBED, smp2p=PROBED remote=1|2|5.
- **AOSS blocked:** TG uses qcom,qmp-mbox and upstream qcom_aoss has no matching OF compatible. The upstream driver is compiled but is not assumed to bind. Do not force the compatible without validating QMP mailbox protocol. Full Stage1 not accepted until fixed and probed.
- No modem boot, NV/EFS/IMEI writes, rmt_storage UIO, remoteproc SSR or firmware load. Step 2 GLINK/QRTR, Step 3 ADSP, Step 4 CDSP/FastRPC, Step 5 modem, Step 6 IPA are not enabled.
- Before first MSS test, back up modemst1/2 and fsg from TG. Normal charging stays on TG until charger policy work 447I is complete.

Acceptance for Stage 1: A52 boots, no unexpected panic, actual runtime probe markers for IPCC/SMEM/SMP2P, no defer storm, no new SELinux denials. AOSS requires separate validated work. This document does not claim that Stage1 is complete or that the modem functions.
