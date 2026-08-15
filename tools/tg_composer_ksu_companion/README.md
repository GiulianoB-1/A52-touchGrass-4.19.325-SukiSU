# TG Composer Reference Recorder v1

KernelSU companion recorder for the Galaxy A52 TouchGrass/GKI display-composer comparison.

## Purpose

Use the same userspace observation layer on the known-good TouchGrass boot and on the broken GKI boot. The module complements, rather than replaces, the kernel-side golden recorders.

## What it records

- Exact Composer PID/cmdline discovery and uptime marker.
- Composer threads, `wchan`, current syscall and kernel stacks where readable.
- Composer fd/fdinfo changes and process maps/status/cgroup/sched snapshots.
- `/dev/dri`, KGSL, ION/dma_heap and Binder device topology.
- `/sys/class/drm`, selected KGSL sysfs, DRM debugfs state where readable.
- HIDL/Binder/service snapshots using `lshal`, `service list` and bounded `dumpsys SurfaceFlinger` calls.
- Android properties, process/thread table, dmesg, logcat and SELinux denial evidence.
- Existing `/proc/tg_display_reference`, `/proc/tg_final_boot_reference` and `/proc/tg_gpu_reference` recorders when the flashed kernel exposes them.
- If an already-mounted writable tracefs supports isolated instances, targeted syscall/Binder/DRM/KGSL tracepoints and selected dynamic kprobe function hits, filtered to the Composer thread set.

## Non-interference rules

The module does **not** use ptrace/strace, change Android properties, restart services, write display/GPU device nodes, or change the global trace buffer. It only uses an isolated tracefs instance when the running kernel already exposes that facility. Trace buffer overwrite is disabled so startup records are retained.

## Capture directory

`/data/local/tmp/tg_ksu_composer_reference/current`

The directory is recreated once per boot by `service.sh`. The recorder waits up to 240 seconds for the QTI graphics Composer process, then monitors it for about 125 seconds after discovery. Precise snapshots are retained at discovery and approximately +1, +3, +5, +10, +20, +40, +80 and +120 seconds.

## Install

Install the generated module ZIP from the KernelSU Manager app, not from custom recovery. Reboot after installation.

## Golden/GKI procedure

1. Install this exact same module on the device.
2. Flash the instrumented TouchGrass golden boot and boot normally.
3. Allow Android to run beyond Composer startup.
4. Run `COLLECT_TOUCHGRASS_HYBRID_REFERENCE.bat` from the PC.
5. Preserve/upload the resulting `TOUCHGRASS_HYBRID_REFERENCE_*.zip`.
6. Later boot the GKI diagnostic image with the same module installed and run the same collector for an apples-to-apples comparison.

The KernelSU module format and lifecycle follow the official KernelSU module guide. `service.sh` runs in KernelSU BusyBox `ash` at late-start service stage.
