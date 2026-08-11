@echo off
setlocal EnableExtensions EnableDelayedExpansion
title TouchGrass Definitive FDR - SD Recorder

where adb >nul 2>nul || (
  echo ERROR: adb.exe is not in PATH or this folder.
  pause
  exit /b 1
)
if not exist "%~dp0tg_fdr_sd_daemon.sh" (
  echo ERROR: tg_fdr_sd_daemon.sh must be next to this BAT file.
  pause
  exit /b 1
)

echo [1/8] Waiting for device...
adb wait-for-device

echo [2/8] Checking ReSukiSU root and FDR device...
adb shell su -c "id" >nul 2>nul || (
  echo ERROR: ReSukiSU root is not working.
  pause
  exit /b 1
)
adb shell su -c "test -c /dev/tg_fdr" >nul 2>nul || (
  echo ERROR: /dev/tg_fdr not found. The definitive FDR kernel is not running.
  pause
  exit /b 1
)

echo [3/8] Waiting for Android boot completion...
set BOOT=
for /L %%I in (1,1,240) do (
  for /f "usebackq delims=" %%B in (`adb shell getprop sys.boot_completed 2^>nul`) do set BOOT=%%B
  if "!BOOT!"=="1" goto :booted
  timeout /t 1 /nobreak >nul
)
echo WARNING: sys.boot_completed did not become 1. Continuing.
:booted

echo [4/8] Locating physical external microSD...
set SD=
for /f "usebackq tokens=3" %%M in (`adb shell su -c "mount | grep ' /mnt/media_rw/' | head -n 1" 2^>nul`) do set SD=%%M
if not defined SD (
  echo ERROR: No /mnt/media_rw external SD mount found.
  pause
  exit /b 1
)
echo SD mount: !SD!

for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set TS=%%T
set REMOTE=!SD!/TouchGrassFDR/TG_!TS!

echo [5/8] Creating session and pushing root daemon...
adb push "%~dp0tg_fdr_sd_daemon.sh" /data/local/tmp/tg_fdr_sd_daemon.sh >nul || exit /b 1
adb shell su -c "chmod 0755 /data/local/tmp/tg_fdr_sd_daemon.sh; mkdir -p '!REMOTE!/static' '!REMOTE!/logs'" || exit /b 1

echo [6/8] Capturing pre-stream manifest...
adb shell su -c "cat /proc/tg_fdr_stats > '!REMOTE!/static/tg_fdr_stats_start.txt'"
adb shell su -c "uname -a > '!REMOTE!/static/uname.txt'"
adb shell su -c "cat /proc/version > '!REMOTE!/static/proc_version.txt'"
adb shell su -c "cat /proc/cmdline > '!REMOTE!/static/cmdline.txt'"
adb shell su -c "getprop > '!REMOTE!/static/getprop.txt'"
adb shell su -c "cat /proc/meminfo > '!REMOTE!/static/meminfo_start.txt'"
adb shell su -c "cat /proc/iomem > '!REMOTE!/static/iomem.txt'"
adb shell su -c "mount > '!REMOTE!/static/mount.txt'"
adb shell su -c "df -h > '!REMOTE!/static/df.txt'"
adb shell su -c "dmesg > '!REMOTE!/logs/dmesg_start.txt'"
adb shell su -c "logcat -b all -d -v threadtime > '!REMOTE!/logs/logcat_start.txt'"

echo [7/8] Starting continuous binary FDR stream on microSD...
adb shell su -c "rm -f /data/local/tmp/tg_fdr_daemon.pid /data/local/tmp/tg_fdr_daemon.log; nohup /data/local/tmp/tg_fdr_sd_daemon.sh '!REMOTE!' >/dev/null 2>&1 &"
timeout /t 2 /nobreak >nul
adb shell su -c "test -s /data/local/tmp/tg_fdr_daemon.pid && cat /data/local/tmp/tg_fdr_daemon.pid" || (
  echo ERROR: FDR daemon failed to start.
  adb shell su -c "cat /data/local/tmp/tg_fdr_daemon.log 2>/dev/null"
  pause
  exit /b 1
)
adb shell su -c "echo SESSION_START > /dev/tg_fdr"

echo [8/8] Recorder is running.
echo.
echo Session directory:
echo   !REMOTE!
echo.
echo Marker example:
echo   adb shell su -c "echo CAMERA_OPEN ^> /dev/tg_fdr"
echo.
echo When the complete validation session is finished, run:
echo   STOP_TOUCHGRASS_FDR.bat
echo.
> "%~dp0LAST_TOUCHGRASS_FDR_SESSION.txt" echo !REMOTE!
pause
