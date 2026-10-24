# Phase447B W3/W4 — three-layer architecture and acceptance contract

Baseline: PR #189, pinned ACK android12-5.10-2026-04_r1 (`f960ed27302b1ff8e61e152fc202554d778deccd`), pinned TG `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`. **Design contract, not a claim that Wave 3 or 4 is ported or safe to flash.** A/B/C variants remain experimental until the core and userspace device interfaces are verified.

## Layer 1 — 5.10 ACK upstream SM6350/FP4 core

Reuse the drivers already present in ACK 5.10 rather than copying TG 4.19:

| Function | Existing ACK 5.10 source | Gap before A52 enable |
|---|---|---|
| PAS Q6 ADSP/CDSP/MSS | `drivers/remoteproc/qcom_q6v5_pas.c` | 5.10 OF table lacks SM6350 PAS match data; backport upstream FP4-compatible descriptors and verify *A52* PAS IDs, firmware, regulator/power-domain voting, carveouts, IRQs |
| AOSS QMP | `drivers/soc/qcom/qcom_aoss.c` | 5.10 OF table lacks `qcom,sm6350-aoss-qmp`; backport matching data/DT after supplier audit |
| SMEM / SMP2P | `drivers/soc/qcom/smem.c`, `drivers/soc/qcom/smp2p.c` | Check actual ACK file names (e.g. `smem.c`), A52 RAM offsets, edge IDs, IPC interrupts, shared regions |
| GLINK/edge | `drivers/rpmsg/qcom_glink_smem.c`, `drivers/rpmsg/qcom_glink_native.c` | Match RPM/APSS/ADSP/CDSP/MSS transport edges, mailbox/IPCC and shared memory |
| QRTR / name service | `net/qrtr/af_qrtr.c`, `net/qrtr/ns.c` | Use actual available transports; ACK includes `net/qrtr/smd.c`. Do not assume SMD is the required GLINK route |
| Sysmon and PDR | `drivers/remoteproc/qcom_sysmon.c`, `drivers/soc/qcom/pdr_interface.c` | Couple QMI services to remoteproc stop/restart; disable panic, upload mode and ramdump |
| RMTFS memory | `drivers/soc/qcom/rmtfs_mem.c` | Reuse existing 5.10 `qcom,rmtfs-mem` if final A52 DT matches; guard against NV writes, mmap and SCM side effects |

Use **mainline SM6350 / Fairphone 4 `sm6350.dtsi`** as the architectural template, not as a drop-in A52 DTB. Supply A52-specific `adsp_mem`, `cdsp_sec_mem`, modem reserved-memory regions, reg/clocks, SMP2P stop states, `qcom,smem-states`, IPCC/mailbox, regulator votes, PAS IDs and firmware filenames. Source origin for each DT property must be recorded; unresolved values block probe enablement.

Boot order: AOSS/SMEM/SMP2P → GLINK/QRTR/QMI/PDR → ADSP → CDSP → MSS → IPA. The remoteprocs remain `auto_boot=false`, started late after `/vendor/firmware_mnt/image` is accessible. Prefer `request_firmware_direct` or the explicit mounted path to avoid a 60-second sysfs fallback; log firmware name/result and every transition. On crash, **log and stop**, with restart levels independent/related; no panic or upload mode.

## Layer 2 — Android/Samsung/Qualcomm userspace interfaces

**Preferred origin:** Qualcomm **5.10** vendor baseline from CodeLinaro (candidate `clo/la/kernel/msm-5.10` at a suitable **kernel.lnx.5.10** SoC branch / holi or lahaina). Find and pin an *actual revision* containing each requested API BEFORE copying anything; public upstream ACK with the same tree name is not proof that it contains the downstream char interfaces. Avoid importing TG 4.19 wholesale.

The layer is *adapter code above the upstream IPC and remoteproc core*, not a second provider for the same DT compatible. Required research/ports: GLINK packet char devices; subsystem/SSR compatibility nodes; rmt_storage/rmtfs adapter as actually required by ROM; diag and `at_mdm0` later. The existing ACK `rmtfs_mem` owns any `qcom,rmtfs-mem` DT binding; do **not** add a second owner. Protect all NV/EFS/IMEI/RPMB storage writes and any interface that would grant a vendor service NV write access.

