@echo off
setlocal
set "FGO_MANAGER_ROOT=%~dp0"
set "FGO_SHORTCUT_DIR=%~1"
powershell -NoProfile -Command "$ErrorActionPreference = 'Stop'; $root = $env:FGO_MANAGER_ROOT; $folder = if ($env:FGO_SHORTCUT_DIR) { $env:FGO_SHORTCUT_DIR } else { [Environment]::GetFolderPath('DesktopDirectory') }; if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw 'Shortcut destination folder does not exist.' }; $path = Join-Path $folder 'FGO-py Manager.lnk'; if (Test-Path -LiteralPath $path) { throw 'FGO-py Manager.lnk already exists. Remove or rename it before creating a new shortcut.' }; $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path); $shortcut.TargetPath = Join-Path $root 'start-manager.bat'; $shortcut.WorkingDirectory = $root; $shortcut.IconLocation = (Join-Path $root 'deploy\Windows\manager-grail.ico') + ',0'; $shortcut.Description = 'Start FGO-py Emulator Manager and open the Web UI'; $shortcut.Save(); Write-Host ('Created shortcut: ' + $path)"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo Shortcut creation failed. See the error above.
    pause
)
exit /b %RESULT%
