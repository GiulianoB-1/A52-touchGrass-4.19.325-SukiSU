# Runtime incident: Linux 4.19.153 SukiSU + SUSFS build

Date reported: 2026-07-09
Device: Samsung Galaxy A52 5G (`a52xq`)
Status: **QUARANTINED, DO NOT FLASH**

## Affected build

- Workflow run: `29022538463`
- Project commit: `d004e2318a812010e642aed81a188d880a556931`
- Workflow artifact: `a52xq-sukisu-susfs-8`
- Packaged ZIP: `A52XQ-touchGrass-4.19.153-SukiSU-Ultra-SUSFS-v1.5.5-RCU-Fixed-AnyKernel3.zip`
- Packaged ZIP SHA-256: `46da5b2f90b738bca1bc6519d4f69b2cf65bd8fdaef81c2eb6abb2f3c1ea9367`
- Raw kernel Image SHA-256: `28651e9532dc17c1fcb74f236c3042b75d4a91602890030ae1875909555b8e97`
- Raw kernel Image size: `50845712` bytes
- Linux release string: `4.19.153-touchGrassKernel+`
- SukiSU commit: `278d822a4ebd214bcfd774b7910cb11cdc560bb9`
- SUSFS: `v1.5.5`, NON-GKI, custom dual-ABI integration

## Observed device failure

The kernel initially booted and was used on the device. Later, the phone froze completely. After a forced restart, it did not pass the Samsung logo.

Restoring previously used kernels changed the failure point but did not recover Android. One rollback reached the Android logo and then bootlooped. Removing BPF APEX modules did not recover the installation. The device recovered only after `/data` was wiped.

This is a runtime failure. A successful CI compilation and object audit did not establish runtime safety.

## Current assessment

The exact root cause is not yet proven. The failed image combined several invasive changes:

- Linux `4.19.152` to `4.19.153` update
- custom BPF verifier repair
- pinned SukiSU Ultra integration and Linux 4.19 compatibility patches
- SELinux RCU deadlock correction
- SUSFS v1.5.5 VFS patch
- A52XQ-specific manual resolution of SUSFS patch rejects
- custom SukiSU reboot-supercall and legacy `prctl` SUSFS ABI glue
- mount namespace and mount-ID spoofing changes

The closest previous 4.19.153 SukiSU RCU-fixed control also used the BPF repair but did not contain the new SUSFS integration. Its raw Image SHA-256 is:

`856c934e28ff93848ebf0fd1a6b342e748a51480b77ecf86504a4a27c4119218`

That makes the newly ported SUSFS runtime paths the leading regression area, particularly the mount-namespace, mount-ID, and compatibility glue. This remains a hypothesis until reproduced or confirmed by a kernel log.

The forced reboot may also have left `/data` inconsistent. The fact that restoring the boot kernel did not recover the phone, while wiping `/data` did, indicates that persistent userspace state or filesystem damage participated in the bootloop. It does not by itself identify which kernel function caused the initial freeze.

## Safety action

- Workflow `05-sukisu-susfs.yml` is quarantined.
- Do not distribute or flash the affected ZIP or raw Image.
- Do not label the affected package as known-working.
- Do not build another flashable SUSFS package until the runtime regression is isolated.
- The Android Common Kernel 6.1 probe work is separate and is not implicated by this incident.

## Required investigation before another device test

1. Preserve any available `/sys/fs/pstore`, recovery logs, and filesystem repair output.
2. Reconstruct the final generated source for the affected image.
3. Compare it with the working 4.19.153 SukiSU RCU-fixed source.
4. Remove the custom SUSFS integration and verify the control image independently.
5. Reintroduce SUSFS features one at a time, beginning with all mount-hiding and mount-ID spoofing disabled.
6. Add runtime guards, lock analysis, and mount-namespace stress testing.
7. Use a disposable test installation or complete data backup for every future device test.

No new flashable ZIP is authorized from this workflow while the incident remains open.
