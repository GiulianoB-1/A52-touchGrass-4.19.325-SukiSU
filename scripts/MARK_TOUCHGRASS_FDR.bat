@echo off
setlocal EnableExtensions
if "%~1"=="" (
  echo Usage: MARK_TOUCHGRASS_FDR.bat MARKER_NAME
  echo Marker names are truncated to 31 bytes by the kernel recorder.
  exit /b 1
)
set MARK=%~1
adb wait-for-device
adb shell su -c "echo '%MARK%' > /dev/tg_fdr"
if errorlevel 1 (
  echo ERROR: marker write failed.
  exit /b 1
)
echo FDR marker added: %MARK%
