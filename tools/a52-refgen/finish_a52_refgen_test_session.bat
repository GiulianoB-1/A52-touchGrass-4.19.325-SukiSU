@echo off
setlocal EnableExtensions

if "%~3"=="" (
  echo Usage:
  echo   finish_a52_refgen_test_session.bat ^<session-directory^> ^<collector.zip^|collector-directory^|ramoops-raw-1MiB.bin^> ^<unknown^|stable^|black^>
  exit /b 2
)

set "SESSION=%~1"
set "CAPTURE=%~2"
set "SCREEN=%~3"
set "SCRIPT_DIR=%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%SCRIPT_DIR%manage-a52-refgen-test-session.py" finish "%SESSION%" "%CAPTURE%" "%SCREEN%"
  set "RC=%ERRORLEVEL%"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3 was not found.
    exit /b 3
  )
  python "%SCRIPT_DIR%manage-a52-refgen-test-session.py" finish "%SESSION%" "%CAPTURE%" "%SCREEN%"
  set "RC=%ERRORLEVEL%"
)

echo.
if "%RC%"=="0" (
  echo PASS: evidence session completed and checksum-locked handoff files were created.
) else (
  echo FAIL: preserve the original capture and review the reported validation error.
)
exit /b %RC%
