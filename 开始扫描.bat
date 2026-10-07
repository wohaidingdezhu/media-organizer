@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not "%~1"=="" goto scan
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" media_gui.py
  if errorlevel 1 pause
  exit /b
)
python media_gui.py
if errorlevel 1 pause
exit /b
:scan
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" media_scan.py --edit-tags %*
  if errorlevel 1 pause
  exit /b
)
python media_scan.py --edit-tags %*
if errorlevel 1 pause
