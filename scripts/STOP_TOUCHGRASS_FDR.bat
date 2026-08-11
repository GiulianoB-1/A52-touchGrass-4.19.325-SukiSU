@echo off
setlocal EnableExtensions EnableDelayedExpansion
title TouchGrass Definitive FDR - Stop

where adb >nul 2>nul || exit /b 1
if not exist "%~dp0LAST_TOUCHGRASS_FDR_SESSION.txt" (
  echo ERROR: LAST_TOUCHGRASS_FDR_SESSION.txt not found.
  pause
  exit /b 1
)
set /p REMOTE=<"%~dp0LAST_TOUCHGRASS_FDR_SESSION.txt"
adb wait-for-device

echo [1/5] Adding SESSION_END marker...
adb shell su -c "echo SESSION_END > /dev/tg_fdr"
timeout /t 1 /nobreak >nul

echo [2/5] Stopping SD stream...
adb shell su -c "if test -s /data/local/tmp/tg_fdr_daemon.pid; then kill $(cat /data/local/tmp/tg_fdr_daemon.pid) 2>/dev/null; fi"
timeout /t 1 /nobreak >nul

echo [3/5] Capturing final state and integrity hash...
adb shell su -c "cat /proc/tg_fdr_stats > '%REMOTE%/static/tg_fdr_stats_end.txt'"
adb shell su -c "cat /proc/meminfo > '%REMOTE%/static/meminfo_end.txt'"
adb shell su -c "dmesg > '%REMOTE%/logs/dmesg_end.txt'"
adb shell su -c "logcat -b all -d -v threadtime > '%REMOTE%/logs/logcat_end.txt'"
adb shell su -c "sha256sum '%REMOTE%/stream.tgfdr' > '%REMOTE%/stream.tgfdr.sha256'"
adb shell su -c "sync"

for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set TS=%%T
set LOCAL=%~dp0TOUCHGRASS_FDR_CAPTURE_%TS%
mkdir "%LOCAL%" >nul 2>nul

echo [4/5] Pulling root-owned SD session through a binary tar stream...
adb exec-out su -c "tar -C '%REMOTE%' -cf - ." > "%LOCAL%\capture.tar"
if errorlevel 1 (
  echo ERROR: Failed to export SD session.
  pause
  exit /b 1
)
tar -xf "%LOCAL%\capture.tar" -C "%LOCAL%"
del "%LOCAL%\capture.tar"

echo [5/5] Creating upload ZIP...
powershell -NoProfile -Command "Compress-Archive -Force -Path '%LOCAL%\*' -DestinationPath '%LOCAL%.zip'"

echo.
echo Finished:
echo   %LOCAL%.zip
pause
