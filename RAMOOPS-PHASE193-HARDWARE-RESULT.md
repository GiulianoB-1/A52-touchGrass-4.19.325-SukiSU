# Phase 193 A52 hardware result

The untouched 1 MiB RAMOOPS capture proves the RSCC driver and platform device
match, but driver core defers the probe before the callback because one supplier
is not ready.

```text
RSCCCORE match path=device-attach dev=af20000.qcom,sde_rscc drv=sde_rsc rc=1
RSCCCORE driver-probe enter dev=af20000.qcom,sde_rscc drv=sde_rsc
RSCCCORE really-probe enter dev=af20000.qcom,sde_rscc drv=sde_rsc
RSCCCORE link n=0 s=af00000.qcom,dispcc st=1 fl=0x160
RSCCCORE link n=0 of=qcom,dispcc@af00000 drv=disp_cc-lagoon
RSCCCORE link n=1 s=af01004.qcom,gdsc st=0 fl=0x160
RSCCCORE link n=1 of=qcom,gdsc@af01004 drv=none
RSCCCORE suppliers dev=af20000.qcom,sde_rscc rc=-517 reason=-
RSCCCORE deferred-add dev=af20000.qcom,sde_rscc reason=-
```

The blocking supplier is `/soc/qcom,gdsc@af01004`, compatible `qcom,gdsc`,
regulator name `mdss_core_gdsc`. The existing A52 legacy GDSC bridge binds the
working UFS node but rejects this display regulator by name.

The kernel reached BOOT_READY and remained alive through 46.140115 seconds with
all eight CPUs online. No panic, watchdog reset, or lockup was recorded.
