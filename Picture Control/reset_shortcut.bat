@echo off
cd /d "%~dp0"
echo Recreating photo_preset_to_nikon.lnk so it launches the current source...

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 "%~dp0recreate_shortcut.py"
) else (
    python "%~dp0recreate_shortcut.py"
)

if errorlevel 1 (
    echo.
    echo Shortcut setup failed.
    pause
    exit /b 1
)

echo.
echo Done. Use photo_preset_to_nikon.lnk or launch_converter.bat to start the converter.
pause
