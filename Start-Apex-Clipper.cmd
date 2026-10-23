@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\launcher.py"
  goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "scripts\launcher.py"
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python "scripts\launcher.py"
  goto finished
)
echo Python 3.11 or 3.12 is required. Install Python, then open this launcher again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1
:finished
if errorlevel 1 pause
