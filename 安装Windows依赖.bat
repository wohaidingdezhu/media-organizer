@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-windows.txt
if errorlevel 1 goto failed
echo Setup complete. Double-click the scan launcher to start.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.9+ with pip and Tcl/Tk, and enable Add Python to PATH.
pause
exit /b 1
