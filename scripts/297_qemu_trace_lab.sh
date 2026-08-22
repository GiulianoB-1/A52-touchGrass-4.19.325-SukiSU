#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
PHONE_OUT="$PWD/workspace/gki-phase199-out"
QOUT="$PWD/workspace/gki-phase297-qemu-out"
ART="$PWD/phase297-lab-out"
PHONE="$ART/exact-phone"
QART="$ART/qemu"
ANALYSIS="$ART/analysis"
ROOTFS="$PWD/workspace/phase297-rootfs"
HDRS="$PWD/workspace/phase297-uapi"
QIMAGE="$QOUT/arch/arm64/boot/Image"
INITRAMFS="$QART/initramfs-phase297.cpio.gz"
SERIAL="$QART/serial.log"
STRICT_FLAGS='-Werror=declaration-after-statement -Werror=implicit-function-declaration -Werror=incompatible-pointer-types -Werror=return-type'

fail_report() {
  local rc=$?
  set +e
  mkdir -p "$ART" "$ANALYSIS"
  printf '%s\n' "$rc" > "$ART/exit-status.txt"
  if [[ -f "$SERIAL" ]]; then
    grep -nE 'Kernel panic|Oops:|BUG:|WARNING:|Call trace:|PHASE297_' "$SERIAL" \
      > "$ANALYSIS/serial-key-events.txt" 2>/dev/null || true
  fi
  exit "$rc"
}
trap fail_report EXIT

rm -rf "$ART" "$QOUT" "$ROOTFS" "$HDRS"
mkdir -p "$PHONE" "$QART" "$ANALYSIS" "$ROOTFS" "$HDRS"

cat > "$ART/README.txt" <<'TXT'
Phase297 is a NON-FLASHABLE exact-source virtual diagnostics lab.

The exact A52 Phase296 source/config is reconstructed and its real A52 display
objects are compiled/audited. A second config from that exact same source tree
is then booted on QEMU ARM64 virt with virtio-gpu so serial, initcall timing,
DRM-core ioctls and function-graph tracing can be exercised automatically.

QEMU does NOT emulate SM7125 SDE, Qualcomm DSI/PHY, the Samsung panel, A52 DTBO,
or Samsung HWC. Therefore QEMU success is a preflight/debugging result, not
proof that the physical A52 display path works. Never flash the QEMU Image.
TXT

# Reconstruct and compile the exact phone candidate first. This intentionally
# reuses the same production script that produces the hardware Phase296 image.
bash scripts/296_ci_build.sh

test -s "$PHONE_OUT/arch/arm64/boot/Image"
test -s phase296-out/package/boot.img
cp phase296-out/config/final.config "$PHONE/phase296-final.config"
cp phase296-out/BUILD-IDENTITY.json "$PHONE/BUILD-IDENTITY.json"
cp phase296-out/package/repack-report.json "$PHONE/repack-report.json"
sha256sum phase296-out/compile/Image phase296-out/package/boot.img \
  > "$PHONE/phase296-phone-binaries.sha256"

# Force a strict recompile of the exact objects carrying the new probes. This
# catches C89 declaration ordering and ABI/prototype mistakes independently of
# the full Image build, while using the exact phone config and generated tree.
PHONE_OBJECTS=(
  drivers/a52_display/msm/msm_drv.o
  drivers/a52_display/msm/msm_atomic.o
  drivers/a52_display/msm/sde/sde_kms.o
)
for rel in "${PHONE_OBJECTS[@]}"; do
  rm -f "$PHONE_OUT/$rel"
done
set +e
make -C "$ROOT" O="$PHONE_OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  KCFLAGS="$STRICT_FLAGS" -j"$(nproc)" "${PHONE_OBJECTS[@]}" \
  2>&1 | tee "$PHONE/strict-object-build.log"
strict_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$strict_rc" > "$PHONE/strict-object-build.status"
test "$strict_rc" -eq 0

for rel in "${PHONE_OBJECTS[@]}"; do
  test -s "$PHONE_OUT/$rel"
  base="$(basename "$rel" .o)"
  llvm-nm -n "$PHONE_OUT/$rel" > "$PHONE/${base}.nm.txt"
  llvm-objdump -dr --no-show-raw-insn "$PHONE_OUT/$rel" > "$PHONE/${base}.objdump.txt"
  strings -a "$PHONE_OUT/$rel" | grep -F 'P276 296' > "$PHONE/${base}.markers.txt" || true
done

