@echo off
rem Double-click this file to start "Frames" on Windows.
rem Custom port: run in cmd: python backend\scripts\launcher.py start --port 8080
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "RUN_DIR=%~dp0"
set "BACKEND_DIR=%RUN_DIR%..\backend"
set "LAUNCHER=%BACKEND_DIR%\scripts\launcher.py"

if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
  set "PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Starting Frames ...
"%PY%" "%LAUNCHER%" start --open-browser
set "STATUS=%ERRORLEVEL%"

echo.
if "%STATUS%"=="0" (
  echo Frames is running in background. You can close this window.
) else (
  echo Start failed, exit code %STATUS%. Please send the output above to the developer.
)
pause
exit /b %STATUS%
