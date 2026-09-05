# Phase319 Golden collector v1.2

This collector is intended to preserve early Phase319 kernel markers before the kernel ring buffer wraps.

The design is based on the proven Phase315G fix: continuously stream `/dev/kmsg` from `post-fs-data.sh` into persistent storage under `/data/adb`, then export the preserved log from the KernelSU module Action.

Expected Phase319 marker prefix: `TG319F`.
