# Phase447B read-only Windows/PowerShell bring-up collector.
# powershell -ExecutionPolicy Bypass -File scripts/447b_collect_windows.ps1 -Variant B
param([ValidateSet("A","B","C")][string]$Variant = "A")
$ErrorActionPreference="Stop"
$stamp=Get-Date -Format "yyyyMMdd_HHmmss"
$out=Join-Path (Get-Location) ("P447B_" + $Variant + "_" + $stamp)
New-Item -ItemType Directory -Path $out -Force | Out-Null
function Save-ADB([string]$name,[string]$cmd){
  $target=Join-Path $out ($name + ".txt")
  ("Command: adb shell " + $cmd) | Out-File -FilePath $target -Encoding utf8
  try {
    & adb shell $cmd 2>&1 | Out-File -FilePath $target -Append -Encoding utf8
  } catch {
    ("ERROR " + $_.Exception.Message) | Out-File -FilePath $target -Append
  }
}
if (-not (Get-Command adb -ErrorAction SilentlyContinue)){
  throw "adb not found in PATH. Install Android platform-tools."
}
Write-Host "P447B: read-only ADB capture. No device writes are issued."
& adb wait-for-device
Save-ADB "identity" "uname -a; cat /proc/cmdline"
Save-ADB "touch" "getevent -lp 2>/dev/null | grep -i -C3 touch"
Write-Host "Sampling power supplies ~30 seconds after ADB became available"
Start-Sleep -Seconds 30
$power=@'
for d in battery usb ac otg; do echo ===$d===; for f in capacity status health present voltage_now current_now temp online type usb_type; do cat /sys/class/power_supply/$d/$f 2>/dev/null || true; done; done
'@
Save-ADB "battery-30s" $power
# P447E: collect *effective* sysfs paths and current SELinux labels.
# This is read-only and does not change SELinux enforcement or vendor files.
$labels=@'
for d in /sys/class/power_supply/*; do
  [ -e "$d" ] || continue
  echo "===== $d ====="
  readlink -f "$d" 2>&1
  ls -lZd "$d" "$(readlink -f "$d")" 2>&1
  for f in type present capacity voltage_now temp status online; do
    if [ -e "$d/$f" ]; then
      echo "--- $d/$f"
      readlink -f "$d/$f"
      ls -lZ "$d/$f" 2>&1
      cat "$d/$f" 2>&1
    fi
  done
done
'@
Save-ADB "power_supply_labels" $labels
$policy=@'
echo "===== effective SELinux enforcement ====="
getenforce 2>&1
echo "===== vendor/odm genfs_contexts files (if mounted as text) ====="
for f in /vendor/etc/selinux/genfs_contexts /odm/etc/selinux/genfs_contexts /system/etc/selinux/plat_genfs_contexts /system_ext/etc/selinux/genfs_contexts /product/etc/selinux/genfs_contexts; do
  if [ -f "$f" ]; then
    echo "### $f"
    grep -iE "genfscon sysfs.*(power_supply|battery|i2c|thermal|samsung_mobile)" "$f" 2>&1 || true
  fi
done
echo "===== visible policy candidates ====="
find /vendor/etc/selinux /odm/etc/selinux /system/etc/selinux /system_ext/etc/selinux -maxdepth 3 -type f -name "*genfs*" -print 2>/dev/null
'@
Save-ADB "selinux_genfscon_expected" $policy
Save-ADB "samsung_battery_platform" 'for d in /sys/bus/platform/devices/*battery* /sys/bus/platform/devices/samsung_mobile*; do [ -e "$d" ] && { echo "$d"; readlink -f "$d"; ls -Zd "$d"; }; done'
Save-ADB "hal_health_thermal_denials" 'dmesg 2>&1 | grep -E "hal_health_default|hal_thermal_default|P447E battery|P447E I2C|P447 W2 battery" | tail -n 300'
Save-ADB "dumpsys-battery" "dumpsys battery"
Save-ADB "thermal" 'ls /sys/class/thermal/thermal_zone*/{type,temp} 2>&1'
Write-Host "Sampling modem and DSP ~60 seconds after ADB became available"
Start-Sleep -Seconds 30
$remote=@'
for d in /sys/class/remoteproc/remoteproc*; do echo ===$d===; cat $d/name $d/state $d/firmware 2>/dev/null; done
'@
Save-ADB "remoteproc-60s" $remote
Save-ADB "qrtr-60s" "if command -v qrtr-lookup >/dev/null 2>&1; then qrtr-lookup; else echo qrtr-lookup_missing; fi; cat /proc/net/qrtr 2>/dev/null || true"
Save-ADB "vendor-dev-nodes" "ls -l /dev/subsys* /dev/glink* /dev/smd* /dev/diag /dev/at_mdm* /dev/qrtr* /dev/rmnet* 2>&1"
# Static rc references are not evidence of successful open(); parsed as init references.
Save-ADB "vendor-init-dev-references" 'for d in /vendor/etc/init /odm/etc/init /system/etc/init; do if test -d "$d"; then grep -rn "/dev/" "$d" 2>/dev/null | head -n 400; fi; done'
Save-ADB "vendor-hal-open-errors" 'logcat -d -b all -t 5000 2>&1 | grep -iE "avc: denied|open(at)?\(|cannot open|failed to open|no such file|/dev/" | tail -n 1200'

Save-ADB "net-devices" "ip link show"
Save-ADB "dmesg" "dmesg 2>&1 | tail -n 1800"
# P447: one-time 40-before/20-after HWC dump can occur before this
# collector. Keep EVERY marker line; the regular dmesg file is truncated.
Save-ADB "flicker-ring" 'dmesg 2>&1 | grep -F "P447 FLICKER_RING"'
# Safety diagnostics retain all emergency inhibit lines, including early boot.
Save-ADB "charger-guard" 'dmesg 2>&1 | grep -E "P447G CHG_GUARD|P447 W2 battery|qcom-spmi-adc5.*(invalid channel|probe)"'
Save-ADB "charger-vbus-iio" 'for d in /sys/bus/iio/devices/iio:device*; do [ -d "$d" ] || continue; echo "===== $d ====="; cat "$d/name" 2>&1; ls "$d" | grep -E "(in_voltage|in_temp|name)" | head -n 70; done; echo "===== POWER_SUPPLY ====="; for d in /sys/class/power_supply/*; do [ -e "$d" ] || continue; echo "$d"; cat "$d/uevent" 2>&1; done'
Save-ADB "logcat" "logcat -d -b all -t 2500 2>&1"
Write-Host ("P447B capture saved to " + $out + ". No firmware/NV commands used.")

# Optional offline /dev owner analysis; never needs root or changes the phone.
$py=Get-Command python -ErrorAction SilentlyContinue
$map=Join-Path (Get-Location) "merge/userspace-nodes.csv"
$audit=Join-Path (Get-Location) "scripts/447b_vendor_node_audit.py"
if ($py -and (Test-Path $map) -and (Test-Path $audit)) {
  & python $audit --input $out --map $map --out (Join-Path $out "devnode-audit")
  if ($LASTEXITCODE -ne 0) { Write-Warning "P447B node-audit failed. Logs retained for inspection." }
} else {
  Write-Host "P447B: run python scripts/447b_vendor_node_audit.py --input <capture-dir> --map merge/userspace-nodes.csv --out <output-dir> when Python is available"
}