python3 - <<'PY'
from pathlib import Path
root=Path('gki/common')
out=Path('phase297-lab-out/analysis')
files=[
 root/'drivers/a52_display/msm/msm_drv.c',
 root/'drivers/a52_display/msm/msm_atomic.c',
 root/'drivers/a52_display/msm/sde/sde_kms.c',
]
markers=[
 'P276 296R r=%d','P276 296S o=%d/%d a=%d/%d',
 'P276 296O e','P276 296O x r=%d','P276 296A e','P276 296A x r=%d',
 'P276 296C e n=%d','P276 296C x r=%d q=1','P276 296C x r=0 q=0','P276 296C x r=%d q=2',
 'P276 296W e','P276 296K p','P276 296K c','P276 296K x',
]
all_text='\n'.join(p.read_text() for p in files)
missing=[m for m in markers if m not in all_text]
if missing:
 raise SystemExit('missing Phase296 markers: '+repr(missing))
lines=[]
for p in files:
 text=p.read_text()
 src=text.splitlines()
 lines.append(f'FILE {p}')
 for marker in markers:
  for idx,line in enumerate(src,1):
   if marker in line:
    lo=max(1,idx-4); hi=min(len(src),idx+4)
    lines.append(f'  MARKER {marker!r} line={idx}')
    for n in range(lo,hi+1):
     lines.append(f'    {n:5d}: {src[n-1]}')
(out/'phase296-marker-source-context.txt').write_text('\n'.join(lines)+'\n')
PY

# Build a generic ARM64 virt kernel from the already-patched exact Phase296
# source tree. This config is deliberately separate from the phone config.
make -C "$ROOT" O="$QOUT" ARCH=arm64 LLVM=1 LLVM_IAS=1 defconfig
cfg="$ROOT/scripts/config"
for s in \
  PCI PCI_HOST_GENERIC VIRTIO VIRTIO_PCI VIRTIO_MMIO \
  SERIAL_AMBA_PL011 SERIAL_AMBA_PL011_CONSOLE \
  DEVTMPFS DEVTMPFS_MOUNT BLK_DEV_INITRD RD_GZIP TMPFS PROC_FS SYSFS \
  DEBUG_FS TRACING FTRACE FUNCTION_TRACER FUNCTION_GRAPH_TRACER DYNAMIC_FTRACE \
  SCHED_TRACER TRACEPOINTS KALLSYMS KALLSYMS_ALL PRINTK_TIME MAGIC_SYSRQ \
  DEBUG_KERNEL DETECT_HUNG_TASK WQ_WATCHDOG DEBUG_ATOMIC_SLEEP DEBUG_LIST \
  DEBUG_OBJECTS PROVE_LOCKING DEBUG_LOCK_ALLOC \
  DRM DRM_KMS_HELPER DRM_VIRTIO_GPU; do
  "$cfg" --file "$QOUT/.config" -e "$s"
done
for s in DRM_FBDEV_EMULATION FB; do
  "$cfg" --file "$QOUT/.config" -d "$s"
done
"$cfg" --file "$QOUT/.config" --set-val DEFAULT_HUNG_TASK_TIMEOUT 30
make -C "$ROOT" O="$QOUT" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig </dev/null
cp "$QOUT/.config" "$QART/qemu-final.config"

required=(
  SERIAL_AMBA_PL011_CONSOLE DEVTMPFS BLK_DEV_INITRD RD_GZIP DEBUG_FS TRACING
  FTRACE FUNCTION_GRAPH_TRACER DYNAMIC_FTRACE PROVE_LOCKING DRM DRM_VIRTIO_GPU
  PCI_HOST_GENERIC VIRTIO_PCI
)
for s in "${required[@]}"; do
  grep -Fxq "CONFIG_${s}=y" "$QOUT/.config" || {
    echo "Phase297 missing required QEMU config: CONFIG_${s}=y" >&2
    exit 1
  }
done
grep -Fxq '# CONFIG_DRM_FBDEV_EMULATION is not set' "$QOUT/.config"
grep -Fxq '# CONFIG_FB is not set' "$QOUT/.config"

set +e
make -C "$ROOT" O="$QOUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee "$QART/qemu-build.log"
qbuild_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$qbuild_rc" > "$QART/qemu-build.status"
test "$qbuild_rc" -eq 0
test -s "$QIMAGE"
cp "$QIMAGE" "$QART/Image-qemu-virt-NOT-FOR-A52"
sha256sum "$QART/Image-qemu-virt-NOT-FOR-A52" > "$QART/Image-qemu-virt-NOT-FOR-A52.sha256"

# Install the exact source-tree UAPI and build a static ARM64 /init that opens
# virtio DRM, sends core DRM ioctls (including an empty atomic request), and
# exports a bounded function-graph trace to the serial console.
make -C "$ROOT" O="$QOUT" ARCH=arm64 INSTALL_HDR_PATH="$HDRS" headers_install
mkdir -p "$ROOTFS"/{dev,proc,sys,tmp,run}
aarch64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror \
  -idirafter "$HDRS/include" \
  tests/qemu/phase297_trace_init.c -o "$ROOTFS/init"
