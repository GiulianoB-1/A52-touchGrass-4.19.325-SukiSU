@echo off
setlocal EnableExtensions EnableDelayedExpansion

where adb >NUL 2>&1
if errorlevel 1 (
  echo ERROR: adb.exe is not in PATH.
  exit /b 1
)

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%T
set OUT=TOUCHGRASS_HYBRID_REFERENCE_%TS%
set ZIP=%OUT%.zip

if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%ZIP%" del /q "%ZIP%"
mkdir "%OUT%"
mkdir "%OUT%\kernel-recorders"
mkdir "%OUT%\system"
mkdir "%OUT%\ksu-module"

echo [1/8] Waiting for device...
adb wait-for-device

echo [2/8] Verifying KernelSU root access...
adb shell su -c "id" > "%OUT%\system\root-check.txt" 2>&1
findstr /C:"uid=0" "%OUT%\system\root-check.txt" >NUL
if errorlevel 1 (
  echo ERROR: adb shell could not obtain KernelSU root.
  type "%OUT%\system\root-check.txt"
  exit /b 1
)

echo [3/8] Capturing precise kernel recorders...
adb exec-out su -c "cat /proc/tg_display_reference 2>/dev/null" > "%OUT%\kernel-recorders\tg_display_reference.txt"
adb exec-out su -c "cat /proc/tg_boot_reference 2>/dev/null" > "%OUT%\kernel-recorders\tg_boot_reference.txt"
adb exec-out su -c "cat /proc/tg_final_boot_reference 2>/dev/null" > "%OUT%\kernel-recorders\tg_final_boot_reference.txt"
adb exec-out su -c "cat /proc/tg_gpu_reference 2>/dev/null" > "%OUT%\kernel-recorders\tg_gpu_reference.txt"

echo [4/8] Capturing KernelSU companion recorder...
adb exec-out su -c "cat /data/adb/modules/tg_composer_reference/module.prop 2>/dev/null" > "%OUT%\ksu-module\module.prop"
adb exec-out su -c "cd /data/local/tmp && (toybox tar -cf - tg_ksu_composer_reference 2>/dev/null || tar -cf - tg_ksu_composer_reference 2>/dev/null)" > "%OUT%\ksu-module\tg_ksu_composer_reference.tar"
for %%F in ("%OUT%\ksu-module\tg_ksu_composer_reference.tar") do set TARSIZE=%%~zF
if "%TARSIZE%"=="0" (
  echo WARNING: KSU recorder tar is empty. Capturing directory listing for diagnosis.
  adb exec-out su -c "find /data/local/tmp/tg_ksu_composer_reference -maxdepth 4 -type f -print 2>/dev/null" > "%OUT%\ksu-module\recorder-files.txt"
) else (
  where tar >NUL 2>&1
  if not errorlevel 1 (
    mkdir "%OUT%\ksu-module\extracted"
    tar -xf "%OUT%\ksu-module\tg_ksu_composer_reference.tar" -C "%OUT%\ksu-module\extracted" >NUL 2>&1
  )
)

echo [5/8] Capturing Android/system state...
adb shell getprop > "%OUT%\system\getprop.txt" 2>&1
adb shell ps -A > "%OUT%\system\ps-A.txt" 2>&1
adb shell ps -A -T -o PID,TID,PPID,USER,STAT,NAME,ARGS > "%OUT%\system\ps-threads.txt" 2>&1
adb exec-out su -c "dmesg" > "%OUT%\system\dmesg.txt"
adb logcat -b all -d -v threadtime > "%OUT%\system\logcat-all.txt" 2>&1
adb exec-out su -c "ls -laZ /dev/dri /dev/kgsl-3d0 /dev/ion /dev/dma_heap /dev/binder /dev/hwbinder /dev/vndbinder 2>&1" > "%OUT%\system\device-nodes.txt"
adb exec-out su -c "ls -la /sys/class/drm 2>&1" > "%OUT%\system\sys-class-drm.txt"
adb exec-out su -c "cat /sys/kernel/debug/dri/0/state 2>/dev/null" > "%OUT%\system\drm-state.txt"
adb exec-out su -c "cat /sys/kernel/debug/binder/state 2>/dev/null" > "%OUT%\system\binder-state.txt"

echo [6/8] Capturing service publication state...
adb shell lshal > "%OUT%\system\lshal.txt" 2>&1
adb shell service list > "%OUT%\system\service-list.txt" 2>&1
adb shell dumpsys SurfaceFlinger > "%OUT%\system\dumpsys-SurfaceFlinger.txt" 2>&1

echo [7/8] Writing capture identity...
(
  echo TOUCHGRASS_HYBRID_REFERENCE_V1
  echo captured=%DATE% %TIME%
  echo expected_module=tg_composer_reference
  echo includes=tg_display_reference,tg_boot_reference,tg_final_boot_reference,tg_gpu_reference,KernelSU companion,logcat,dmesg,binder,DRM,services
) > "%OUT%\CAPTURE-IDENTITY.txt"

powershell -NoProfile -Command "$files=Get-ChildItem -Recurse -File '%OUT%'; $rows=foreach($f in $files){$h=Get-FileHash -Algorithm SHA256 $f.FullName; '{0}  {1}' -f $h.Hash.ToLower(),$f.FullName.Substring((Resolve-Path '%OUT%').Path.Length+1)}; $rows | Set-Content -Encoding ASCII '%OUT%\SHA256SUMS.txt'"

echo [8/8] Creating ZIP...
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\*' -DestinationPath '%ZIP%' -CompressionLevel Optimal -Force"
if errorlevel 1 (
  echo ERROR: Failed to create %ZIP%
  exit /b 1
)

echo.
echo DONE: %ZIP%
echo Upload this ZIP for golden-vs-GKI comparison.
endlocal
