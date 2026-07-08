# A52 touchGrass Linux 4.19.325 + SukiSU

Cloud build project for the Samsung Galaxy A52 5G (`a52xq`).

## Safety order

1. Build the untouched touchGrass Linux 4.19.152 baseline.
2. Apply official Linux 4.19 stable updates incrementally.
3. Build and test Linux 4.19.325 without root modifications.
4. Integrate a pinned SukiSU Ultra revision.
5. Build a separate experimental kernel image.
6. Package only after the kernel image builds successfully.

The known working KernelSU-Next + SUSFS flashable ZIP remains the rollback image and is not modified by this repository.

## Completed checkpoint

`01 - Baseline Linux 4.19.152`

The exact pinned touchGrass source compiled successfully in GitHub Actions and produced a verified kernel `Image`, configuration, logs, and checksums.

## Current stage

`02 - Official Linux 4.19.152 to 4.19.153 update`

The second workflow generates the exact upstream stable delta from the pinned Greg Kroah-Hartman Linux tags. It first runs a clean apply check against the Samsung and Qualcomm tree. If any hunk conflicts, it stops, preserves partial results and `.rej` files, and uploads a conflict report. It compiles Linux 4.19.153 only if the complete delta applies successfully.

SukiSU is intentionally not integrated during this stage.

## Pinned sources

See [`sources.lock`](sources.lock). Do not replace pinned revisions with floating branch names during the bring-up process.
