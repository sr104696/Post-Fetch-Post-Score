@echo off
setlocal

:: ── Reddit Gem Finder Launcher ──────────────────────────────────────────────
:: Put this .bat in the same folder as server.py and reddit_gem_finder.html
:: Double-click to run.

set "DIR=%~dp0"
set "SERVER=%DIR%server.py"
set "HTML=%DIR%reddit_gem_finder.html"

:: Check Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and try again.
    pause
    exit /b 1
)

:: Check server.py exists
if not exist "%SERVER%" (
    echo ERROR: server.py not found in %DIR%
    pause
    exit /b 1
)

:: Check HTML exists
if not exist "%HTML%" (
    echo ERROR: reddit_gem_finder.html not found in %DIR%
    pause
    exit /b 1
)

echo.
echo  Reddit Gem Finder
echo  -----------------
echo  Starting proxy on http://localhost:8000 ...
echo  Opening browser...
echo.
echo  Close this window to stop the server.
echo.

:: Open the HTML file in the default browser (slight delay so server starts first)
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000/reddit_gem_finder.html"

:: Start the server in this window (blocks until Ctrl+C or window close)
python "%SERVER%"

echo.
echo Server stopped.
pause
