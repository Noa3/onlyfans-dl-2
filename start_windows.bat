@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto launch
call setup_windows.bat --no-pause
if errorlevel 1 exit /b 1
:launch
".venv\Scripts\python.exe" onlyfans-dl.py
if errorlevel 1 (
    echo.
    echo The app could not start or closed with an error. Rerun setup_windows.bat if dependencies are missing.
    pause
    exit /b 1
)
