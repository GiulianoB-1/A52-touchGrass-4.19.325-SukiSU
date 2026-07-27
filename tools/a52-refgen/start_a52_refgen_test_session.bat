@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "KIT_ROOT=%SCRIPT_DIR%.."
set "OUTPUT_ROOT=%~1"
if "%OUTPUT_ROOT%"=="" set "OUTPUT_ROOT=%CD%\a52-refgen-test-sessions"

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%SCRIPT_DIR%manage-a52-refgen-test-session.py" start --kit-root "%KIT_ROOT%" --tools "%SCRIPT_DIR%" --output-root "%OUTPUT_ROOT%"
  set "RC=%ERRORLEVEL%"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3 was not found.
    exit /b 3
  )
  python "%SCRIPT_DIR%manage-a52-refgen-test-session.py" start --kit-root "%KIT_ROOT%" --tools "%SCRIPT_DIR%" --output-root "%OUTPUT_ROOT%"
  set "RC=%ERRORLEVEL%"
)

echo.
if "%RC%"=="0" (
  echo PASS: candidate identity was frozen into a new test-session directory.
  echo Latest session pointer: %OUTPUT_ROOT%\LATEST-A52-REFGEN-SESSION.txt
) else (
  echo FAIL: no device test should be started from this candidate state.
)
exit /b %RC%
