@echo off
setlocal
cd /d "%~dp0"
echo OnlyFans DL - desktop setup
echo This creates a local .venv and installs packages from PyPI.
echo.
if exist ".venv\Scripts\python.exe" goto deps
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto missing_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto deps
:use_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
:deps
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import tkinter, requests, keyring"
if errorlevel 1 goto missing_tk
echo.
echo Setup finished. Open start_windows.bat to launch the app.
echo Optional: install_browser_windows.bat enables Browser login.
echo Paste copied request does not need the browser helper.
if /i not "%~1"=="--no-pause" pause
exit /b 0
:missing_python
echo Install Python 3.10 or newer, including Tcl/Tk, then rerun this file.
goto failed
:missing_tk
echo Python needs Tcl/Tk support. Modify or reinstall Python with Tcl/Tk selected.
:failed
echo Setup did not finish. Read the error above; no app credentials were requested.
pause
exit /b 1
