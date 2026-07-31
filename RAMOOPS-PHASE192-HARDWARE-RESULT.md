# A52 phase 192 RAMOOPS analysis

## Capture integrity

- Uploaded collector ZIP is readable despite `result_zip_rc=4`.
- Frozen early snapshot and later raw export are both exactly 1 MiB.
- They are byte-for-byte identical.
- SHA-256: `d63c3d21470bebbcbb139143420eb32ed1ad57ce4f0a5a0e7cb2848f93fbb35b`.

## Decoder result

The exact phase-192 artifact decoder recovered 800 unique records from sequence 7 through 7150. Five compared slots were unrecoverable. The last recovered event is heartbeat tick 56 at 58.396 seconds with all eight CPUs online.

## RSCC result

The RPMh helper path is healthy:

```text
RSCC rpmh-register enter
RSCC state=rpmh-before compat=qcom,sde-rsc-rpmh node=1 pdev=1 bound=-
RSCC rpmh-probe enter dev=af20000.rsc:sde_rsc_rpmh
RSCC rpmh-probe exit rc=0 index=0
RSCC rpmh-register exit rc=0
RSCC state=rpmh-after ... bound=sde_rsc_rpmh
```

The main device and main driver both exist, and driver registration succeeds:

```text
RSCC main-register enter
RSCC state=main-before compat=qcom,sde-rsc node=1 pdev=1 bound=-
RSCC main-register exit rc=0
```

However, no `RSCC probe enter` record appears. No internal probe stage, cleanup, component-add, or bind record appears either.

This proves that the main RSCC callback is not failing internally. The driver core never invokes it. The remaining gate is before the callback:

1. platform-bus matching returns no match, or
2. driver core matches the pair but defers it before the callback, most likely at supplier-link checking or another generic pre-probe stage.

## DTB dependencies

The preserved DTB contains `/soc/qcom,sde_rscc`, compatible `qcom,sde-rsc`, with:

- MMIO regions `drv` and `wrapper`
- `vdd-supply` from `/soc/qcom,gdsc@af01004`
- three clocks from `/soc/qcom,dispcc@af00000`
- an RPMh child at `/soc/rsc@af20000/sde_rsc_rpmh`

The RPMh child and DISPCC are already proven bound. Phase 193 should trace the platform match result, the generic `really_probe()` entry, and every supplier device link without changing any link state.

## Display state

The DRM component master still has writeback and primary DSI present, while the RSCC slot remains absent. The kernel reaches `BOOT_READY` and remains alive behind the black screen. No panic, watchdog reset, or lockup is present in the persistent recorder.
