@echo off
rem frames selfcheck 入口包装（Windows）— 见 docs/11-local-packaging.md §7
rem 用法：run\selfcheck.bat [--json] [--port 8000] [--skip-net]
rem 注意：Windows 实机尚未冒烟，见 docs/12-windows-smoke-checklist.md
setlocal
set "RUN_DIR=%~dp0"
set "BACKEND_DIR=%RUN_DIR%..\backend"
set "SCRIPT=%BACKEND_DIR%\scripts\selfcheck.py"

if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
  set "PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" "%SCRIPT%" %*
exit /b %ERRORLEVEL%
