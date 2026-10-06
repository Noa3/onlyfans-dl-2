@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
call setup_windows.bat --no-pause
if errorlevel 1 exit /b 1
:install
echo Installing the optional Playwright package and Chromium browser.
echo The browser download can be large. No login details are needed for installation.
".venv\Scripts\python.exe" -m pip install -r requirements-browser.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
echo.
echo Browser helper installed. Use Browser login inside the app.
pause
exit /b 0
:failed
echo Browser helper installation did not finish. Paste copied request still works without it.
pause
exit /b 1
