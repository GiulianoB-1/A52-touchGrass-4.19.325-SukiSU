@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
set "KIT_ROOT=%SCRIPT_DIR%.."
set "REPORT=%KIT_ROOT%\candidate-preflight.json"

echo A52 REFGEN candidate preflight
echo This check is local and non-destructive. It does not flash the phone.
echo.

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%SCRIPT_DIR%validate-a52-refgen-hardware-inputs.py" candidate "%KIT_ROOT%" --report "%REPORT%"
  set "RC=%ERRORLEVEL%"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3 was not found.
    exit /b 3
  )
  python "%SCRIPT_DIR%validate-a52-refgen-hardware-inputs.py" candidate "%KIT_ROOT%" --report "%REPORT%"
  set "RC=%ERRORLEVEL%"
)

echo.
if "%RC%"=="0" (
  echo PASS: candidate hash, size, required tools, and kit manifest are valid.
  echo Report: %REPORT%
  echo.
  echo No flashing was performed.
) else (
  echo FAIL: do not flash this candidate. Review %REPORT%
)
exit /b %RC%
