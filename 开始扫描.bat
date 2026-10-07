@echo off
setlocal
cd /d "%~dp0" || exit /b 1
set PYTHONUTF8=1
set "media_python=python"
if exist ".venv\Scripts\python.exe" set "media_python=.venv\Scripts\python.exe"
if "%~1"=="" (
  "%media_python%" -X utf8 media_gui.py
) else (
  "%media_python%" -X utf8 media_scan.py --edit-tags %*
)
set "media_status=%errorlevel%"
if not "%media_status%"=="0" pause
exit /b %media_status%
