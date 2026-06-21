@echo off
setlocal

set "APP_DIR=%~dp0"
set "PYTHONW=%APP_DIR%.venv\Scripts\pythonw.exe"
set "PYTHON=%APP_DIR%.venv\Scripts\python.exe"

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" -m noxlab_share
    exit /b 0
)

if exist "%PYTHON%" (
    start "" "%PYTHON%" -m noxlab_share
    exit /b 0
)

echo Python virtual environment was not found.
echo Run these commands first:
echo   python -m venv .venv
echo   .\.venv\Scripts\Activate.ps1
echo   pip install -r requirements.txt
pause
exit /b 1
