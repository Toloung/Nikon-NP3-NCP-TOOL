@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "APP_DIR=%~dp0"
set "APP_SCRIPT=%APP_DIR%photo_preset_to_nikon.py"

cd /d "%APP_DIR%"

if not exist "%APP_SCRIPT%" (
  echo ERROR: photo_preset_to_nikon.py was not found in:
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
  echo Converter exited with error code %APP_EXIT%.
  pause
)

exit /b %APP_EXIT%
