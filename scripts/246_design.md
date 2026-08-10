# Phase 246 - subsys initcall corridor

## Evidence from Phase 245 hardware

Capture: `A52_RAW_RAMOOPS_20260810_094102.zip`

The bundled Phase210+ R48/RS48 transport-fusion decoder recovered a contiguous
sequence range 1-254 with no gaps. The final records are:

- `OFPOP root end` at about 576 ms
- `OFPOP exit rc=0` at about 576 ms

Before that boundary, RPMh `cxlvl` and GPUCC probe successfully and OF population
creates `3d9106c.qcom,gdsc`, `3d9100c.qcom,gdsc`, and KGSL. No current-boot
`CXF243` match/supplier/provider record and no `A52GDSC` registration record is
present after OF population.

This does **not** prove that Phase245 permissive fw_devlink fixed the CX gate. The
boot did not reach the retained CXF243 corridor.

## Static correction from failed Phase 244

Exact f960 `init/main.c` defines:

`static void __init do_initcall_level(int level, char *command_line)`

The old Phase244 parser searched the obsolete shape `do_initcall_level(int level)`,
which is why its CI patch failed before a kernel build.

## Phase 246 change

Keep the Phase245 functional state unchanged:

- `fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE`
- Phase243 CX/GX match/supplier/provider hooks retained
- Phase244 overlay still not applied
- no DT/provider/KGSL/GPUCC/initcall-order change

Add diagnostic-only level-4 records:

- `CXF246 V q=<0..2> l=4` - subsys level entered
- `CXF246 S n=<index> f=<symbol>` - immediately before each subsys initcall
- `CXF246 X q=<0..2> n=<count>` - emitted only if the whole level returns

`CXF246` is admitted as critical by the existing R48/RS48 recorder. Only one
logical S record is emitted per initcall; the physical three-bank transport is
unchanged.

## Interpretation

1. No `CXF246 V`: execution never reaches level 4.
2. `V` followed by `S` records: level 4 is active; the last `S` is the exact
   initcall entered before the stall/reset.
3. Last `S` is `a52_legacy_gdsc_init`: use existing `A52GDSC driver-register
   enter/exit` and CXF243 M/R/L/G/P records to localize inside registration/probe.
4. `CXF246 X` appears: all subsys initcalls return, so the failure is later and
   the next missing boot-phase boundary becomes the target.
