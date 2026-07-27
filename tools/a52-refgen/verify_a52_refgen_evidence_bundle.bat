@echo off
setlocal EnableExtensions

if "%~1"=="" (
  echo Usage:
  echo   verify_a52_refgen_evidence_bundle.bat ^<A52_REFGEN_EVIDENCE_....zip^>
  echo.
  echo Keep the matching .sha256 and .receipt.json files beside the ZIP.
  exit /b 2
)

set "BUNDLE=%~1"
set "SCRIPT_DIR=%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%SCRIPT_DIR%manage-a52-refgen-test-session.py" verify "%BUNDLE%"
  set "RC=%ERRORLEVEL%"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3 was not found.
    exit /b 3
  )
  python "%SCRIPT_DIR%manage-a52-refgen-test-session.py" verify "%BUNDLE%"
  set "RC=%ERRORLEVEL%"
)

echo.
if "%RC%"=="0" (
  echo PASS: the evidence bundle and all internal files are valid.
) else (
  echo FAIL: the evidence bundle is incomplete, altered, or missing its receipt files.
)
exit /b %RC%
