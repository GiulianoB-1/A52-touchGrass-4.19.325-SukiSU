# Phase 228 tri-track cumulative recorder

## Objective

Preserve strong evidence for three boot boundaries in one capture:

1. `vold` and `vold_prepare_subdirs`
2. `odsign` and `odrefresh`
3. SurfaceFlinger and the late KGSL device state

The detailed Phase 217, 222, 225 and 226 records remain unchanged. Phase 228 adds a compact cumulative checkpoint named `TRIPOST 228` every two heartbeat seconds. Each checkpoint repeats the latest state already observed for all three tracks. This allows a late surviving record to retain the earlier vold and ODS state even when detailed records from the beginning of boot have been overwritten by later events.

## Record format

```text
TRIPOST 228 t=<tick> v=<stage>,<value>,<count> o=<proc>,<stage>,<value>,<count> f=<stage>,<value>,<count> g=<open>,<probe>,<register>,<node>
```

Fields:

- `t`: heartbeat second
- `v`: latest vold-related stage, value and total matched record count
- `o`: latest ODS process, meaningful operation stage, value and total ODS record count
- `f`: latest main SurfaceFlinger lifecycle stage, value and launch count
- `g`: `/dev/kgsl-3d0` open result, platform-probe-seen, device-register-seen and node-create-seen

ODS process values:

- `0`: no ODS process identified yet
- `1`: odsign
- `2`: odrefresh

Stage values:

- `0`: none or no new meaningful operation
- `1`: exec
- `2`: exec return
- `3`: exit
- `4`: open
- `5`: ioctl input
- `6`: ioctl output
- `7`: connect input
- `8`: connect output

Periodic ODS task snapshots increase the ODS record count but do not replace the last meaningful ODS operation stage. SurfaceFlinger state is taken only from the BOOTPOST main-process lifecycle records, preventing thread exits from replacing the main process SIGABRT result.

Values and counters are clipped to the range `-999..999` so the complete checkpoint remains inside the 73-byte protected message payload.

## Retention and error correction

`TRIPOST` is added to the post-capacity critical allowlist. The existing shortened RS(255,207) layout remains unchanged with 48 parity symbols and CRC32C. Reducing Reed-Solomon parity is not needed because the cumulative checkpoint fits in the existing protected payload. Keeping RS48 preserves correction of up to 24 unknown byte-symbol errors per physical copy.

## Behavioral scope

This phase is observation-only. It does not:

- change vold, odsign, odrefresh, SurfaceFlinger or KGSL behavior
- change service ordering, timeouts, signals or return values
- bypass odsign or weaken fs-verity
- change the device tree, clocks, regulators, IOMMU, display or GPU configuration
- record file contents, command buffers, keys, tokens, Binder payloads or process memory

## Expected evidence

A useful capture should contain both the original detailed records and repeated cumulative checkpoints. The final surviving `TRIPOST 228` record should summarize the latest known state of every track reached before the recorder stopped or the device restarted.

A replay against the Phase 227 210-second trace produces the expected terminal summary shape: odsign remains at exit stage with value 9, SurfaceFlinger remains at main exit stage with value 6 and 11 launches, and KGSL remains `open=-2, probe=0, register=0, node=0`.

Phase 228 does not yet identify the sender of a signal delivered to odsign. It preserves the exit value and surrounding vold, ODS, SurfaceFlinger and KGSL progression so the next decision can be based on one correlated timeline.
