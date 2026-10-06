@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" media_gui.py
  if errorlevel 1 pause
  exit /b
)
python media_gui.py
if errorlevel 1 pause
