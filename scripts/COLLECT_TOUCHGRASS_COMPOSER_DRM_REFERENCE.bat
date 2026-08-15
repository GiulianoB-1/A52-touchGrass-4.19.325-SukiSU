@echo off
setlocal EnableExtensions EnableDelayedExpansion
title TouchGrass Composer DRM Golden Reference Collector

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
set "OUT=TOUCHGRASS_COMPOSER_DRM_GOLDEN_%STAMP%"
set "ZIP=%OUT%.zip"
mkdir "%OUT%" >nul 2>&1

echo [1/9] Waiting for the known-good TouchGrass Android boot...
adb wait-for-device
adb get-state > "%OUT%\adb_state.txt" 2>&1
adb shell su -c "id" > "%OUT%\root_check.txt" 2>&1

echo [2/9] Capturing the precise Composer/DRM golden timeline...
adb shell su -c "cat /proc/tg_display_reference" > "%OUT%\tg_display_reference.txt" 2>&1
adb shell su -c "cat /proc/tg_boot_reference" > "%OUT%\tg_boot_reference.txt" 2>&1
adb shell su -c "cat /proc/tg_gpu_reference" > "%OUT%\tg_gpu_reference.txt" 2>&1
adb shell su -c "cat /proc/kallsyms" > "%OUT%\kallsyms.txt" 2>&1

echo [3/9] Capturing Composer, SurfaceFlinger and HAL process/thread state...
adb shell ps -A > "%OUT%\ps_A.txt" 2>&1
adb shell ps -A -T > "%OUT%\ps_A_T.txt" 2>&1
adb shell ps -A -Z > "%OUT%\ps_A_Z.txt" 2>&1
adb shell "pidof vendor.qti.hardware.display.composer-service" > "%OUT%\composer_pid.txt" 2>&1
adb shell "pidof surfaceflinger" > "%OUT%\surfaceflinger_pid.txt" 2>&1
adb shell "cat /proc/$(pidof vendor.qti.hardware.display.composer-service)/status 2>/dev/null" > "%OUT%\composer_status.txt" 2>&1
adb shell "cat /proc/$(pidof vendor.qti.hardware.display.composer-service)/maps 2>/dev/null" > "%OUT%\composer_maps.txt" 2>&1
adb shell "ls -l /proc/$(pidof vendor.qti.hardware.display.composer-service)/fd 2>/dev/null" > "%OUT%\composer_fds.txt" 2>&1

echo [4/9] Capturing HIDL/Binder service-registration state...
adb shell service list > "%OUT%\service_list.txt" 2>&1
adb shell lshal > "%OUT%\lshal.txt" 2>&1
adb shell lshal --debug > "%OUT%\lshal_debug.txt" 2>&1
adb shell dumpsys -l > "%OUT%\dumpsys_list.txt" 2>&1
adb shell dumpsys SurfaceFlinger > "%OUT%\dumpsys_SurfaceFlinger.txt" 2>&1
adb shell dumpsys display > "%OUT%\dumpsys_display.txt" 2>&1
adb shell su -c "ls -l /dev/binder* /dev/vndbinder* /dev/hwbinder* 2>/dev/null" > "%OUT%\binder_devices.txt" 2>&1
adb shell su -c "cat /sys/kernel/debug/binder/state 2>/dev/null" > "%OUT%\binder_state.txt" 2>&1
adb shell su -c "cat /sys/kernel/debug/binder/transactions 2>/dev/null" > "%OUT%\binder_transactions.txt" 2>&1

echo [5/9] Capturing exact DRM/display topology and properties visible to userspace...
adb shell su -c "ls -lR /dev/dri /sys/class/drm /sys/class/graphics 2>/dev/null" > "%OUT%\drm_sysfs.txt" 2>&1
adb shell su -c "for f in /sys/class/drm/*/status /sys/class/drm/*/modes /sys/class/drm/*/enabled /sys/class/drm/*/dpms; do echo ===$f===; cat $f 2>/dev/null; done" > "%OUT%\drm_connector_state.txt" 2>&1
adb shell su -c "find /sys/kernel/debug/dri -maxdepth 3 -type f -print 2>/dev/null" > "%OUT%\drm_debugfs_files.txt" 2>&1
adb shell su -c "for f in /sys/kernel/debug/dri/0/state /sys/kernel/debug/dri/0/clients /sys/kernel/debug/dri/0/name; do echo ===$f===; cat $f 2>/dev/null; done" > "%OUT%\drm_debugfs_state.txt" 2>&1

echo [6/9] Capturing allocator, GPU, IOMMU and device dependencies...
adb shell su -c "ls -l /dev/ion /dev/dma_heap/* /dev/kgsl-3d0 2>/dev/null" > "%OUT%\graphics_allocator_devices.txt" 2>&1
adb shell su -c "ls -lR /sys/class/kgsl 2>/dev/null" > "%OUT%\kgsl_sysfs.txt" 2>&1
adb shell su -c "ls -lR /sys/kernel/iommu_groups 2>/dev/null" > "%OUT%\iommu_groups.txt" 2>&1
adb shell su -c "find /sys/bus/platform/devices -maxdepth 1 -type l -print 2>/dev/null" > "%OUT%\platform_devices.txt" 2>&1

echo [7/9] Capturing Android properties and logs around graphics startup...
adb shell getprop > "%OUT%\getprop.txt" 2>&1
adb shell logcat -b all -d -v threadtime > "%OUT%\logcat_all_threadtime.txt" 2>&1
adb shell su -c "dmesg" > "%OUT%\dmesg.txt" 2>&1
adb shell uname -a > "%OUT%\uname.txt" 2>&1
adb shell cat /proc/version > "%OUT%\proc_version.txt" 2>&1
adb shell cat /proc/cmdline > "%OUT%\cmdline.txt" 2>&1
adb exec-out su -c "cat /proc/config.gz" > "%OUT%\config.gz" 2>nul

echo [8/9] Capturing memory/device inventories for dependency correlation...
adb shell cat /proc/modules > "%OUT%\modules.txt" 2>&1
adb shell cat /proc/devices > "%OUT%\devices.txt" 2>&1
adb shell cat /proc/meminfo > "%OUT%\meminfo.txt" 2>&1
adb shell cat /proc/mounts > "%OUT%\mounts.txt" 2>&1
adb shell cat /proc/interrupts > "%OUT%\interrupts.txt" 2>&1

echo [9/9] Hashing and creating the upload ZIP...
powershell -NoProfile -Command "$files=Get-ChildItem -LiteralPath '%OUT%' -File; $lines=foreach($f in $files){$h=(Get-FileHash -Algorithm SHA256 -LiteralPath $f.FullName).Hash.ToLower(); '{0}  {1}' -f $h,$f.Name}; $lines | Set-Content -Encoding ASCII -LiteralPath '%OUT%\SHA256SUMS.txt'"
powershell -NoProfile -Command "Compress-Archive -LiteralPath '%OUT%' -DestinationPath '%ZIP%' -Force"

echo.
echo [DONE] Upload this file to ChatGPT:
echo   %CD%\%ZIP%
explorer /select,"%CD%\%ZIP%" >nul 2>&1
pause
endlocal
