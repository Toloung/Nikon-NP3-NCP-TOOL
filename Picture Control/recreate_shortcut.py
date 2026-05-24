from __future__ import annotations
import subprocess
from pathlib import Path

SHORTCUT_NAME = 'photo_preset_to_nikon.lnk'
LAUNCHER_NAME = 'launch_converter.bat'

PROJECT_DIR = Path(__file__).resolve().parent
SHORTCUT_PATH = PROJECT_DIR / SHORTCUT_NAME
TARGET_LAUNCHER = PROJECT_DIR / LAUNCHER_NAME
WORKING_DIR = PROJECT_DIR


def ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_powershell_command() -> str:
    return (
        "& {"
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(SHORTCUT_PATH)});"
        f"$s.TargetPath = {ps_quote(TARGET_LAUNCHER)};"
        "$s.Arguments = '';"
        f"$s.WorkingDirectory = {ps_quote(WORKING_DIR)};"
        f"$s.IconLocation = {ps_quote(TARGET_LAUNCHER)};"
        "$s.Description = 'Launch the current Photoshop to Nikon Converter source';"
        "$s.Save();"
        "}"
    )


def create_shortcut_with_win32com() -> bool:
    try:
        import win32com.client
    except ImportError:
        return False

    shell = win32com.client.Dispatch('WScript.Shell')
    shortcut = shell.CreateShortcut(str(SHORTCUT_PATH))
    shortcut.TargetPath = str(TARGET_LAUNCHER)
    shortcut.Arguments = ''
    shortcut.WorkingDirectory = str(WORKING_DIR)
    shortcut.IconLocation = str(TARGET_LAUNCHER)
    shortcut.Description = 'Launch the current Photoshop to Nikon Converter source'
    shortcut.Save()
    return True


def create_shortcut_with_powershell() -> bool:
    try:
        subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-Command',
                build_powershell_command(),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        print('PowerShell shortcut creation failed:')
        print(exc.stdout)
        print(exc.stderr)
        return False


def main() -> int:
    print('Recreating shortcut:', SHORTCUT_PATH)
    print('Target launcher:', TARGET_LAUNCHER)
    if not TARGET_LAUNCHER.exists():
        print('Error: launcher not found:', TARGET_LAUNCHER)
        return 1

    if create_shortcut_with_win32com():
        print('Shortcut created using win32com.client')
        return 0

    print('win32com.client not available; trying PowerShell fallback')
    if create_shortcut_with_powershell():
        print('Shortcut created using PowerShell')
        return 0

    print('Failed to create shortcut. Please run the script with pywin32 installed or update the shortcut manually.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
