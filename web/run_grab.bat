@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set NO_PROXY=localhost,127.0.0.1,::1
set no_proxy=localhost,127.0.0.1,::1
title Damai Ticket Grab
cd /d "%~dp0damai"

rem Interpreter: a .venv next to the repo root, else whatever "python" is on PATH.
set "PY=%~dp0..\.venv\Scripts\python.exe"
if exist "%PY%" goto have_py
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.9+ first,
    echo   then run: pip install -r requirements.txt
    pause
    exit /b 1
)
:have_py

echo [INFO] interpreter : %PY%
echo [INFO] working dir : %CD%
echo.
"%PY%" damai.py

echo.
echo ============================================
echo  Script exited. Press any key to close.
echo ============================================
pause >nul
