@echo off
setlocal EnableExtensions

if "%~1"=="" (
  echo Usage:
  echo   validate_a52_refgen_capture.bat ^<collector.zip^|collector-directory^|ramoops-raw-1MiB.bin^> [unknown^|stable^|black]
  exit /b 2
)

set "CAPTURE=%~1"
set "SCREEN=%~2"
set "SCRIPT_DIR=%~dp0"
set "REPORT=%CD%\a52-refgen-capture-intake.json"
set "OUTPUT=%CD%\a52-refgen-display-diagnosis"
set "ANALYSE_ARGS="

if not "%SCREEN%"=="" (
  if /I not "%SCREEN%"=="unknown" if /I not "%SCREEN%"=="stable" if /I not "%SCREEN%"=="black" (
    echo ERROR: screen result must be unknown, stable, or black.
    exit /b 2
  )
  set "ANALYSE_ARGS=--analyse %SCREEN% --analysis-output %OUTPUT%"
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%SCRIPT_DIR%validate-a52-refgen-hardware-inputs.py" capture "%CAPTURE%" --report "%REPORT%" --tools "%SCRIPT_DIR%" %ANALYSE_ARGS%
  set "RC=%ERRORLEVEL%"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3 was not found.
    exit /b 3
  )
  python "%SCRIPT_DIR%validate-a52-refgen-hardware-inputs.py" capture "%CAPTURE%" --report "%REPORT%" --tools "%SCRIPT_DIR%" %ANALYSE_ARGS%
  set "RC=%ERRORLEVEL%"
)

echo.
if "%RC%"=="0" (
  echo PASS: capture intake validation completed.
  echo Report: %REPORT%
  if not "%SCREEN%"=="" echo Diagnosis: %OUTPUT%\diagnosis.md
) else (
  echo FAIL: preserve the original capture and review %REPORT%
)
exit /b %RC%
