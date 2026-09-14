@echo off
rem Double-click this file to stop "Frames" on Windows.
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

echo Stopping Frames ...
"%PY%" "%LAUNCHER%" stop
set "STATUS=%ERRORLEVEL%"

echo.
if "%STATUS%"=="0" (
  echo Frames stopped. You can close this window.
) else (
  echo Stop did not fully succeed, exit code %STATUS%. Please send the output above to the developer.
)
pause
exit /b %STATUS%
