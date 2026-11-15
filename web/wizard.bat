@echo off
setlocal

rem Damai ticket config wizard - just double click this file.
rem All paths here are ASCII only on purpose (non-ASCII inside .bat gets garbled).

rem The wizard itself only needs the standard library, so it runs on the system
rem Python which ships with tkinter. The grabbing script is launched separately
rem by the wizard, using the isolated venv that has selenium installed.

set "WIZARD_PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%WIZARD_PYW%" (
    echo [ERROR] System Python with tkinter not found at:
    echo   %WIZARD_PYW%
    echo.
    echo Falling back to whatever "pythonw" is on PATH...
    start "" pythonw "%~dp0damai_wizard.py"
    exit /b 0
)

start "" "%WIZARD_PYW%" "%~dp0damai_wizard.py"
