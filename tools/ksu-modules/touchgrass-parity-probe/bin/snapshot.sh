#!/system/bin/sh
MODDIR=${MODDIR:-${0%/*}/..}
export MODDIR
. "$MODDIR/bin/common.sh"

session=$1
phase=$2
out="$session/$phase"
mkdir -p "$out"/{proc,commands,debugfs,sysfs,firmware,devices,android}

# Core identity and boot contract
run_capture "$out/commands" uname uname -a
run_capture "$out/commands" getprop getprop
run_capture "$out/commands" id id
run_capture "$out/commands" date date
run_capture "$out/commands" uptime uptime
run_capture "$out/commands" mounts cat /proc/mounts
run_capture "$out/commands" processes ps -A -T -Z
run_capture "$out/commands" top top -b -n 1
run_capture "$out/commands" services service list
run_capture "$out/commands" lshal lshal
run_capture "$out/commands" packages cmd package list packages -f
run_capture "$out/commands" features pm list features
run_capture "$out/commands" libraries pm list libraries
run_capture "$out/commands" block_by_name ls -lZ /dev/block/by-name
run_capture "$out/commands" character_devices ls -lZ /dev/ion /dev/dma_heap /dev/qseecom /dev/dri /dev/graphics /dev/kgsl-3d0
run_capture "$out/commands" loaded_modules cat /proc/modules
run_capture "$out/commands" modinfo_tree find /sys/module -maxdepth 2 -type f
run_capture "$out/commands" platform_drivers find /sys/bus/platform/drivers -maxdepth 2 -type l
run_capture "$out/commands" platform_devices find /sys/bus/platform/devices -maxdepth 2 -type l
run_capture "$out/commands" i2c_devices find /sys/bus/i2c/devices -maxdepth 2 -type l
run_capture "$out/commands" spi_devices find /sys/bus/spi/devices -maxdepth 2 -type l
run_capture "$out/commands" deferred_probes cat /sys/kernel/debug/devices_deferred
run_capture "$out/commands" bootstat bootstat -p
run_capture "$out/commands" service_bootanim getprop init.svc.bootanim

# Android framework views that can expose display and secure-service ordering.
run_capture_long "$out/android" 45 surfaceflinger dumpsys SurfaceFlinger
run_capture_long "$out/android" 30 display dumpsys display
run_capture_long "$out/android" 30 power dumpsys power
run_capture_long "$out/android" 30 input dumpsys input
run_capture_long "$out/android" 30 thermal dumpsys thermalservice
run_capture_long "$out/android" 30 battery dumpsys battery
run_capture_long "$out/android" 45 activity_processes dumpsys activity processes
run_capture_long "$out/android" 45 meminfo dumpsys meminfo
run_capture_long "$out/android" 30 sensorservice dumpsys sensorservice
run_capture_long "$out/android" 30 graphicsstats dumpsys graphicsstats
run_capture_long "$out/android" 30 hardware_properties dumpsys hardware_properties
run_capture_long "$out/android" 30 display_cmd cmd display get-displays

# Logs are capped after capture to prevent a runaway archive.
run_capture_long "$out/commands" 45 dmesg dmesg
run_capture_long "$out/commands" 60 logcat logcat -b all -d -v threadtime
for f in "$out/commands/dmesg.txt" "$out/commands/logcat.txt"; do
  [ -f "$f" ] || continue
  if [ "$(wc -c <"$f" 2>/dev/null)" -gt 16777216 ]; then
    tail -c 16777216 "$f" >"$f.tail" 2>/dev/null && mv "$f.tail" "$f"
  fi
done

# Important procfs state.
for f in \
  /proc/cmdline /proc/bootconfig /proc/version /proc/cpuinfo /proc/meminfo \
  /proc/iomem /proc/ioports /proc/interrupts /proc/softirqs /proc/devices \
  /proc/misc /proc/filesystems /proc/partitions /proc/uptime /proc/loadavg \
  /proc/stat /proc/vmstat /proc/zoneinfo /proc/buddyinfo /proc/pagetypeinfo \
  /proc/slabinfo /proc/vmallocinfo /proc/timer_list /proc/wakelocks \
  /proc/sys/kernel/tainted /proc/sys/kernel/random/boot_id \
  /proc/pressure/cpu /proc/pressure/io /proc/pressure/memory; do
  [ -r "$f" ] || continue
  n=$(sanitize_name "$f")
  copy_capped "$f" "$out/proc/$n.txt" 8388608
done

[ -r /proc/config.gz ] && copy_binary_capped /proc/config.gz "$out/proc/config.gz" 4194304
if [ -r /proc/kallsyms ]; then
  if have gzip; then
    gzip -c /proc/kallsyms >"$out/proc/kallsyms.gz" 2>/dev/null || true
  else
    copy_capped /proc/kallsyms "$out/proc/kallsyms.txt" 16777216
  fi
  grep -E \
    ' ion_|dma_buf_|qseecom|qtee|scm_|msm_drm|sde_|dsi_|ss_panel|refgen|regulator|clk_|iommu|smmu|remoteproc|rpmsg|icc_|genpd|firmware' \
    /proc/kallsyms >"$out/proc/kallsyms-relevant.txt" 2>/dev/null || true
fi

# Exact debugfs files.
for f in \
  /sys/kernel/debug/ion/heaps /sys/kernel/debug/ion/clients \
  /sys/kernel/debug/dma_buf/bufinfo /sys/kernel/debug/qseecom \
  /sys/kernel/debug/qtee_shmbridge /sys/kernel/debug/dri/0/state \
  /sys/kernel/debug/dri/0/summary /sys/kernel/debug/clk/clk_summary \
  /sys/kernel/debug/regulator/regulator_summary /sys/kernel/debug/gpio \
  /sys/kernel/debug/wakeup_sources /sys/kernel/debug/suspend_stats \
  /sys/kernel/debug/pm_genpd/pm_genpd_summary \
  /sys/kernel/debug/interconnect/interconnect_summary \
  /sys/kernel/debug/rpmh/stats /sys/kernel/debug/rpm_stats \
  /proc/ion/heaps /proc/ion/clients; do
  [ -r "$f" ] || continue
  n=$(sanitize_name "$f")
  copy_capped "$f" "$out/debugfs/$n.txt" 8388608
done

# Bounded trees across subsystems that commonly differ between vendor 4.19 and ACK.
copy_tree_capped /sys/kernel/debug/clk "$out/debugfs/clk" '.*' 1200 262144
copy_tree_capped /sys/kernel/debug/regulator "$out/debugfs/regulator" '.*' 800 262144
copy_tree_capped /sys/kernel/debug/pinctrl "$out/debugfs/pinctrl" '.*' 1200 262144
copy_tree_capped /sys/kernel/debug/dri "$out/debugfs/dri" '.*' 1000 524288
copy_tree_capped /sys/kernel/debug/ion "$out/debugfs/ion" '.*' 500 524288
copy_tree_capped /sys/kernel/debug/dma_buf "$out/debugfs/dma_buf" '.*' 500 524288
copy_tree_capped /sys/kernel/debug/iommu "$out/debugfs/iommu" '.*' 800 262144
copy_tree_capped /sys/kernel/debug/remoteproc "$out/debugfs/remoteproc" '.*' 500 524288
copy_tree_capped /sys/kernel/debug/rpmsg "$out/debugfs/rpmsg" '.*' 500 262144
copy_tree_capped /sys/kernel/debug/interconnect "$out/debugfs/interconnect" '.*' 500 262144
copy_tree_capped /sys/kernel/debug/pm_genpd "$out/debugfs/pm_genpd" '.*' 500 262144

# Sysfs state. Capture small text controls, status and uevents, not large binaries.
sys_pattern='/(uevent|status|state|name|type|modalias|driver_override|power_state|runtime_status|runtime_active_time|runtime_suspended_time|control|autosuspend_delay_ms|wakeup|enabled|enable|disable|rate|current_rate|min_rate|max_rate|voltage|microvolts|brightness|actual_brightness|max_brightness|bl_power|modes|mode|dpms|connected|online|present|capacity|temp|temperature|cur_state|max_state|governor|available_governors|scaling_cur_freq|scaling_min_freq|scaling_max_freq|available_frequencies|polling_interval|trans_stat|errors|stats)$'
for base in \
  /sys/class/drm /sys/class/backlight /sys/class/lcd /sys/class/graphics \
  /sys/class/thermal /sys/class/devfreq /sys/devices/system/cpu/cpufreq \
  /sys/class/power_supply /sys/class/input /sys/class/leds /sys/class/extcon \
  /sys/class/typec /sys/class/remoteproc /sys/class/rpmsg \
  /sys/kernel/iommu_groups /sys/bus/platform/devices \
  /sys/bus/i2c/devices /sys/bus/spi/devices /sys/power; do
  [ -d "$base" ] || continue
  n=$(sanitize_name "$base")
  copy_tree_capped "$base" "$out/sysfs/$n" "$sys_pattern" 1600 262144
  list_tree "$base" "$out/sysfs/$n-LISTING.txt"
done

# Firmware and module metadata, listings only unless small.
for base in /vendor/firmware_mnt/image /vendor/firmware /odm/firmware /lib/firmware; do
  [ -d "$base" ] || continue
  n=$(sanitize_name "$base")
  list_tree "$base" "$out/firmware/$n-LISTING.txt"
done
hash_or_stat /sys/kernel/btf/vmlinux "$out/devices/btf-vmlinux.txt"
hash_or_stat /sys/kernel/kheaders.tar.xz "$out/devices/kheaders.txt"
list_tree /sys/firmware "$out/devices/sys-firmware-LISTING.txt"
list_tree /sys/module "$out/devices/sys-module-LISTING.txt"

# Device tree is archived once, during the before snapshot.
if [ "$phase" = "before" ]; then
  for base in /proc/device-tree /sys/firmware/devicetree/base; do
    [ -d "$base" ] || continue
    {
      echo "source=$base"
      find "$base" -maxdepth 12 -printf '%y %m %s %p -> %l\n' 2>/dev/null
    } >"$session/device-tree-listing.txt" 2>/dev/null || true
    if have tar; then
      tar -czf "$session/device-tree.tar.gz" -C "${base%/*}" "${base##*/}" 2>"$session/device-tree-tar-errors.txt" || true
    fi
    find "$base" -maxdepth 12 -type f 2>/dev/null \
      | grep -E '/(reserved-memory|qcom,ion|ion|display|dsi|panel|refgen|qseecom|clock|regulator|iommu|smmu|remoteproc|firmware)' \
      | head -n 2000 \
      | while IFS= read -r f; do
          rel=${f#"$base"/}
          dst="$session/device-tree-decoded/$(echo "$rel" | tr '/' '_')"
          mkdir -p "${dst%/*}"
          od -An -tx1 "$f" >"$dst.hex.txt" 2>/dev/null || true
          strings "$f" >"$dst.strings.txt" 2>/dev/null || true
        done
    break
  done
fi

{
  echo "phase=$phase"
  echo "finished=$(timestamp)"
  echo "bytes=$(du -sk "$out" 2>/dev/null | awk '{print $1 * 1024}')"
} >"$out/SNAPSHOT.txt"
exit 0
