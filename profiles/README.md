# P169 AutoFDO: Clang 22 + ThinLTO

This branch reproduces the **P168** TouchGrass 4.19.206 kernel, including `-O2`, pinned AOSP `clang-r596125`, ThinLTO, and the existing ThinLTO import limit of 25. It adds Kbuild AutoFDO support. The P168 branch is unchanged.

## Modes

- **TRAINING** (default): `CONFIG_AUTOFDO_CLANG=y` and `CLANG_AUTOFDO_PROFILE` absent. Compiler emits source discriminators and minimal debug info for sampling. **This is not yet profile-guided optimization.**
- **PROFILED**: place a validated sample profile at `profiles/a52xq.autofdo` on this branch. P169 copies it outside the kernel source tree and exports `CLANG_AUTOFDO_PROFILE` before compilation. The profile's SHA-256 is included in the built-output cache key.

The P169 workflow is a fork of P168's complete build and packaging pipeline, not a kernel version upgrade. `-ffunction-sections` and `-fsplit-machine-functions` are intentionally excluded in this first attempt, as Linux 4.19 initcall placement and ThinLTO linker interactions require caution.

## Collection limitations on the Samsung A52 5G

Prior Phase87: CoreSight ETM4X registered for 8 CPUs, but `simpleperf record -e cs-etm:k` was rejected by the bootloader. ARM SPE was physically absent (`PMSVer=0`). Do not blindly re-enable ETM or SPE.

Alternative exploratory route: collect actual hardware PMUv3 samples with the *training* kernel while executing a representative workload:

```bash
adb shell su -c 'simpleperf record -e cpu-cycles:k -a --duration 120 -o /data/local/tmp/p169-cycles.perf.data'
adb pull /data/local/tmp/p169-cycles.perf.data
```

The exact `out/vmlinux` from the training build is essential for conversion. On the host, install Google AutoFDO `create_llvm_prof` and run:

```bash
bash scripts/169_convert_pmu_nonlbr.sh vmlinux p169-cycles.perf.data a52xq.autofdo
```

`create_llvm_prof --use_lbr=false` may accept non-LBR samples. Validate non-zero function coverage, symbol mapping, profile addresses and workload stability **before** committing any profile to this branch. `llvm-profgen` typically requires branch stacks/ETM and is not interchangeable with this fallback.

The build workflow checks `CONFIG_AUTOFDO_CLANG`, `CONFIG_LTO_CLANG`, `CONFIG_THINLTO`, retains vmlinux and the prior initcall-order audit. Flash/boot validation and repeatable A/B performance testing are still required.

AutoFDO can improve CPU hot paths only after a *real representative hardware profile* is generated, accepted and tested. It is not a runtime scheduler or magic "optimize" switch.
