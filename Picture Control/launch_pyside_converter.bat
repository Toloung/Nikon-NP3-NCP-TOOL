@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "APP_DIR=%~dp0"
set "APP_SCRIPT=%APP_DIR%pyside_converter.py"

cd /d "%APP_DIR%"

if not exist "%APP_SCRIPT%" (
  echo ERROR: pyside_converter.py was not found in:
  echo %APP_DIR%
  pause
  exit /b 1
)

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%APP_SCRIPT%"
  set "APP_EXIT=!errorlevel!"
) else (
  python "%APP_SCRIPT%"
  set "APP_EXIT=!errorlevel!"
)

if not "%APP_EXIT%"=="0" (
  echo.
  echo PySide6 converter exited with error code %APP_EXIT%.
  echo If PySide6 is missing, run:
  echo python -m pip install -r requirements.txt
  pause
)

exit /b %APP_EXIT%
