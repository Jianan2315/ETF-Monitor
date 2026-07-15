@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ETF Monitor Launcher

set "PYTHON_CMD="

where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo ============================================================
    echo [ERROR] Python 3 was not found.
    echo ============================================================
    echo Install Python 3 and select "Add Python to PATH".
    echo Then run this BAT file again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating project virtual environment...
    %PYTHON_CMD% -m venv ".venv"
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to create .venv
        echo.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" "launcher.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Launcher finished with exit code %EXIT_CODE%.
echo.
pause
exit /b %EXIT_CODE%