file "$ROOTFS/init" | tee "$QART/init-file.txt"
file "$ROOTFS/init" | grep -Eq 'ARM aarch64|ARM64'
sudo mknod -m 600 "$ROOTFS/dev/console" c 5 1
sudo mknod -m 666 "$ROOTFS/dev/null" c 1 3
(
  cd "$ROOTFS"
  find . -print0 | cpio --null -o --format=newc 2>"$QART/cpio.log"
) | gzip -9n > "$INITRAMFS"
test -s "$INITRAMFS"
sha256sum "$INITRAMFS" > "$QART/initramfs-phase297.cpio.gz.sha256"

# initcall_debug supplies boot-time function timing; panic/oops/lockup are fatal.
# The guest then turns on function_graph tracing for DRM/workqueue functions.
set +e
timeout --foreground --signal=TERM 120 \
  qemu-system-aarch64 \
    -machine virt,gic-version=3 \
    -cpu cortex-a72 \
    -smp 4 \
    -m 2048 \
    -nographic \
    -no-reboot \
    -device virtio-gpu-pci \
    -kernel "$QIMAGE" \
    -initrd "$INITRAMFS" \
    -append 'console=ttyAMA0 earlycon=pl011,0x09000000 rdinit=/init nokaslr loglevel=8 ignore_loglevel initcall_debug panic=1 oops=panic hung_task_panic=1 softlockup_panic=1 rcupdate.rcu_cpu_stall_timeout=21 drm.debug=0x1ff' \
    2>&1 | tee "$SERIAL"
qemu_rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$qemu_rc" > "$QART/qemu-run.status"

# QEMU may return a non-zero code on a deliberate guest reboot; serial markers
# are the authoritative success criterion.
grep -Fq 'PHASE297_BOOT_OK' "$SERIAL"
grep -Fq 'PHASE297_TRACE_READY' "$SERIAL"
grep -Fq 'PHASE297_DRM card_open=0' "$SERIAL"
grep -Fq 'PHASE297_DRM_COMPLETE' "$SERIAL"
grep -Fq 'PHASE297_FTRACE_BEGIN' "$SERIAL"
grep -Fq 'PHASE297_FTRACE_END' "$SERIAL"
grep -Fq 'PHASE297_TRACE_COMPLETE trace_rc=0' "$SERIAL"
if grep -Eq 'Kernel panic|Oops:|BUG: unable to handle|soft lockup|hung_task_panic' "$SERIAL"; then
  echo 'Phase297 detected a fatal kernel diagnostic in QEMU serial output' >&2
  exit 1
fi

grep -nE 'PHASE297_|drm:|\[drm\]|calling .*\+|initcall .* returned' "$SERIAL" \
  > "$ANALYSIS/serial-key-events.txt" || true
awk '/PHASE297_FTRACE_BEGIN/{p=1} p{print} /PHASE297_FTRACE_END/{p=0}' "$SERIAL" \
  > "$ANALYSIS/function-graph-trace.txt"

python3 - <<'PY'
from pathlib import Path
phone=Path('phase296-out/config/final.config').read_text().splitlines()
qemu=Path('phase297-lab-out/qemu/qemu-final.config').read_text().splitlines()
def asmap(lines):
 d={}
 for line in lines:
  if line.startswith('CONFIG_'):
   k=line.split('=',1)[0]; d[k]=line
  elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
   k=line[2:].split(' ',1)[0]; d[k]=line
 return d
p=asmap(phone); q=asmap(qemu)
keys=sorted(set(p)|set(q))
out=[]
for k in keys:
 if p.get(k) != q.get(k):
  out.append(f'{k}\n  phone: {p.get(k,"<absent>")}\n  qemu : {q.get(k,"<absent>")}')
Path('phase297-lab-out/analysis/phone-vs-qemu-config.diff.txt').write_text('\n'.join(out)+'\n')
PY

cat > "$ANALYSIS/INTERPRETATION.txt" <<'TXT'
What this lab proves:
- The exact Phase296 A52 source/config reconstructs and produces the phone Image.
- The exact A52 msm_drv/msm_atomic/sde_kms objects pass a forced strict compile.
- Phase296 marker strings and call-site disassembly are archived from those objects.
- The same patched 5.10 source can boot under ARM64 QEMU with panic/oops/lockup fatal.
- With FB and DRM_FBDEV_EMULATION disabled, a real userspace process can open a DRM
  card, negotiate DRM client caps, query resources, submit an atomic ioctl, and
  produce function-graph traces of the generic DRM/workqueue path.

What it cannot prove:
- Whether Samsung's physical userspace opens /dev/dri/card* on the A52.
- Any SM7125 SDE, DSI controller/PHY, panel, regulator, clock or DT behavior.
- Whether the target F0 5A 5A panel transaction succeeds on hardware.

Therefore Phase296 ramoops remains the decisive physical boundary test, but this
lab removes compiler, binary-placement, generic-DRM and tracing uncertainty before
we spend a hardware boot.
TXT

printf '0\n' > "$ART/exit-status.txt"
trap - EXIT
echo 'Phase297 exact-source QEMU trace lab: PASS'
