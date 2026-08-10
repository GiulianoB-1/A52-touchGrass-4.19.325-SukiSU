@echo off
setlocal EnableExtensions EnableDelayedExpansion
title TouchGrass Final Boot Reference Collector

where adb >nul 2>&1 || (
  echo [ERROR] adb.exe not found in PATH.
  pause
  exit /b 1
)
where powershell >nul 2>&1 || (
  echo [ERROR] PowerShell not found.
  pause
  exit /b 1
)

for /f %%A in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%A"
set "OUT=TOUCHGRASS_FINAL_BOOT_TRACE_%STAMP%"
set "ZIP=%OUT%.zip"
mkdir "%OUT%" >nul 2>&1

echo [1/8] Waiting for Android ADB...
adb wait-for-device
adb get-state > "%OUT%\adb_state.txt" 2>&1
adb shell su -c "id" > "%OUT%\root_check.txt" 2>&1

echo [2/8] Collecting the two in-kernel reference timelines...
adb shell su -c "cat /proc/tg_boot_reference" > "%OUT%\tg_boot_reference.txt" 2>&1
adb shell su -c "cat /proc/tg_gpu_reference" > "%OUT%\tg_gpu_reference.txt" 2>&1
adb shell su -c "cat /proc/kallsyms" > "%OUT%\kallsyms.txt" 2>&1

echo [3/8] Collecting kernel identity and logs...
adb shell uname -a > "%OUT%\uname.txt" 2>&1
adb shell cat /proc/version > "%OUT%\proc_version.txt" 2>&1
adb shell cat /proc/cmdline > "%OUT%\cmdline.txt" 2>&1
adb shell su -c "dmesg" > "%OUT%\dmesg.txt" 2>&1
adb exec-out su -c "cat /proc/config.gz" > "%OUT%\config.gz" 2>nul
adb shell cat /proc/interrupts > "%OUT%\interrupts.txt" 2>&1
adb shell cat /proc/iomem > "%OUT%\iomem.txt" 2>&1

echo [4/8] Collecting Android userspace boot state...
adb shell getprop > "%OUT%\getprop.txt" 2>&1
adb shell logcat -b all -d -v threadtime > "%OUT%\logcat_all_threadtime.txt" 2>&1
adb shell ps -A > "%OUT%\ps_A.txt" 2>&1
adb shell ps -A -Z > "%OUT%\ps_A_Z.txt" 2>&1
adb shell service list > "%OUT%\service_list.txt" 2>&1
adb shell dumpsys -l > "%OUT%\dumpsys_list.txt" 2>&1
adb shell dumpsys SurfaceFlinger > "%OUT%\dumpsys_SurfaceFlinger.txt" 2>&1
adb shell dumpsys display > "%OUT%\dumpsys_display.txt" 2>&1

echo [5/8] Collecting storage and filesystem state...
adb shell cat /proc/mounts > "%OUT%\mounts.txt" 2>&1
adb shell cat /proc/filesystems > "%OUT%\filesystems.txt" 2>&1
adb shell cat /proc/partitions > "%OUT%\partitions.txt" 2>&1
adb shell su -c "ls -lR /dev/block/by-name /dev/block/platform 2>/dev/null" > "%OUT%\block_by_name.txt" 2>&1
adb shell su -c "ls -lR /sys/block 2>/dev/null" > "%OUT%\sys_block.txt" 2>&1
adb shell su -c "ls -lR /sys/class/scsi_host 2>/dev/null" > "%OUT%\scsi_hosts.txt" 2>&1
adb shell su -c "ls -lR /sys/bus/platform/drivers/ufshcd 2>/dev/null" > "%OUT%\ufs_driver.txt" 2>&1

echo [6/8] Collecting IOMMU, Binder, GPU and display state...
adb shell su -c "ls -lR /sys/kernel/iommu_groups 2>/dev/null" > "%OUT%\iommu_groups.txt" 2>&1
adb shell su -c "ls -l /dev/binder* /dev/vndbinder* /dev/hwbinder* 2>/dev/null" > "%OUT%\binder_devices.txt" 2>&1
adb shell su -c "ls -lR /dev/binderfs 2>/dev/null" > "%OUT%\binderfs.txt" 2>&1
adb shell su -c "ls -lR /sys/class/kgsl 2>/dev/null" > "%OUT%\kgsl_sysfs.txt" 2>&1
adb shell su -c "ls -lR /sys/class/drm /sys/class/graphics 2>/dev/null" > "%OUT%\display_sysfs.txt" 2>&1
adb shell su -c "cat /sys/class/kgsl/kgsl-3d0/gpu_model 2>/dev/null; cat /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage 2>/dev/null" > "%OUT%\kgsl_summary.txt" 2>&1

echo [7/8] Collecting module, device and memory inventories...
adb shell cat /proc/modules > "%OUT%\modules.txt" 2>&1
adb shell cat /proc/devices > "%OUT%\devices.txt" 2>&1
adb shell cat /proc/meminfo > "%OUT%\meminfo.txt" 2>&1
adb shell su -c "find /sys/bus/platform/devices -maxdepth 1 -type l -print 2>/dev/null" > "%OUT%\platform_devices.txt" 2>&1

echo [8/8] Hashing and creating ZIP...
powershell -NoProfile -Command "$files=Get-ChildItem -LiteralPath '%OUT%' -File; $lines=foreach($f in $files){$h=(Get-FileHash -Algorithm SHA256 -LiteralPath $f.FullName).Hash.ToLower(); '{0}  {1}' -f $h,$f.Name}; $lines | Set-Content -Encoding ASCII -LiteralPath '%OUT%\SHA256SUMS.txt'"
powershell -NoProfile -Command "Compress-Archive -LiteralPath '%OUT%' -DestinationPath '%ZIP%' -Force"

echo.
echo [DONE] Upload this file to ChatGPT:
echo   %CD%\%ZIP%
explorer /select,"%CD%\%ZIP%" >nul 2>&1
pause
endlocal
