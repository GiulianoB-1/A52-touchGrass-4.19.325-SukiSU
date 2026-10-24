# P458 DSP: SCM-on-demand deferred-device audit

**Baseline**: hardware-booted P457 bundles `GKI_20261024_204318_manual` and `GKI_20261024_204359_boot_plus120s`, with `devices_deferred.txt` and early `dmesg.txt`. This report distinguishes directly observed evidence from source-based expectations. It does not claim an SCM runtime boot test.

## Baseline deferred devices

| Device | Baseline probe evidence | Why currently deferred | Expected on successful manual SCM registration | Explicit P458 policy |
|---|---|---|---|---|
| `aab0000.qcom,venus` | `subsys-pil-tz ... Failed to setup proxy regulators(rc:-517)` at 1.380 s; subsequent deferred probes at 2.157, 2.165, 2.199, 2.240 s | **CONFIRMED** regulator dependency `-EPROBE_DEFER`, independent of proof of SCM | SCM itself does **not** repair the confirmed regulator dependency. Other dependencies could become available as deferred probes retry. The downstream `pil_tz_driver_probe` can register a remote-subsystem descriptor and could later boot Venus; this is not safe to assume inert | Force `-EPROBE_DEFER` in the legacy `subsys-pil-tz` probe on Lagoon for *exactly* Venus and NPU names, before any PIL/SSR setup. No Venus firmware load |
| `9800000.qcom,npu` | `subsys-pil-tz` emits `for npu segments only will be dumped`, then probe returns deferred at 1.387 s and repeatedly around 2.16–2.25 s | **NOT PROVEN**: source/log does not identify the exact provider that returned `-517`. Do not infer that SCM is the only blocker | Retry may traverse additional legacy PIL/SSR initialization. Some paths can register restartable remote-subsystem firmware operations | Same early `-EPROBE_DEFER` guard on Lagoon. No NPU firmware load |
| `soc:qcom,smp2p-npu` | Probe deferred at 1.083 s and repeatedly at 2.064–2.255 s | **NOT PROVEN**: 5.10 SMP2P source requires mailbox/IPCC, SMEM state/alloc and IRQ. NPU peer not running may affect state, but exact failing resource cannot be determined from this log | No firmware loading from SMP2P itself, but rebinding could expose NPU edge/state interfaces. SCM binding cannot be assumed sufficient | Early `-EPROBE_DEFER` only for exact NPU SMP2P device; unrelated modem/ADSP SMP2P untouched |
| `3000000.qcom,lpass` | Deferred 1.273 s and ~7 retries; `/sys/class/remoteproc` empty; AOSS `P448 AOSS probe rc=0` | **SOURCE CONFIRMED**: 5.10 `qcom_q6v5_pas.c:adsp_probe` explicitly returns `-EPROBE_DEFER` when `!qcom_scm_is_available()`. Other unobserved dependencies may still be encountered after SCM becomes available | SCM provider should make PAS proceed to regulators/PDs, `rproc_add` registering an **offline** remoteproc. Not guaranteed until tested; `auto_boot=false` prevents automatic firmware start | No extra hold on ADSP. Only manual remoteproc `state=start` may load `adsp.mdt` |

### Mandatory provenance and invariants

- The *exact* four devices above are present in **both** supplied P457 `devices_deferred.txt` files. Kernel's `probe of ... returned 0` indicates a deferred probe in these tracing logs, **not** success.
- Existing P457 qcom SMMU source and the Lagoon `!of_machine_is_compatible("qcom,lagoon") && qcom_scm_is_available()` guard are unchanged. The apps and KGSL SMMUs previously registered successfully at ~1.11 s. The strict P457 byte-delta gate is retained.
- P457's nine availability-gated SCM callsites and fail-closed UFS ICE bypass are untouched.
- The final boot-v2 DTB has an **inert** `/firmware/scm` node with `compatible = "qcom,scm-sm6350", "qcom,scm"`, `#reset-cells = <1>`, `status = "disabled"`. No OF overlay or generic population may activate it at boot.
- `/sys/kernel/a52_dsp/scm_enable` is a root-writable one-shot; the *only* SCM platform-device allocation/addition is in its write path. `platform_device_add()` returning zero reports registration, not necessarily a successfully bound SCM driver. Check `available=1` and `probe of firmware:scm returned 1`.
- The 5.10 PAS descriptor stays `auto_boot=false`, `firmware_name="adsp.mdt"`, `pas_id=1`, `lcx`/`lmx` at 384 when starting. `CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT` must remain disabled.
- Firmware loading for Venus/NPU is prevented by exact-name early-return guards on Lagoon *before* legacy PIL subsystem registration. Audit these guards as a mandatory compiled-source gate; a missing/renamed driver path must fail the build instead of silently bypassing protection.

### Runtime acceptance, **not verifiable in CI**

1. **Before knob**: no `probe of firmware:scm`; four deferred devices unchanged; both SMMUs probe normally; `remoteproc` absent; P449 guard active and H0 default OFF.
2. **After writing 1**: `P458 SCM RUNTIME_ENABLE rc=0 available=1`, `probe of firmware:scm returned 1`; ADSP remoteproc registers `offline`. All other three stay deferred with `P458 DSP FREEZE` logs. Absence of Venus/NPU firmware requests, subsystem start or restart must be checked.
3. **Manual ADSP**: find `remoteproc*/name` == `adsp`; write `start` to its state. Confirm P451 rails 384 and PAS/GLINK status. A failure here does not prove SCM is the only remaining blocker.
4. **Tests separate by reboot**: H0 charging test and DSP/SCM test must not run simultaneously.

**Safety note:** this is a one-shot *experimental* kernel path. The static gates establish intended wiring and freeze behavior but cannot guarantee successful SCM binding, trusted-firmware PAS acceptance or runtime stability.
