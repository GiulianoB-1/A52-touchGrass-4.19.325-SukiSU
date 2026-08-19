# Phase 280: Phase279 evidence visibility

Phase 280 continues the Phase279 display root-cause investigation.

## Why Phase279 did not expose its intended SMMU records

Phase278/279 emitted their read-only SMMU diagnostics with `SMMU P278` and `SMMU P279` format prefixes. The existing persistent recorder admission gate returns before sequence allocation for format strings outside its admitted namespaces. Therefore those SMMU diagnostics were never persisted, and their rejection also produced no sequence-number gap.

Phase280 does not change the recorder implementation. It re-emits the same Phase278/279 read-only values through the existing admitted and critical `P276` namespace. Long Phase279 records are split so each rendered line fits the legacy 73-byte record payload.

## Non-perturbation contract

No new SMMU register reads, software page-table walks, mapping/unmapping, TLB operation, ATS request, fault clear, stream routing, DSI control-flow change, watchdog change, or recorder implementation change is introduced.

The CI audit proves `dsi_ctrl.c` and the recorder source are byte-identical before and after Phase280. In `arm-smmu.c`, stripping diagnostic `a52_ackfr_record()` calls leaves the Phase279 source unchanged.

## Hardware question

At the already-proven DSI command-DMA timeout, determine whether:

1. command IOVA first/last bytes translate correctly;
2. live TTBR0/TCR match the cached context-bank roots;
3. ACTLR/SCTLR/S2CR/SMR/CBAR remain coherent;
4. a context-bank fault appears after kickoff;
5. a global SMMU stream fault appears after kickoff; or
6. all SMMU state is clean while DMA completion remains absent, moving the root-cause frontier downstream into DSI DMA fetch/coherency/interconnect/controller behavior.

This phase is diagnostic-only and is not a display fix.
