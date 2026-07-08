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

## Current stage

`01 - Baseline build`

The first GitHub Actions workflow clones the exact known source commit and verifies that the original Linux 4.19.152 kernel can still be compiled in a clean Ubuntu runner.

## Pinned sources

See [`sources.lock`](sources.lock). Do not replace pinned revisions with floating branch names during the bring-up process.
