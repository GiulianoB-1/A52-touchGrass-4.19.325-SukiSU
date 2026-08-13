# Phase 258 no-live-namei A/B build result

## Candidate

- Branch: `agent/a52-phase258-no-namei-ab-v1`
- Exact build head: `bc7f64300d2f0a558378c9890db5a326a73083a0`
- Workflow run: `31685518694` (historical Phase257 fast workflow, Run 14)
- GitHub artifact ID: `9175964428`
- Original artifact name: `A52XQ-Phase257-FAST-ONE-COMPILE-14-NOT-HARDWARE-VALIDATED`
- GitHub artifact digest: `sha256:d9a522d0c1f2b591b931c17fbe2d3ef06c440cf39d1d7a897b4f8eb90410c2f6`

The workflow/artifact retain Phase257 labels because the already-proven Phase257 one-compile workflow was deliberately reused unchanged. On this build head the committed Phase255 cumulative chain routes to `scripts/258_phase257_no_namei_ab.py` before compilation.

## Build gates

All Run 14 gates passed:

- cumulative-script preparation/self-tests
- pinned GKI/TouchGrass/Phase227 source restoration
- Phase206 reconstruction and Phase208-216 replay
- cumulative Phase217 -> Phase258 A/B application
- single final kernel compile
- compiled marker audit
- boot repack audit
- artifact upload

## Runtime A/B contract

Phase258 retains the live Phase257 KGSL publication instrumentation (`add/addx`, `wr/wrx`, `md`, and late `s1-s3`) but removes the executable Phase257 `fs/namei.c` mknod/unlink instrumentation and the live node-snapshot call.

The unchanged legacy workflow requires the old Phase257 source/binary marker strings. The Phase258 overlay therefore places those literal strings in an inert `static const char ... __used` compatibility array. It has no callsite, counters, current-task access, ktime access, syscall hook, or recorder call and cannot emit `mk/ul/s4/s5` records at runtime.

Independent Image comparison against Phase257 Run13:

- `a52_r257_kgsl_node_snapshot`: Phase257 Run13 PRESENT; Phase258 Run14 ABSENT
- `a52_r257_kgsl_pub_snapshot`: PRESENT in both
- `F257 add` and `F257 s1` formats: PRESENT in Phase258

## Checksums

- `boot.img`: `7989517bfef14ad5896ad8e7a5d3004ecb7d803212889205312d9cf0b52a309a`
- `Image`: `bbe15dfd0b34ac6904ef22b60d4d63a01d0f8a57e30b30872f3adb040463cace`
- `Image.gz`: `ecf8f4b1aefebb20ee7b12a442ebf371f5e71d805d4571322dcd329b0ec20bb8`
- final config: `7436c392354a806f3aefbf042ff2d2411145eae1856d95ded28cd0ec22b8dfab`

The repack report says `repacked-audited`, `flashable_candidate: true`, with board/cmdline/DTB/ramdisk/header/page-size/address/partition invariants preserved.

## Hardware decision

This candidate is not hardware validated yet.

Flash this exact Phase258 boot image and collect ramoops after a sufficiently long boot. The decisive A/B result is:

- if `odsign/odrefresh`, zygote, and SurfaceFlinger progression returns, the Phase257 regression is isolated to the removed live namei syscall instrumentation (or its live node-snapshot dependency);
- if the boot still stalls before odsign/zygote, the namei probe is not the cause and the next A/B should split the remaining Phase257 `core.c` versus `open.c` instrumentation.

If SurfaceFlinger returns, use the retained `F257 s1-s3` snapshots at the late `/dev/kgsl-3d0` open boundary. `mk/ul/s4/s5` are intentionally unavailable as runtime records in this A/B.