For each adapter: record exact vendor branch/SHA, file path and dependency ABI, userspace pathname and major/minor, DT binding owner, Kconfig, any ioctl numbers, SELinux labels, ueventd entries, probe supplier rc, and read/write/mmap constraints. Tie new adapters to independent default-OFF P447 wave gates. Adapter unavailable or unverified means Wave B/C acceptance **cannot** be claimed even if remoteproc reports `running`.

## Layer 3 — Qualcomm 5.10 audio-kernel (Wave 4)

Prefer CodeLinaro `clo/la/platform/vendor/opensource/audio-kernel` with a **pinned compatible 5.10 branch/revision**, above upstream ADSP + GLINK/APR/GPR. Port the Q6 AFE/ASM/ADM/APM stack, Bolero/WSA/WCD codecs and Samsung amp path with A52 clock, SoundWire, audio DSP, regulator and DT dependencies. Do not duplicate upstream RPMsg, remoteproc or GLINK drivers. Wave 4 must be independently disableable when ADSP/firmware fails.

## Later waves using the same layering

**Wave 5 — SSC/sensors:** keep upstream ADSP PAS + GLINK/QMI core in Layer 1; adapt Qualcomm 5.10 SSC/sensor transport and any downstream `/dev` interfaces in Layer 2; verify the Samsung Android sensor HAL/SSC names and SELinux nodes against actual logcat. ET7xx fingerprint and NFC/eSE have distinct SPI/I2C hardware ports: audit GSI-only SEs and wrapper SID DMA first. Each adapter retains its own default-off driver gate. Do not infer that a userspace sensor device is provided by ADSP merely because remoteproc reports running.

**Wave 6 — Wi-Fi/BT:** Layer 1 contains MSS/SMEM/GLINK plus verified PCIe/UART hardware and regulator/clock/reset suppliers. Prefer a **revision-pinned Qualcomm msm-5.10** CNSS2/ICNSS + QCA WLAN host driver and BT transport for the userspace-facing Layer 2, rather than a TG 4.19 bulk copy. Verify actual vendor `cnss-daemon`, netlink, `wlan0`, HCI, firmware filenames, `/sys` contracts and security labels. No WLAN firmware/NV calibration writes. Gate Wi-Fi and BT independently, and do not attempt to enable Wi-Fi before its required MSS dependency is verified. Later audio layer does not substitute for network/sensor adapters.

## Read-only vendor userspace contract

`merge/userspace-nodes.csv` is a **hypothesis-to-verification** map, not a list of observed opened paths. The collector captures actual `logcat -b all`, dmesg/AVC/open errors and boot-time node listings. Run `scripts/447b_vendor_node_audit.py --input <capture-dir> --map merge/userspace-nodes.csv --out <report-dir>` to produce observed path→owner-layer evidence. Every path HAL/init actually attempts to open must become a row and be mapped to Layer 1, 2, 3, or to a documented **unmapped blocker**. If no attempted-open log exists, note `NOT_OBSERVED` instead of claiming coverage.

**Important:** absence from logcat is not proof nobody opened a path. For complete open() tracing, add bounded process-scoped `sys_enter_openat/openat2` tracing after a functioning root shell is available, keeping ttyGS3/U446 logs responsive.

## Preservation, acceptance and safety

Variants: A (`a52.wave=2`), B (`a52.wave=3 a52.mss=0 a52.ipa=0`), C (`a52.wave=3`). These cmdline changes do not create absent drivers. No modem NV/EFS writes, learned fuel-gauge writes, touch firmware, security hooks or Samsung upload-mode changes. Preserve 446AG touch, GSI-wrapper DMA SID mapping, flicker HWC trace with handoff=0, ttyGS3/U446 and KSU 35171. Wave 3 acceptance requires actual rproc state and GLINK/QRTR evidence and the vendor attempted-open inventory, not merely a successful Image build.
