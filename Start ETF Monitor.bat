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

:launch
".venv\Scripts\python.exe" "launcher.py"
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="130" goto restart_prompt

goto finished

:restart_prompt
echo.
echo ETF Monitor has stopped.
set "RESTART_OPTION="
set /p "RESTART_OPTION=Restart ETF Monitor? [Y/N] (press Enter or 0 to restart): "
if not defined RESTART_OPTION goto launch
if "%RESTART_OPTION%"=="0" goto launch
if /I "%RESTART_OPTION%"=="Y" goto launch
if /I "%RESTART_OPTION%"=="YES" goto launch
if /I "%RESTART_OPTION%"=="N" goto finished
if /I "%RESTART_OPTION%"=="NO" goto finished
echo Please enter Y, N, or 0. Press Enter to restart.
goto restart_prompt

:finished
echo.
echo Launcher finished with exit code %EXIT_CODE%.
echo.
pause
exit /b %EXIT_CODE%
