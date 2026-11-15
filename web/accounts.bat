@echo off
setlocal

rem Damai multi-account manager - just double click this file.
rem All text here is ASCII only on purpose (non-ASCII inside .bat gets garbled).
rem
rem It is only a thin launcher: it picks the venv Python that has selenium
rem installed, then calls account_runner.py. You can also pass arguments:
rem     accounts.bat status
rem     accounts.bat login
rem     accounts.bat login --auto
rem     accounts.bat run
rem     accounts.bat disable "Account 02"

rem Interpreter: a .venv next to this file, else whatever "python" is on PATH.
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto have_py
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.9+ first,
    echo   then run: pip install -r damai\requirements.txt
    pause
    exit /b 1
)
:have_py

cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
rem Keep local webdriver traffic off any system proxy
set NO_PROXY=localhost,127.0.0.1,::1
set no_proxy=localhost,127.0.0.1,::1
title Damai Multi-Account

if not "%~1"=="" (
    "%PY%" account_runner.py %*
    echo.
    pause
    exit /b 0
)

:menu
cls
echo ============================================================
echo   Damai multi-account manager
echo ------------------------------------------------------------
echo   Passwords are optional. If you saved one, it is kept in
echo   credentials.json and never written to accounts.json.
echo   Captcha / slider / SMS are always done by you. Payment is
echo   never automated.
echo ------------------------------------------------------------
echo   [1] Check login status of every account      (status)
echo   [2] Log in accounts by hand                  (login)
echo   [3] Log in using saved passwords             (login --auto)
echo   [4] Start ticket grabbing                    (run)
echo   [5] Open accounts.json in Notepad
echo   [6] Show command help
echo   [0] Exit
echo ============================================================
echo.
set "choice="
set /p choice=Select an option: 

if "%choice%"=="1" (
    "%PY%" account_runner.py status
    goto done
)
if "%choice%"=="2" (
    "%PY%" account_runner.py login
    goto done
)
if "%choice%"=="3" (
    "%PY%" account_runner.py login --auto
    goto done
)
if "%choice%"=="4" (
    "%PY%" account_runner.py run
    goto done
)
if "%choice%"=="5" (
    start "" notepad "%~dp0accounts.json"
    goto menu
)
if "%choice%"=="6" (
    "%PY%" account_runner.py --help
    goto done
)
if "%choice%"=="0" exit /b 0
goto menu

:done
echo.
pause
exit /b 0
