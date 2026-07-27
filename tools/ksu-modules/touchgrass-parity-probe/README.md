# A52 TouchGrass Runtime Parity Probe v1.1

A broad, bounded and read-only KernelSU module for capturing the working
TouchGrass 4.19 kernel as a behavioral reference for the ACK/GKI 5.10 port.

## What it captures

- full kernel identity, configuration and relevant symbols
- device tree, reserved memory, firmware listings and driver bindings
- ION, heap 19, DMA-BUF, CMA, IOMMU and SMMU state
- QSEECOM, SCM and shared-memory bridge activity
- DRM, MSM KMS, DSI and Samsung panel lifecycle
- clocks, regulators, interconnects, power domains and runtime PM
- remote processors, RPMsg, firmware loading and subsystem restart state
- GPIO, pinctrl, I2C, SPI, thermal, CPU frequency and devfreq state
- Android display, power, input, thermal and service-manager views
- broad tracepoints plus targeted function and return-value probes when available
- before and after snapshots around a controlled screen off/on cycle

## Safety boundaries

- no kernel-memory writes
- no module loading or unloading
- no system-file replacement
- no persistent tracing daemon
- refuses to replace an already active tracer
- bounded trace buffer with overwrite mode
- restores every tracing setting it changes
- caps copied files and directory traversal
- snapshot-only fallback if tracing is unavailable

## Use

1. Boot the working TouchGrass kernel.
2. Install `TouchGrass-Parity-Probe-v1.1.0.zip` in KernelSU Manager.
3. Reboot and allow the automatic boot capture to finish.
4. Press the module Action button for the 90-second deep screen-cycle capture.
5. Upload the generated `TouchGrass-Parity-Probe-v1.1-*.tar.gz` archives.

## Privacy

The archive can include Android properties, logs, package names, service names,
device identifiers and filenames. Review it before publishing it publicly.
