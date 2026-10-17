# Work order 446S: power-key state, resume crash, suspend blocker, empty-frame flicker, touch (Claude → main ChatGPT, 2026-10-17)

## Goal
- The phone completes a real suspend and resume without crashing.
- The power key works (screen off/on).
- We see exactly why every other frame is empty.
- Touch probes.

## Priority order (agreed by Claude and ChatGPT)
1. memlat crash
2. PON key state and events
3. Suspend-blocker / GMU instrumentation
4. Unsampled commit trace
5. Touch and volume keys

## Evidence (446R log GKI_2026-10-17_14-13-21-906, build 5.10.252-g69db75c08e9d #46)
1. **PON key.** It is bound on pm6350 power-on@800 with irq 219 (hwirq 0x00800134 = sid 0, periph 0x08, irq_id 0 = KPDPWR, correct). But all 5 edges log `rt_sts=04 pressed=1`.
   - 0x04 is bit2 CBLPWR (USB cable). KPDPWR bit0 is never set.
   - There is one edge per press.
   - So the state interpretation is wrong. Delivery of EV_KEY press+release is unproven.
2. **Resume crash.** `kernel BUG at kernel/time/timer.c:973` (BUG_ON(!timer->function)): `devfreq_monitor_resume ← devfreq_memlat_ev_handler+0x1f0 ← devfreq_resume_device ← devfreq_resume ← dpm_resume ← suspend_devices_and_enter`.
   - Before it, at suspend (124.85 s): WARN workqueue.c:3067 `__flush_work` from `devfreq_monitor_suspend ← devfreq_memlat_ev_handler+0x190`.
   - Cause: at 116.46 s, 6× `Unable to start HW monitor! (-95)` + `governor_store: Governor mem_latency not started(-95)` (cpu0/cpu6 l3-lat, cpu-llcc-lat, llcc-ddr-lat). The memlat start_monitor returns before `devfreq_monitor_start()`. On 5.10 that is the only place `devfreq->work` is initialised (on 4.19, `devfreq_add_device` did it).
3. **Suspend blocker.** Suspend entry at 124.793 s. The suspend thread then sat ~8.68 s in device suspend: `GMU CX gdsc off timeout` at 128.46 s and 133.47 s, the second printed by the suspend thread (PID 4073) itself. The power key then aborted the suspend: `PM: Pending Wakeup Sources: …power-on@800` / "Some devices failed to suspend, or early wake event detected". No completed suspend/resume cycle.
4. **Empty frames.** In the sampled commits (every 15th) from n=390 (~90 s) to n=990, the pattern alternates exactly: 21 with `active_planes=0` and 21 with the image. All rc=0. Because the stride is odd, this points to every other commit being empty. It is not proven until every commit is logged.
5. **Touch:** no fts / sec_input lines (not built).
6. **gpio-keys:** "deferred probe timeout, ignoring dependency"; no input device (the "returned 0" is a deferral).

## Changes
A. **Keep the build-integrity audit** (`a52_final_tree_manifest_audit.py --strict`). Add the 446S markers as required contracts. Print PASS/FAIL into the build summary and as one early kernel line (`P446S MANIFEST PASS <fingerprint>`).

B. **Resume crash fix (on, priority 1):**
  - In the QCOM memlat governor (`governor_memlat.c`), and any other QCOM governor that calls `devfreq_monitor_suspend/resume/update_interval` (bw_hwmon, cache_hwmon, etc. if built): add a per-node `mon_started` flag, set only after `devfreq_monitor_start()` really ran and cleared in stop. Call the monitor suspend/resume/interval functions only when `mon_started`.
  - Audit `governor_store` and the failed DEVFREQ_GOV_START rollback: after a failed start, the device must not be left with a governor whose suspend/resume handlers act on an uninitialised work.
  - Defence in depth: in the common `devfreq_monitor_suspend/resume`, return early if `!devfreq->work.work.func`, with a one-time log.
  - Diagnostic only: log why `start_hwmon` returns -95 (which perf event / PMU counter, CPU, event id). Do not try to fix it in this build.
  - Log: `P446S MEMLAT skip_monitor dev=… event=suspend|resume`.

