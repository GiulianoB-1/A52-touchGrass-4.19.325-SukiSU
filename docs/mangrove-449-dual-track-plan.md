# Phase 449 build gates — F / G / H / GH
Date: 2026-10-23. Basis: E boot captures 180747 / 180833, Golden TG FDR,
TouchGrass live DT and ROM reference-pack. Wave default remains 2.

## E capture acceptance
P448 IPCC probe IRQ18, SMEM hwlock3 version=0xc0000 and SMP2P
MPSS 435/428 PID1, ADSP 443/429 PID2, CDSP 94/432 PID5 all pass.
USB configured high speed. No new display vblank faults. GLINK and QRTR
remote endpoints absent as expected with all remoteprocs off. AOSS did
not bind. qrtr-ns and RIL restart without remote peers.
During USB-attached boot: Tbat 34.0C, Tchg rises 38.7C to 44.2C and
FG die 40.6C to 46.5C. Never claim the FG die is battery thermistor.

## F — IN THIS COMMIT, experimental guarded Step 2
F = E plus charger thermistor / mixed-temp one-way emergency inhibit:
TG 0.1C thresholds Tbat>=450, Tchg>=550,
Tbat>=420 && Tchg>=500. Hardware cutoff = clear only SM5714 CNTL1
ENQ4FET bit3, with readback and never re-enable. No current controls,
charging status machine, MUIC or PDIC. A missing Tchg sensor with VBUS
present inhibits (fail closed). TG recovery values 480/390 are
RECORDED ONLY; do NOT resume in F without full sec_battery.
Add P449 CHG_GUARD rule=... log and primary-boot initcall_debug.
Do NOT promote F to normal daily-driver/charging-safe status.

## G — PENDING, do not publish an incomplete image as G
Separate F + working 5.10-native AOSS + PAS ADSP and GLINK.
AOSS must have exclusive 0xc300000 mailbox ownership and 5.10
load_state genpds; migrate/remap vendor AOP consumers as needed.
ADSP needs TG live-DT firmware, memory, IRQ, PD, SCM/PAS and power
sequence, explicit opt-in, no MSS/CDSP/IPA, and non-panicking SSR.
Only publish G when source, config, packaged DTB and runtime checks
can be meaningfully tested. No combined USB reconnect yet.

## H — PENDING, charger safety-critical
F + complete, independently tested SM5714/sec_battery/MUIC/PDIC
conservative charging implementation, no remoteproc. No direct fast
charging until validated. Verify actual current limits, health,
temperature zones, full termination and recharge, all TG supplies
and thermal suspend. Normal charging remains on TouchGrass until
this is demonstrated in repeated supervised tests.

## GH — PENDING, conditional integration only
GH = G + H with separate G and H artifacts retained for isolation.
GH is NOT built until BOTH components exist, pass independent CI
and H is proven on hardware under supervised charging. GH is never
the first experiment with an unvalidated charger. Only after separate
G and H tests can the combined image be evaluated; during initial
combined test leave external charger disconnected. Any later
charging test stays supervised with an immediate safe recovery path.
Never assume an extra matrix job is free of CI time/resources.

The single push workflow builds existing A/B/C/D/E and F only.
No new remoteproc or charger enable bits. USB VBUS-at-boot fix
remains a separately reviewed and staged task.

## 449 failed-run recovery and simplified matrix
Run 428 F compile failure: duplicate unsafe declaration + escaped C tab text.
Fixed both on isolated staging branch, with precompile source validation.
Only two compiled variants now: E=last-tested DAILY baseline, F=experimental
THERM-GUARD-TEST. Neither is normal charging-certified. Retired A/B/C/D
variants remain available in old Actions runs. No G/H/GH image is published
without its validated driver integration. The unnamed raw base boot upload
was removed to prevent accidental flash of an ambiguous file.
