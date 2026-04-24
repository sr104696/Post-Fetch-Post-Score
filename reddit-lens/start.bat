@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python not found. Install from https://www.python.org/downloads/
    pause & exit /b 1
)

if not exist ".venv\" (
    echo First-time setup — installing packages...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -q -r requirements.txt
)

:: Launch with pythonw so no console window appears
start "" ".venv\Scripts\pythonw.exe" app.py