C. **PON key fix (on, priority 2),** following TG 6bf351bd `drivers/input/misc/qpnp-power-on.c` `qpnp_pon_input_dispatch()`:
  - Request KPDPWR with **IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING**, keeping `enable_irq_wake`.
  - Verify the register: log the exact SPMI address read. It must be PON base 0x800 + 0x10 = RT_STS, on the pm6350 sid 0 PON, same as TG's `QPNP_PON_RT_STS(pon)`. Also read it once at probe with the key idle, and log it.
  - **`key = !!(rt_sts & BIT(0))`** (QPNP_PON_KPDPWR_N_SET). Never use the whole byte.
  - Report `input_report_key(KEY_POWER, key)` + `input_sync`.
  - TG missed-press handling: if a release arrives without a preceding press (`key == 0` and the last reported state was 0), report 1, sync, report 0, sync.
  - Log: `P446S PON edge=N addr=… rt_sts=XX key=K`, plus one line per `EV_KEY` actually emitted (value 1/0), so delivery is proven, not only IRQ arrival.
  - Low priority: the pm6150l@4 second PON -16 (EBUSY). Log which resource is busy.

D. **Suspend-blocker visibility (on, diagnostic, priority 3):**
  - Enable `pm_print_times`, or equivalent timing, so every device's suspend/resume callback duration is logged.
  - In the GMU CX GDSC off path, log: which regulator/GDSC, the GDSCR value before and after, the timeout used, and the caller. Compare with TG adreno/GMU suspend.
  - Log the exact abort reason: which device callback failed and its error code, or which wakeup source.
  - Log the PON IRQ parent chain (PDC hwirq / GIC) once at probe, for later s2idle wake verification.
  - Do not change GPU power behaviour in this build.

E. **Empty-frame trace (on, diagnostic, priority 4):**
  1. Log **every** commit (no sampling) for a window: from the first commit after 85 s, the next 300 commits. Fields: n, time, plane count, fb ids, formats, crtc active/enable, **PID/comm of the ioctl caller**, `DRM_MODE_ATOMIC_*` flags.
  2. In the plane atomic_check / prepare_fb paths: log every case where a plane that userspace sent is dropped or rejected, with the reason. This shows whether the empty state comes from Android or from the driver.
  3. Every 5 s, a 1-line summary counting zero-plane commits per second.

F. **Touch (on, priority 5):** port the STM fts5cu56a driver + Samsung sec_input common (TG `drivers/input/touchscreen/stm/fts5cu56a/`) on geni I2C bus 35 addr 0x49, with its regulators, IRQ GPIO, pinctrl and fw path (`tsp_stm/fts5cu56a_a52xq.bin`; skip the fw update when versions match). Log the probe result and the first touch event.

G. **gpio-keys:** print the exact supplier it waits for (devlink supplier / `dev_err_probe` reason). Fix it if it is a simple DT/pinctrl issue.

H. **Keep:** `no_console_suspend` ON (the ramoops did not capture the 446R crash; PuTTY did); the U2 USB handover OFF.

## Do NOT
- Touch the 446P QCOM packet builder, 446N REARM, ION 146/148/149, or the 446R GEM free fix.
- Enable CAL31, the GDSC-recovery variant, or a dma-buf flags=0 importer fallback.
- Change display or GPU power behaviour in this build.
- "Fix" the flicker by forcing a plane in the kernel. This build is for seeing who sends empty frames.

## Build checks
- The manifest audit PASS includes 446P, 446N, 146/148/149, QSEECOM 19/27, dma_fence union, FENCE1C, U1 QUSB2, Phase305, Phase423, Phase438, FENCE1G, 446R GEM free, **446S PON, 446S MEMLAT guard**.
- grep the final tree: the PON code uses `BIT(0)` for the key state, and the IRQ is requested with both edges.

## Runtime acceptance
- `P446S PON` lines show key=1 on press **and** key=0 on release, with matching EV_KEY lines.
- No workqueue WARN and no timer.c BUG during suspend or resume; `P446S MEMLAT skip_monitor` lines appear instead.
- The suspend timing log names whatever takes seconds (expected: GMU). Ideally we see `PM: suspend exit` and the screen comes back on with a second press.
- The unsampled commit log shows who sends the zero-plane commits, and whether the kernel dropped any plane.
- Touch probe OK + events on swipe (if ported).

## Collect
- The PuTTY log.
- Notes: does power turn the screen off/on? Does it survive two sleep/wake cycles? Is the flicker still there? Does touch work?
- Ramoops is optional.
