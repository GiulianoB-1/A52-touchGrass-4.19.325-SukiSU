#!/system/bin/sh
set -u

OUT="${1:?usage: tg_fdr_static_collect.sh <output-dir> [label]}"
LABEL="${2:-snapshot}"
BASE="$OUT/static/$LABEL"
mkdir -p "$BASE" || exit 2

capture() {
    name="$1"
    shift
    data="$BASE/$name"
    status="$BASE/$name.status"
    mkdir -p "$(dirname "$data")"
    start="$(date +%s 2>/dev/null || echo 0)"
    sh -c "$*" >"$data" 2>&1
    rc=$?
    bytes="$(wc -c <"$data" 2>/dev/null || echo 0)"
    end="$(date +%s 2>/dev/null || echo 0)"
    {
        echo "rc=$rc"
        echo "bytes=$bytes"
        echo "start_epoch=$start"
        echo "end_epoch=$end"
        echo "command=$*"
    } >"$status"
}

capture kernel/uname.txt 'uname -a'
capture kernel/version.txt 'cat /proc/version'
capture kernel/cmdline.txt 'cat /proc/cmdline'
capture kernel/config.txt 'zcat /proc/config.gz 2>/dev/null || cat /proc/config.gz'
capture kernel/kallsyms.txt 'cat /proc/kallsyms'
capture kernel/modules.txt 'cat /proc/modules'
capture kernel/interrupts.txt 'cat /proc/interrupts'
capture kernel/iomem.txt 'cat /proc/iomem'
capture kernel/devices.txt 'cat /proc/devices'
capture kernel/filesystems.txt 'cat /proc/filesystems'
capture kernel/tg_fdr_stats.txt 'cat /proc/tg_fdr_stats'

capture memory/meminfo.txt 'cat /proc/meminfo'
capture memory/vmstat.txt 'cat /proc/vmstat'
capture memory/buddyinfo.txt 'cat /proc/buddyinfo'
capture memory/pagetypeinfo.txt 'cat /proc/pagetypeinfo'
capture memory/zoneinfo.txt 'cat /proc/zoneinfo'
capture memory/slabinfo.txt 'cat /proc/slabinfo 2>/dev/null'

capture android/getprop.txt 'getprop'
capture android/ps_az.txt 'ps -A -Z'
capture android/services.txt 'service list'
capture android/surfaceflinger.txt 'dumpsys SurfaceFlinger'
capture android/gfxinfo.txt 'dumpsys gfxinfo'
capture android/power.txt 'dumpsys power'
capture android/battery.txt 'dumpsys battery'
capture android/thermalservice.txt 'dumpsys thermalservice'
capture android/media_camera.txt 'dumpsys media.camera'
capture android/media_audio_flinger.txt 'dumpsys media.audio_flinger'
capture android/usb.txt 'dumpsys usb'
capture android/wifi.txt 'dumpsys wifi'
capture android/bluetooth_manager.txt 'dumpsys bluetooth_manager'

capture storage/mount.txt 'mount'
capture storage/df.txt 'df -h'
capture storage/partitions.txt 'cat /proc/partitions'
capture storage/block_tree.txt 'find /sys/class/block -maxdepth 2 -print 2>/dev/null | sort'
capture storage/ufs_tree.txt 'find /sys/bus/platform/drivers/ufshcd /sys/bus/platform/drivers/ufs_qcom -maxdepth 3 -print 2>/dev/null | sort'

capture debug/devices_deferred.txt 'cat /sys/kernel/debug/devices_deferred 2>/dev/null'
capture debug/clk_summary.txt 'cat /sys/kernel/debug/clk/clk_summary 2>/dev/null'
capture debug/regulator_summary.txt 'cat /sys/kernel/debug/regulator/regulator_summary 2>/dev/null'
capture debug/genpd_summary.txt 'cat /sys/kernel/debug/pm_genpd/pm_genpd_summary 2>/dev/null'
capture debug/wakeup_sources.txt 'cat /sys/kernel/debug/wakeup_sources 2>/dev/null'
capture debug/pinctrl_tree.txt 'find /sys/kernel/debug/pinctrl -maxdepth 3 -type f -print 2>/dev/null | sort'
capture debug/dri_tree.txt 'find /sys/kernel/debug/dri -maxdepth 4 -print 2>/dev/null | sort'
capture debug/drm_state.txt 'cat /sys/kernel/debug/dri/0/state 2>/dev/null'
capture debug/kgsl_tree.txt 'find /sys/kernel/debug/kgsl /sys/class/kgsl -maxdepth 4 -print 2>/dev/null | sort'
capture debug/binder_tree.txt 'find /sys/kernel/debug/binder -maxdepth 3 -type f -print 2>/dev/null | sort'
capture debug/binder_state.txt 'cat /sys/kernel/debug/binder/state 2>/dev/null'
capture debug/binder_stats.txt 'cat /sys/kernel/debug/binder/stats 2>/dev/null'
capture debug/usb_devices.txt 'cat /sys/kernel/debug/usb/devices 2>/dev/null'

capture power/power_supply_tree.txt 'find /sys/class/power_supply -maxdepth 3 -print 2>/dev/null | sort'
capture power/thermal_tree.txt 'find /sys/class/thermal -maxdepth 3 -print 2>/dev/null | sort'
capture power/cpufreq_tree.txt 'find /sys/devices/system/cpu/cpufreq -maxdepth 3 -print 2>/dev/null | sort'

capture input/input_devices.txt 'cat /proc/bus/input/devices 2>/dev/null'
capture audio/asound_tree.txt 'find /proc/asound -maxdepth 4 -print 2>/dev/null | sort'
capture media/video_nodes.txt 'ls -la /dev/video* /dev/media* 2>/dev/null'
capture usb/udc.txt 'find /sys/class/udc -maxdepth 3 -print 2>/dev/null | sort'
capture network/ip_addr.txt 'ip addr 2>/dev/null'
capture network/ip_link.txt 'ip link 2>/dev/null'
capture network/ip_route.txt 'ip route show table all 2>/dev/null'
capture network/proc_net.txt 'find /proc/net -maxdepth 2 -type f -print 2>/dev/null | sort'

DT="$BASE/device-tree.tar"
tar -C /sys/firmware/devicetree/base -cf "$DT" . >"$DT.log" 2>&1
rc=$?
printf 'rc=%s\nbytes=%s\ncommand=tar device-tree\n' "$rc" "$(wc -c <"$DT" 2>/dev/null || echo 0)" >"$DT.status"

PS="$BASE/pstore.tar"
tar -C /sys/fs/pstore -cf "$PS" . >"$PS.log" 2>&1
rc=$?
printf 'rc=%s\nbytes=%s\ncommand=tar pstore\n' "$rc" "$(wc -c <"$PS" 2>/dev/null || echo 0)" >"$PS.status"

echo "label=$LABEL" >"$BASE/SNAPSHOT_COMPLETE.txt"
echo "epoch=$(date +%s 2>/dev/null || echo 0)" >>"$BASE/SNAPSHOT_COMPLETE.txt"
sync
