# A52 touchGrass Linux 4.19.325 + SukiSU

Cloud build project for the Samsung Galaxy A52 5G (`a52xq`).

## Safety order

1. Build the untouched touchGrass Linux 4.19.152 baseline.
2. Apply official Linux 4.19 stable updates incrementally.
3. Repair and audit the touchGrass BPF verifier backports.
4. Build and test Linux 4.19.325 without root modifications.
5. Integrate a pinned SukiSU Ultra revision.
6. Build a separate experimental kernel image.
7. Package only after the kernel image builds successfully.

The known working KernelSU-Next + SUSFS flashable ZIP remains the rollback image and is not modified by this repository.

## Completed checkpoint

`01 - Baseline Linux 4.19.152`

The exact pinned touchGrass source compiled successfully in GitHub Actions and produced a verified kernel `Image`, configuration, logs, and checksums.

## Active checkpoints

### `02 - Official Linux 4.19.152 to 4.19.153 update`

This workflow generates the exact upstream stable delta from the pinned Greg Kroah-Hartman Linux tags. Known Android and vendor conflicts are accepted only after strict content checks. It compiles Linux 4.19.153 after all expected changes are present.

### `03 - Repair and audit BPF verifier`

This workflow recreates the Linux 4.19.153 tree, removes the dead `REG_LIVE_DONE` workaround, unifies the verifier ID-map types, restores the missing `explore_alu_limits` pruning guard, and compiles `kernel/bpf/verifier.o` with incompatible-pointer and implicit-function warnings treated as errors. It then performs a complete kernel build and uploads the repaired source patch, audit report, object file, kernel `Image`, logs, configurations, and checksums.

Runtime BPF loading tests are deferred until a later device-testing checkpoint. The output of this workflow is not yet a flashable ZIP.

SukiSU is intentionally not integrated during these checkpoints.

## Pinned sources

See [`sources.lock`](sources.lock). Do not replace pinned revisions with floating branch names during the bring-up process.
