@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV=%USERPROFILE%\.local\bin\uv.exe"
    ) else (
        echo uv was not found. Install uv, then run this launcher again.
        echo Installation guide: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
)
"%UV%" run python -m emu --open-browser %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo Emulator Manager failed with exit code %RESULT%.
    pause
)
exit /b %RESULT%
