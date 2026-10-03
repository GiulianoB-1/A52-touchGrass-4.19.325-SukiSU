# Cuttlefish SurfaceFlinger Lab

This is the parallel **SF-Q** investigation for the A52 project.

The purpose is to separate the generic Android graphics pipeline from the
A52-specific Qualcomm SDE/DSI/RPMh/SMMU hardware path.

## Kernel-lineage rule

Do **not** assume the heavily modified TouchGrass features are present in the
current GKI 5.10 display-port kernel. EEVDF, CASS, MGLRU, the newer Binder work,
and similar changes belong to the TouchGrass line unless a phase explicitly
ports them into GKI. SF-Q uses the GKI display project only as the A52 comparison
target; it does not inherit TouchGrass feature assumptions.

## SF-Q1 goal

Establish a clean Android 16 Cuttlefish baseline for:

```
SurfaceFlinger
  -> Composer/HWC
  -> display creation / hotplug
  -> bootanimation
  -> composition
  -> present/fence activity
```

SF-Q1 does **not** attempt to emulate the A52 DSI engine. It gives us a known-good
Android 16 framework/Composer reference and a place to reproduce stalls by fault
injection later.

## Host

Use an x86_64 Ubuntu host with KVM. The Steam Deck Ubuntu installation is a good
candidate if `/dev/kvm` is available.

Run:

```bash
cd cuttlefish-sf-lab
chmod +x *.sh
./check_host.sh
```

If KVM is available, install the Cuttlefish host packages:

```bash
./install_host.sh
sudo reboot
```

After reboot:

```bash
cd <repo>/cuttlefish-sf-lab
./check_host.sh
```

## Get Android 16 Cuttlefish

The lab is pinned conceptually to:

```
branch: android16-release
CI target: aosp_cf_x86_64_only_phone (userdebug)
```

First try the automated fetch:

```bash
./fetch_android16.sh
```

If Android Build API access is unavailable, use the official CI UI and download
the image zip plus `cvd-host_package.tar.gz` from the **same build**, then run:

```bash
./prepare_instance.sh \
  ~/Downloads/cvd-host_package.tar.gz \
  ~/Downloads/aosp_cf_x86_64_phone-img-<BUILD>.zip
```

## Launch

```bash
./launch.sh
```

Default graphics mode is GfxStream. To test Virgl instead:

```bash
CF_GPU_MODE=drm_virgl ./launch.sh
```

The interactive Cuttlefish UI is normally exposed at:

```
https://localhost:8443
```

## Capture SF-Q1

Once Android reports boot complete:

```bash
./collect_sf_baseline.sh
```

The capture intentionally restarts **only SurfaceFlinger** inside a 20-second
Perfetto trace window. It also samples SurfaceFlinger/init/bootanimation state
every 100 ms so the restart boundary can be compared directly with the A52
Phase432 timeline. It records:

- sched switch/wakeup/waking
- Binder transactions
- gfx/view/wm/am/binder_driver/hal/input atrace categories
- SurfaceFlinger layer state with HWC metadata
- SurfaceFlinger transactions
- before/after SurfaceFlinger dumps
- full logcat plus an isolated restart-window logcat
- a 100 ms `sf-restart-timeline.tsv` with SF PID/init state and bootanimation state
- process/thread state
- system properties

Captures are written under:

```
~/cuttlefish-sf-q1/captures/sfq1_<timestamp>/
```

Open `sfq1.perfetto-trace` in:

```
https://ui.perfetto.dev
```

Then run the lightweight text extraction:

```bash
python3 analyze_sf_baseline.py \
  ~/cuttlefish-sf-q1/captures/sfq1_<timestamp>
```

## Stop

```bash
./stop.sh
```

## What comes next

After SF-Q1 is clean, SF-Q2 will add targeted AOSP SurfaceFlinger/Composer
instrumentation around the exact transitions we care about:

```
Composer connection
hotplug
physical display creation
commit start
validateDisplay enter/exit
setClientTarget
presentDisplay enter/exit
present fence acquisition/signal
```

SF-Q3 will deliberately inject delays / stuck fences / Composer response delays
to see which condition reproduces the A52's pre-atomic stall signature.

The real A52 remains authoritative for the later:

```
F0 5A 5A -> SW_TRIGGER -> BUSY -> missing DMA_DONE
```

failure, because Cuttlefish does not emulate Qualcomm SDE/DSI hardware.


## SF-Q1B: no-bootanimation / no-client control

After SF-Q1, use this control to test whether a healthy SurfaceFlinger naturally
avoids HWC present work when there are no drawable framework/app clients.

The script sets `debug.sf.nobootanimation=1`, stops zygote temporarily, restarts
SurfaceFlinger, observes a six-second no-client window, then starts zygote again
while the same Perfetto trace continues.

```bash
bash ./collect_sf_no_bootanim_control.sh
```

This is intentionally stronger than merely disabling bootanimation on an already
booted system, because existing SystemUI/Launcher layers would otherwise keep
giving SurfaceFlinger work to present.
